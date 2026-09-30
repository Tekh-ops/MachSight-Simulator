"""
simulator/faults/__init__.py
=============================
Factory function to build faults from config dicts.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from simulator.faults.base import BaseFault, FaultRecord, FaultStatus
from simulator.faults.mechanical import DrivetrainJamFault, MotorDragFault, WheelSlipFault
from simulator.faults.sensor_faults import (
    UltrasonicStuckFault,
    UltrasonicNoiseFault,
    UltrasonicBiasFault,
    CurrentBiasFault,
    CurrentSpikeFault,
    RPMStuckFault,
    RPMDropoutFault,
    IntermittentFault,
)

FAULT_REGISTRY: Dict[str, Any] = {
    "drivetrain_jam": DrivetrainJamFault,
    "motor_drag": MotorDragFault,
    "wheel_slip": WheelSlipFault,
    "ultrasonic_stuck": UltrasonicStuckFault,
    "ultrasonic_noise": UltrasonicNoiseFault,
    "ultrasonic_bias": UltrasonicBiasFault,
    "current_bias": CurrentBiasFault,
    "current_spike": CurrentSpikeFault,
    "rpm_stuck": RPMStuckFault,
    "rpm_dropout": RPMDropoutFault,
    "intermittent": IntermittentFault,
}


DEFAULT_COMPONENT_MAP: Dict[str, str] = {
    "drivetrain_jam": "drivetrain",
    "motor_drag": "motor",
    "wheel_slip": "wheel",
    "ultrasonic_stuck": "ultrasonic_sensor",
    "ultrasonic_noise": "ultrasonic_sensor",
    "ultrasonic_bias": "ultrasonic_sensor",
    "current_bias": "current_sensor",
    "current_spike": "current_sensor",
    "rpm_stuck": "rpm_sensor",
    "rpm_dropout": "rpm_sensor",
    "intermittent": "electrical",
}


def _build_fault_from_config(cfg: dict) -> BaseFault:
    """Construct a BaseFault from a config dict."""
    fault_type = cfg["type"]
    cls = FAULT_REGISTRY.get(fault_type)
    if cls is None:
        raise ValueError(f"Unknown fault type: {fault_type!r}")

    dur = None
    if "duration_s" in cfg and cfg["duration_s"] is not None:
        dur = float(cfg["duration_s"])
    elif "duration_seconds" in cfg and cfg["duration_seconds"] is not None:
        dur = float(cfg["duration_seconds"])
    elif "duration" in cfg and cfg["duration"] is not None:
        dur = float(cfg["duration"])

    component = cfg.get("component", DEFAULT_COMPONENT_MAP.get(fault_type, "general"))

    record = FaultRecord(
        fault_id=cfg.get("fault_id", str(uuid.uuid4())[:8]),
        fault_type=fault_type,
        start_s=float(cfg.get("start_s", cfg.get("start_time", 0.0))),
        duration_s=dur,
        severity=float(cfg.get("severity", cfg.get("severity_start", 0.5))),
        severity_end=float(cfg["severity_end"]) if "severity_end" in cfg else None,
        component=component,
        status=FaultStatus.PENDING,
    )

    # Type-specific extra kwargs
    kwargs: dict = {}

    if fault_type == "ultrasonic_stuck":
        kwargs["stuck_value_cm"] = float(cfg.get("stuck_value_cm", 50.0))
    elif fault_type == "ultrasonic_bias":
        kwargs["bias_cm"] = float(cfg.get("bias_cm", 20.0))
    elif fault_type == "current_bias":
        kwargs["bias_a"] = float(cfg.get("bias_a", 1.0))
    elif fault_type == "intermittent":
        inner_cfg = cfg.get("inner_fault", {"type": "current_spike"})
        inner_fault = _build_fault_from_config(inner_cfg)
        kwargs["inner_fault"] = inner_fault
        kwargs["on_seconds"] = float(cfg.get("on_seconds", 2.0))
        kwargs["off_seconds"] = float(cfg.get("off_seconds", 3.0))

    return cls(record=record, **kwargs)


__all__ = [
    "BaseFault",
    "FaultRecord",
    "DrivetrainJamFault",
    "MotorDragFault",
    "WheelSlipFault",
    "UltrasonicStuckFault",
    "UltrasonicNoiseFault",
    "UltrasonicBiasFault",
    "CurrentBiasFault",
    "CurrentSpikeFault",
    "RPMStuckFault",
    "RPMDropoutFault",
    "IntermittentFault",
    "FAULT_REGISTRY",
    "_build_fault_from_config",
]
