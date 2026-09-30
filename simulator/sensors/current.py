"""
simulator/sensors/current.py
=============================
Motor current sensor model.

True value: vehicle.motor_current (derived from physics engine).

Observable faults (applied by FaultManager):
    bias   — constant additive offset in Amperes
    spike  — occasional unrealistic high-current readings
    noise  — increased Gaussian noise
"""

from __future__ import annotations

import random
from typing import Optional

from simulator.sensors.base import BaseSensor, SensorQuality, SensorReading


class CurrentSensor(BaseSensor):
    """Hall-effect or shunt-based motor current sensor.

    Parameters
    ----------
    sample_rate_hz  : default 50 Hz
    noise_sigma     : Gaussian noise std deviation in Amperes
    spike_prob      : probability of a random spike per sample
    spike_magnitude : max spike amplitude above true value (A)
    """

    def __init__(
        self,
        sample_rate_hz: float = 50.0,
        noise_sigma: float = 0.05,
        spike_prob: float = 0.002,
        spike_magnitude: float = 5.0,
    ) -> None:
        super().__init__(sample_rate_hz)
        self.noise_sigma = noise_sigma
        self.spike_prob = spike_prob
        self.spike_magnitude = spike_magnitude

        # Fault injection state
        self.fault_bias_a: float = 0.0
        self.fault_noise_multiplier: float = 1.0
        self.fault_spike_extra_prob: float = 0.0

    def _measure(
        self, true_value: Optional[float], sim_time: float
    ) -> SensorReading:
        if true_value is None:
            true_value = 0.0

        # ── Spike ──────────────────────────────────────────────────────────────
        effective_spike_prob = min(1.0, self.spike_prob + self.fault_spike_extra_prob)
        if random.random() < effective_spike_prob:
            spike = random.uniform(self.spike_magnitude * 0.5, self.spike_magnitude)
            return SensorReading(
                value=round(true_value + spike, 3),
                timestamp=sim_time,
                quality=SensorQuality.INVALID,
            )

        # ── Gaussian noise ──────────────────────────────────────────────────────
        effective_sigma = self.noise_sigma * self.fault_noise_multiplier
        measured = true_value + random.gauss(0.0, effective_sigma)

        # ── Bias ────────────────────────────────────────────────────────────────
        measured += self.fault_bias_a

        # Physical lower bound: current can't be negative
        measured = max(0.0, measured)

        quality = (
            SensorQuality.NOISY
            if self.fault_noise_multiplier > 2.0
            else SensorQuality.GOOD
        )

        return SensorReading(
            value=round(measured, 3),
            timestamp=sim_time,
            quality=quality,
        )

    def apply_bias(self, bias_a: float) -> None:
        """Apply additive current bias in Amperes."""
        self.fault_bias_a = bias_a

    def apply_spike(self, extra_probability: float) -> None:
        """Increase spike probability by extra_probability."""
        self.fault_spike_extra_prob = max(0.0, min(1.0, extra_probability))

    def apply_noise(self, multiplier: float) -> None:
        self.fault_noise_multiplier = max(1.0, multiplier)

    def clear_faults(self) -> None:
        self.fault_bias_a = 0.0
        self.fault_noise_multiplier = 1.0
        self.fault_spike_extra_prob = 0.0
