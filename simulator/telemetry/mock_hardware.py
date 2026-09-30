"""
simulator/telemetry/mock_hardware.py
=====================================
Mock Hardware Telemetry Source.

Simulates an embedded hardware adapter (e.g. ESP32 microcontroller bridge)
receiving raw physical sensor readings from hardware components, converting
raw units to canonical SI/engineering units, and emitting canonical TelemetryMessage
instances.

CRITICAL Architectural Rule
---------------------------
This class contains ZERO vehicle dynamics, physics engines, or diagnostic logic.
It validates that the TelemetrySource abstraction and canonical contract work
identically with a hardware-shaped acquisition pipeline.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional, Union

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


class MockHardwareTelemetrySource(TelemetrySource):
    """TelemetrySource simulating an embedded hardware adapter.

    Takes raw packets (e.g. millimeters, milliamperes, raw sensor names)
    and converts them into canonical TelemetryMessage objects.
    """

    def __init__(
        self,
        device_id: str = "esp32-rc-01",
        machine_id: str = "rc-hardware-01",
        transport: str = "serial",
        firmware_version: str = "v1.2.0",
    ) -> None:
        super().__init__()
        self.device_id = device_id
        self.machine_id = machine_id
        self.transport = transport
        self.firmware_version = firmware_version
        self.session_id = str(uuid.uuid4())

        self._queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue()
        self._sequence: int = 0

    # ── TelemetrySource Lifecycle ─────────────────────────────────────────────

    async def connect(self) -> None:
        """Simulate establishing serial / BLE connection with embedded device."""
        self._state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        """Simulate releasing hardware communication port."""
        self._streaming = False
        self._state = ConnectionState.DISCONNECTED

    async def start(self) -> None:
        """Enable telemetry stream."""
        if not self.is_connected:
            await self.connect()
        self._streaming = True

    async def stop(self) -> None:
        """Halt telemetry stream."""
        self._streaming = False

    async def get_next(self, timeout: Optional[float] = None) -> Optional[TelemetryMessage]:
        if not self._streaming:
            return None
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None

    # ── Raw Packet Normalisation Boundary ─────────────────────────────────────

    def normalize_raw_packet(self, raw: Dict[str, Any]) -> TelemetryMessage:
        """Translate raw hardware sensor readings into canonical TelemetryMessage.

        Hardware Translation Map:
            - distance_mm / hc_sr04_mm / raw_dist_mm   → distance_cm (mm / 10.0)
            - distance_cm                              → distance_cm
            - motor_current_ma / ina219_ma / raw_ma    → current_a   (mA / 1000.0)
            - motor_current_a / current_a              → current_a
            - wheel_speed_rpm / encoder_rpm / raw_rpm  → wheel_rpm
            - wheel_rpm                                → wheel_rpm
            - throttle_pwm / raw_pwm                   → pwm_command
            - drive_mode / raw_mode                    → mode
        """
        self._sequence += 1
        ts = raw.get("timestamp", time.time())

        # 1. Ultrasonic Distance Normalisation (mm → cm)
        # 1. Ultrasonic Distance Normalisation (mm → cm)
        dist_cm: Optional[float] = None
        if raw.get("distance_mm") is not None:
            dist_cm = round(float(raw["distance_mm"]) / 10.0, 2)
        elif raw.get("hc_sr04_mm") is not None:
            dist_cm = round(float(raw["hc_sr04_mm"]) / 10.0, 2)
        elif raw.get("distance_cm") is not None:
            dist_cm = float(raw["distance_cm"])

        # 2. Current Sensor Normalisation (mA → A)
        cur_a: Optional[float] = None
        if raw.get("motor_current_ma") is not None:
            cur_a = round(float(raw["motor_current_ma"]) / 1000.0, 3)
        elif raw.get("ina219_ma") is not None:
            cur_a = round(float(raw["ina219_ma"]) / 1000.0, 3)
        elif raw.get("current_a") is not None:
            cur_a = float(raw["current_a"])
        elif raw.get("motor_current_a") is not None:
            cur_a = float(raw["motor_current_a"])

        # 3. Wheel Speed RPM Normalisation
        rpm: Optional[float] = None
        if raw.get("wheel_speed_rpm") is not None:
            rpm = float(raw["wheel_speed_rpm"])
        elif raw.get("encoder_rpm") is not None:
            rpm = float(raw["encoder_rpm"])
        elif raw.get("wheel_rpm") is not None:
            rpm = float(raw["wheel_rpm"])
        elif raw.get("rpm") is not None:
            rpm = float(raw["rpm"])

        # 4. Operating State
        pwm = raw.get("throttle_pwm", raw.get("pwm_command", raw.get("pwm", 0)))
        mode = raw.get("drive_mode", raw.get("mode", "idle"))

        # 5. Sensor Status
        status_raw = raw.get("sensor_status", {})
        sensor_status = SensorStatus(
            distance=status_raw.get("distance", "ok" if dist_cm is not None else "dropped"),
            current=status_raw.get("current", "ok" if cur_a is not None else "dropped"),
            wheel_rpm=status_raw.get("wheel_rpm", "ok" if rpm is not None else "dropped"),
        )

        # 6. Canonical Assembly
        return TelemetryMessage(
            timestamp=ts,
            sequence=raw.get("sequence", self._sequence),
            machine=MachineInfo(
                machine_id=raw.get("machine_id", self.machine_id),
                asset_type=raw.get("asset_type", "rc_vehicle"),
            ),
            operating_state=OperatingState(
                mode=str(mode).lower(),
                pwm_command=int(pwm),
            ),
            sensors=SensorReadings(
                distance_cm=dist_cm,
                current_a=cur_a,
                wheel_rpm=rpm,
            ),
            sensor_status=sensor_status,
            source=SourceInfo(
                source_type="hardware",
                device_id=raw.get("device_id", self.device_id),
                transport=self.transport,
                firmware_version=self.firmware_version,
                session_id=self.session_id,
            ),
        )

    def feed_raw_packet(self, raw: Dict[str, Any]) -> TelemetryMessage:
        """Feed a raw packet from physical/simulated hardware into the source queue."""
        canonical = self.normalize_raw_packet(raw)
        self._queue.put_nowait(canonical)
        return canonical

    def feed_raw_packets(self, packets: List[Dict[str, Any]]) -> List[TelemetryMessage]:
        """Batch feed raw hardware packets."""
        return [self.feed_raw_packet(p) for p in packets]
