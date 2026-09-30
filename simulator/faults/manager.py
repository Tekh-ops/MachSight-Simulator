"""
simulator/faults/manager.py
============================
FaultManager — orchestrates all active and scheduled faults.

Responsibilities:
    - Register faults with a start time
    - Activate pending faults when sim_time reaches their start_s
    - Apply active faults each tick
    - Remove expired faults and call their remove() cleanup
    - Expose active fault list for /simulation/status (dev endpoint)
    - Expose ground-truth fault list for evaluation logging
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional, TYPE_CHECKING

from simulator.faults.base import BaseFault, FaultRecord, FaultStatus

if TYPE_CHECKING:
    from simulator.core.vehicle import VehicleState
    from simulator.sensors.ultrasonic import UltrasonicSensor
    from simulator.sensors.current import CurrentSensor
    from simulator.sensors.rpm import RPMSensor

logger = logging.getLogger(__name__)


class FaultManager:
    """Manages the complete fault lifecycle for one simulation run."""

    def __init__(self) -> None:
        self._faults: Dict[str, BaseFault] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, fault: BaseFault) -> str:
        """Register a fault.  Returns the fault_id."""
        fid = fault.record.fault_id
        self._faults[fid] = fault
        logger.debug("Fault registered: %s (%s)", fid, fault.fault_type)
        return fid

    def register_from_config(self, fault_config: dict) -> str:
        """Build and register a fault from a config dict.

        Supported keys:
            type, start_s, duration_s, severity, severity_end,
            stuck_value_cm (ultrasonic_stuck),
            bias_cm (ultrasonic_bias),
            bias_a (current_bias),
            on_seconds / off_seconds (intermittent),
            inner_fault (intermittent — nested fault config dict)
        """
        from simulator.faults import _build_fault_from_config

        fault = _build_fault_from_config(fault_config)
        return self.register(fault)

    def clear_all(self) -> None:
        """Remove all registered faults (used on scenario reset)."""
        self._faults.clear()

    def clear_fault(
        self,
        fault_id: str,
        sim_time: float,
        vehicle: "VehicleState",
        ultrasonic: "UltrasonicSensor",
        current_sensor: "CurrentSensor",
        rpm_sensor: "RPMSensor",
    ) -> bool:
        """Manually clear a specific fault by ID.  Returns True if found."""
        fault = self._faults.get(fault_id)
        if fault is None:
            return False
        if fault.record.status == FaultStatus.ACTIVE:
            fault.remove(vehicle, ultrasonic, current_sensor, rpm_sensor)
        fault.clear(sim_time)
        return True

    # ── Tick update ───────────────────────────────────────────────────────────

    def tick(
        self,
        sim_time: float,
        vehicle: "VehicleState",
        ultrasonic: "UltrasonicSensor",
        current_sensor: "CurrentSensor",
        rpm_sensor: "RPMSensor",
    ) -> None:
        """Called once per simulation tick.

        1. Activates pending faults whose start_s has arrived.
        2. Applies all active faults.
        3. Expires active faults whose duration has elapsed.
        """
        to_expire: List[str] = []

        for fid, fault in self._faults.items():
            record = fault.record

            # ── Activate pending faults ──────────────────────────────────────
            if record.status == FaultStatus.PENDING:
                if sim_time >= record.start_s:
                    fault.activate(sim_time)
                    logger.info(
                        "Fault ACTIVATED: %s (%s, severity=%.2f)",
                        fid,
                        fault.fault_type,
                        record.severity,
                    )

            # ── Apply active faults ──────────────────────────────────────────
            if record.status == FaultStatus.ACTIVE:
                fault.apply(sim_time, vehicle, ultrasonic, current_sensor, rpm_sensor)

                # ── Check expiry ─────────────────────────────────────────────
                if record.duration_s is not None and record.activated_at is not None:
                    if sim_time - record.activated_at >= record.duration_s:
                        to_expire.append(fid)

        for fid in to_expire:
            fault = self._faults[fid]
            fault.remove(vehicle, ultrasonic, current_sensor, rpm_sensor)
            fault.complete()
            logger.info("Fault COMPLETED: %s (%s)", fid, fault.fault_type)

    # ── Status / ground truth ─────────────────────────────────────────────────

    @property
    def active_faults(self) -> List[dict]:
        """Summary list for /simulation/status (development endpoint)."""
        return [
            {
                "fault_id": f.record.fault_id,
                "type": f.fault_type,
                "component": f.record.component,
                "severity": f.record.severity,
                "status": f.record.status.value,
                "start_s": f.record.start_s,
                "end_s": f.record.end_s,
            }
            for f in self._faults.values()
            if f.record.status == FaultStatus.ACTIVE
        ]

    @property
    def all_faults_ground_truth(self) -> List[dict]:
        """Full ground-truth fault list for evaluation logging."""
        return [
            {
                "fault_id": f.record.fault_id,
                "type": f.fault_type,
                "fault_type": f.fault_type,
                "component": f.record.component,
                "start_s": f.record.start_s,
                "start_time": f.record.start_s,
                "duration_s": f.record.duration_s,
                "duration_seconds": f.record.duration_s,
                "end_s": f.record.end_s,
                "end_time": f.record.end_s,
                "severity": f.record.severity,
                "severity_end": f.record.severity_end,
                "status": f.record.status.value,
                "activated_at": f.record.activated_at,
                "cleared_at": f.record.cleared_at,
            }
            for f in self._faults.values()
        ]
