"""
simulator/faults/base.py
=========================
Base class and data model for all faults.

Faults are modular, time-scheduled, severity-parameterised effects.

Fault lifecycle:
    PENDING   → not yet triggered (start_s in the future)
    ACTIVE    → currently affecting simulation
    COMPLETED → duration exhausted
    CLEARED   → manually removed before completion
"""

from __future__ import annotations

import dataclasses
import enum
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from simulator.core.vehicle import VehicleState
    from simulator.sensors.ultrasonic import UltrasonicSensor
    from simulator.sensors.current import CurrentSensor
    from simulator.sensors.rpm import RPMSensor


class FaultStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CLEARED = "cleared"


@dataclasses.dataclass
class FaultRecord:
    """Metadata describing a scheduled or active fault."""

    fault_id: str
    fault_type: str
    start_s: float
    duration_s: Optional[float]   # None = permanent until cleared
    severity: float               # 0.0–1.0
    severity_end: Optional[float] = None  # for ramping faults
    component: str = "general"
    status: FaultStatus = FaultStatus.PENDING
    activated_at: Optional[float] = None
    cleared_at: Optional[float] = None

    @property
    def end_s(self) -> Optional[float]:
        """Calculated scheduled end time in seconds."""
        if self.duration_s is None:
            return None
        return self.start_s + self.duration_s

    @property
    def is_ramping(self) -> bool:
        return self.severity_end is not None

    def current_severity(self, sim_time: float) -> float:
        """Return interpolated severity for ramping faults."""
        if not self.is_ramping or self.duration_s is None or self.activated_at is None:
            return self.severity
        elapsed = sim_time - self.activated_at
        fraction = min(1.0, elapsed / max(self.duration_s, 1e-6))
        return self.severity + fraction * (self.severity_end - self.severity)  # type: ignore[operator]


class BaseFault(ABC):
    """Abstract fault.

    Subclasses implement ``apply()`` which modifies vehicle state and/or
    sensor fault flags based on the current severity.
    """

    fault_type: str = "unknown"

    def __init__(self, record: FaultRecord) -> None:
        self.record = record

    def activate(self, sim_time: float) -> None:
        self.record.status = FaultStatus.ACTIVE
        self.record.activated_at = sim_time

    def complete(self) -> None:
        self.record.status = FaultStatus.COMPLETED

    def clear(self, sim_time: float) -> None:
        self.record.status = FaultStatus.CLEARED
        self.record.cleared_at = sim_time

    @abstractmethod
    def apply(
        self,
        sim_time: float,
        vehicle: "VehicleState",
        ultrasonic: "UltrasonicSensor",
        current_sensor: "CurrentSensor",
        rpm_sensor: "RPMSensor",
    ) -> None:
        """Apply fault effects to vehicle state and sensors."""

    @abstractmethod
    def remove(
        self,
        vehicle: "VehicleState",
        ultrasonic: "UltrasonicSensor",
        current_sensor: "CurrentSensor",
        rpm_sensor: "RPMSensor",
    ) -> None:
        """Remove fault effects (called when fault expires or is cleared)."""
