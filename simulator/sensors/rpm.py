"""
simulator/sensors/rpm.py
=========================
Wheel RPM sensor model (hall-effect encoder simulation).

True value: vehicle.wheel_rpm (from physics engine).

Observable faults (applied by FaultManager):
    stuck    — frozen reading
    dropout  — occasional None / unavailable
    noise    — increased jitter
"""

from __future__ import annotations

import random
from typing import Optional

from simulator.sensors.base import BaseSensor, SensorQuality, SensorReading


class RPMSensor(BaseSensor):
    """Hall-effect wheel encoder sensor.

    Parameters
    ----------
    sample_rate_hz : default 20 Hz
    noise_sigma    : jitter in RPM units
    dropout_prob   : probability of a missing sample per measurement
    """

    def __init__(
        self,
        sample_rate_hz: float = 20.0,
        noise_sigma: float = 3.0,
        dropout_prob: float = 0.003,
    ) -> None:
        super().__init__(sample_rate_hz)
        self.noise_sigma = noise_sigma
        self.dropout_prob = dropout_prob

        # Fault injection state
        self.fault_stuck: Optional[float] = None   # RPM — stuck value
        self.fault_dropout_extra: float = 0.0
        self.fault_noise_multiplier: float = 1.0

    def _measure(
        self, true_value: Optional[float], sim_time: float
    ) -> SensorReading:
        if true_value is None:
            true_value = 0.0

        # ── Stuck fault ────────────────────────────────────────────────────────
        if self.fault_stuck is not None:
            return SensorReading(
                value=self.fault_stuck,
                timestamp=sim_time,
                quality=SensorQuality.STALE,
            )

        # ── Dropout ────────────────────────────────────────────────────────────
        effective_dropout = min(1.0, self.dropout_prob + self.fault_dropout_extra)
        if random.random() < effective_dropout:
            return SensorReading(
                value=None,
                timestamp=sim_time,
                quality=SensorQuality.DROPPED,
            )

        # ── Gaussian noise (encoder quantisation is small enough to model as Gaussian)
        effective_sigma = self.noise_sigma * self.fault_noise_multiplier
        measured = true_value + random.gauss(0.0, effective_sigma)
        measured = max(0.0, measured)

        quality = (
            SensorQuality.NOISY
            if self.fault_noise_multiplier > 2.0
            else SensorQuality.GOOD
        )

        return SensorReading(
            value=round(measured, 2),
            timestamp=sim_time,
            quality=quality,
        )

    def apply_stuck(self, value_rpm: Optional[float]) -> None:
        """Freeze sensor at value_rpm.  Pass None to clear."""
        self.fault_stuck = value_rpm

    def apply_dropout(self, extra_probability: float) -> None:
        self.fault_dropout_extra = max(0.0, min(1.0, extra_probability))

    def apply_noise(self, multiplier: float) -> None:
        self.fault_noise_multiplier = max(1.0, multiplier)

    def clear_faults(self) -> None:
        self.fault_stuck = None
        self.fault_dropout_extra = 0.0
        self.fault_noise_multiplier = 1.0
