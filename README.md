# MachSight-Simulator

Physics-inspired RC-car Digital Twin, Hardware Abstraction Layer (HAL), and Fault Injection Server.

---

## Overview

`MachSight-Simulator` serves as a high-fidelity software digital twin and hardware abstraction testbed for physical RC car hardware (ESP32 microcontrollers, motor drivers, current sensors, wheel encoders, and ultrasonic rangefinders) during the development, testing, and benchmark evaluation of the MachSight diagnostic platform.

### Core Architecture & Hardware Seam

```text
CURRENT: Software Digital Twin                FUTURE: Physical Hardware

+--------------------------+                  +--------------------------+
| Simulation Engine        |                  | ESP32 Microcontroller    |
| (Vehicle Dynamics)       |                  | (Sensors: HC-SR04,       |
+------------+-------------+                  |  INA219, Optical Encoder)|
             |                                +------------+-------------+
             v                                             |
+--------------------------+                  +------------v-------------+
| SimulatorTelemetrySource |                  | BluetoothTelemetrySource |
| (HAL Implementation)     |                  | (Hardware HAL Adapter)   |
+------------+-------------+                  +------------+-------------+
             |                                             |
             +----------------------+----------------------+
                                    |
                                    v
                     +-----------------------------+
                     | Canonical Telemetry Contract|
                     |  (docs/TELEMETRY_CONTRACT)  |
                     +--------------+--------------+
                                    |
                                    v
                     +-----------------------------+
                     | WebSocket Forwarder         |
                     | (Network Transport Layer)   |
                     +--------------+--------------+
                                    |
                                    v
                     +-----------------------------+
                     | MachSight Backend           |
                     | (/telemetry/ingest endpoint)|
                     +-----------------------------+
```

### Key Architectural Invariants

1. **Hardware Boundary**: MachSight consumes machine telemetry, not simulator or hardware telemetry.
2. **First-Principles Physics Twin**: Dynamic RPM lag, motor back-EMF, battery sag under load, braking deceleration, and wheel slip decoupling.
3. **Hardware Seam & Interchangeability**: Implements the `TelemetrySource` contract. In the future, switching from the simulator to physical ESP32 Bluetooth telemetry requires only changing the source adapter, with zero alterations to downstream anomaly detection or diagnostic pipelines.
4. **Information Isolation**: Diagnostic models in MachSight receive **only** realistic noisy telemetry. Hidden ground-truth simulation variables (slip ratio, mechanical load, true velocity, active fault labels) are isolated to dedicated evaluation endpoints and logging channels.

---

## Features

- **Hardware Abstraction Layer (HAL)**:
  - `TelemetrySource` abstract base with explicit connection states (`DISCONNECTED`, `CONNECTING`, `CONNECTED`, `DEGRADED`, `FAILED`).
  - `SimulatorTelemetrySource`: pulls packets from the physics engine.
  - `MockHardwareTelemetrySource`: proves the HAL boundary by ingesting raw hardware packets (mm, mA, raw ticks) without physics or diagnostics.
  - `BluetoothTelemetrySource`: stub for physical ESP32 BLE integration.
  - `TelemetryReplaySource`: replays recorded `telemetry.jsonl` through the same HAL boundary without physics or ground-truth leakage.
- **Formal Telemetry Contract**:
  - Versioned contract (`schema_version: 2`) with physical ranges, sensor health (`sensor_status`), and source provenance (`source`).
  - Detailed specification: [docs/TELEMETRY_CONTRACT.md](docs/TELEMETRY_CONTRACT.md).
  - Machine-readable schema: [schemas/telemetry.schema.json](schemas/telemetry.schema.json).
  - Hardware integration guide: [docs/HARDWARE_INTEGRATION.md](docs/HARDWARE_INTEGRATION.md).
- **Validation & Sequence Integrity**:
  - `TelemetryValidator`: checks physical limits, schema compliance, and strictly rejects hidden ground-truth leakage.
  - `SequenceTracker`: detects gaps, packet loss, duplicate packets, and out-of-order deliveries.
