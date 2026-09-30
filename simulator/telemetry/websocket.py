"""
simulator/telemetry/websocket.py
=================================
Telemetry Transport Layer.

Defines delivery transports for canonical TelemetryMessage streams:
1. LocalBroadcaster: in-process WebSocket publisher for local observer clients.
2. MachSightForwarder: network transport forwarding canonical telemetry to MachSight.

CRITICAL Architectural Principle
--------------------------------
Transport delivers data; it does NOT generate, modify, or diagnose data.
Transport failures (e.g. backend unreachable) are strictly distinguished
from sensor failures (e.g. HC-SR04 dropout) and source failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from enum import Enum
from typing import Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from simulator.config import settings
from simulator.telemetry.schema import TelemetryMessage

logger = logging.getLogger(__name__)


class TransportState(str, Enum):
    """Lifecycle and connectivity state of the transport layer."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


# ── Transport Exceptions ──────────────────────────────────────────────────────

class TransportError(Exception):
    """Base exception for transport layer failures."""


class BackendUnavailableError(TransportError):
    """Raised when the downstream ingestion endpoint cannot be reached."""


# ── Local Observer Broadcaster ────────────────────────────────────────────────

class LocalBroadcaster:
    """Maintains a set of local WebSocket subscribers and broadcasts to them.

    These are observer clients (dashboards, debug monitors, integration tests)
    inspecting the raw canonical feed.
    """

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def subscribe(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("Local subscriber connected (total=%d)", len(self._clients))
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(ws)
            logger.info("Local subscriber disconnected (total=%d)", len(self._clients))

    async def broadcast(self, msg: TelemetryMessage) -> None:
        if not self._clients:
            return
        payload = json.dumps(msg.model_dump())
        dead: Set[WebSocket] = set()
        for ws in self._clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead


# ── MachSight Forwarder Transport ─────────────────────────────────────────────

class MachSightForwarder:
    """Forwards canonical TelemetryMessage to MachSight ingestion endpoint.

    Handles transport state tracking, reconnection, network imperfection
    simulation, and distinguished failure accounting.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._ws = None
        self._state: TransportState = TransportState.DISCONNECTED
        self._sent_count: int = 0
        self._drop_count: int = 0
        self._failure_count: int = 0

    @property
    def state(self) -> TransportState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == TransportState.CONNECTED

    @property
    def stats(self) -> dict:
        return {
            "state": self._state.value,
            "packets_sent": self._sent_count,
            "packets_dropped": self._drop_count,
            "connection_failures": self._failure_count,
        }

    async def connect(self) -> bool:
        """Attempt to connect to MachSight WebSocket endpoint."""
        self._state = TransportState.CONNECTING
        try:
            import websockets  # type: ignore

            self._ws = await websockets.connect(self._url, ping_interval=20)
            self._state = TransportState.CONNECTED
            logger.info("Transport connected to MachSight at %s", self._url)
            return True
        except Exception as exc:
            self._failure_count += 1
            self._state = TransportState.FAILED
            logger.warning("MachSight transport connection failed (%s): %s", self._url, exc)
            return False

    async def forward(self, msg: TelemetryMessage) -> bool:
        """Forward a canonical telemetry message to MachSight.

        Returns True if successfully dispatched, False if dropped or connection failed.
        """
        # 1. Network imperfection simulation
        if settings.network_packet_loss > 0.0:
            if random.random() < settings.network_packet_loss:
                self._drop_count += 1
                return False  # Simulated network loss

        if settings.network_latency_ms > 0.0 or settings.network_jitter_ms > 0.0:
            jitter = (
                random.uniform(0, settings.network_jitter_ms)
                if settings.network_jitter_ms > 0
                else 0.0
            )
            delay = (settings.network_latency_ms + jitter) / 1000.0
            await asyncio.sleep(delay)

        # 2. Connection management
        if self._ws is None or self._state != TransportState.CONNECTED:
            if not await self.connect():
                return False

        # 3. Payload serialisation & dispatch
        payload = json.dumps(msg.to_machsight_dict())
        try:
            await self._ws.send(payload)
            self._sent_count += 1
            return True
        except Exception as exc:
            logger.warning("MachSight dispatch failed (%s) — marking transport reconnecting", exc)
            self._state = TransportState.RECONNECTING
            self._ws = None
            return False

    async def close(self) -> None:
        """Gracefully terminate transport connection."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._state = TransportState.DISCONNECTED


# ── Module Singletons ─────────────────────────────────────────────────────────

broadcaster = LocalBroadcaster()
forwarder = MachSightForwarder(settings.machsight_ws_url)
