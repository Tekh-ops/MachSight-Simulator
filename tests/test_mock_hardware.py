"""tests/test_mock_hardware.py — Mock hardware source, unit normalization, and conformance tests."""

from __future__ import annotations

import asyncio
import pytest

from simulator.telemetry.mock_hardware import MockHardwareTelemetrySource
from simulator.telemetry.schema import TelemetryMessage
from simulator.telemetry.source import ConnectionState, SimulatorTelemetrySource
from simulator.telemetry.validation import TelemetryValidator


# ── Raw Packet Unit Normalisation ─────────────────────────────────────────────

def test_mock_hardware_converts_raw_units_and_names():
    source = MockHardwareTelemetrySource(
        device_id="esp32-mock-01",
        machine_id="rc-car-01",
    )

    raw_packet = {
        "distance_mm": 824,         # 824 mm
        "motor_current_ma": 2730,   # 2730 mA
        "wheel_speed_rpm": 312.6,   # 312.6 RPM
        "throttle_pwm": 175,
        "drive_mode": "forward",
    }

    msg = source.normalize_raw_packet(raw_packet)

    # Invariants: Canonical Units
    assert msg.sensors.distance_cm == 82.4       # mm -> cm
    assert msg.sensors.current_a == 2.73         # mA -> A
    assert msg.sensors.wheel_rpm == 312.6        # RPM
    assert msg.operating_state.pwm_command == 175
    assert msg.operating_state.mode == "forward"
    assert msg.source.source_type == "hardware"
    assert msg.source.device_id == "esp32-mock-01"
    assert msg.sensor_status.distance == "ok"
    assert msg.sensor_status.current == "ok"
    assert msg.sensor_status.wheel_rpm == "ok"


def test_mock_hardware_sensor_aliases():
    """Verify hardware-specific names (HC-SR04, INA219, encoder) map cleanly."""
    source = MockHardwareTelemetrySource()
    raw = {
        "hc_sr04_mm": 500,
        "ina219_ma": 1500,
        "encoder_rpm": 250.0,
    }
    msg = source.normalize_raw_packet(raw)
    assert msg.sensors.distance_cm == 50.0
    assert msg.sensors.current_a == 1.5
    assert msg.sensors.wheel_rpm == 250.0


def test_mock_hardware_missing_sensor_status():
    source = MockHardwareTelemetrySource()
    raw = {
        "distance_mm": None,
        "motor_current_ma": 1000,
        "wheel_speed_rpm": None,
    }
    # Handled safely without exception
    msg = source.normalize_raw_packet(raw)
    assert msg.sensors.distance_cm is None
    assert msg.sensors.wheel_rpm is None
    assert msg.sensors.current_a == 1.0
    assert msg.sensor_status.distance == "dropped"
    assert msg.sensor_status.wheel_rpm == "dropped"


# ── Streaming Lifecycle ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mock_hardware_streaming():
    source = MockHardwareTelemetrySource()
    await source.connect()
    await source.start()

    source.feed_raw_packet({"distance_mm": 1000, "motor_current_ma": 500, "wheel_speed_rpm": 120.0})
    source.feed_raw_packet({"distance_mm": 900, "motor_current_ma": 600, "wheel_speed_rpm": 150.0})

    msg1 = await source.get_next(timeout=1.0)
    msg2 = await source.get_next(timeout=1.0)

    assert msg1 is not None and msg1.sensors.distance_cm == 100.0
    assert msg2 is not None and msg2.sensors.distance_cm == 90.0

    await source.stop()
    await source.disconnect()
    assert source.connection_state == ConnectionState.DISCONNECTED


# ── Conformance Test: Simulator vs Hardware Equivalence ───────────────────────

@pytest.mark.asyncio
async def test_conformance_both_sources_pass_validator():
    """Proves that both Simulator and MockHardware sources satisfy the exact same contract."""
    # 1. Simulator source packet
    sim_queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue()
    sim_source = SimulatorTelemetrySource(sim_queue)
    await sim_source.start()

    from simulator.telemetry.schema import (
        MachineInfo,
        OperatingState,
        SensorReadings,
        SensorStatus,
        SourceInfo,
    )
    sim_msg = TelemetryMessage(
        sequence=1,
        machine=MachineInfo(machine_id="rc-sim-01"),
        operating_state=OperatingState(mode="forward", pwm_command=150),
        sensors=SensorReadings(distance_cm=45.0, current_a=1.2, wheel_rpm=300.0),
        sensor_status=SensorStatus(distance="ok", current="ok", wheel_rpm="ok"),
        source=SourceInfo(source_type="simulator", device_id="rc-sim-01"),
    )
    await sim_queue.put(sim_msg)
    sim_out = await sim_source.get_next(timeout=1.0)
    assert sim_out is not None

    # 2. Hardware source packet
    hw_source = MockHardwareTelemetrySource(device_id="esp32-rc-01", machine_id="rc-sim-01")
    await hw_source.start()
    hw_source.feed_raw_packet({
        "distance_mm": 450,
        "motor_current_ma": 1200,
        "wheel_speed_rpm": 300.0,
        "throttle_pwm": 150,
        "drive_mode": "forward",
    })
    hw_out = await hw_source.get_next(timeout=1.0)
    assert hw_out is not None

    # 3. Both must pass TelemetryValidator without errors
    sim_report = TelemetryValidator.validate_message(sim_out)
    hw_report = TelemetryValidator.validate_message(hw_out)

    assert sim_report.is_valid is True
    assert hw_report.is_valid is True

    # 4. Sensor values match exactly despite different origin
    assert sim_out.sensors.distance_cm == hw_out.sensors.distance_cm == 45.0
    assert sim_out.sensors.current_a == hw_out.sensors.current_a == 1.2
    assert sim_out.sensors.wheel_rpm == hw_out.sensors.wheel_rpm == 300.0

    # 5. MachSight flat dictionaries match in structure
    assert sim_out.to_machsight_dict().keys() == hw_out.to_machsight_dict().keys()

    await sim_source.stop()
    await hw_source.stop()
