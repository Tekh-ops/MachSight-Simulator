"""tests/test_telemetry.py — Telemetry schema and MachSight compatibility tests."""

from __future__ import annotations

import pytest

from simulator.telemetry.schema import (
    TelemetryMessage,
    MachineInfo,
    OperatingState,
    SensorReadings,
    SourceInfo,
)
from simulator.core.vehicle import VehicleMode


def test_telemetry_message_construction():
    msg = TelemetryMessage(
        machine=MachineInfo(machine_id="rc_car_01", asset_type="rc_vehicle"),
        sequence=10,
        sensors=SensorReadings(
            distance_cm=45.2,
            current_a=1.35,
            wheel_rpm=310.0,
        ),
        operating_state=OperatingState(
            pwm_command=160,
            mode=VehicleMode.FORWARD.value,
        ),
    )
    assert msg.machine.machine_id == "rc_car_01"
    assert msg.sensors.wheel_rpm == 310.0
    assert msg.sequence == 10


def test_to_machsight_dict_format():
    """MachSight expects flat fields including 'rpm', 'current_a', 'distance_cm'."""
    msg = TelemetryMessage(
        machine=MachineInfo(machine_id="test_car", asset_type="rc_vehicle"),
        sequence=5,
        sensors=SensorReadings(
            distance_cm=88.5,
            current_a=0.95,
            wheel_rpm=240.0,
        ),
        operating_state=OperatingState(
            pwm_command=120,
            mode="forward",
        ),
    )
    machsight_dict = msg.to_machsight_dict()

    # MachSight requires these keys
    assert machsight_dict["machine_id"] == "test_car"
    assert machsight_dict["car_id"] == "test_car"
    assert machsight_dict["rpm"] == 240.0
    assert machsight_dict["current_a"] == 0.95
    assert machsight_dict["distance_cm"] == 88.5
    assert machsight_dict["mode"] == "forward"
    assert "timestamp" in machsight_dict

    # CRITICAL: Ground truth and simulator hidden state must NOT be in dict
    forbidden_keys = [
        "ground_truth",
        "faults",
        "active_faults",
        "mechanical_load",
        "wheel_slip",
        "true_position_m",
        "true_velocity_mps",
    ]
    for key in forbidden_keys:
        assert key not in machsight_dict, f"Hidden simulator state leaked in telemetry: {key}"


def test_null_sensor_handling_in_machsight_dict():
    """Ensure None values for sensor dropouts are safely omitted/handled."""
    msg = TelemetryMessage(
        machine=MachineInfo(machine_id="test_car", asset_type="rc_vehicle"),
        sequence=6,
        sensors=SensorReadings(
            distance_cm=None,
            current_a=None,
            wheel_rpm=None,
        ),
        operating_state=OperatingState(
            pwm_command=0,
            mode="idle",
        ),
    )
    d = msg.to_machsight_dict()
    assert "distance_cm" not in d
    assert "current_a" not in d
    assert "rpm" not in d
