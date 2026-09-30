# Dataset Generation Guide

## Overview

The `DatasetGenerator` creates reproducible, multi-run synthetic telemetry datasets
for training, evaluation, and regression testing of diagnostic systems.

Every dataset is self-contained and captures:
- **Observable telemetry** only in `telemetry.jsonl` (no ground-truth leakage)
- **Hidden physical state** separately in `ground_truth.jsonl`
- **Declarative scenario config** in `scenario.yaml` (reproducibility snapshot)
- **Run metadata** in `metadata.json`

---

## Quick Start

### Command Line

```bash
# All built-in scenarios, 1 run each
python -m simulator generate-dataset --scenarios all --runs 1

# Selected scenarios, 5 runs, fixed seed, 30s duration
python -m simulator generate-dataset \
    --scenarios healthy_cruise,sudden_drivetrain_jam \
    --runs 5 --seed 42 --duration 30 --name experiment_001
```

### Python API

```python
from simulator.evaluation.dataset_generator import DatasetGenerator
from pathlib import Path

gen = DatasetGenerator(output_dir=Path("datasets"))
ds_path = gen.generate(
    scenario_names=["healthy_cruise", "sudden_drivetrain_jam"],
    runs_per_scenario=5,
    base_seed=42,
    dataset_name="experiment_001",
    duration_override_s=30.0,
)
```

---

## Output Structure

```
datasets/
└── experiment_001/
    ├── manifest.json
    ├── healthy_cruise/
    │   ├── run_000000/
    │   │   ├── telemetry.jsonl       # Observable telemetry ONLY
    │   │   ├── ground_truth.jsonl    # Hidden physical state
    │   │   ├── scenario.yaml         # Declarative config snapshot
    │   │   └── metadata.json         # Run summary
    │   ├── run_000001/
    │   └── ...
    └── sudden_drivetrain_jam/
        ├── run_000000/
        └── ...
```

### manifest.json

```json
{
  "dataset_id": "experiment_001",
  "dataset_name": "experiment_001",
  "total_runs": 10,
  "successful_runs": 10,
  "failed_runs": 0,
  "scenario_set": ["healthy_cruise", "sudden_drivetrain_jam"],
  "configuration_hash": "a1b2c3d4...",
  "seeds": [42, 43, 44, 45, 46, 47, 48, 49, 50, 51],
  "run_directories": [
    "healthy_cruise/run_000000",
    "healthy_cruise/run_000001",
    ...
  ]
}
```

### telemetry.jsonl

Each line is a flat JSON dict compatible with MachSight `IncomingTelemetry`:

```json
{"schema_version": 2, "machine_id": "rc-sim-01", "car_id": "rc-sim-01",
 "asset_type": "rc_vehicle", "timestamp": 1234567890.1, "sequence": 42,
 "mode": "forward", "pwm_command": 160, "current_a": 1.34, "distance_cm": 85.2}
```

> **Ground-truth keys (`is_healthy`, `wheel_slip`, `active_faults`, etc.) are
> NEVER present in this file.**

### ground_truth.jsonl

Hidden from MachSight. Evaluator-only. Each line:

```json
{"sim_time": 12.4, "wall_time": 1234567890.1, "position_m": 8.2,
 "velocity_mps": 0.66, "wheel_rpm_true": 192.1, "motor_current_true": 1.34,
 "battery_state": 0.99, "battery_voltage": 7.38, "wheel_slip": 0.0,
 "mechanical_load": 0.05, "obstacle_distance_m": 76.8,
 "is_healthy": true, "active_faults": [], "active_fault_details": []}
```

---

## Reproducibility

Dataset runs with the same `base_seed` and configuration produce identical sensor
values, enabling rigorous regression testing:

```python
gen1 = DatasetGenerator(output_dir=Path("d1"))
gen2 = DatasetGenerator(output_dir=Path("d2"))

path1 = gen1.generate(["healthy_cruise"], 1, base_seed=42, duration_override_s=10)
path2 = gen2.generate(["healthy_cruise"], 1, base_seed=42, duration_override_s=10)

# These are bit-for-bit identical:
telem1 = (path1 / "healthy_cruise/run_000000/telemetry.jsonl").read_text()
telem2 = (path2 / "healthy_cruise/run_000000/telemetry.jsonl").read_text()
```

Configuration hash in `manifest.json` also changes when scenario names, seed, or run
count changes — useful for detecting accidental config drift.

---

## Loading Ground Truth

```python
from simulator.evaluation.ground_truth import GroundTruthTimeline

timeline = GroundTruthTimeline.load_from_run_dir(run_dir)

# Check if a fault was active at a given simulation time
timeline.is_fault_active_at(12.5)  # bool

# Get the physical machine state at fault onset
onset_state = timeline.get_state_at_onset("motor_jam")

# Primary fault type at time t
fault_type = timeline.get_primary_fault_type(15.0)

# Total fault events in this run
print(timeline.fault_events)  # List[FaultEvent]
```

---

## Available Scenarios

| Scenario                  | Type          | Notes                              |
|--------------------------|---------------|-------------------------------------|
| `healthy_cruise`          | Healthy       | Baseline, no faults                |
| `gradual_motor_drag`      | Mechanical    | Progressive bearing wear           |
| `sudden_drivetrain_jam`   | Mechanical    | Immediate full jam at T=40s        |
| `wheel_slip`              | Mechanical    | Traction loss on low-friction surface |
| `sensor_failures`         | Sensor        | Multi-sensor disconnection         |
| `intermittent_fault`      | Electrical    | Cycling current spike fault        |
| `obstacle_approach`       | Environmental | Object detected at close range     |
| `full_demo`               | Multi-fault   | Compound scenario for demos        |
| `compound_drag_obstacle`  | Compound      | Bearing drag + obstacle simultaneously |
| `compound_slip_sensor_noise` | Compound   | Wheel slip + sensor noise          |
| `compound_jam_sensor_fault`  | Compound    | Jam + sensor dropout concurrently  |
| `stop_and_go`             | Healthy       | Aggressive throttle changes        |
| `high_load_cruise`        | Healthy       | Normal high current without fault  |
