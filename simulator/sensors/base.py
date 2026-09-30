"""
simulator/sensors/base.py
==========================
Base class for all sensor models.

Each sensor has:
- A sampling rate (Hz)
- A configurable noise model
- Internal quality tracking
- A next-sample timestamp scheduler

Sensor faults (bias, stuck, dropout, spike) are applied as transforms on
top of the true value produced by the physics engine.
"""

from __future__ import annotations

import enum
import time
from abc import ABC, abstractmethod
from typing import Optional


class SensorQuality(str, enum.Enum):
    GOOD = "good"
    NOISY = "noisy"
    STALE = "stale"
    DROPPED = "dropped"
    INVALID = "invalid"


class SensorReading:
    """A single sensor sample with quality metadata."""

    __slots__ = ("value", "timestamp", "quality")

    def __init__(
        self,
        value: Optional[float],
        timestamp: float,
        quality: SensorQuality = SensorQuality.GOOD,
    ) -> None:
        self.value = value
        self.timestamp = timestamp
        self.quality = quality

    def __repr__(self) -> str:
        return (
            f"SensorReading(value={self.value!r}, "
            f"quality={self.quality.value}, "
            f"ts={self.timestamp:.4f})"
        )


class BaseSensor(ABC):
    """Abstract sensor with scheduled sampling.

    Sub-classes override ``_measure(true_value)`` to apply noise and
    sensor-specific faults.  The base class handles the sampling schedule.
    """

    def __init__(self, sample_rate_hz: float) -> None:
        self._sample_rate_hz = sample_rate_hz
        self._sample_interval = 1.0 / sample_rate_hz
        self._next_sample_time: float = 0.0
        self._last_reading: Optional[SensorReading] = None

    @property
    def sample_rate_hz(self) -> float:
        return self._sample_rate_hz

    def is_due(self, sim_time: float) -> bool:
        """Return True if this sensor should emit a reading at sim_time."""
        return sim_time >= self._next_sample_time

    def sample(
        self, true_value: Optional[float], sim_time: float
    ) -> Optional[SensorReading]:
        """Produce a reading if due at sim_time, else return None.

        Parameters
        ----------
        true_value : internal ground-truth value
        sim_time   : current simulation clock (seconds)

        Returns the SensorReading if it was time to sample, else None.
        """
        if not self.is_due(sim_time):
            return None
        self._next_sample_time = sim_time + self._sample_interval
        reading = self._measure(true_value, sim_time)
        self._last_reading = reading
        return reading

    @abstractmethod
    def _measure(
        self, true_value: Optional[float], sim_time: float
    ) -> SensorReading:
        """Transform ground-truth value into observable measurement."""

    def reset(self) -> None:
        """Reset sensor to initial state (e.g. on scenario restart)."""
        self._next_sample_time = 0.0
        self._last_reading = None
