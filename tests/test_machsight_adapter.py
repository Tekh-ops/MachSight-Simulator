"""
tests/test_machsight_adapter.py
===============================
Unit tests for the MachSight integration adapter, result parser, and evaluator.
"""

import json
from pathlib import Path
import pytest

from simulator.evaluation.dataset_generator import DatasetGenerator
from simulator.integration import (
    AnomalyDetectionRecord,
    DiagnosticRunResult,
    InvestigationRecord,
    MachSightClient,
    MachSightEvaluator,
    ResultParser,
    normalize_fault_class,
)


def test_normalize_fault_class():
    """Verify mapping of MachSight textual diagnoses to canonical classes."""
    assert normalize_fault_class("Mechanical obstruction / drivetrain jam") == "motor_jam"
    assert normalize_fault_class("drivetrain_jam") == "motor_jam"
    assert normalize_fault_class("Mechanical drag") == "bearing_fault"
    assert normalize_fault_class("bearing_fault") == "bearing_fault"
    assert normalize_fault_class("wheel_slip") == "wheel_slip"
    assert normalize_fault_class("loss of traction") == "wheel_slip"
    assert normalize_fault_class("ultrasonic_disconnect") == "sensor_fault"
    assert normalize_fault_class("inconclusive") == "inconclusive"
    assert normalize_fault_class("healthy") == "healthy"
    assert normalize_fault_class(None) == "healthy"


def test_result_parser_broadcast():
    """Verify parsing of MachSight WebSocket broadcast messages."""
    # 1. Processed telemetry broadcast
    proc_msg = {
        "type": "processed",
        "event": "processed",
        "data": {
            "id": 101,
            "raw_id": 55,
            "timestamp": 12.34,
            "current_rpm_ratio": 0.005,
            "current_zscore": 3.2,
            "rpm_zscore": -2.1,
            "mahalanobis_distance": 6.8,
            "distance_plausible": 1,
            "bucket_used": "high_throttle",
            "is_anomaly": 1,
        }
    }
    rec1 = ResultParser.parse_broadcast_message(proc_msg)
    assert isinstance(rec1, AnomalyDetectionRecord)
    assert rec1.is_anomaly is True
    assert rec1.anomaly_score == 6.8
    assert rec1.timestamp == 12.34
    assert rec1.raw_id == 55

    # 2. Diagnosis broadcast
    diag_msg = {
        "type": "diagnosis",
        "event": "diagnosis",
        "data": {
            "trace_id": "tr-999",
            "machine_id": "car_01",
            "timestamp": 14.5,
            "step_type": "diagnosis",
            "payload": json.dumps({
                "action": "diagnose",
                "diagnosis": "Mechanical obstruction / drivetrain jam",
                "suspected_component": "drivetrain",
                "confidence": 0.95,
                "severity": "critical",
                "reasoning": "Current spike paired with zero wheel rotation.",
            }),
        }
    }
    rec2 = ResultParser.parse_broadcast_message(diag_msg)
    assert isinstance(rec2, InvestigationRecord)
    assert rec2.trace_id == "tr-999"
    assert rec2.diagnosis == "Mechanical obstruction / drivetrain jam"
    assert rec2.suspected_component == "drivetrain"
    assert rec2.confidence == 0.95


def test_evaluator_healthy_run(tmp_path: Path):
    """Evaluating a healthy run with no anomalies should produce TN and healthy diagnosis."""
    gen = DatasetGenerator(output_dir=tmp_path)
    ds_path = gen.generate(
        scenario_names=["healthy_cruise"],
        runs_per_scenario=1,
        base_seed=11,
        duration_override_s=2.0,
    )
    run_dir = ds_path / "healthy_cruise" / "run_000000"

    # Simulate MachSight output for healthy run
    run_result = DiagnosticRunResult(run_id="run_000000", machine_id="car_01")
    # Feed 10 normal samples
    for t in [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]:
        run_result.anomaly_detections.append(
            AnomalyDetectionRecord(timestamp=t, is_anomaly=False, anomaly_score=0.5)
        )

    evaluator = MachSightEvaluator()
    res = evaluator.evaluate_run(run_dir, run_result)

    assert not res.ground_truth_has_fault
    assert not res.predicted_has_anomaly
    assert res.is_detection_correct
    assert res.is_diagnosis_correct
    assert res.expected_fault_class == "healthy"
    assert res.predicted_fault_class == "healthy"
    assert res.sample_tn == 10
    assert res.sample_fp == 0


