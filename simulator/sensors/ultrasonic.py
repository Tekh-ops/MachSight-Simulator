"""
simulator/sensors/ultrasonic.py
================================
Ultrasonic distance sensor model.

True value: obstacle_distance_m converted to cm (from Environment model).

Observable faults (applied by FaultManager):
    stuck         — returns a fixed value regardless of true distance
    noisy         — dramatically increased Gaussian noise
    bias          — constant additive offset
    dropout       — occasional None readings
    spike         — occasional unrealistic values
"""

from __future__ import annotations

import random
from typing import Optional

from simulator.sensors.base import BaseSensor, SensorQuality, SensorReading


class UltrasonicSensor(BaseSensor):
    """Simulated HC-SR04 style ultrasonic distance sensor.

    Parameters
    ----------
    sample_rate_hz  : sampling frequency (default 10 Hz)
    noise_sigma     : Gaussian noise standard deviation in cm
    min_cm / max_cm : sensor measurement range
    dropout_prob    : probability of a dropped reading per sample
    spike_prob      : probability of a random spike per sample
    """

    def __init__(
        self,
        sample_rate_hz: float = 10.0,
        noise_sigma: float = 1.2,
        min_cm: float = 2.0,
        max_cm: float = 400.0,
        dropout_prob: float = 0.005,
        spike_prob: float = 0.003,
    ) -> None:
        super().__init__(sample_rate_hz)
        self.noise_sigma = noise_sigma
        self.min_cm = min_cm
        self.max_cm = max_cm
        self.dropout_prob = dropout_prob
        self.spike_prob = spike_prob

        # Fault injection state (set by FaultManager)
        self.fault_stuck: Optional[float] = None    # cm — stuck value
        self.fault_noise_multiplier: float = 1.0
        self.fault_bias_cm: float = 0.0
        self.fault_dropout_extra: float = 0.0

    def _measure(
        self, true_value_m: Optional[float], sim_time: float
    ) -> SensorReading:
        """Convert true distance (metres) to observable cm reading."""

        # ── Stuck fault ───────────────────────────────────────────────────────
        if self.fault_stuck is not None:
            return SensorReading(
                value=self.fault_stuck,
                timestamp=sim_time,
                quality=SensorQuality.STALE,
            )

        # ── No obstacle in range ──────────────────────────────────────────────
        if true_value_m is None:
            # Sensor reports max range when nothing is detected
            true_cm = self.max_cm
        else:
            true_cm = true_value_m * 100.0

        # ── Dropout ───────────────────────────────────────────────────────────
        effective_dropout = min(1.0, self.dropout_prob + self.fault_dropout_extra)
        if random.random() < effective_dropout:
            return SensorReading(
                value=None,
                timestamp=sim_time,
                quality=SensorQuality.DROPPED,
            )

        # ── Spike ─────────────────────────────────────────────────────────────
        if random.random() < self.spike_prob:
            spike_val = random.uniform(0.0, self.max_cm)
            return SensorReading(
                value=spike_val,
                timestamp=sim_time,
                quality=SensorQuality.INVALID,
            )

        # ── Gaussian noise ────────────────────────────────────────────────────
        effective_sigma = self.noise_sigma * self.fault_noise_multiplier
        noisy_cm = true_cm + random.gauss(0.0, effective_sigma)

        # ── Bias ──────────────────────────────────────────────────────────────
        noisy_cm += self.fault_bias_cm

        # ── Clamp to sensor range ─────────────────────────────────────────────
        noisy_cm = max(self.min_cm, min(self.max_cm, noisy_cm))

        quality = (
            SensorQuality.NOISY
            if self.fault_noise_multiplier > 2.0
            else SensorQuality.GOOD
        )

        return SensorReading(
            value=round(noisy_cm, 2),
            timestamp=sim_time,
            quality=quality,
        )

    def apply_stuck(self, value_cm: Optional[float]) -> None:
        """Freeze the sensor at value_cm.  Pass None to clear."""
        self.fault_stuck = value_cm

    def apply_noise(self, multiplier: float) -> None:
        """Scale noise.  1.0 = normal, >1 = noisier."""
        self.fault_noise_multiplier = max(1.0, multiplier)

    def apply_bias(self, bias_cm: float) -> None:
        """Apply additive bias in cm."""
        self.fault_bias_cm = bias_cm

    def apply_dropout(self, extra_probability: float) -> None:
        """Increase dropout probability.  0.0 = no extra dropout."""
        self.fault_dropout_extra = max(0.0, min(1.0, extra_probability))

    def clear_faults(self) -> None:
        """Remove all sensor faults."""
        self.fault_stuck = None
        self.fault_noise_multiplier = 1.0
        self.fault_bias_cm = 0.0
        self.fault_dropout_extra = 0.0
