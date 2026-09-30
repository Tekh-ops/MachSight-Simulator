# Evaluation Guide

## Overview

The evaluation system performs **blind end-to-end assessment** of MachSight's
diagnostic accuracy. MachSight receives only canonical telemetry and is scored
against hidden ground truth stored exclusively on the evaluator side.

---

## Evaluation Architecture

```
telemetry.jsonl
      │
      ▼ (stream via WebSocket)
MachSight Backend
      │
      ▼ (broadcast: anomaly events, diagnosis)
MachSightClient (listener)
      │
      ▼
DiagnosticRunResult (anomaly_detections + investigations)
      │
      ▼
MachSightEvaluator
      │
  ground_truth.jsonl (hidden, evaluator-only)
      │
      ▼
DetectionMetrics + DiagnosisMetrics
```

MachSight **never sees** `ground_truth.jsonl`. The evaluator aligns MachSight's
outputs to ground truth after the fact.

---

## Running Evaluation

### Against Live MachSight Backend

```bash
# Start MachSight backend first:
cd ../MachSight && uvicorn backend.main:app --port 8000

# Then run benchmark with evaluation:
python -m simulator benchmark --suite regression_ci \
    --evaluate-machsight --machsight-url http://localhost:8000
```

### Programmatic

```python
import asyncio
from simulator.integration import MachSightClient, MachSightEvaluator
from pathlib import Path
import json

client = MachSightClient(base_url="http://localhost:8000")
evaluator = MachSightEvaluator(client=client)

# Load a pre-generated dataset
dataset_path = Path("datasets/experiment_001")
with (dataset_path / "manifest.json").open() as f:
    manifest = json.load(f)

async def run():
    run_results = {}
    for rel_dir in manifest["run_directories"]:
        run_dir = dataset_path / rel_dir
        telem_file = run_dir / "telemetry.jsonl"
        result = await client.stream_telemetry_file(
            telemetry_path=telem_file,
            machine_id="eval_car",
            wait_after_s=5.0,
        )
        run_results[run_dir.name] = result

    det, diag = evaluator.evaluate_dataset(dataset_path, manifest, run_results)
    print(f"Detection F1: {det.f1:.3f}, Diagnosis Accuracy: {diag.accuracy:.3f}")

asyncio.run(run())
```

---

## Detection Metrics

Computed at the **sample level** (per anomaly detection event) and **event level**
(per fault episode):

| Metric                | Formula                          | Notes                              |
|----------------------|----------------------------------|------------------------------------|
| **Precision**         | TP / (TP + FP)                   | How often anomaly alerts are real  |
| **Recall**            | TP / (TP + FN)                   | How many real faults are caught    |
| **F1**                | 2·P·R / (P + R)                  | Balance of precision and recall    |
| **FPR**               | FP / (FP + TN)                   | False alarm rate in healthy periods|
| **Median Latency**    | median(t_detected - t_onset)     | Time from fault onset to detection |
| **p95 Latency**       | 95th percentile latency          | Worst-case detection delay         |

### Sample-level counting rules

- **TP**: MachSight flags anomaly during a fault-active window (per ground truth)
- **FP**: MachSight flags anomaly during a healthy window
- **TN**: MachSight does not flag anomaly during healthy window
- **FN**: MachSight does not flag anomaly during fault-active window

---

## Diagnosis Metrics

Multi-class classification performance over fault episodes:

| Metric              | Description                                   |
|--------------------|-----------------------------------------------|
| **Accuracy**        | Correct diagnoses / total fault episodes      |
| **Macro F1**        | Unweighted average F1 over all fault classes  |
| **Per-class F1**    | F1 for each canonical fault class             |
| **Confusion Matrix**| True class × predicted class count matrix     |
| **Median Latency**  | Median time from fault onset to final diagnosis |

### Canonical Fault Classes

| Class         | MachSight Diagnosis Keywords                         |
|--------------|------------------------------------------------------|
| `healthy`     | No anomaly detected, or `None`                       |
| `motor_jam`   | "jam", "obstruction", "locked", "stall"              |
| `bearing_fault` | "drag", "bearing", "friction", "resistance"        |
| `wheel_slip`  | "slip", "traction", "spin"                           |
| `sensor_fault`| "ultrasonic", "sensor_stuck", "sensor_bias", "dropout" |
| `inconclusive`| "inconclusive", "unknown"                            |

---

## Reading Benchmark Reports

After a benchmark run, outputs in `results/<suite>_<timestamp>/`:

### summary.json

```json
{
  "benchmark_id": "regression_ci_20260930_163910",
  "total_runs": 5,
  "successful_runs": 5,
  "data_quality": {
    "total_packets": 3995,
    "packet_loss_rate": 0.0,
    "duplicate_packets": 0,
    "sensor_dropout_rate": 0.0008,
    "invalid_measurement_count": 0
  },
  "detection": {
    "precision": 0.90,
    "recall": 0.88,
    "f1": 0.89,
    "false_positive_rate": 0.05,
    "median_latency_s": 0.42,
    "p95_latency_s": 1.2
  },
  "diagnosis": {
    "accuracy": 0.85,
    "macro_f1": 0.83,
    "per_class_f1": {"motor_jam": 0.95, "healthy": 1.0, ...}
  }
}
```

### REPORT.md

Human-readable report with tables and confusion matrix.

### runs.csv

Per-run breakdown including packet counts, dropouts, and fault presence.

---

## Error Modes

The evaluation system distinguishes three categories of failure:

| Category              | Recorded In                    | Description                          |
|----------------------|--------------------------------|--------------------------------------|
| **Simulator failure** | `metadata.json` error field    | Run generation failed                |
| **Transport failure** | `run_result.infrastructure_errors` | WS connection lost mid-stream    |
| **Model failure**     | `run_result.investigations`    | MachSight returned inconclusive/wrong |

Only transport failures reduce `successful_runs` in the manifest. Model failures are
captured and scored as incorrect diagnoses.
