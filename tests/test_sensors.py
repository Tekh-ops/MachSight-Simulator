"""tests/test_sensors.py — Sensor model unit tests."""

from __future__ import annotations

import random
import pytest

from simulator.sensors.ultrasonic import UltrasonicSensor
from simulator.sensors.current import CurrentSensor
from simulator.sensors.rpm import RPMSensor
from simulator.sensors.base import SensorQuality


# ── Ultrasonic ────────────────────────────────────────────────────────────────

class TestUltrasonicSensor:
    def setup_method(self):
        random.seed(0)
        self.sensor = UltrasonicSensor(
            sample_rate_hz=10.0,
            noise_sigma=0.0,
            dropout_prob=0.0,
            spike_prob=0.0,
        )

    def test_converts_metres_to_cm(self):
        reading = self.sensor.sample(true_value=1.5, sim_time=0.0)
        assert reading is not None
        assert abs(reading.value - 150.0) < 1.0

    def test_distance_decreases_approaching_obstacle(self):
        sensor = UltrasonicSensor(noise_sigma=0.0, dropout_prob=0.0, spike_prob=0.0)
        r1 = sensor.sample(true_value=2.0, sim_time=0.0)
        r2 = sensor.sample(true_value=1.0, sim_time=0.1)
        assert r1 is not None
        assert r2 is not None
        assert r2.value < r1.value

    def test_returns_max_range_when_no_obstacle(self):
        reading = self.sensor.sample(true_value=None, sim_time=0.0)
        assert reading is not None
        assert reading.value == pytest.approx(400.0)

    def test_clamps_to_min_range(self):
        reading = self.sensor.sample(true_value=0.001, sim_time=0.0)
        assert reading is not None
        assert reading.value >= 2.0

    def test_noise_is_configurable(self):
        noisy = UltrasonicSensor(noise_sigma=50.0, dropout_prob=0.0, spike_prob=0.0)
        readings = []
        for i in range(20):
            r = noisy.sample(true_value=1.0, sim_time=i * 0.1)
            if r and r.value is not None:
                readings.append(r.value)
        spread = max(readings) - min(readings)
        assert spread > 5.0, "High noise should spread readings"

    def test_stuck_fault(self):
        self.sensor.apply_stuck(50.0)
        r1 = self.sensor.sample(true_value=1.0, sim_time=0.0)
        r2 = self.sensor.sample(true_value=3.0, sim_time=0.1)
        assert r1.value == 50.0
        assert r2.value == 50.0
        assert r1.quality == SensorQuality.STALE

    def test_bias(self):
        sensor = UltrasonicSensor(noise_sigma=0.0, dropout_prob=0.0, spike_prob=0.0)
        sensor.apply_bias(20.0)
        r = sensor.sample(true_value=1.0, sim_time=0.0)  # 100 cm true
        assert r is not None
        assert abs(r.value - 120.0) < 1.0

    def test_clear_faults_resets_stuck(self):
        self.sensor.apply_stuck(50.0)
        self.sensor.clear_faults()
        r = self.sensor.sample(true_value=2.0, sim_time=0.0)
        assert r is not None
        assert r.value != 50.0


# ── Current sensor ────────────────────────────────────────────────────────────

class TestCurrentSensor:
    def setup_method(self):
        random.seed(0)
        self.sensor = CurrentSensor(
            sample_rate_hz=50.0,
            noise_sigma=0.0,
            spike_prob=0.0,
        )

    def test_returns_true_value_without_noise(self):
        r = self.sensor.sample(2.5, sim_time=0.0)
        assert r is not None
        assert abs(r.value - 2.5) < 0.01

    def test_current_clamped_to_zero(self):
        r = self.sensor.sample(-5.0, sim_time=0.0)
        assert r is not None
        assert r.value >= 0.0

    def test_bias_adds_offset(self):
        self.sensor.apply_bias(1.0)
        r = self.sensor.sample(2.5, sim_time=0.0)
        assert r is not None
        assert abs(r.value - 3.5) < 0.01

    def test_spike_produces_high_value(self):
        sensor = CurrentSensor(noise_sigma=0.0, spike_prob=1.0, spike_magnitude=10.0)
        r = sensor.sample(1.0, sim_time=0.0)
        assert r is not None
        assert r.value > 3.0
        assert r.quality == SensorQuality.INVALID

    def test_clear_faults(self):
        self.sensor.apply_bias(2.0)
        self.sensor.clear_faults()
        r = self.sensor.sample(1.0, sim_time=0.0)
        assert r is not None
        assert abs(r.value - 1.0) < 0.01


# ── RPM sensor ────────────────────────────────────────────────────────────────

class TestRPMSensor:
    def setup_method(self):
        random.seed(0)
        self.sensor = RPMSensor(
            sample_rate_hz=20.0,
            noise_sigma=0.0,
            dropout_prob=0.0,
        )

    def test_returns_true_rpm(self):
        r = self.sensor.sample(300.0, sim_time=0.0)
        assert r is not None
        assert abs(r.value - 300.0) < 1.0

    def test_stuck_freezes_at_value(self):
        self.sensor.apply_stuck(250.0)
        r1 = self.sensor.sample(300.0, sim_time=0.0)
        r2 = self.sensor.sample(50.0, sim_time=0.05)
        assert r1.value == 250.0
        assert r2.value == 250.0
        assert r1.quality == SensorQuality.STALE

    def test_dropout_produces_none(self):
        sensor = RPMSensor(noise_sigma=0.0, dropout_prob=1.0)
        r = sensor.sample(300.0, sim_time=0.0)
        assert r is not None
        assert r.value is None
        assert r.quality == SensorQuality.DROPPED

    def test_clamped_to_zero(self):
        r = self.sensor.sample(-10.0, sim_time=0.0)
        assert r is not None
        assert r.value >= 0.0

    def test_clear_faults_removes_stuck(self):
        self.sensor.apply_stuck(200.0)
        self.sensor.clear_faults()
        r = self.sensor.sample(350.0, sim_time=0.0)
        assert r is not None
        assert r.value != 200.0


# ── Sampling rate tests ────────────────────────────────────────────────────────

def test_sensor_sampling_rate_respected():
    """Sensor should only emit a reading at its scheduled interval."""
    sensor = UltrasonicSensor(sample_rate_hz=10.0)  # 100ms interval
    r0 = sensor.sample(1.0, sim_time=0.0)
    r1 = sensor.sample(1.0, sim_time=0.05)  # too early
    r2 = sensor.sample(1.0, sim_time=0.10)  # due
    assert r0 is not None
    assert r1 is None, "Should not emit mid-interval"
    assert r2 is not None
