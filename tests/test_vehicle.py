"""tests/test_vehicle.py — Vehicle dynamics unit tests."""

from __future__ import annotations

import pytest
from simulator.core.vehicle import VehicleDynamics, VehicleState, VehicleMode
from simulator.config import SimulatorConfig


@pytest.fixture
def cfg():
    return SimulatorConfig(
        _env_file=None,
        SIM_HOST="127.0.0.1",
        SIM_PORT=8765,
        MACHINE_ID="test",
        ASSET_TYPE="rc_vehicle",
        SIM_FREQUENCY_HZ=100,
        CURRENT_SENSOR_HZ=50,
        RPM_SENSOR_HZ=20,
        ULTRASONIC_SENSOR_HZ=10,
        MAX_RPM=400.0,
        MAX_ACCELERATION=120.0,
        MAX_DECELERATION=200.0,
        ROLLING_RESISTANCE=0.05,
        MOTOR_RESPONSE_TIME=0.25,
        VEHICLE_MASS_KG=1.2,
        WHEEL_RADIUS_M=0.033,
        PWM_MIN=0,
        PWM_MAX=255,
        BATTERY_NOMINAL_V=7.4,
        BATTERY_CAPACITY_MAH=2000.0,
        ULTRASONIC_NOISE_SIGMA=0.0,
        CURRENT_NOISE_SIGMA=0.0,
        RPM_NOISE_SIGMA=0.0,
        ULTRASONIC_MIN_CM=2.0,
        ULTRASONIC_MAX_CM=400.0,
        NETWORK_PACKET_LOSS=0.0,
        NETWORK_LATENCY_MS=0.0,
        NETWORK_JITTER_MS=0.0,
        MACHSIGHT_WS_URL="ws://localhost:8000/telemetry/ingest",
        LOG_LEVEL="WARNING",
        RECORD_RUNS=False,
        RUNS_DIR="runs",
    )


@pytest.fixture
def dynamics(cfg):
    return VehicleDynamics(cfg)


def _run_ticks(state, dynamics, n_ticks, dt=0.01, extra_load=0.0, slip=None):
    for _ in range(n_ticks):
        state = dynamics.update(state, dt, extra_load=extra_load, wheel_slip_override=slip)
    return state


# ── Idle ──────────────────────────────────────────────────────────────────────

def test_idle_produces_zero_rpm(dynamics):
    state = VehicleState(pwm_command=0, mode=VehicleMode.IDLE)
    state = _run_ticks(state, dynamics, 100)
    assert state.wheel_rpm < 1.0, f"Expected near-zero RPM at idle, got {state.wheel_rpm}"


def test_idle_produces_near_zero_current(dynamics):
    state = VehicleState(pwm_command=0, mode=VehicleMode.IDLE)
    state = _run_ticks(state, dynamics, 100)
    # Idle current should be very low (mostly just I_IDLE)
    assert state.motor_current < 0.5


# ── Acceleration ──────────────────────────────────────────────────────────────

def test_increasing_pwm_increases_rpm(dynamics):
    state_low = VehicleState(pwm_command=80, mode=VehicleMode.FORWARD)
    state_low = _run_ticks(state_low, dynamics, 200)

    state_high = VehicleState(pwm_command=200, mode=VehicleMode.FORWARD)
    state_high = _run_ticks(state_high, dynamics, 200)

    assert state_high.wheel_rpm > state_low.wheel_rpm, (
        f"Higher PWM should produce higher RPM: {state_high.wheel_rpm} vs {state_low.wheel_rpm}"
    )


def test_rpm_changes_gradually(dynamics):
    """RPM should not jump instantly — first-order lag must be present."""
    state = VehicleState(pwm_command=0, mode=VehicleMode.FORWARD)
    state.pwm_command = 200

    rpms = []
    for _ in range(50):
        state = dynamics.update(state, 0.01)
        rpms.append(state.wheel_rpm)

    # Check that RPM is increasing over first 50 ticks (not a step function)
    for i in range(1, 10):
        assert rpms[i] > rpms[i - 1], "RPM should increase smoothly"

    # Should not reach max RPM in just 50 ticks
    assert rpms[-1] < dynamics.cfg.max_rpm * 0.95


def test_decreasing_pwm_decreases_rpm(dynamics):
    state = VehicleState(pwm_command=200, mode=VehicleMode.FORWARD)
    state = _run_ticks(state, dynamics, 200)
    high_rpm = state.wheel_rpm

    state.pwm_command = 0
    state = _run_ticks(state, dynamics, 200)
    assert state.wheel_rpm < high_rpm * 0.2


# ── Load effects ──────────────────────────────────────────────────────────────

def test_mechanical_load_reduces_rpm(dynamics):
    state_no_load = VehicleState(pwm_command=150, mode=VehicleMode.FORWARD)
    state_no_load = _run_ticks(state_no_load, dynamics, 300, extra_load=0.0)

    state_load = VehicleState(pwm_command=150, mode=VehicleMode.FORWARD)
    state_load = _run_ticks(state_load, dynamics, 300, extra_load=0.8)

    assert state_no_load.wheel_rpm > state_load.wheel_rpm, (
        f"Load should reduce RPM: {state_no_load.wheel_rpm} vs {state_load.wheel_rpm}"
    )


def test_mechanical_load_increases_current(dynamics):
    state_no_load = VehicleState(pwm_command=150, mode=VehicleMode.FORWARD)
    state_no_load = _run_ticks(state_no_load, dynamics, 200, extra_load=0.0)

    state_load = VehicleState(pwm_command=150, mode=VehicleMode.FORWARD)
    state_load = _run_ticks(state_load, dynamics, 200, extra_load=0.7)

    assert state_load.motor_current > state_no_load.motor_current, (
        "Higher load should increase current"
    )


# ── Braking ───────────────────────────────────────────────────────────────────

def test_braking_reduces_rpm_faster(dynamics):
    state = VehicleState(pwm_command=200, mode=VehicleMode.FORWARD)
    state = _run_ticks(state, dynamics, 200)
    pre_rpm = state.wheel_rpm

    state.mode = VehicleMode.BRAKING
    state = _run_ticks(state, dynamics, 50)
    post_rpm = state.wheel_rpm

    assert post_rpm < pre_rpm * 0.5, "Braking should halve RPM quickly"


# ── Wheel slip ────────────────────────────────────────────────────────────────

def test_wheel_slip_reduces_velocity_not_rpm(dynamics):
    """With full slip, RPM stays up but velocity drops."""
    state_no_slip = VehicleState(pwm_command=180, mode=VehicleMode.FORWARD)
    state_no_slip = _run_ticks(state_no_slip, dynamics, 300, slip=0.0)

    state_slip = VehicleState(pwm_command=180, mode=VehicleMode.FORWARD)
    state_slip = _run_ticks(state_slip, dynamics, 300, slip=0.8)

    assert state_slip.wheel_rpm > state_no_slip.wheel_rpm * 0.5, "RPM should remain high with slip"
    assert state_slip.velocity_mps < state_no_slip.velocity_mps, "Velocity should be lower with slip"
