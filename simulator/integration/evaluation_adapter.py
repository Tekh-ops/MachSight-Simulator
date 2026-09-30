"""
simulator/integration/evaluation_adapter.py
===========================================
Adapter evaluating MachSight diagnostic predictions against hidden ground-truth
timelines produced by MachSight-Simulator.

CRITICAL INVARIANT:
MachSight runs blind. This adapter runs solely on the evaluator side, taking
pre-recorded or real-time diagnostic outputs from MachSight and aligning them
with ground_truth.jsonl.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from simulator.evaluation.ground_truth import GroundTruthTimeline
from simulator.evaluation.metrics import DetectionMetrics, DiagnosisMetrics
from simulator.integration.machsight_client import MachSightClient
from simulator.integration.result_parser import DiagnosticRunResult

logger = logging.getLogger(__name__)


def normalize_fault_class(raw_class: Optional[str]) -> str:
    """Normalize fault names and diagnosis descriptions into canonical categories."""
    if not raw_class:
        return "healthy"

    c = raw_class.lower().strip()
    if c in ("healthy", "normal", "none"):
        return "healthy"

    # Motor / drivetrain jam
    if "jam" in c or "obstruction" in c or "locked" in c or "stall" in c:
        return "motor_jam"

    # Bearing / drag / mechanical resistance
    if "drag" in c or "bearing" in c or "friction" in c or "resistance" in c:
        return "bearing_fault"

    # Wheel slip / traction loss
    if "slip" in c or "traction" in c or "spin" in c:
        return "wheel_slip"

    # Sensor faults
    if "ultrasonic" in c or "sensor_stuck" in c or "sensor_bias" in c or "sensor_disconnect" in c or "sensor" in c:
        return "sensor_fault"

    # Inconclusive
    if "inconclusive" in c or "unknown" in c:
        return "inconclusive"

    return c


@dataclass
class RunEvaluationResult:
    """Evaluation output for a single run comparing MachSight to Ground Truth."""
    scenario_name: str
    run_id: str
    ground_truth_has_fault: bool
    ground_truth_fault_types: List[str]
    predicted_has_anomaly: bool
    detection_latency_s: Optional[float]
    diagnosis_latency_s: Optional[float]
    expected_fault_class: str
    predicted_fault_class: str
    is_detection_correct: bool
    is_diagnosis_correct: bool
    sample_tp: int = 0
    sample_fp: int = 0
    sample_tn: int = 0
    sample_fn: int = 0


class MachSightEvaluator:
    """Evaluates MachSight predictions against ground-truth timelines."""

    def __init__(self, client: Optional[MachSightClient] = None) -> None:
        self.client = client

    def evaluate_run(
        self,
        run_dir: Path,
        run_result: DiagnosticRunResult,
    ) -> RunEvaluationResult:
        """Evaluate a single run's diagnostic results against its ground truth."""
        timeline = GroundTruthTimeline.load_from_run_dir(run_dir)
        scen_name = run_dir.parent.name
        run_id = run_dir.name

        has_gt_fault = timeline.has_faults
        primary_fault = timeline.fault_events[0] if timeline.fault_events else None
        expected_class = normalize_fault_class(primary_fault.fault_type if primary_fault else "healthy")
        fault_onset_t = primary_fault.start_time if primary_fault else None

        # Sample-level classification
        sample_tp = 0
        sample_fp = 0
        sample_tn = 0
        sample_fn = 0

        first_detection_ts: Optional[float] = None
        for det in run_result.anomaly_detections:
            t = det.timestamp
            is_anom = det.is_anomaly
            fault_active = timeline.is_fault_active_at(t)

            if is_anom:
                if fault_active:
                    sample_tp += 1
                else:
                    sample_fp += 1
                if first_detection_ts is None and fault_onset_t is not None and t >= fault_onset_t:
                    first_detection_ts = t
            else:
                if fault_active:
                    sample_fn += 1
                else:
                    sample_tn += 1

        # Event-level detection latency
        detection_latency_s: Optional[float] = None
        if fault_onset_t is not None and first_detection_ts is not None:
            detection_latency_s = max(0.0, first_detection_ts - fault_onset_t)

        # Diagnosis evaluation
        predicted_class_raw = run_result.final_diagnosis
        if not predicted_class_raw and not run_result.has_anomaly:
            predicted_class_raw = "healthy"
        predicted_class = normalize_fault_class(predicted_class_raw)

        diagnosis_latency_s: Optional[float] = None
        if fault_onset_t is not None and run_result.diagnosis_timestamp is not None:
            diagnosis_latency_s = max(0.0, run_result.diagnosis_timestamp - fault_onset_t)

        is_detection_correct = (run_result.has_anomaly == has_gt_fault)
        is_diagnosis_correct = (predicted_class == expected_class)

        return RunEvaluationResult(
            scenario_name=scen_name,
            run_id=run_id,
            ground_truth_has_fault=has_gt_fault,
            ground_truth_fault_types=[f.fault_type for f in timeline.fault_events],
            predicted_has_anomaly=run_result.has_anomaly,
            detection_latency_s=detection_latency_s,
            diagnosis_latency_s=diagnosis_latency_s,
            expected_fault_class=expected_class,
            predicted_fault_class=predicted_class,
            is_detection_correct=is_detection_correct,
            is_diagnosis_correct=is_diagnosis_correct,
            sample_tp=sample_tp,
            sample_fp=sample_fp,
            sample_tn=sample_tn,
            sample_fn=sample_fn,
        )

    def evaluate_dataset(
        self,
        dataset_path: Path,
        manifest_data: Dict[str, Any],
        run_results_map: Optional[Dict[str, DiagnosticRunResult]] = None,
    ) -> Tuple[DetectionMetrics, DiagnosisMetrics]:
        """Evaluate an entire dataset against MachSight results and aggregate metrics.

        Parameters
        ----------
        dataset_path: Path to dataset containing scenario runs.
        manifest_data: Loaded manifest.json dict.
        run_results_map: Pre-computed or streamed DiagnosticRunResult keyed by run directory name.
                         If None and a client is present, runs will be evaluated via client.
        """
        all_evals: List[RunEvaluationResult] = []
        all_classes: Set[str] = {"healthy"}

        # Collect runs
        for rel_run_dir in manifest_data.get("run_directories", []):
            run_dir = dataset_path / rel_run_dir
            run_key = run_dir.name

            run_result = None
            if run_results_map and run_key in run_results_map:
                run_result = run_results_map[run_key]
            else:
                # Fallback: create empty run result (e.g. offline evaluation without live MachSight)
                run_result = DiagnosticRunResult(run_id=run_key, machine_id=run_key)

            res = self.evaluate_run(run_dir, run_result)
            all_evals.append(res)
            all_classes.add(res.expected_fault_class)
            all_classes.add(res.predicted_fault_class)

        # 1. Aggregate Detection Metrics (sample-level + latency)
        total_tp = sum(e.sample_tp for e in all_evals)
        total_fp = sum(e.sample_fp for e in all_evals)
        total_tn = sum(e.sample_tn for e in all_evals)
        total_fn = sum(e.sample_fn for e in all_evals)

        # If sample counts were not present (event-level fallback)
        if (total_tp + total_fp + total_tn + total_fn) == 0:
            for e in all_evals:
                if e.ground_truth_has_fault and e.predicted_has_anomaly:
                    total_tp += 1
                elif not e.ground_truth_has_fault and e.predicted_has_anomaly:
                    total_fp += 1
                elif not e.ground_truth_has_fault and not e.predicted_has_anomaly:
                    total_tn += 1
                else:
                    total_fn += 1

        det_latencies = [e.detection_latency_s for e in all_evals if e.detection_latency_s is not None]
        detection_metrics = DetectionMetrics(
            tp=total_tp,
            fp=total_fp,
            tn=total_tn,
            fn=total_fn,
            latencies=det_latencies,
        ).compute()

        # 2. Aggregate Diagnosis Metrics (confusion matrix & classification)
        sorted_classes = sorted(all_classes)
        confusion: Dict[str, Dict[str, int]] = {c: {c2: 0 for c2 in sorted_classes} for c in sorted_classes}

        for e in all_evals:
            tc = e.expected_fault_class
            pc = e.predicted_fault_class
            confusion[tc][pc] += 1

        diag_latencies = [e.diagnosis_latency_s for e in all_evals if e.diagnosis_latency_s is not None]
        diagnosis_metrics = DiagnosisMetrics(
            confusion_matrix=confusion,
            latencies=diag_latencies,
        ).compute(all_classes=sorted_classes)

        return detection_metrics, diagnosis_metrics
