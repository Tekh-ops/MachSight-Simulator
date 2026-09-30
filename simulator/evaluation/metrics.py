"""
simulator/evaluation/metrics.py
================================
Evaluation metrics engine for both simulator data-generation quality
and MachSight diagnostic performance.

Metrics Separations:
--------------------
1. Simulator-side metrics (Data quality, Scenario correctness, Dataset balance)
   - Calculated independently of MachSight.
   - Measures validity, reproducibility, and physical execution integrity.

2. Diagnostic metrics (Anomaly detection, Fault classification, Latencies)
   - Evaluates MachSight predictions against hidden ground-truth.
   - Distinct detection latency vs diagnosis latency.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ── Simulator-Side Quality Metrics ─────────────────────────────────────────────

@dataclass
class DataQualityMetrics:
    """Measures data transmission and sensor fidelity."""
    total_packets: int = 0
    packet_loss_rate: float = 0.0
    duplicate_packets: int = 0
    timestamp_jitter_ms: float = 0.0
    sensor_dropout_rate: float = 0.0
    invalid_measurement_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioCorrectnessMetrics:
    """Verifies that scenario execution matched declarative specifications."""
    fault_onset_error_s: float = 0.0
    fault_duration_error_s: float = 0.0
    severity_correct: bool = True
    is_reproducible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetBalanceMetrics:
    """Characterizes dataset class and fault distribution."""
    total_runs: int = 0
    healthy_runs: int = 0
    fault_runs: int = 0
    fault_type_distribution: Dict[str, int] = field(default_factory=dict)
    severity_distribution: Dict[str, int] = field(default_factory=dict)
    sensor_fault_distribution: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Diagnostic Performance Metrics ────────────────────────────────────────────

@dataclass
class DetectionMetrics:
    """Binary anomaly detection performance (sample-level or event-level)."""
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    false_positive_rate: float = 0.0
    latencies: List[float] = field(default_factory=list)
    median_latency_s: Optional[float] = None
    p95_latency_s: Optional[float] = None

    def compute(self) -> DetectionMetrics:
        """Compute precision, recall, F1, FPR, and percentiles from counts."""
        actual_pos = self.tp + self.fn
        actual_neg = self.tn + self.fp
        pred_pos = self.tp + self.fp

        self.precision = self.tp / pred_pos if pred_pos > 0 else 0.0
        self.recall = self.tp / actual_pos if actual_pos > 0 else 0.0
        if (self.precision + self.recall) > 0:
            self.f1 = 2 * (self.precision * self.recall) / (self.precision + self.recall)
        else:
            self.f1 = 0.0

        self.false_positive_rate = self.fp / actual_neg if actual_neg > 0 else 0.0

        if self.latencies:
            sorted_lat = sorted(self.latencies)
            n = len(sorted_lat)
            mid = n // 2
            self.median_latency_s = sorted_lat[mid] if n % 2 != 0 else (sorted_lat[mid - 1] + sorted_lat[mid]) / 2.0
            idx_95 = min(n - 1, int(math.ceil(0.95 * n)) - 1)
            self.p95_latency_s = sorted_lat[idx_95]
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "median_latency_s": round(self.median_latency_s, 4) if self.median_latency_s is not None else None,
            "p95_latency_s": round(self.p95_latency_s, 4) if self.p95_latency_s is not None else None,
        }


@dataclass
class DiagnosisMetrics:
    """Multi-class fault classification performance."""
    accuracy: float = 0.0
    macro_f1: float = 0.0
    per_class_f1: Dict[str, float] = field(default_factory=dict)
    confusion_matrix: Dict[str, Dict[str, int]] = field(default_factory=dict)
    latencies: List[float] = field(default_factory=list)
    median_latency_s: Optional[float] = None
    p95_latency_s: Optional[float] = None

    def compute(self, all_classes: Optional[List[str]] = None) -> DiagnosisMetrics:
        """Calculate overall accuracy and macro F1 from confusion matrix."""
        classes = all_classes or list(self.confusion_matrix.keys())
        total_samples = 0
        correct_samples = 0
        f1_scores: List[float] = []

        for c in classes:
            row = self.confusion_matrix.get(c, {})
            tp = row.get(c, 0)
            total_samples += sum(row.values())
            correct_samples += tp

            # Precision for class c
            col_sum = sum(self.confusion_matrix.get(other, {}).get(c, 0) for other in classes)
            prec = tp / col_sum if col_sum > 0 else 0.0

            # Recall for class c
            row_sum = sum(row.values())
            rec = tp / row_sum if row_sum > 0 else 0.0

            f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
            self.per_class_f1[c] = round(f1, 4)
            if row_sum > 0:  # Only average classes present in ground truth
                f1_scores.append(f1)

        self.accuracy = round(correct_samples / total_samples, 4) if total_samples > 0 else 0.0
        self.macro_f1 = round(sum(f1_scores) / len(f1_scores), 4) if f1_scores else 0.0

        if self.latencies:
            sorted_lat = sorted(self.latencies)
            n = len(sorted_lat)
            mid = n // 2
            self.median_latency_s = sorted_lat[mid] if n % 2 != 0 else (sorted_lat[mid - 1] + sorted_lat[mid]) / 2.0
            idx_95 = min(n - 1, int(math.ceil(0.95 * n)) - 1)
            self.p95_latency_s = sorted_lat[idx_95]
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "per_class_f1": self.per_class_f1,
            "confusion_matrix": self.confusion_matrix,
            "median_latency_s": round(self.median_latency_s, 4) if self.median_latency_s is not None else None,
            "p95_latency_s": round(self.p95_latency_s, 4) if self.p95_latency_s is not None else None,
        }


# ── Aggregate Benchmark Report ────────────────────────────────────────────────

@dataclass
class BenchmarkSummaryReport:
    """Consolidated benchmark results across N runs."""
    benchmark_id: str
    scenario_set: List[str]
    total_runs: int
    successful_runs: int
    failed_runs: int
    data_quality: DataQualityMetrics = field(default_factory=DataQualityMetrics)
    detection_metrics: DetectionMetrics = field(default_factory=DetectionMetrics)
    diagnosis_metrics: DiagnosisMetrics = field(default_factory=DiagnosisMetrics)
    execution_time_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "scenario_set": self.scenario_set,
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "data_quality": self.data_quality.to_dict(),
            "detection": self.detection_metrics.to_dict(),
            "diagnosis": self.diagnosis_metrics.to_dict(),
            "execution_time_s": round(self.execution_time_s, 2),
        }

    def generate_markdown(self) -> str:
        """Produce formatted human-readable report."""
        det = self.detection_metrics
        diag = self.diagnosis_metrics
        dq = self.data_quality

        lines = [
            f"# Benchmark Evaluation Report: {self.benchmark_id}",
            "",
            "## 1. Executive Summary",
            f"- **Scenarios**: {', '.join(self.scenario_set)}",
            f"- **Total Runs Evaluated**: {self.total_runs} (Success: {self.successful_runs}, Failed: {self.failed_runs})",
            f"- **Wall-Clock Time**: {self.execution_time_s:.2f} s",
            "",
            "## 2. Simulator Data Generation Quality",
            f"- **Packets Emitted**: {dq.total_packets}",
            f"- **Packet Loss Rate**: {dq.packet_loss_rate * 100:.2f}%",
            f"- **Duplicates Detected**: {dq.duplicate_packets}",
            f"- **Sensor Dropouts**: {dq.sensor_dropout_rate * 100:.2f}%",
            f"- **Invalid Measurements**: {dq.invalid_measurement_count}",
            "",
            "## 3. Anomaly Detection Performance",
            f"- **Precision**: {det.precision * 100:.2f}%",
            f"- **Recall**: {det.recall * 100:.2f}%",
            f"- **F1-Score**: {det.f1 * 100:.2f}%",
            f"- **False Positive Rate (FPR)**: {det.false_positive_rate * 100:.2f}%",
            f"- **Median Detection Latency**: {f'{det.median_latency_s:.3f} s' if det.median_latency_s is not None else 'N/A'}",
            f"- **p95 Detection Latency**: {f'{det.p95_latency_s:.3f} s' if det.p95_latency_s is not None else 'N/A'}",
            "",
            "## 4. Fault Diagnosis Performance",
            f"- **Classification Accuracy**: {diag.accuracy * 100:.2f}%",
            f"- **Macro F1**: {diag.macro_f1 * 100:.2f}%",
            f"- **Median Diagnosis Latency**: {f'{diag.median_latency_s:.3f} s' if diag.median_latency_s is not None else 'N/A'}",
            "",
            "### Per-Class F1 Scores",
            "| Fault Class | F1 Score |",
            "|---|---|",
        ]
        for c, score in diag.per_class_f1.items():
            lines.append(f"| `{c}` | {score * 100:.2f}% |")

        lines.extend([
            "",
            "### Confusion Matrix",
        ])
        if diag.confusion_matrix:
            headers = ["True \\ Pred"] + list(diag.confusion_matrix.keys())
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "---|"*len(headers))
            for true_c, row in diag.confusion_matrix.items():
                row_vals = [f"`{true_c}`"] + [str(row.get(col, 0)) for col in diag.confusion_matrix.keys()]
                lines.append("| " + " | ".join(row_vals) + " |")

        return "\n".join(lines) + "\n"