- **Sensors Modeled**:
  - HC-SR04 Ultrasonic Distance Sensor (cm) with speed-of-sound conversions, min/max range clipping, Gaussian noise, and obstacle tracking.
  - ACS712 / INA219 Motor Current Sensor (Amperes) with idle current, back-EMF load scaling, and thermal/resistive surges.
  - Optical Wheel Speed Encoder (RPM) with pulse-interval conversion, quantization, and dropout simulation.
  - Battery Model with nominal 2S LiPo voltage (7.4V), internal resistance, and dynamic voltage sag under current draw.
- **Fault Injection Suite**:
  - Mechanical: `drivetrain_jam`, `motor_drag`, `wheel_slip`
  - Sensor-level: `ultrasonic_stuck`, `ultrasonic_noise`, `ultrasonic_bias`, `current_bias`, `current_spike`, `rpm_stuck`, `rpm_dropout`
  - Meta/Compound: `intermittent` (periodic ON/OFF cycling)
- **Deterministic Scenario Engine**:
  - Declarative YAML scenario definitions with repeatable pseudo-random seeds.
  - 13 built-in scenarios: `healthy_cruise`, `sudden_drivetrain_jam`, `gradual_motor_drag`, `wheel_slip`, `sensor_failures`, `intermittent_fault`, `obstacle_approach`, `full_demo`, `compound_drag_obstacle`, `compound_slip_sensor_noise`, `compound_jam_sensor_fault`, `stop_and_go`, `high_load_cruise`.
- **Driver Profile Engine**: Named control profiles (`idle`, `slow_cruise`, `normal_cruise`, `high_load`, `stop_and_go`, `custom`) modulating PWM and direction dynamically during runs.
- **Automated Dataset Generator**:
  - Headless multi-run generation: `DatasetGenerator` → `datasets/<name>/<scenario>/run_XXXXXX/`
  - Ground-truth isolation: `telemetry.jsonl` never exposes fault labels or physics state.
  - Reproducible by seed; manifest with configuration hash.
- **Benchmark Suite Runner**: declarative YAML benchmark definitions, data quality metrics, and optional live MachSight diagnostic evaluation.
- **MachSight Integration Adapter**: async WebSocket client for streaming telemetry, capturing live anomaly/diagnosis events, and blind evaluation against ground truth.
- **Evaluation Metrics Engine**: detection metrics (precision/recall/F1/FPR/latency), diagnosis metrics (accuracy/macro-F1/confusion matrix), and benchmark summary reports.
- **Telemetry Streaming & Forwarding**:
  - Live local broadcast on `ws://<host>:<port>/ws/telemetry`.
  - Automatic forwarder to MachSight ingestion at `ws://<machsight-host>:8000/telemetry/ingest`.
- **Run Evaluation Recorder**:
  - Records dual synchronized streams: `telemetry.jsonl` (blind sensor feed) and `ground_truth.jsonl` (physics state & labels) alongside `scenario.yaml` and `metadata.json`.

---

## Installation & Setup

### Prerequisites

