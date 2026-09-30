# Phase 4: Advanced Simulator + Automated Evaluation

## Overview

Phase 4 extends MachSight-Simulator with an automated dataset generation engine, a
benchmark suite runner, a telemetry replay facility, and a full MachSight integration
adapter for end-to-end blind diagnostic evaluation.

---

## Architecture

```
MachSight-Simulator/
├── simulator/
│   ├── scenarios/
│   │   ├── driver.py              # Driver profile engine
│   │   └── definitions/          # 13 built-in YAML scenarios
│   │
│   ├── evaluation/
│   │   ├── dataset_generator.py  # Multi-run headless dataset generation
│   │   ├── ground_truth.py       # Ground-truth schema + timeline
│   │   ├── metrics.py            # Evaluation metrics engine
│   │   ├── recorder.py           # Ground-truth recorder
│   │   └── replay.py             # TelemetryReplaySource (HAL boundary)
│   │
│   ├── benchmarks/
│   │   ├── benchmark.py          # BenchmarkRunner
│   │   └── definitions/
│   │       ├── benchmark_v1.yaml
│   │       └── regression_ci.yaml
│   │
│   └── integration/
│       ├── machsight_client.py   # MachSight WS + REST client
│       ├── result_parser.py      # Diagnostic result normalizer
│       └── evaluation_adapter.py # Ground-truth vs. prediction aligner
```

---

## Phase 4A: Dataset Generation

### What it produces

For each scenario run, the generator creates a directory:

```
datasets/<dataset_name>/<scenario>/run_XXXXXX/
    telemetry.jsonl      # Canonical flat packets (no GT leakage)
    ground_truth.jsonl   # Hidden physical state (evaluator-only)
    scenario.yaml        # Scenario definition snapshot
    metadata.json        # Run metadata and fault list
```

Plus a `manifest.json` at the dataset root:

```json
{
  "dataset_id": "my_dataset",
  "dataset_name": "my_dataset",
  "total_runs": 6,
  "successful_runs": 6,
  "scenario_set": ["healthy_cruise", "sudden_drivetrain_jam", ...],
  "configuration_hash": "abc123...",
  "run_directories": ["healthy_cruise/run_000000", ...]
}
```

### Ground-Truth Isolation Contract

> **CRITICAL**: `ground_truth.jsonl` is **never** sent to MachSight. It stays on-disk
> for the evaluator only. MachSight only ever sees `telemetry.jsonl`.

The following fields are forbidden in telemetry stream (enforced by `TelemetryValidator`):

- `is_healthy`, `active_faults`, `active_fault_details`
- `wheel_slip`, `mechanical_load`, `wheel_rpm_true`
- `motor_current_true`, `true_position_m`, `true_velocity_mps`

### CLI Usage

```bash
# Generate 5 runs per scenario for all built-in scenarios
python -m simulator generate-dataset --scenarios all --runs 5

# Generate for specific scenarios with a fixed seed
python -m simulator generate-dataset \
    --scenarios healthy_cruise,sudden_drivetrain_jam \
    --runs 10 --seed 42 --duration 30 --output-dir datasets/ --name my_dataset
```

---

## Phase 4B: Benchmark Suite Runner

### Running a Benchmark

```bash
python -m simulator benchmark --suite regression_ci
python -m simulator benchmark --suite benchmark_v1 --runs 3 --seed 100
```

### With Live MachSight Evaluation

```bash
# Start MachSight backend first, then run evaluation
python -m simulator benchmark --suite regression_ci \
    --evaluate-machsight --machsight-url http://localhost:8000
```

### Output Files

```
results/<suite>_<timestamp>/
    summary.json          # Full structured metrics
    runs.csv              # Per-run data quality rows
    confusion_matrix.csv  # Diagnosis confusion matrix
    REPORT.md             # Human-readable markdown report
    dataset/              # Generated runs
```

### Benchmark Suite YAML Format

```yaml
name: regression_ci
base_seed: 42
runs_per_scenario: 1
scenarios:
  - healthy_cruise
  - sudden_drivetrain_jam
  - gradual_motor_drag
  - wheel_slip
  - sensor_failures
```

