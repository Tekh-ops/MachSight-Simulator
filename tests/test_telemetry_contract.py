"""tests/test_telemetry_contract.py — Contract validation, serialization, and boundary isolation tests."""

from __future__ import annotations

import json
import time
import pytest

from simulator.telemetry.schema import (
    SCHEMA_VERSION,
    MachineInfo,
    OperatingState,
    SensorReadings,
    SensorStatus,
    SourceInfo,
    TelemetryMessage,
)
from simulator.telemetry.validation import (
    SequenceTracker,
    TelemetryValidationError,
    TelemetryValidator,
)


def make_valid_message(**kwargs) -> TelemetryMessage:
    params = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": time.time(),
        "sequence": 100,
        "machine": MachineInfo(machine_id="rc-car-01", asset_type="rc_vehicle"),
        "operating_state": OperatingState(mode="forward", pwm_command=160),
        "sensors": SensorReadings(distance_cm=45.2, current_a=1.35, wheel_rpm=310.0),
        "sensor_status": SensorStatus(distance="ok", current="ok", wheel_rpm="ok"),
        "source": SourceInfo(source_type="simulator", device_id="rc-sim-01", transport="websocket"),
    }
    params.update(kwargs)
    return TelemetryMessage(**params)


# ── 1. Contract Serialization & Roundtrip ──────────────────────────────────────

def test_telemetry_json_roundtrip():
    original = make_valid_message()
    wire_json = original.model_dump_json()
    data = json.loads(wire_json)

    restored = TelemetryMessage.model_validate(data)
    assert restored.schema_version == original.schema_version
    assert restored.timestamp == original.timestamp
    assert restored.sequence == original.sequence
    assert restored.machine.machine_id == original.machine.machine_id
    assert restored.sensors.distance_cm == original.sensors.distance_cm
    assert restored.sensors.current_a == original.sensors.current_a
    assert restored.sensors.wheel_rpm == original.sensors.wheel_rpm
    assert restored.sensor_status.distance == original.sensor_status.distance
    assert restored.source.source_type == original.source.source_type


# ── 2. Validator Pass / Fail Boundaries ───────────────────────────────────────

def test_valid_message_passes_validation():
    msg = make_valid_message()
    report = TelemetryValidator.validate_message(msg)
    assert report.is_valid is True
    assert len(report.errors) == 0


def test_validator_rejects_negative_sequence():
    with pytest.raises(Exception):  # Pydantic or validator ge=0
        make_valid_message(sequence=-5)


def test_validator_detects_out_of_range_sensor_values():
    # Impossible current draw > 50A
    msg = make_valid_message(
        sensors=SensorReadings(distance_cm=50.0, current_a=999.0, wheel_rpm=100.0)
    )
    report = TelemetryValidator.validate_message(msg)
    assert report.is_valid is False
    assert any("current_a" in err for err in report.errors)


def test_validator_detects_out_of_range_distance():
    msg = make_valid_message(
        sensors=SensorReadings(distance_cm=2500.0, current_a=2.0, wheel_rpm=100.0)
    )
    report = TelemetryValidator.validate_message(msg)
    assert report.is_valid is False
    assert any("distance_cm" in err for err in report.errors)


# ── 3. Ground Truth Isolation ──────────────────────────────────────────────────

def test_ground_truth_strictly_forbidden_in_telemetry_wire():
    """Ensure that hidden simulator variables cannot be injected into the wire payload."""
    leak_data = {
        "schema_version": 2,
        "timestamp": time.time(),
        "sequence": 1,
        "machine": {"machine_id": "test", "asset_type": "rc_vehicle"},
        "operating_state": {"mode": "forward", "pwm_command": 100},
        "sensors": {"distance_cm": 50.0, "current_a": 1.0, "wheel_rpm": 200.0},
        "sensor_status": {"distance": "ok", "current": "ok", "wheel_rpm": "ok"},
        "source": {"source_type": "simulator", "device_id": "sim-01", "transport": "websocket"},
        # LEAK:
        "true_velocity_mps": 2.5,
        "mechanical_load": 0.8,
    }
    report = TelemetryValidator.validate_dict(leak_data)
    assert report.is_valid is False
    assert any("Forbidden ground-truth key" in err for err in report.errors)


def test_machsight_dict_excludes_ground_truth():
    msg = make_valid_message()
    d = msg.to_machsight_dict()
    forbidden = ["mechanical_load", "wheel_slip", "true_velocity_mps", "active_faults", "ground_truth"]
    for key in forbidden:
        assert key not in d, f"Found leaked key {key} in MachSight payload"


# ── 4. Sequence Integrity Tracking ─────────────────────────────────────────────

def test_sequence_tracker_clean_advancement():
    tracker = SequenceTracker(session_id="session-1")
    r1 = tracker.record(100)
    r2 = tracker.record(101)
    r3 = tracker.record(102)

    assert r1["is_gap"] is False
    assert r2["is_gap"] is False
    assert r3["is_gap"] is False
    assert tracker.stats["gaps_detected"] == 0
    assert tracker.stats["duplicates"] == 0


def test_sequence_tracker_detects_gap_and_duplicates():
    tracker = SequenceTracker(session_id="session-1")
    tracker.record(1842)
    # Gap: 1843 and 1844 missing
    gap_result = tracker.record(1845)
    assert gap_result["is_gap"] is True
    assert gap_result["missing_count"] == 2
    assert tracker.stats["gaps_detected"] == 2

    # Duplicate: receiving 1845 again
    dup_result = tracker.record(1845)
    assert dup_result["is_duplicate"] is True
    assert tracker.stats["duplicates"] == 1


# ── 5. Hardware-Shaped Message Acceptance ──────────────────────────────────────

def test_hardware_origin_message_passes_contract():
    """Verify that a message marked source_type='hardware' satisfies the exact same contract."""
    hw_msg = TelemetryMessage(
        schema_version=SCHEMA_VERSION,
        timestamp=time.time(),
        measurement_timestamp=time.time() - 0.005,
        sequence=50,
        machine=MachineInfo(machine_id="rc-physical-01", asset_type="rc_vehicle"),
        operating_state=OperatingState(mode="forward", pwm_command=180),
        sensors=SensorReadings(distance_cm=65.0, current_a=2.1, wheel_rpm=285.0),
        sensor_status=SensorStatus(distance="ok", current="ok", wheel_rpm="ok"),
        source=SourceInfo(
            source_type="hardware",
            device_id="esp32-car-01",
            transport="bluetooth",
            firmware_version="v2.1.0",
            session_id="hw-session-42",
        ),
    )
    report = TelemetryValidator.validate_message(hw_msg)
    assert report.is_valid is True
    # Downstream backend format must match
    d = hw_msg.to_machsight_dict()
    assert d["machine_id"] == "rc-physical-01"
    assert d["rpm"] == 285.0
    assert d["current_a"] == 2.1
