"""
simulator/integration/machsight_client.py
=========================================
Asynchronous client for streaming telemetry to MachSight and collecting
real-time diagnostic and anomaly evaluation events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx
import websockets

from simulator.integration.result_parser import (
    AnomalyDetectionRecord,
    DiagnosticRunResult,
    InvestigationRecord,
    ResultParser,
)

logger = logging.getLogger(__name__)


class MachSightConnectionError(Exception):
    """Raised when MachSight backend is unreachable or connection drops."""
    pass


class MachSightProtocolError(Exception):
    """Raised when MachSight backend rejects telemetry or emits invalid frames."""
    pass


class MachSightTimeoutError(Exception):
    """Raised when waiting for diagnostic response exceeds timeout."""
    pass


class MachSightClient:
    """Async client managing communication with MachSight backend."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        ingest_ws_url: Optional[str] = None,
        broadcast_ws_url: Optional[str] = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Convert http/https base_url to ws/wss if ws urls not provided
        ws_base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.ingest_ws_url = ingest_ws_url or f"{ws_base}/telemetry/ingest"
        self.broadcast_ws_url = broadcast_ws_url or f"{ws_base}/ws"
        self.timeout_s = timeout_s

    async def check_health(self) -> bool:
        """Check if MachSight backend is reachable and responsive."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except Exception as e:
            logger.debug("Health check failed (%s): %s", type(e).__name__, e)
            return False

    async def fetch_telemetry_history(
        self,
        machine_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[AnomalyDetectionRecord]:
        """Query REST GET /api/telemetry for historical processed telemetry."""
        params: Dict[str, Any] = {"limit": limit}
        if machine_id:
            params["machine_id"] = machine_id

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(f"{self.base_url}/api/telemetry", params=params)
                resp.raise_for_status()
                return ResultParser.parse_rest_telemetry(resp.json())
        except Exception as e:
            logger.error("Failed to fetch telemetry history: %s", e)
            raise MachSightConnectionError(f"Failed to fetch telemetry: {e}") from e

    async def fetch_investigations(
        self,
        machine_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[InvestigationRecord]:
        """Query REST GET /api/investigations for diagnostic investigations."""
        params: Dict[str, Any] = {"limit": limit}
        if machine_id:
            params["machine_id"] = machine_id

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(f"{self.base_url}/api/investigations", params=params)
                resp.raise_for_status()
                return ResultParser.parse_rest_investigations(resp.json())
        except Exception as e:
            logger.error("Failed to fetch investigations: %s", e)
            raise MachSightConnectionError(f"Failed to fetch investigations: {e}") from e

    async def stream_packets(
        self,
        packets: List[Dict[str, Any]],
        machine_id: str,
        run_id: str = "run",
        speed: float = 0.0,
        wait_after_s: float = 2.0,
    ) -> DiagnosticRunResult:
        """Stream a list of canonical telemetry packets to MachSight while capturing results.

        Parameters
        ----------
        packets: List of telemetry dictionaries (flat canonical or MachSight format).
        machine_id: Unique identifier of the vehicle/asset under test.
        run_id: Identifier of the run for tracking.
        speed: Replay speed multiplier (0.0 = as fast as possible).
        wait_after_s: Duration to keep listening for asynchronous diagnoses after streaming finishes.
        """
        run_result = DiagnosticRunResult(run_id=run_id, machine_id=machine_id)
        stop_listener = asyncio.Event()

        # Listener task for /ws broadcast channel
        async def listen_broadcasts():
            try:
                async with websockets.connect(self.broadcast_ws_url) as ws:
                    logger.debug("Connected to broadcast ws: %s", self.broadcast_ws_url)
                    while not stop_listener.is_set():
                        try:
                            msg_text = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            parsed = ResultParser.parse_broadcast_message(msg_text)
                            if parsed is None:
                                continue

                            if isinstance(parsed, AnomalyDetectionRecord):
                                # If machine_id matches or was unset in broadcast
                                if not parsed.machine_id or parsed.machine_id == machine_id:
                                    run_result.anomaly_detections.append(parsed)
                            elif isinstance(parsed, InvestigationRecord):
                                if not parsed.machine_id or parsed.machine_id == machine_id:
                                    run_result.investigations.append(parsed)
                        except asyncio.TimeoutError:
                            continue
                        except websockets.ConnectionClosed:
                            break
            except Exception as e:
                err_msg = f"Broadcast listener error ({type(e).__name__}): {e}"
                logger.warning(err_msg)
                run_result.infrastructure_errors.append(err_msg)

        listener_task = asyncio.create_task(listen_broadcasts())

        # Give listener a moment to establish connection
        await asyncio.sleep(0.05)

        # Ingestion task: stream to /telemetry/ingest
        try:
            async with websockets.connect(self.ingest_ws_url) as ingest_ws:
                logger.debug("Connected to ingest ws: %s", self.ingest_ws_url)
                last_ts: Optional[float] = None

                for p in packets:
                    # Ensure packet has machine_id
                    packet_dict = dict(p)
                    if "machine_id" not in packet_dict:
                        packet_dict["machine_id"] = machine_id

                    # Pacing if requested
                    if speed > 0 and last_ts is not None:
                        current_ts = float(packet_dict.get("timestamp", 0.0))
                        delta_t = current_ts - last_ts
                        if delta_t > 0:
                            await asyncio.sleep(delta_t / speed)
                    last_ts = float(packet_dict.get("timestamp", 0.0))

                    await ingest_ws.send(json.dumps(packet_dict))
        except Exception as e:
            err_msg = f"Ingestion error ({type(e).__name__}): {e}"
            logger.error(err_msg)
            run_result.infrastructure_errors.append(err_msg)

        # Wait post-stream to catch final LLM/rule diagnosis
        if wait_after_s > 0:
            await asyncio.sleep(wait_after_s)

        stop_listener.set()
        try:
            await asyncio.wait_for(listener_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            listener_task.cancel()

        return run_result

    async def stream_telemetry_file(
        self,
        telemetry_path: Path,
        machine_id: str,
        run_id: str = "run",
        speed: float = 0.0,
        wait_after_s: float = 2.0,
    ) -> DiagnosticRunResult:
        """Read and stream a telemetry.jsonl file."""
        if not telemetry_path.exists():
            raise FileNotFoundError(f"Telemetry file not found: {telemetry_path}")

        packets: List[Dict[str, Any]] = []
        with telemetry_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    packets.append(json.loads(line))

        return await self.stream_packets(
            packets=packets,
            machine_id=machine_id,
            run_id=run_id,
            speed=speed,
            wait_after_s=wait_after_s,
        )
