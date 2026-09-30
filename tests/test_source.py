"""tests/test_source.py — Hardware Abstraction Layer & TelemetrySource lifecycle tests."""

from __future__ import annotations

import asyncio
import pytest

from simulator.telemetry.schema import (
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
    TelemetrySource,
)


def make_dummy_message(seq: int = 1) -> TelemetryMessage:
    return TelemetryMessage(
        sequence=seq,
        machine=MachineInfo(machine_id="rc-sim-01"),
        sensors=SensorReadings(distance_cm=50.0, current_a=1.2, wheel_rpm=300.0),
    )


# ── SimulatorTelemetrySource Lifecycle ────────────────────────────────────────

@pytest.mark.asyncio
async def test_simulator_source_lifecycle():
    queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue()
    source = SimulatorTelemetrySource(queue)

    # Initial state
    assert source.connection_state == ConnectionState.DISCONNECTED
    assert source.is_connected is False
    assert source.is_streaming is False

    # Connect
    await source.connect()
    assert source.connection_state == ConnectionState.CONNECTED
    assert source.is_connected is True

    # Start streaming
    await source.start()
    assert source.is_streaming is True

    # Put packet and retrieve via get_next
    msg = make_dummy_message(1)
    await queue.put(msg)
    retrieved = await source.get_next(timeout=1.0)
    assert retrieved is not None
    assert retrieved.sequence == 1

    # Stop streaming
    await source.stop()
    assert source.is_streaming is False
    assert await source.get_next(timeout=0.1) is None

    # Disconnect
    await source.disconnect()
    assert source.connection_state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_simulator_source_async_iteration():
    queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue()
    source = SimulatorTelemetrySource(queue)
    await source.start()

    # Put 3 messages
    for i in range(1, 4):
        await queue.put(make_dummy_message(i))

    received = []
    # Test __aiter__ / telemetry generator
    async for msg in source:
        received.append(msg.sequence)
        if len(received) == 3:
            await source.stop()
            break

    assert received == [1, 2, 3]


# ── BluetoothTelemetrySource Stub Verification ────────────────────────────────

@pytest.mark.asyncio
async def test_bluetooth_source_stub_guidance():
    source = BluetoothTelemetrySource("AA:BB:CC:DD:EE:FF")
    assert source.connection_state == ConnectionState.DISCONNECTED

    with pytest.raises(NotImplementedError, match="requires physical ESP32 hardware"):
        await source.connect()
