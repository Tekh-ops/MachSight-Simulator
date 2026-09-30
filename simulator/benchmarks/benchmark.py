"""
simulator/benchmarks/benchmark.py
==================================
Benchmark Runner & Evaluation Engine.

Executes declarative benchmark suites, computes simulator data quality and
reproducibility metrics, evaluates diagnostic performance against ground truth,
and outputs structured JSON, CSV, and markdown reports.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from simulator.evaluation.dataset_generator import DatasetGenerator
from simulator.evaluation.ground_truth import GroundTruthTimeline
from simulator.evaluation.metrics import (
    BenchmarkSummaryReport,
    DataQualityMetrics,
    DatasetBalanceMetrics,
    DetectionMetrics,
    DiagnosisMetrics,
    ScenarioCorrectnessMetrics,
)
from simulator.telemetry.validation import SequenceTracker, TelemetryValidator

logger = logging.getLogger(__name__)

_BUILTIN_BENCHMARK_DIR = Path(__file__).parent / "definitions"


def list_benchmark_suites() -> List[str]:
    """Return sorted list of available benchmark suite names."""
    if not _BUILTIN_BENCHMARK_DIR.exists():
        return []
    return sorted(p.stem for p in _BUILTIN_BENCHMARK_DIR.glob("*.yaml"))


def load_benchmark_suite(name: str) -> dict:
    """Load benchmark definition dict by name or file path."""
    p = Path(name)
    if not p.exists() and _BUILTIN_BENCHMARK_DIR.exists():
        p = _BUILTIN_BENCHMARK_DIR / f"{name}.yaml"

    if not p.exists():
        raise FileNotFoundError(f"Benchmark suite '{name}' not found. Available: {list_benchmark_suites()}")

    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class BenchmarkRunner:
    """Orchestrates running benchmark experiments and calculating metrics."""

    def __init__(self, results_dir: Optional[Path] = None) -> None:
        self.results_base = results_dir or Path("results")

    def run_benchmark(
        self,
        suite_name_or_dict: Any,
        runs_per_scenario_override: Optional[int] = None,
        seed_override: Optional[int] = None,
        duration_override_s: Optional[float] = None,
        machsight_evaluator: Optional[Any] = None,
    ) -> BenchmarkSummaryReport:
        """Execute a benchmark suite and return summary metrics."""
        start_wall = time.time()

        if isinstance(suite_name_or_dict, str):
            suite_cfg = load_benchmark_suite(suite_name_or_dict)
            suite_name = suite_name_or_dict
        else:
            suite_cfg = suite_name_or_dict
            suite_name = suite_cfg.get("name", "custom_benchmark")

        scenarios = suite_cfg.get("scenarios", [])
        runs_per_scen = (
            runs_per_scenario_override
            if runs_per_scenario_override is not None
            else suite_cfg.get("runs_per_scenario", 1)
        )
        base_seed = (
            seed_override
            if seed_override is not None
            else suite_cfg.get("base_seed", 42)
        )
        dur_override = (
            duration_override_s
            if duration_override_s is not None
            else suite_cfg.get("duration_override_s")
        )

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        benchmark_id = f"{suite_name}_{ts}"
        run_output_dir = self.results_base / benchmark_id
        run_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Executing benchmark '%s' (%d scenarios × %d runs)...", suite_name, len(scenarios), runs_per_scen)

        # 1. Generate dataset for this benchmark run
        generator = DatasetGenerator(output_dir=run_output_dir / "dataset")
        dataset_path = generator.generate(
            scenario_names=scenarios,
            runs_per_scenario=runs_per_scen,
            base_seed=base_seed,
            dataset_name=suite_name,
            duration_override_s=dur_override,
        )

        # 2. Evaluate Data Quality & Scenario Correctness
        total_packets = 0
        total_lost = 0
        total_dups = 0
        total_dropouts = 0
        total_invalid = 0
        run_rows: List[Dict[str, Any]] = []

        manifest_file = dataset_path / "manifest.json"
        with manifest_file.open("r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        for rel_run_dir in manifest_data.get("run_directories", []):
            run_dir = dataset_path / rel_run_dir
            scen_name = run_dir.parent.name
            telemetry_path = run_dir / "telemetry.jsonl"
            gt_path = run_dir / "ground_truth.jsonl"
            meta_path = run_dir / "metadata.json"

            tracker = SequenceTracker()
            run_packets = 0
            run_invalid = 0
            run_dropouts = 0

            if telemetry_path.exists():
                with telemetry_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        packet = json.loads(line)
                        run_packets += 1

                        # Sequence tracking
                        seq = packet.get("sequence", run_packets)
                        track_res = tracker.record(seq)

                        # Check for sensor dropouts (packet has no sensor readings at all)
                        has_sensor_data = (
                            packet.get("distance_cm") is not None
                            or packet.get("current_a") is not None
                            or packet.get("rpm") is not None
                        )
                        if not has_sensor_data:
                            run_dropouts += 1

                        # Validate packet for GT leakage and obvious data issues.
                        # validate_dict tries to parse as a nested TelemetryMessage,
                        # but telemetry.jsonl stores flat machsight dicts — we
                        # specifically check for ground-truth key leaks instead.
                        from simulator.telemetry.validation import FORBIDDEN_GROUND_TRUTH_KEYS
                        leaked_keys = [k for k in FORBIDDEN_GROUND_TRUTH_KEYS if k in packet]
                        if leaked_keys:
                            run_invalid += 1

            total_packets += run_packets
            total_lost += tracker.stats["gaps_detected"]
            total_dups += tracker.stats["duplicates"]
            total_dropouts += run_dropouts
            total_invalid += run_invalid

            # Load ground truth timeline
            timeline = GroundTruthTimeline.load_from_run_dir(run_dir)

            run_rows.append({
                "scenario": scen_name,
                "run_dir": str(run_dir.name),
                "packets": run_packets,
                "lost": tracker.stats["gaps_detected"],
                "duplicates": tracker.stats["duplicates"],
                "dropouts": run_dropouts,
                "invalid": run_invalid,
                "fault_count": len(timeline.fault_events),
                "has_fault": timeline.has_faults,
            })

        loss_rate = total_lost / (total_packets + total_lost) if (total_packets + total_lost) > 0 else 0.0
        dropout_rate = total_dropouts / total_packets if total_packets > 0 else 0.0

        dq_metrics = DataQualityMetrics(
            total_packets=total_packets,
            packet_loss_rate=round(loss_rate, 4),
            duplicate_packets=total_dups,
            sensor_dropout_rate=round(dropout_rate, 4),
            invalid_measurement_count=total_invalid,
        )

        # 3. MachSight Diagnostic Evaluation (if evaluator provided)
        detection_metrics = DetectionMetrics()
        diagnosis_metrics = DiagnosisMetrics()

        if machsight_evaluator is not None:
            detection_metrics, diagnosis_metrics = machsight_evaluator.evaluate_dataset(
                dataset_path=dataset_path,
                manifest_data=manifest_data,
            )

        elapsed = time.time() - start_wall

        summary = BenchmarkSummaryReport(
            benchmark_id=benchmark_id,
            scenario_set=scenarios,
            total_runs=len(run_rows),
            successful_runs=manifest_data.get("successful_runs", len(run_rows)),
            failed_runs=manifest_data.get("failed_runs", 0),
            data_quality=dq_metrics,
            detection_metrics=detection_metrics,
            diagnosis_metrics=diagnosis_metrics,
            execution_time_s=elapsed,
        )

        # 4. Save results to disk: summary.json, runs.csv, confusion_matrix.csv, REPORT.md
        summary_path = run_output_dir / "summary.json"
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2)

        runs_csv_path = run_output_dir / "runs.csv"
        if run_rows:
            with runs_csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(run_rows[0].keys()))
                writer.writeheader()
                writer.writerows(run_rows)

        cm_csv_path = run_output_dir / "confusion_matrix.csv"
        if diagnosis_metrics.confusion_matrix:
            headers = ["true_class"] + list(diagnosis_metrics.confusion_matrix.keys())
            with cm_csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for tc, row in diagnosis_metrics.confusion_matrix.items():
                    writer.writerow([tc] + [row.get(col, 0) for col in diagnosis_metrics.confusion_matrix.keys()])

        report_md_path = run_output_dir / "REPORT.md"
        report_md_path.write_text(summary.generate_markdown(), encoding="utf-8")

        logger.info("Benchmark complete: %s (REPORT: %s)", benchmark_id, report_md_path)
        return summary
