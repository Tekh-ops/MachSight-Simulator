"""
tests/test_metrics.py
=====================
Unit tests for data quality, scenario correctness, and diagnostic evaluation metrics.
"""

import math
import pytest

from simulator.evaluation.metrics import (
    BenchmarkSummaryReport,
    DataQualityMetrics,
    DatasetBalanceMetrics,
    DetectionMetrics,
    DiagnosisMetrics,
    ScenarioCorrectnessMetrics,
)


def test_detection_metrics_computation():
    """Verify precision, recall, F1, FPR, and percentiles."""
    dm = DetectionMetrics(
        tp=80,
        fp=20,
        tn=90,
        fn=10,
        latencies=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    ).compute()

    # precision = 80 / (80 + 20) = 0.80
    assert dm.precision == 0.80
    # recall = 80 / (80 + 10) = 80/90 ~= 0.8889
    assert abs(dm.recall - (80 / 90)) < 1e-4
    # F1 = 2 * (0.8 * 80/90) / (0.8 + 80/90)
    assert dm.f1 > 0.84
    # FPR = 20 / (90 + 20) = 20/110 ~= 0.1818
    assert abs(dm.false_positive_rate - (20 / 110)) < 1e-4
    assert dm.median_latency_s == 0.55
    assert dm.p95_latency_s == 1.0


def test_detection_metrics_zero_division_safety():
    """Verify metrics handle all-zero edges gracefully without NaN or ZeroDivisionError."""
    dm = DetectionMetrics(tp=0, fp=0, tn=0, fn=0).compute()
    assert dm.precision == 0.0
    assert dm.recall == 0.0
    assert dm.f1 == 0.0
    assert dm.false_positive_rate == 0.0
    assert dm.median_latency_s is None


def test_diagnosis_metrics_multiclass_and_confusion():
    """Verify multi-class accuracy, per-class F1, and macro F1."""
    # Classes: healthy, motor_jam, bearing_fault
    cm = {
        "healthy": {"healthy": 10, "motor_jam": 0, "bearing_fault": 0},
        "motor_jam": {"healthy": 1, "motor_jam": 8, "bearing_fault": 1},
        "bearing_fault": {"healthy": 0, "motor_jam": 2, "bearing_fault": 8},
    }
    diag = DiagnosisMetrics(
        confusion_matrix=cm,
        latencies=[0.5, 0.6, 0.7],
    ).compute(all_classes=["healthy", "motor_jam", "bearing_fault"])

    # Total samples = 10 + 10 + 10 = 30
    # Correct = 10 + 8 + 8 = 26
    assert abs(diag.accuracy - (26 / 30)) < 1e-3
    assert diag.macro_f1 > 0.80
    assert diag.per_class_f1["healthy"] > 0.90
    assert diag.median_latency_s == 0.6


def test_benchmark_summary_report_markdown():
    """Verify markdown output generation."""
    report = BenchmarkSummaryReport(
        benchmark_id="test_suite_001",
        scenario_set=["healthy_cruise", "sudden_drivetrain_jam"],
        total_runs=2,
        successful_runs=2,
        failed_runs=0,
        data_quality=DataQualityMetrics(total_packets=200, packet_loss_rate=0.01),
        detection_metrics=DetectionMetrics(tp=10, fp=0, tn=10, fn=0).compute(),
        diagnosis_metrics=DiagnosisMetrics(
            accuracy=1.0,
            macro_f1=1.0,
            per_class_f1={"healthy": 1.0, "motor_jam": 1.0},
            confusion_matrix={"healthy": {"healthy": 1}, "motor_jam": {"motor_jam": 1}},
        ),
        execution_time_s=1.23,
    )
    md = report.generate_markdown()
    assert "# Benchmark Evaluation Report: test_suite_001" in md
    assert "healthy_cruise" in md
    assert "Precision" in md
    assert "Confusion Matrix" in md
