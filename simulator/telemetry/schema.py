"""
simulator/telemetry/schema.py
==============================
Canonical telemetry message definition and contract models.

This module defines the *one* telemetry message contract that every telemetry
source (simulator, mock hardware, and future ESP32 Bluetooth / Serial bridges)
must produce.

CRITICAL Architectural Seam Rules
---------------------------------
1. Downstream consumers (MachSight) consume machine telemetry, not simulator
   or hardware telemetry.
2. Ground truth, fault labels, and simulator internal variables MUST NEVER appear here.
3. The ``sensors`` sub-model contains only observable physical measurements
   with stable, canonical field names and units:
     - ``distance_cm``: float (cm)
     - ``current_a``: float (A)
     - ``wheel_rpm``: float (RPM)
4. ``sensor_status`` provides sensor health / quality metadata for downstream
   diagnostics to distinguish a machine failure from a sensor malfunction.
5. ``source`` identifies the telemetry origin without requiring downstream code
   to branch on source type.
6. The schema is backward-compatible with MachSight backend ``IncomingTelemetry``
   (schema_version 2, flat field aliases accepted on the backend).
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = 2          # Canonical schema version
_SCHEMA_VERSION_MACHSIGHT = 2  # Version MachSight backend expects


class MachineInfo(BaseModel):
    """Machine identity and classification."""
    model_config = ConfigDict(frozen=True)

    machine_id: str = Field(..., description="Unique machine identifier (e.g. 'rc-car-01')")
    asset_type: str = Field(default="rc_vehicle", description="Asset category (e.g. 'rc_vehicle')")


class OperatingState(BaseModel):
    """Observable machine operating mode and control input."""
    model_config = ConfigDict(frozen=True)

    mode: str = Field(default="idle", description="Operating state: idle | forward | reverse | braking | stopped")
    pwm_command: int = Field(default=0, ge=0, le=255, description="Motor throttle duty command [0, 255]")


class SensorReadings(BaseModel):
    """Three observable sensor outputs in canonical engineering units.

    None means the reading is unavailable or dropped (e.g. sensor dropout fault).
    Consumers must tolerate None for any field.
    """
    model_config = ConfigDict(frozen=True)

    distance_cm: Optional[float] = Field(default=None, description="Ultrasonic distance to forward obstacle (cm)")
    current_a: Optional[float] = Field(default=None, description="Motor current draw (Amperes)")
    wheel_rpm: Optional[float] = Field(default=None, description="Wheel rotational speed (Revolutions Per Minute)")


class SensorStatus(BaseModel):
    """Per-sensor health and acquisition status.

    Permits downstream anomaly detectors and diagnostics to distinguish
    an authentic machine defect from a degraded or failing sensor.
    Values: 'ok' | 'stale' | 'dropped' | 'degraded' | 'invalid' | 'disconnected'
    """
    model_config = ConfigDict(frozen=True)

    distance: str = Field(default="ok", description="Distance sensor health")
    current: str = Field(default="ok", description="Current sensor health")
    wheel_rpm: str = Field(default="ok", description="Wheel RPM encoder health")


class SourceInfo(BaseModel):
    """Source provenance and transport metadata.

    Informational only; downstream diagnostic pipelines must NOT branch
    their diagnostic logic based on source type.
    """
    model_config = ConfigDict(frozen=True)

    source_type: str = Field(
        default="simulator",
        description="Source category: 'simulator' | 'hardware' | 'mock_hardware'",
    )
    device_id: str = Field(
        default="rc-sim-01",
        description="Originating physical or virtual hardware device identifier",
    )
    transport: str = Field(
        default="simulation",
        description="Transport medium: 'simulation' | 'websocket' | 'bluetooth' | 'serial' | 'mock'",
    )
    firmware_version: Optional[str] = Field(
        default=None,
        description="Optional embedded firmware version (e.g. 'v1.2.0')",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Unique session UUID for stream continuity and sequence tracking",
    )


class TelemetryMessage(BaseModel):
    """Canonical telemetry payload produced by any TelemetrySource.

    This is the boundary contract object that crosses from any TelemetrySource
    to MachSight. It must NEVER contain fault labels, ground-truth state,
    or simulator-only variables.
    """
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(
        default=SCHEMA_VERSION,
        description="Contract schema version",
    )
    timestamp: float = Field(
        default_factory=time.time,
        description="Measurement event timestamp in UTC Unix epoch seconds",
    )
    measurement_timestamp: Optional[float] = Field(
        default=None,
        description="Optional sensor acquisition timestamp if distinct from transmission timestamp",
    )
    sequence: int = Field(
        default=0,
        ge=0,
        description="Monotonically increasing packet sequence counter within a session",
    )
    machine: MachineInfo
    operating_state: OperatingState = Field(default_factory=OperatingState)
    sensors: SensorReadings = Field(default_factory=SensorReadings)
    sensor_status: SensorStatus = Field(default_factory=SensorStatus)
    source: SourceInfo = Field(default_factory=SourceInfo)

    def to_machsight_dict(self) -> Dict[str, Any]:
        """Return a flat dict compatible with MachSight IncomingTelemetry.

        MachSight backend (schema_version 2) accepts a flat payload with these
        fields at the top level:
            machine_id, car_id, asset_type, timestamp,
            schema_version, mode, pwm_command,
            current_a, rpm, distance_cm

        The ``rpm`` field is the MachSight name for wheel_rpm.
        Hidden simulator state is strictly excluded.
        """
        return {
            "schema_version": _SCHEMA_VERSION_MACHSIGHT,
            "machine_id": self.machine.machine_id,
            "car_id": self.machine.machine_id,
            "asset_type": self.machine.asset_type,
            "timestamp": self.timestamp,
            "mode": self.operating_state.mode,
            "pwm_command": self.operating_state.pwm_command,
            # Sensor values — None is omitted (MachSight defaults to 0.0)
            **({} if self.sensors.current_a is None else {"current_a": self.sensors.current_a}),
            **({} if self.sensors.wheel_rpm is None else {"rpm": self.sensors.wheel_rpm}),
            **({} if self.sensors.distance_cm is None else {"distance_cm": self.sensors.distance_cm}),
        }
