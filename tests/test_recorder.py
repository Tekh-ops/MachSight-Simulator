"""tests/test_recorder.py — Evaluation and run recorder tests."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from simulator.evaluation.recorder import RunRecorder
from simulator.telemetry.schema import (
    TelemetryMessage,
    MachineInfo,
    OperatingState,
    SensorReadings,
)
from simulator.core.vehicle import VehicleMode


def test_run_recorder_creates_files(tmp_path: Path):
    recorder = RunRecorder(tmp_path, "test_scenario")
    run_dir = recorder.run_dir
    assert run_dir.exists()

    msg = TelemetryMessage(
        machine=MachineInfo(machine_id="rc_test", asset_type="rc_vehicle"),
        sequence=1,
        sensors=SensorReadings(
            distance_cm=50.0,
            current_a=1.2,
            wheel_rpm=300.0,
        ),
        operating_state=OperatingState(
            pwm_command=150,
            mode=VehicleMode.FORWARD.value,
        ),
    )
    recorder.record_telemetry(msg)
    recorder.record_ground_truth({"sim_time": 0.1, "true_velocity": 1.5, "fault": "none"})
    recorder.finalise({"scenario": "test_scenario", "machine_id": "rc_test"})

    telemetry_file = run_dir / "telemetry.jsonl"
    gt_file = run_dir / "ground_truth.jsonl"
    meta_file = run_dir / "metadata.json"

    assert telemetry_file.exists()
    assert gt_file.exists()
    assert meta_file.exists()

    # Verify telemetry lines
    with telemetry_file.open("r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == 1
    assert lines[0]["machine_id"] == "rc_test"
    assert lines[0]["rpm"] == 300.0
    # Crucially ensure ground truth is not leaked into telemetry.jsonl
    assert "true_velocity" not in lines[0]

    # Verify ground truth lines
    with gt_file.open("r", encoding="utf-8") as f:
        gt_lines = [json.loads(line) for line in f]
    assert len(gt_lines) == 1
    assert gt_lines[0]["true_velocity"] == 1.5