- Python >= 3.11
- Recommended: [uv](https://github.com/astral-sh/uv) or standard `venv`

### Installation with `uv`

```bash
cd MachSight-Simulator

# Install dependencies
uv sync --extra dev
```

### Configuration

Copy the example environment configuration:

```bash
cp .env.example .env
```

Configuration variables in `.env`:

| Key | Default | Description |
|-----|---------|-------------|
| `SIM_HOST` | `127.0.0.1` | Simulator HTTP/WS bind host |
| `SIM_PORT` | `8765` | Simulator HTTP/WS bind port |
| `MACHINE_ID` | `rc_car_01` | Identifier passed in telemetry |
| `SIM_FREQUENCY_HZ` | `100` | Physics loop update frequency |
| `MACHSIGHT_WS_URL` | `ws://localhost:8000/telemetry/ingest` | MachSight backend ingestion URL |
| `RECORD_RUNS` | `false` | Enable automatic disk recording |

---

## Running the Simulator

### 1. Interactive Server Mode

```bash
uv run python -m simulator
```

Starts the REST API server at `http://127.0.0.1:8765` and WebSocket telemetry feed at `ws://127.0.0.1:8765/ws/telemetry`.

### 2. Run a Named Scenario

```bash
# List all available scenarios
uv run python -m simulator --list-scenarios

# Run the sudden drivetrain jam benchmark scenario with fixed seed
uv run python -m simulator --scenario sudden_drivetrain_jam --seed 42 --record
```

### 3. Generate a Dataset (Phase 4)

```bash
# All scenarios, 1 run each
uv run python -m simulator generate-dataset --scenarios all --runs 1

# Custom scenarios, 5 runs, fixed seed, 30s each
uv run python -m simulator generate-dataset \
    --scenarios healthy_cruise,sudden_drivetrain_jam \
    --runs 5 --seed 42 --duration 30 --name experiment_001
```

### 4. Run a Benchmark Suite

```bash
# List available benchmark suites
uv run python -m simulator --list-benchmarks

# Run regression CI suite (simulator quality metrics only)
uv run python -m simulator benchmark --suite regression_ci

# Run with live MachSight diagnostic evaluation
uv run python -m simulator benchmark --suite regression_ci \
    --evaluate-machsight --machsight-url http://localhost:8000
```

### 5. Replay Recorded Telemetry

```bash
uv run python -m simulator replay \
    --file datasets/experiment_001/healthy_cruise/run_000000/telemetry.jsonl \
    --speed 1.0
```

---

## Control REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/simulation/start` | Start the simulation (optionally load scenario) |
| `POST` | `/simulation/stop` | Stop the simulation |
| `POST` | `/simulation/pause` | Pause the physics engine |
| `POST` | `/simulation/resume` | Resume simulation |
| `POST` | `/simulation/reset` | Reset vehicle and sensors to initial state |
| `POST` | `/control/throttle` | Set `pwm` (0-255), `direction` ("forward"/"reverse"), and `mode` |
| `POST` | `/faults/inject` | Inject a mechanical or sensor fault dynamically |
| `POST` | `/faults/clear` | Clear an active fault by ID or clear all faults |
| `GET` | `/simulation/status` | Read simulation status (runtime, active faults, PWM) |
| `GET` | `/simulation/ground-truth` | *(Dev/Eval only)* Read hidden ground-truth state |
| `GET` | `/scenarios` | List available scenario names |
| `WS` | `/ws/telemetry` | WebSocket stream of telemetry packets |

---

## Telemetry Protocol

Canonical contract structure:

```json
{
  "schema_version": 2,
  "timestamp": 1727700000.125,
  "sequence": 1042,
  "machine": {
    "machine_id": "rc_car_01",
    "asset_type": "rc_vehicle"
  },
  "operating_state": {
    "mode": "forward",
    "pwm_command": 180
  },
  "sensors": {
    "distance_cm": 42.8,
    "current_a": 1.45,
    "wheel_rpm": 312.4
  },
  "sensor_status": {
    "distance": "ok",
    "current": "ok",
    "wheel_rpm": "ok"
  },
  "source": {
    "source_type": "simulator",
    "device_id": "rc-sim-01",
    "transport": "websocket"
  }
}
```

The simulator emits backward-compatible flat payloads for legacy MachSight backend ingestion via `.to_machsight_dict()`.

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/PHASE_4.md](docs/PHASE_4.md) | Phase 4 architecture and feature guide |
| [docs/DATASET_GENERATION.md](docs/DATASET_GENERATION.md) | Dataset generation guide |
| [docs/EVALUATION.md](docs/EVALUATION.md) | End-to-end evaluation guide |
| [docs/TELEMETRY_CONTRACT.md](docs/TELEMETRY_CONTRACT.md) | Canonical telemetry contract spec |
| [docs/HARDWARE_INTEGRATION.md](docs/HARDWARE_INTEGRATION.md) | Physical hardware integration guide |

---

## Running Tests

Execute the comprehensive test suite with:

```bash
uv run --extra dev pytest
```

**92 tests across 15 test files** covering vehicle physics, sensor models, fault injection, HAL boundary, telemetry contract, dataset generation, replay, metrics math, and MachSight adapter.