---

## Phase 4B: MachSight Integration Adapter

The adapter lives in `simulator/integration/` and enables **blind evaluation** of
MachSight's diagnostic performance against hidden ground truth.

### Client

```python
from simulator.integration import MachSightClient, MachSightEvaluator

client = MachSightClient(base_url="http://localhost:8000")

# Stream telemetry and collect results
run_result = await client.stream_telemetry_file(
    telemetry_path=Path("datasets/.../telemetry.jsonl"),
    machine_id="test_car_01",
    wait_after_s=5.0,
)
```

### Evaluator

```python
evaluator = MachSightEvaluator(client=client)

# Single run evaluation
res = evaluator.evaluate_run(run_dir, run_result)
print(res.detection_latency_s, res.is_diagnosis_correct)

# Full dataset evaluation
det_metrics, diag_metrics = evaluator.evaluate_dataset(
    dataset_path=ds_path,
    manifest_data=manifest,
    run_results_map=run_results,
)
```

### Fault Class Normalization

`normalize_fault_class()` converts MachSight's natural-language diagnoses to canonical
evaluation classes:

| MachSight Diagnosis               | Canonical Class |
|----------------------------------|-----------------|
| `"Mechanical obstruction / drivetrain jam"` | `motor_jam`  |
| `"Mechanical drag"`              | `bearing_fault` |
| `"wheel_slip"`, `"loss of traction"` | `wheel_slip` |
| `"ultrasonic_disconnect"`        | `sensor_fault`  |
| `"inconclusive"`                 | `inconclusive`  |
| `"healthy"`, `None`              | `healthy`       |

---

## Phase 4A: Telemetry Replay

```bash
python -m simulator replay --file datasets/.../telemetry.jsonl --speed 1.0
```

The `TelemetryReplaySource` implements the same `TelemetrySource` HAL boundary as the
simulator, meaning MachSight sees identical canonical packets whether they come from
live physics or recorded playback.

---

## Metrics

### Simulator-Side Quality

| Metric                  | Description                                      |
|------------------------|--------------------------------------------------|
| `total_packets`         | Total canonical packets emitted                  |
| `packet_loss_rate`      | Sequence-gap detected drop rate                  |
| `duplicate_packets`     | Detected duplicate sequence numbers              |
| `sensor_dropout_rate`   | % of packets with zero usable sensor readings   |
| `invalid_measurement_count` | Packets with forbidden GT key leakage       |

### Anomaly Detection

| Metric              | Description                                   |
|--------------------|-----------------------------------------------|
| `precision`         | TP / (TP + FP)                               |
| `recall`            | TP / (TP + FN)                               |
| `f1`                | Harmonic mean of precision and recall         |
| `false_positive_rate` | FP / (FP + TN)                            |
| `median_latency_s`  | Median time from fault onset to first anomaly |
| `p95_latency_s`     | 95th percentile detection latency             |

### Fault Diagnosis

| Metric              | Description                                          |
|--------------------|------------------------------------------------------|
| `accuracy`          | Correctly classified / total events                  |
| `macro_f1`          | Un-weighted average F1 across all fault classes      |
| `per_class_f1`      | F1 per canonical fault class                        |
| `confusion_matrix`  | True class × predicted class count matrix            |
| `median_latency_s`  | Median time from fault onset to final diagnosis      |

---

## Test Coverage

```
tests/test_dataset_generator.py   Dataset generation, GT isolation, reproducibility
tests/test_replay.py              TelemetryReplaySource streaming and HAL boundary
tests/test_metrics.py             Metrics math correctness (edge cases + multiclass)
tests/test_machsight_adapter.py   Result parsing, normalization, evaluator logic
```

---

## Constraints Upheld

- ✅ `MachSight/` is **completely unmodified**
- ✅ Ground truth **never** enters `telemetry.jsonl` or any MachSight message
- ✅ All experiments reproducible by seed
- ✅ TelemetryReplaySource uses same HAL boundary as live simulator
- ✅ Evaluation adapter distinguishes simulator failures, transport failures, model failures
