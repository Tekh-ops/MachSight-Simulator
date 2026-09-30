"""
simulator/telemetry/source.py
==============================
Hardware Abstraction Layer: TelemetrySource Contract.

This is the primary architectural boundary separating physical or virtual
acquisition sources from downstream processing.

Architectural Seam Principle
----------------------------
"MachSight must consume machine telemetry, not simulator telemetry or hardware telemetry."

The interface represents:
    "Something that produces canonical machine telemetry."

It must NOT expose simulator-specific concepts (physics, environment, faults,
scenario engine, ground truth).

Implementations:
    - SimulatorTelemetrySource: backed by the simulation engine's output queue.
    - MockHardwareTelemetrySource: receives raw byte/dict packets and normalises units.
    - BluetoothTelemetrySource: future ESP32 BLE GATT client (stub for documentation).
    - SerialTelemetrySource: future USB/UART serial bridge.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import AsyncIterator, Optional

from simulator.telemetry.schema import TelemetryMessage


class ConnectionState(str, Enum):
    """Lifecycle and health state of a TelemetrySource."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    FAILED = "failed"


# ── Exception Hierarchy ────────────────────────────────────────────────────────

class TelemetrySourceError(Exception):
    """Base exception for all telemetry source failures."""


class SourceConnectionError(TelemetrySourceError):
    """Raised when establishing a connection to the device or source fails."""


class SourceDisconnectedError(TelemetrySourceError):
    """Raised when an operation is attempted on a disconnected source."""


class SourceTimeoutError(TelemetrySourceError):
    """Raised when waiting for the next telemetry packet times out."""


# ── Abstract Base Class ───────────────────────────────────────────────────────

class TelemetrySource(ABC):
    """Abstract base for every telemetry origin.

    Defines connection lifecycle, streaming lifecycle, and async iteration.
    Implementations must yield canonical :class:`TelemetryMessage` instances.
    """

    def __init__(self) -> None:
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._streaming: bool = False

    @property
    def connection_state(self) -> ConnectionState:
        """Current connectivity status of the source."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """True if the device/medium is connected."""
        return self._state in (ConnectionState.CONNECTED, ConnectionState.DEGRADED)

    @property
    def is_streaming(self) -> bool:
        """True if active telemetry emission is running."""
        return self._streaming

    # ── Connection Lifecycle ──────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Open connection to the physical device or virtual source."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection and release underlying hardware/network resources."""

    # ── Streaming Lifecycle ───────────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """Begin telemetry acquisition and stream generation."""

    @abstractmethod
    async def stop(self) -> None:
        """Halt telemetry acquisition without necessarily disconnecting hardware."""

    # ── Acquisition Methods ───────────────────────────────────────────────────

    @abstractmethod
    async def get_next(self, timeout: Optional[float] = None) -> Optional[TelemetryMessage]:
        """Fetch the next TelemetryMessage, or return None if timeout expires."""

    async def telemetry(self) -> AsyncIterator[TelemetryMessage]:
        """Async generator yielding TelemetryMessage objects while streaming."""
        while self._streaming:
            try:
                msg = await self.get_next(timeout=1.0)
                if msg is not None:
                    yield msg
            except asyncio.CancelledError:
                break

    def __aiter__(self) -> AsyncIterator[TelemetryMessage]:
        """Support direct `async for msg in source:` syntax."""
        return self.telemetry()


# ── Simulator Telemetry Source ────────────────────────────────────────────────

class SimulatorTelemetrySource(TelemetrySource):
    """TelemetrySource backed by the internal simulation engine.

    Pulls ready canonical TelemetryMessage packets from the engine's async queue.
    Contains no vehicle dynamics or environment logic.
    """

    def __init__(self, queue: "asyncio.Queue[TelemetryMessage]") -> None:
        super().__init__()
        self._queue: "asyncio.Queue[TelemetryMessage]" = queue

    async def connect(self) -> None:
        """Attach to the simulation engine queue."""
        self._state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        """Detach from the simulation engine."""
        self._streaming = False
        self._state = ConnectionState.DISCONNECTED

    async def start(self) -> None:
        """Start pulling packets from the simulation engine."""
        if not self.is_connected:
            await self.connect()
        self._streaming = True

    async def stop(self) -> None:
        """Stop pulling packets."""
        self._streaming = False

    async def get_next(self, timeout: Optional[float] = None) -> Optional[TelemetryMessage]:
        if not self._streaming:
            return None
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None

    async def __anext__(self) -> TelemetryMessage:
        """Compatibility with legacy __anext__ callers."""
        if not self._streaming:
            raise StopAsyncIteration
        msg = await self.get_next()
        if msg is None:
            raise StopAsyncIteration
        return msg


# ── Future Hardware Stubs ─────────────────────────────────────────────────────

class BluetoothTelemetrySource(TelemetrySource):
    """Hardware adapter for future ESP32 Bluetooth / BLE telemetry.

    When the ESP32 hardware becomes available:
    1. Implement connect() using BleakClient.
    2. Register GATT notification handler that normalises raw sensor bytes into
       a canonical TelemetryMessage.
    3. Feed parsed messages into an internal queue for get_next().
    4. Downstream MachSight forwarder and diagnostic pipeline require ZERO changes.

    DO NOT add bleak as a required dependency until physical hardware arrives.
    """

    def __init__(self, device_address: str = "00:00:00:00:00:00") -> None:
        super().__init__()
        self.device_address = device_address

    async def connect(self) -> None:
        raise NotImplementedError(
            "BluetoothTelemetrySource requires physical ESP32 hardware. "
            "Use SimulatorTelemetrySource or MockHardwareTelemetrySource for testing."
        )

    async def disconnect(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    async def start(self) -> None:
        raise NotImplementedError("Bluetooth hardware not attached.")

    async def stop(self) -> None:
        self._streaming = False

    async def get_next(self, timeout: Optional[float] = None) -> Optional[TelemetryMessage]:
        raise NotImplementedError("Bluetooth hardware not attached.")
