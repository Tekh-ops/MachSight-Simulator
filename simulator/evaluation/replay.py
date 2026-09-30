"""
simulator/evaluation/replay.py
===============================
Telemetry Replay Source.

Replays recorded `telemetry.jsonl` files through the exact same TelemetrySource
abstraction boundary without running physics simulation.

CRITICAL INVARIANT:
The replay system strictly streams recorded observable telemetry. It NEVER
injects ground-truth state, hidden variables, or fault annotations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Union

from simulator.telemetry.schema import (
    MachineInfo,
    OperatingState,
    SensorReadings,
    SensorStatus,
    SourceInfo,
    TelemetryMessage,
)
from simulator.telemetry.source import (
    ConnectionState,
    SourceDisconnectedError,
    TelemetrySource,
)

logger = logging.getLogger(__name__)


class TelemetryReplaySource(TelemetrySource):
    """TelemetrySource backed by a recorded telemetry.jsonl file."""

    def __init__(
        self,
        telemetry_file_or_dir: Optional[Union[str, Path]] = None,
        realtime: bool = False,
        speed_multiplier: float = 1.0,
        telemetry_file: Optional[Union[str, Path]] = None,
        loop: bool = False,
    ) -> None:
        super().__init__()
        target = telemetry_file if telemetry_file is not None else telemetry_file_or_dir
        if target is None:
            raise ValueError("telemetry_file or telemetry_file_or_dir must be provided")

        p = Path(target)
        if p.is_dir():
            self.telemetry_path = p / "telemetry.jsonl"
        else:
            self.telemetry_path = p

        if not self.telemetry_path.exists():
            raise FileNotFoundError(f"Telemetry replay file not found: {self.telemetry_path}")

        self.realtime = realtime
        self.speed_multiplier = max(0.001, speed_multiplier)
        self.loop = loop

        self._messages: List[TelemetryMessage] = []
        self._current_idx: int = 0
        self._load_records()

    def _load_records(self) -> None:
        """Parse JSONL lines into canonical TelemetryMessage instances."""
        self._messages.clear()
        with self.telemetry_path.open("r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)

                # Ensure flat or nested format is cleanly parsed
                if "sensors" in d and "machine" in d:
                    msg = TelemetryMessage.model_validate(d)
                else:
                    # Convert flat MachSight-compatible dict back to TelemetryMessage
                    msg = TelemetryMessage(
                        schema_version=int(d.get("schema_version", 2)),
                        timestamp=float(d.get("timestamp", time.time())),
                        sequence=int(d.get("sequence", line_idx)),
                        machine=MachineInfo(
                            machine_id=str(d.get("machine_id", "replayed_car")),
                            asset_type=str(d.get("asset_type", "rc_vehicle")),
                        ),
                        operating_state=OperatingState(
                            mode=str(d.get("mode", d.get("state", "idle"))),
                            pwm_command=int(d.get("pwm_command", 0)),
                        ),
                        sensors=SensorReadings(
                            distance_cm=d.get("distance_cm", d.get("ultrasonic_distance_cm")),
                            current_a=d.get("current_a", d.get("motor_current_a")),
                            wheel_rpm=d.get("rpm", d.get("wheel_rpm")),
                        ),
                        sensor_status=SensorStatus(
                            distance="ok" if d.get("distance_cm") is not None else "dropped",
                            current="ok" if d.get("current_a") is not None else "dropped",
                            wheel_rpm="ok" if d.get("rpm") is not None else "dropped",
                        ),
                        source=SourceInfo(
                            source_type="replay",
                            device_id=str(d.get("machine_id", "replayed_car")),
                            transport="replay",
                        ),
                    )
                self._messages.append(msg)

        logger.info("Loaded %d telemetry messages for replay from %s", len(self._messages), self.telemetry_path)

    @property
    def total_messages(self) -> int:
        return len(self._messages)

    # ── TelemetrySource Interface ─────────────────────────────────────────────

    async def connect(self) -> None:
        self._state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        self._streaming = False
        self._state = ConnectionState.DISCONNECTED

    async def start(self) -> None:
        if not self.is_connected:
            await self.connect()
        self._streaming = True

    async def stop(self) -> None:
        self._streaming = False

    async def get_next(self, timeout: Optional[float] = None) -> Optional[TelemetryMessage]:
        if not self._streaming or self._current_idx >= len(self._messages):
            return None

        msg = self._messages[self._current_idx]
        self._current_idx += 1

        if self.realtime and self._current_idx < len(self._messages):
            next_msg = self._messages[self._current_idx]
            dt = (next_msg.timestamp - msg.timestamp) / self.speed_multiplier
            if 0 < dt < 10.0:  # Sanity clamp delay
                await asyncio.sleep(dt)

        if self._current_idx >= len(self._messages):
            if self.loop:
                self._current_idx = 0
            else:
                self._streaming = False

        return msg

    async def telemetry(self) -> AsyncIterator[TelemetryMessage]:
        if not self._streaming:
            await self.start()
        while self._streaming:
            msg = await self.get_next(timeout=1.0)
            if msg is not None:
                yield msg
            else:
                break

    async def telemetry_stream(self) -> AsyncIterator[TelemetryMessage]:
        """Convenience alias for telemetry() generator."""
        async for m in self.telemetry():
            yield m

    def reset(self) -> None:
        """Rewind replay stream to beginning."""
        self._current_idx = 0