def test_evaluator_fault_run_with_latency(tmp_path: Path):
    """Evaluating a fault run should correctly compute detection latency and diagnosis."""
    gen = DatasetGenerator(output_dir=tmp_path)
    ds_path = gen.generate(
        scenario_names=["sudden_drivetrain_jam"],
        runs_per_scenario=1,
        base_seed=42,
        duration_override_s=6.0,
    )
    run_dir = ds_path / "sudden_drivetrain_jam" / "run_000000"

    import yaml
    scen_data = {
        "name": "sudden_drivetrain_jam",
        "faults": [{"type": "motor_jam", "start_s": 2.0, "duration_s": 3.0, "component": "drivetrain"}],
    }
    with (run_dir / "scenario.yaml").open("w", encoding="utf-8") as f:
        yaml.dump(scen_data, f)

    # sudden_drivetrain_jam starts at T=2.0s
    run_result = DiagnosticRunResult(run_id="run_000000", machine_id="car_01")
    # Before fault: healthy
    for t in [0.5, 1.0, 1.5]:
        run_result.anomaly_detections.append(
            AnomalyDetectionRecord(timestamp=t, is_anomaly=False, anomaly_score=0.4)
        )
    # At T=2.3 (300ms latency), anomaly detected
    run_result.anomaly_detections.append(
        AnomalyDetectionRecord(timestamp=2.3, is_anomaly=True, anomaly_score=8.5)
    )
    # Diagnosis at T=2.8
    run_result.investigations.append(
        InvestigationRecord(
            trace_id="tr-1",
            machine_id="car_01",
            timestamp=2.8,
            step_type="diagnosis",
            diagnosis="Mechanical obstruction / drivetrain jam",
            suspected_component="drivetrain",
            confidence=0.92,
            severity="critical",
            reasoning="Jam detected",
        )
    )

    evaluator = MachSightEvaluator()
    res = evaluator.evaluate_run(run_dir, run_result)

    assert res.ground_truth_has_fault
    assert res.predicted_has_anomaly
    assert res.is_detection_correct
    assert res.is_diagnosis_correct
    assert res.expected_fault_class == "motor_jam"
    assert res.predicted_fault_class == "motor_jam"
    assert res.detection_latency_s is not None
    assert abs(res.detection_latency_s - 0.3) < 0.05
    assert res.diagnosis_latency_s is not None
    assert abs(res.diagnosis_latency_s - 0.8) < 0.05


def test_evaluator_dataset_aggregation(tmp_path: Path):
    """Verify multi-run dataset evaluation builds confusion matrix and metrics."""
    gen = DatasetGenerator(output_dir=tmp_path)
    ds_path = gen.generate(
        scenario_names=["healthy_cruise", "sudden_drivetrain_jam"],
        runs_per_scenario=1,
        base_seed=10,
        duration_override_s=3.0,
    )
    with (ds_path / "manifest.json").open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Mock results
    results_map = {
        "run_000000": DiagnosticRunResult(
            run_id="run_000000",
            machine_id="car_01",
            anomaly_detections=[AnomalyDetectionRecord(timestamp=1.0, is_anomaly=False, anomaly_score=0.1)],
        ),
    }

    evaluator = MachSightEvaluator()
    det_metrics, diag_metrics = evaluator.evaluate_dataset(
        dataset_path=ds_path,
        manifest_data=manifest,
        run_results_map=results_map,
    )

    assert isinstance(det_metrics.f1, float)
    assert isinstance(diag_metrics.accuracy, float)
    assert "healthy" in diag_metrics.confusion_matrix
