"""
simulator/telemetry
===================
Canonical machine telemetry, hardware abstraction layer, and transport modules.
"""

from simulator.telemetry.schema import (
    SCHEMA_VERSION,
    MachineInfo,
    OperatingState,
    SensorReadings,
    SensorStatus,
    SourceInfo,
    TelemetryMessage,
)
from simulator.telemetry.source import (
    BluetoothTelemetrySource,
    ConnectionState,
    SimulatorTelemetrySource,
    SourceConnectionError,
    SourceDisconnectedError,
    SourceTimeoutError,
    TelemetrySource,
    TelemetrySourceError,
)
from simulator.telemetry.mock_hardware import (
    MockHardwareTelemetrySource,
)
from simulator.telemetry.validation import (
    SequenceTracker,
    TelemetryValidationError,
    TelemetryValidator,
    ValidationReport,
)
from simulator.telemetry.websocket import (
    BackendUnavailableError,
    LocalBroadcaster,
    MachSightForwarder,
    TransportError,
    TransportState,
    broadcaster,
    forwarder,
)

__all__ = [
    # Schema
    "SCHEMA_VERSION",
    "MachineInfo",
    "OperatingState",
    "SensorReadings",
    "SensorStatus",
    "SourceInfo",
    "TelemetryMessage",
    # Source / HAL
    "TelemetrySource",
    "SimulatorTelemetrySource",
    "MockHardwareTelemetrySource",
    "BluetoothTelemetrySource",
    "ConnectionState",
    "TelemetrySourceError",
    "SourceConnectionError",
    "SourceDisconnectedError",
    "SourceTimeoutError",
    # Validation
    "TelemetryValidator",
    "ValidationReport",
    "TelemetryValidationError",
    "SequenceTracker",
    # Transport
    "LocalBroadcaster",
    "MachSightForwarder",
    "TransportState",
    "TransportError",
    "BackendUnavailableError",
    "broadcaster",
    "forwarder",
]
