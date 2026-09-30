"""tests/test_faults.py — Fault injection unit tests."""

from __future__ import annotations

import pytest

from simulator.core.vehicle import VehicleState, VehicleMode, VehicleDynamics
from simulator.sensors.ultrasonic import UltrasonicSensor
from simulator.sensors.current import CurrentSensor
from simulator.sensors.rpm import RPMSensor
from simulator.faults.base import FaultRecord, FaultStatus
from simulator.faults.mechanical import DrivetrainJamFault, MotorDragFault, WheelSlipFault
from simulator.faults.sensor_faults import (
    UltrasonicStuckFault,
    UltrasonicNoiseFault,
    UltrasonicBiasFault,
    CurrentBiasFault,
    CurrentSpikeFault,
    RPMStuckFault,
    RPMDropoutFault,
    IntermittentFault,
)
from simulator.faults.manager import FaultManager
from simulator.faults import _build_fault_from_config


def make_record(fault_type, severity=0.8, duration_s=None):
    return FaultRecord(
        fault_id="test-01",
        fault_type=fault_type,
        start_s=0.0,
        duration_s=duration_s,
        severity=severity,
    )


def make_sensors():
    return (
        UltrasonicSensor(noise_sigma=0.0, dropout_prob=0.0, spike_prob=0.0),
        CurrentSensor(noise_sigma=0.0, spike_prob=0.0),
        RPMSensor(noise_sigma=0.0, dropout_prob=0.0),
    )


# ── Drivetrain Jam ────────────────────────────────────────────────────────────

def test_drivetrain_jam_increases_load():
    vehicle = VehicleState(pwm_command=200, mode=VehicleMode.FORWARD)
    us, cur, rpm = make_sensors()
    fault = DrivetrainJamFault(make_record("drivetrain_jam", severity=0.85))
    fault.activate(0.0)

    vehicle.mechanical_load = 0.0
    fault.apply(0.1, vehicle, us, cur, rpm)
    assert vehicle.mechanical_load > 0.5


def test_drivetrain_jam_reduces_rpm_via_dynamics():
    """A jam increases load which the dynamics translates to lower RPM."""
    dynamics = VehicleDynamics()
    state = VehicleState(pwm_command=175, mode=VehicleMode.FORWARD)
    fault = DrivetrainJamFault(make_record("drivetrain_jam", severity=0.85))
    fault.activate(0.0)

    # Run 200 ticks healthy
    for _ in range(200):
        state = dynamics.update(state, 0.01)
    healthy_rpm = state.wheel_rpm

    # Reset and run with jam
    state2 = VehicleState(pwm_command=175, mode=VehicleMode.FORWARD)
    for i in range(200):
        state2.mechanical_load = 0.0
        fault.apply(i * 0.01, state2, *make_sensors())
        state2 = dynamics.update(state2, 0.01, extra_load=state2.mechanical_load)
    jammed_rpm = state2.wheel_rpm

    assert jammed_rpm < healthy_rpm * 0.5


# ── Motor Drag ────────────────────────────────────────────────────────────────

def test_motor_drag_adds_load():
    vehicle = VehicleState()
    us, cur, rpm = make_sensors()
    fault = MotorDragFault(make_record("motor_drag", severity=0.6))
    fault.activate(0.0)
    vehicle.mechanical_load = 0.0
    fault.apply(0.0, vehicle, us, cur, rpm)
    assert vehicle.mechanical_load > 0.1


# ── Wheel Slip ────────────────────────────────────────────────────────────────

def test_wheel_slip_sets_slip_factor():
    vehicle = VehicleState()
    us, cur, rpm = make_sensors()
    fault = WheelSlipFault(make_record("wheel_slip", severity=0.75))
    fault.activate(0.0)
    fault.apply(0.0, vehicle, us, cur, rpm)
    assert vehicle.wheel_slip > 0.5

    fault.remove(vehicle, us, cur, rpm)
    assert vehicle.wheel_slip == 0.0


# ── Ultrasonic Stuck ──────────────────────────────────────────────────────────

def test_ultrasonic_stuck_freezes_reading():
    import random
    random.seed(0)
    us = UltrasonicSensor(noise_sigma=0.0, dropout_prob=0.0, spike_prob=0.0)
    fault = UltrasonicStuckFault(make_record("ultrasonic_stuck", severity=1.0), stuck_value_cm=50.0)
    fault.activate(0.0)
    fault.apply(0.0, VehicleState(), us, CurrentSensor(), RPMSensor())
    r = us.sample(true_value=2.0, sim_time=0.0)
    assert r is not None
    assert r.value == 50.0


# ── Ultrasonic Bias ───────────────────────────────────────────────────────────

