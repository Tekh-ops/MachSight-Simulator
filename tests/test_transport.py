"""tests/test_transport.py — Source vs Transport separation and failure handling tests."""

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
from simulator.telemetry.websocket import (
    LocalBroadcaster,
    MachSightForwarder,
    TransportState,
)


def make_sample_message() -> TelemetryMessage:
    return TelemetryMessage(
        sequence=1,
        machine=MachineInfo(machine_id="rc-sim-01"),
        operating_state=OperatingState(mode="forward", pwm_command=100),
        sensors=SensorReadings(distance_cm=50.0, current_a=1.0, wheel_rpm=200.0),
        sensor_status=SensorStatus(distance="ok", current="ok", wheel_rpm="ok"),
    )


# ── LocalBroadcaster ──────────────────────────────────────────────────────────

def test_local_broadcaster_initial_state():
    broadcaster = LocalBroadcaster()
    assert broadcaster.client_count == 0


@pytest.mark.asyncio
async def test_local_broadcaster_broadcast_empty_safely():
    broadcaster = LocalBroadcaster()
    msg = make_sample_message()
    # Broadcaster with 0 clients should safely do nothing without raising
    await broadcaster.broadcast(msg)
    assert broadcaster.client_count == 0


# ── MachSightForwarder & Transport Failure Handling ───────────────────────────

def test_forwarder_initial_state():
    forwarder = MachSightForwarder("ws://127.0.0.1:9999/non_existent")
    assert forwarder.state == TransportState.DISCONNECTED
    assert forwarder.is_connected is False
    assert forwarder.stats["packets_sent"] == 0


@pytest.mark.asyncio
async def test_forwarder_unreachable_backend_distinguished_failure():
    """Verify transport failure when MachSight is unavailable is accounted for cleanly.

    It must NOT mutate sensor values or sensor health.
    """
    # Point to a dead/unbound local port
    forwarder = MachSightForwarder("ws://127.0.0.1:59999/telemetry/ingest")
    msg = make_sample_message()

    # Attempt to forward
    sent = await forwarder.forward(msg)
    assert sent is False
    assert forwarder.state in (TransportState.FAILED, TransportState.DISCONNECTED)
    assert forwarder.stats["connection_failures"] >= 1

    # Verify sensor readings remain completely uncorrupted
    assert msg.sensors.distance_cm == 50.0
    assert msg.sensor_status.distance == "ok"
    assert msg.sensors.current_a == 1.0


@pytest.mark.asyncio
async def test_forwarder_graceful_close():
    forwarder = MachSightForwarder("ws://127.0.0.1:59999/telemetry/ingest")
    await forwarder.close()
    assert forwarder.state == TransportState.DISCONNECTED
