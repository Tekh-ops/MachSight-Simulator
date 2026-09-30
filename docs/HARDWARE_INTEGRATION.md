# Hardware Integration Guide: ESP32 / Bluetooth Transition

**Document Version**: `2.0` (Phase 3 Architectural Baseline)  
**Status**: Architecture Seam Standard  

---

## 1. Architectural Seam: The `TelemetrySource` Contract

The central design requirement of MachSight is:

> **"MachSight must consume machine telemetry, not simulator telemetry or hardware telemetry."**

The software simulator and the future physical RC car are two interchangeable implementations of the abstract `TelemetrySource` interface:

```text
CURRENT ARCHITECTURE (Software Digital Twin):

+--------------------------+
| Simulation Engine        |
| (Vehicle + Sensors)      |
+------------+-------------+
             |
             v
+--------------------------+
| SimulatorTelemetrySource |  implements TelemetrySource
+------------+-------------+
             |
             v [Canonical TelemetryMessage]
+--------------------------+
| MachSightForwarder       |  Transport Layer (WebSocket)
+------------+-------------+
             |
             v
+--------------------------+
| MachSight Backend        |  (/telemetry/ingest endpoint)
+--------------------------+


FUTURE ARCHITECTURE (Physical RC Machine):

+--------------------------+
| ESP32 Microcontroller    |
| (Sensors: HC-SR04,       |
|  INA219, Optical Encoder)|
+------------+-------------+
             |
             v [BLE GATT Notifications / UART Bytes]
+--------------------------+
| BluetoothTelemetrySource |  implements TelemetrySource (Hardware Adapter)
+------------+-------------+
             |
             v [SAME Canonical TelemetryMessage]
+--------------------------+
| MachSightForwarder       |  Transport Layer (WebSocket)
+------------+-------------+
             |
             v
+--------------------------+
| MachSight Backend        |  (/telemetry/ingest endpoint - UNCHANGED)
+--------------------------+
```

---

## 2. TelemetrySource Abstract Interface

Defined in [simulator/telemetry/source.py](file:///Users/drunkenstein/dev/async_hack/MachSight-Simulator/simulator/telemetry/source.py):

```python
class TelemetrySource(ABC):
    @property
    def connection_state(self) -> ConnectionState: ...

    @property
    def is_connected(self) -> bool: ...

    @property
    def is_streaming(self) -> bool: ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish physical/virtual connection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down connection and release hardware handles."""

    @abstractmethod
    async def start(self) -> None:
        """Start acquisition and telemetry streaming."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop acquisition."""

    @abstractmethod
    async def get_next(self, timeout: Optional[float] = None) -> Optional[TelemetryMessage]:
        """Fetch next canonical telemetry message."""

    async def telemetry(self) -> AsyncIterator[TelemetryMessage]:
        """Async generator yielding TelemetryMessage objects."""
```

The interface contains **no** physics engine references, no vehicle state, and no fault injection logic.

---

## 3. The Physical Hardware Adapter Boundary

The physical hardware adapter owns the entire burden of translating device-specific registers and units into canonical units:

```text
Raw ESP32 Packet (e.g. 824 mm, 2730 mA, 156 ticks)
                   │
                   ▼
       Hardware Adapter Layer
       - Unit conversion: mm → cm (82.4 cm)
       - Unit conversion: mA → A  (2.73 A)
       - Pulse conversion: ticks → RPM (312.6 RPM)
       - Canonical field naming
       - Contract validation
                   │
                   ▼
       Canonical TelemetryMessage
```

---

## 4. Working Proof: `MockHardwareTelemetrySource`

To prove this hardware abstraction without waiting for physical silicon, the codebase provides [simulator/telemetry/mock_hardware.py](file:///Users/drunkenstein/dev/async_hack/MachSight-Simulator/simulator/telemetry/mock_hardware.py).

`MockHardwareTelemetrySource` implements `TelemetrySource` and converts raw hardware packets:

```python
raw_hardware_packet = {
    "distance_mm": 824,         # raw ultrasonic reading in mm
    "motor_current_ma": 2730,   # raw current reading in mA
    "wheel_speed_rpm": 312.6,   # raw tachometer reading
    "throttle_pwm": 175,
    "drive_mode": "forward"
}

# The adapter converts it into canonical TelemetryMessage:
# - sensors.distance_cm = 82.4
# - sensors.current_a = 2.73
# - sensors.wheel_rpm = 312.6
# - source.source_type = "hardware"
```

---

## 5. Future `BluetoothTelemetrySource` Implementation Guide

When the ESP32 physical car is available:

1. Install `bleak` in the execution environment:
   ```bash
   uv add bleak
   ```
2. Implement `BluetoothTelemetrySource`:

```python
from bleak import BleakClient
from simulator.telemetry.source import TelemetrySource, ConnectionState
from simulator.telemetry.schema import (
    TelemetryMessage, MachineInfo, OperatingState,
    SensorReadings, SensorStatus, SourceInfo
)

class BluetoothTelemetrySource(TelemetrySource):
    def __init__(self, ble_address: str, notify_char_uuid: str):
        super().__init__()
        self._address = ble_address
        self._char_uuid = notify_char_uuid
        self._client: Optional[BleakClient] = None
        self._queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue()
        self._seq = 0

    async def connect(self) -> None:
        self._state = ConnectionState.CONNECTING
        self._client = BleakClient(self._address)
        await self._client.connect()
        self._state = ConnectionState.CONNECTED

    async def start(self) -> None:
        if not self.is_connected:
            await self.connect()
        await self._client.start_notify(self._char_uuid, self._on_gatt_notification)
        self._streaming = True

    def _on_gatt_notification(self, sender, data: bytearray):
        # 1. Unpack binary struct or JSON from ESP32
        # (e.g. 24-byte C struct: seq, timestamp_ms, dist_mm, current_ma, rpm, pwm)
        dist_mm, cur_ma, rpm, pwm = struct.unpack("<fffi", data[8:24])

        # 2. Convert to canonical engineering units
        self._seq += 1
        msg = TelemetryMessage(
            sequence=self._seq,
            machine=MachineInfo(machine_id="rc-car-01", asset_type="rc_vehicle"),
            operating_state=OperatingState(mode="forward", pwm_command=pwm),
            sensors=SensorReadings(
                distance_cm=dist_mm / 10.0,
                current_a=cur_ma / 1000.0,
                wheel_rpm=rpm,
            ),
            sensor_status=SensorStatus(distance="ok", current="ok", wheel_rpm="ok"),
            source=SourceInfo(
                source_type="hardware",
                device_id="esp32-rc-01",
                transport="bluetooth",
            ),
        )
        self._queue.put_nowait(msg)

    async def get_next(self, timeout: Optional[float] = None) -> Optional[TelemetryMessage]:
        if not self._streaming:
            return None
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    async def stop(self) -> None:
        if self._client and self._streaming:
            await self._client.stop_notify(self._char_uuid)
        self._streaming = False

    async def disconnect(self) -> None:
        await self.stop()
        if self._client:
            await self._client.disconnect()
        self._state = ConnectionState.DISCONNECTED
```

---

## 6. Zero-Downtime Migration Checklist

1. **Verify Unit Conversion**: Ensure distance is in $cm$ (not $mm$ or inches), current is in $A$ (not $mA$), and speed is in $RPM$.
2. **Schema Conformance**: Run `TelemetryValidator.validate_message(msg)` on received packets.
3. **No Downstream Changes**: Point the existing `MachSightForwarder` to `BluetoothTelemetrySource` instead of `SimulatorTelemetrySource`. Zero changes to backend feature extractors or anomaly detection models are needed.