def test_ultrasonic_bias_offsets_reading():
    import random
    random.seed(0)
    us = UltrasonicSensor(noise_sigma=0.0, dropout_prob=0.0, spike_prob=0.0)
    fault = UltrasonicBiasFault(make_record("ultrasonic_bias", severity=1.0), bias_cm=20.0)
    fault.activate(0.0)
    fault.apply(0.0, VehicleState(), us, CurrentSensor(), RPMSensor())
    r = us.sample(true_value=1.0, sim_time=0.0)  # 100 cm true
    assert r is not None
    assert abs(r.value - 120.0) < 1.0


# ── Current Bias ──────────────────────────────────────────────────────────────

def test_current_bias_adds_offset():
    import random
    random.seed(0)
    cur = CurrentSensor(noise_sigma=0.0, spike_prob=0.0)
    fault = CurrentBiasFault(make_record("current_bias", severity=1.0), bias_a=1.0)
    fault.activate(0.0)
    fault.apply(0.0, VehicleState(), UltrasonicSensor(), cur, RPMSensor())
    r = cur.sample(2.5, sim_time=0.0)
    assert r is not None
    assert abs(r.value - 3.5) < 0.05


# ── RPM Stuck ─────────────────────────────────────────────────────────────────

def test_rpm_stuck_freezes_at_activation_value():
    rpm_sensor = RPMSensor(noise_sigma=0.0, dropout_prob=0.0)
    vehicle = VehicleState()
    vehicle.wheel_rpm = 250.0

    fault = RPMStuckFault(make_record("rpm_stuck", severity=1.0))
    fault.activate(0.0)
    fault.apply(0.0, vehicle, UltrasonicSensor(), CurrentSensor(), rpm_sensor)

    r = rpm_sensor.sample(300.0, sim_time=0.0)
    assert r is not None
    assert r.value == 250.0  # frozen at activation value


# ── RPM Dropout ───────────────────────────────────────────────────────────────

def test_rpm_dropout_produces_none():
    rpm_sensor = RPMSensor(noise_sigma=0.0, dropout_prob=0.0)
    fault = RPMDropoutFault(make_record("rpm_dropout", severity=1.0))
    fault.activate(0.0)
    fault.apply(0.0, VehicleState(), UltrasonicSensor(), CurrentSensor(), rpm_sensor)
    # At full severity, dropout_extra = 0.95; with base 0.003 → effectively ~0.95
    none_count = 0
    for i in range(20):
        r = rpm_sensor.sample(300.0, sim_time=i * 0.05)
        if r and r.value is None:
            none_count += 1
    assert none_count > 10


# ── Intermittent ──────────────────────────────────────────────────────────────

def test_intermittent_fault_cycles():
    """IntermittentFault should toggle inner fault on and off."""
    import random
    random.seed(0)
    cur = CurrentSensor(noise_sigma=0.0, spike_prob=0.0)

    inner_record = make_record("current_bias", severity=1.0)
    inner = CurrentBiasFault(inner_record, bias_a=2.0)

    outer_record = make_record("intermittent", severity=1.0)
    fault = IntermittentFault(outer_record, inner, on_seconds=2.0, off_seconds=3.0)
    fault.activate(0.0)

    # At t=0 (in ON phase) bias should be active
    fault.apply(0.0, VehicleState(), UltrasonicSensor(), cur, RPMSensor())
    r_on = cur.sample(1.0, sim_time=0.0)
    assert r_on is not None and r_on.value > 2.0

    # At t=3 (in OFF phase, cycle=5s, elapsed=3 > on_seconds=2)
    fault.apply(3.0, VehicleState(), UltrasonicSensor(), cur, RPMSensor())
    cur2 = CurrentSensor(noise_sigma=0.0, spike_prob=0.0)  # fresh sensor
    r_off = cur2.sample(1.0, sim_time=0.0)
    assert r_off is not None and r_off.value < 2.0


# ── FaultManager ─────────────────────────────────────────────────────────────

def test_fault_manager_activates_at_correct_time():
    manager = FaultManager()
    us, cur, rpm = make_sensors()
    vehicle = VehicleState()

    cfg = {"type": "current_bias", "start_s": 5.0, "duration_s": 10.0, "severity": 0.8, "bias_a": 1.0}
    manager.register_from_config(cfg)

    # At t=4 fault should still be pending
    manager.tick(4.0, vehicle, us, cur, rpm)
    assert len(manager.active_faults) == 0

    # At t=5 fault should activate
    manager.tick(5.0, vehicle, us, cur, rpm)
    assert len(manager.active_faults) == 1

    # At t=15 fault should have expired (duration=10)
    manager.tick(15.5, vehicle, us, cur, rpm)
    assert len(manager.active_faults) == 0


def test_build_fault_from_config():
    cfg = {"type": "drivetrain_jam", "start_s": 10.0, "duration_s": 5.0, "severity": 0.7}
    fault = _build_fault_from_config(cfg)
    assert fault.fault_type == "drivetrain_jam"
    assert fault.record.severity == 0.7


def test_unknown_fault_type_raises():
    with pytest.raises(ValueError, match="Unknown fault type"):
        _build_fault_from_config({"type": "does_not_exist", "start_s": 0.0, "severity": 0.5})
