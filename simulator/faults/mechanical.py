"""
simulator/faults/mechanical.py
================================
Mechanical fault implementations.

Implemented faults:
    DrivetrainJamFault   — sudden mechanical resistance spike
    MotorDragFault       — gradual resistance increase (ramping)
    WheelSlipFault       — wheel spins faster than vehicle moves
"""

from __future__ import annotations

from simulator.faults.base import BaseFault, FaultRecord
from simulator.core.vehicle import VehicleState
from simulator.sensors.ultrasonic import UltrasonicSensor
from simulator.sensors.current import CurrentSensor
from simulator.sensors.rpm import RPMSensor


class DrivetrainJamFault(BaseFault):
    """Sudden drivetrain jam.

    Severity → extra mechanical_load added to VehicleState.
    At severity 1.0: load increases to ~0.95, RPM drops to near zero,
    current surges to model max.
    """

    fault_type = "drivetrain_jam"

    def apply(
        self,
        sim_time: float,
        vehicle: VehicleState,
        ultrasonic: UltrasonicSensor,
        current_sensor: CurrentSensor,
        rpm_sensor: RPMSensor,
    ) -> None:
        sev = self.record.current_severity(sim_time)
        # Jam adds substantial load — peaks at 0.9 at full severity
        vehicle.mechanical_load = min(1.0, vehicle.mechanical_load + sev * 0.9)

    def remove(
        self,
        vehicle: VehicleState,
        ultrasonic: UltrasonicSensor,
        current_sensor: CurrentSensor,
        rpm_sensor: RPMSensor,
    ) -> None:
        # Mechanical load is recalculated each tick; nothing to reset explicitly.
        pass


class MotorDragFault(BaseFault):
    """Gradual increase in mechanical drag (bearing wear, contamination).

    Uses severity_start / severity_end for smooth ramping over the
    fault duration.  At full severity the load increase is 0.6.
    """

    fault_type = "motor_drag"

    def apply(
        self,
        sim_time: float,
        vehicle: VehicleState,
        ultrasonic: UltrasonicSensor,
        current_sensor: CurrentSensor,
        rpm_sensor: RPMSensor,
    ) -> None:
        sev = self.record.current_severity(sim_time)
        # Drag adds moderate resistance — max 0.6 at full severity
        vehicle.mechanical_load = min(1.0, vehicle.mechanical_load + sev * 0.6)

    def remove(
        self,
        vehicle: VehicleState,
        ultrasonic: UltrasonicSensor,
        current_sensor: CurrentSensor,
        rpm_sensor: RPMSensor,
    ) -> None:
        pass


class WheelSlipFault(BaseFault):
    """Wheel slip: motor spins but traction is lost.

    The RPM stays high while vehicle velocity/position increases slowly.
    Creates a cross-sensor inconsistency (high RPM, slow distance decrease).
    """

    fault_type = "wheel_slip"

    def apply(
        self,
        sim_time: float,
        vehicle: VehicleState,
        ultrasonic: UltrasonicSensor,
        current_sensor: CurrentSensor,
        rpm_sensor: RPMSensor,
    ) -> None:
        sev = self.record.current_severity(sim_time)
        # wheel_slip from 0 to 0.95 at max severity
        vehicle.wheel_slip = min(0.95, sev * 0.95)

    def remove(
        self,
        vehicle: VehicleState,
        ultrasonic: UltrasonicSensor,
        current_sensor: CurrentSensor,
        rpm_sensor: RPMSensor,
    ) -> None:
        vehicle.wheel_slip = 0.0
