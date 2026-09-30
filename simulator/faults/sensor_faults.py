"""
simulator/faults/sensor_faults.py
===================================
Sensor-level fault implementations.

Implemented faults:
    UltrasonicStuckFault     — sensor frozen at last/configured value
    UltrasonicNoiseFault     — excessive measurement noise
    UltrasonicBiasFault      — constant additive offset
    CurrentBiasFault         — constant additive offset
    CurrentSpikeFault        — elevated spike probability
    RPMStuckFault            — encoder frozen
    RPMDropoutFault          — frequent missing readings
    IntermittentFault        — wraps another fault, toggles on/off
"""

from __future__ import annotations

import random
from typing import Optional

from simulator.faults.base import BaseFault, FaultRecord
from simulator.core.vehicle import VehicleState
from simulator.sensors.ultrasonic import UltrasonicSensor
from simulator.sensors.current import CurrentSensor
from simulator.sensors.rpm import RPMSensor


class UltrasonicStuckFault(BaseFault):
    """Ultrasonic sensor frozen at a fixed reading."""

    fault_type = "ultrasonic_stuck"

    def __init__(self, record: FaultRecord, stuck_value_cm: float = 50.0) -> None:
        super().__init__(record)
        self.stuck_value_cm = stuck_value_cm

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        sev = self.record.current_severity(sim_time)
        if sev > 0.1:
            ultrasonic.apply_stuck(self.stuck_value_cm)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        ultrasonic.apply_stuck(None)


class UltrasonicNoiseFault(BaseFault):
    """Dramatically increased ultrasonic noise."""

    fault_type = "ultrasonic_noise"

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        sev = self.record.current_severity(sim_time)
        # multiplier 1.0 (normal) → 10.0 (extreme noise) at full severity
        ultrasonic.apply_noise(1.0 + sev * 9.0)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        ultrasonic.apply_noise(1.0)


class UltrasonicBiasFault(BaseFault):
    """Constant additive offset on ultrasonic readings."""

    fault_type = "ultrasonic_bias"

    def __init__(self, record: FaultRecord, bias_cm: float = 20.0) -> None:
        super().__init__(record)
        self.bias_cm = bias_cm

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        sev = self.record.current_severity(sim_time)
        ultrasonic.apply_bias(self.bias_cm * sev)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        ultrasonic.apply_bias(0.0)


class CurrentBiasFault(BaseFault):
    """Constant additive offset on current readings."""

    fault_type = "current_bias"

    def __init__(self, record: FaultRecord, bias_a: float = 1.0) -> None:
        super().__init__(record)
        self.bias_a = bias_a

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        sev = self.record.current_severity(sim_time)
        current_sensor.apply_bias(self.bias_a * sev)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        current_sensor.apply_bias(0.0)


class CurrentSpikeFault(BaseFault):
    """Elevated current spike probability."""

    fault_type = "current_spike"

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        sev = self.record.current_severity(sim_time)
        # Extra spike probability: 0 → 0.3 at full severity
        current_sensor.apply_spike(sev * 0.3)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        current_sensor.apply_spike(0.0)


class RPMStuckFault(BaseFault):
    """Wheel encoder frozen at last reading."""

    fault_type = "rpm_stuck"

    def __init__(self, record: FaultRecord) -> None:
        super().__init__(record)
        self._stuck_value: Optional[float] = None

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        sev = self.record.current_severity(sim_time)
        if sev > 0.1:
            if self._stuck_value is None:
                # Capture the current wheel RPM at fault onset
                self._stuck_value = vehicle.wheel_rpm
            rpm_sensor.apply_stuck(self._stuck_value)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        rpm_sensor.apply_stuck(None)
        self._stuck_value = None


class RPMDropoutFault(BaseFault):
    """Frequent RPM sensor dropout — readings become unavailable."""

    fault_type = "rpm_dropout"

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        sev = self.record.current_severity(sim_time)
        # Extra dropout: 0 → 0.95 at full severity
        rpm_sensor.apply_dropout(sev * 0.95)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        rpm_sensor.apply_dropout(0.0)


class IntermittentFault(BaseFault):
    """Wraps another fault and toggles it on/off cyclically.

    Parameters
    ----------
    inner_fault  : the BaseFault to toggle
    on_seconds   : seconds in ON state
    off_seconds  : seconds in OFF state
    """

    fault_type = "intermittent"

    def __init__(
        self,
        record: FaultRecord,
        inner_fault: BaseFault,
        on_seconds: float = 2.0,
        off_seconds: float = 3.0,
    ) -> None:
        super().__init__(record)
        self.inner = inner_fault
        self.on_seconds = on_seconds
        self.off_seconds = off_seconds
        self._cycle_start: Optional[float] = None
        self._is_on: bool = False

    def apply(self, sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor):
        if self._cycle_start is None:
            self._cycle_start = sim_time
            self._is_on = True

        elapsed_in_cycle = (sim_time - self._cycle_start) % (self.on_seconds + self.off_seconds)
        should_be_on = elapsed_in_cycle < self.on_seconds

        if should_be_on and not self._is_on:
            self._is_on = True
        elif not should_be_on and self._is_on:
            self._is_on = False
            self.inner.remove(vehicle, ultrasonic, current_sensor, rpm_sensor)

        if self._is_on:
            self.inner.apply(sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor)

    def remove(self, vehicle, ultrasonic, current_sensor, rpm_sensor):
        self._cycle_start = None
        self._is_on = False
        self.inner.remove(vehicle, ultrasonic, current_sensor, rpm_sensor)
