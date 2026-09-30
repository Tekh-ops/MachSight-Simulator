"""
simulator/evaluation/ground_truth.py
=====================================
Formal Ground-Truth Schema and Event Alignment Utilities.

This module provides typed models and analytical helpers for evaluating
telemetry runs against the hidden physical and fault state of the digital twin.

CRITICAL INVARIANT:
Ground truth is evaluator-only data and MUST NEVER be exposed in telemetry
streamed or forwarded to MachSight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class MachineState:
    """Internal physics state of the machine at a discrete time."""
    sim_time: float
    wall_time: float
    position_m: float
    velocity_mps: float
    wheel_rpm_true: float
    motor_current_true: float
    battery_state: float
    battery_voltage: float
    wheel_slip: float
    mechanical_load: float


@dataclass
class EnvironmentState:
    """State of obstacles and track surroundings."""
    obstacle_distance_m: float


@dataclass
class FaultEvent:
    """Explicit ground-truth fault episode."""
    fault_id: str
    fault_type: str
    component: str
    start_time: float
    end_time: Optional[float]
    severity: float
    severity_end: Optional[float] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    def is_active_at(self, t: float) -> bool:
        if t < self.start_time:
            return False
        if self.end_time is not None and t > self.end_time:
            return False
        return True


@dataclass
class GroundTruthSnapshot:
    """Per-tick ground-truth snapshot recorded during simulation."""
    machine: MachineState
    environment: EnvironmentState
    is_healthy: bool
    active_faults: List[str]
    active_fault_details: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> GroundTruthSnapshot:
        m = MachineState(
            sim_time=float(d.get("sim_time", 0.0)),
            wall_time=float(d.get("wall_time", 0.0)),
            position_m=float(d.get("position_m", 0.0)),
            velocity_mps=float(d.get("velocity_mps", 0.0)),
            wheel_rpm_true=float(d.get("wheel_rpm_true", 0.0)),
            motor_current_true=float(d.get("motor_current_true", 0.0)),
            battery_state=float(d.get("battery_state", 1.0)),
            battery_voltage=float(d.get("battery_voltage", 7.4)),
            wheel_slip=float(d.get("wheel_slip", 0.0)),
            mechanical_load=float(d.get("mechanical_load", 0.0)),
        )
        env = EnvironmentState(obstacle_distance_m=float(d.get("obstacle_distance_m", 999.0)))
        active = [str(x) for x in d.get("active_faults", [])]
        details = list(d.get("active_fault_details", []))
        is_healthy = bool(d.get("is_healthy", len(active) == 0))
        return cls(machine=m, environment=env, is_healthy=is_healthy, active_faults=active, active_fault_details=details)


class GroundTruthTimeline:
    """Analytical timeline of ground-truth events across a simulation run."""

    def __init__(self, snapshots: List[GroundTruthSnapshot], fault_events: List[FaultEvent]) -> None:
        self.snapshots = snapshots
        self.fault_events = fault_events

    @property
    def total_duration_s(self) -> float:
        if not self.snapshots:
            return 0.0
        return self.snapshots[-1].machine.sim_time - self.snapshots[0].machine.sim_time

    @property
    def has_faults(self) -> bool:
        return len(self.fault_events) > 0

    def is_fault_active_at(self, t: float) -> bool:
        """Check if any fault is active at time t."""
        return any(f.is_active_at(t) for f in self.fault_events)

    def get_active_fault_events(self, t: float) -> List[FaultEvent]:
        """Return all faults active at time t."""
        return [f for f in self.fault_events if f.is_active_at(t)]

    def get_primary_fault_type(self, t: float) -> Optional[str]:
        """Return the primary fault type active at time t, or None if healthy."""
        active = self.get_active_fault_events(t)
        return active[0].fault_type if active else None

    def get_state_at_onset(self, fault_type_or_id: str) -> Optional[MachineState]:
        """Find the machine physical state at the exact onset of a fault."""
        target_event = None
        for f in self.fault_events:
            if f.fault_type == fault_type_or_id or f.fault_id == fault_type_or_id:
                target_event = f
                break

        if target_event is None:
            return None

        onset_t = target_event.start_time
        # Find closest snapshot at or immediately following onset_t
        for snap in self.snapshots:
            if snap.machine.sim_time >= onset_t:
                return snap.machine

        return self.snapshots[-1].machine if self.snapshots else None

    @classmethod
    def load_from_run_dir(cls, run_dir: Path) -> GroundTruthTimeline:
        """Parse ground_truth.jsonl and metadata.json from a run directory."""
        gt_file = run_dir / "ground_truth.jsonl"
        meta_file = run_dir / "metadata.json"
        scenario_file = run_dir / "scenario.yaml"

        snapshots: List[GroundTruthSnapshot] = []
        if gt_file.exists():
            with gt_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        snapshots.append(GroundTruthSnapshot.from_dict(json.loads(line)))

        # Extract fault events from metadata or scenario
        fault_events: List[FaultEvent] = []
        import yaml
        if scenario_file.exists():
            with scenario_file.open("r", encoding="utf-8") as f:
                scen = yaml.safe_load(f) or {}
                for idx, f_cfg in enumerate(scen.get("faults", [])):
                    st = float(f_cfg.get("start_s", f_cfg.get("start_time", 0.0)))
                    dur = f_cfg.get("duration_s", f_cfg.get("duration_seconds"))
                    dur = float(dur) if dur is not None else None
                    et = st + dur if dur is not None else None
                    from simulator.faults import DEFAULT_COMPONENT_MAP
                    ft = f_cfg.get("type", "unknown")
                    comp = f_cfg.get("component", DEFAULT_COMPONENT_MAP.get(ft, "general"))
                    fault_events.append(
                        FaultEvent(
                            fault_id=f_cfg.get("fault_id", f"fault-{idx:03d}"),
                            fault_type=ft,
                            component=comp,
                            start_time=st,
                            end_time=et,
                            severity=float(f_cfg.get("severity", 0.5)),
                            severity_end=float(f_cfg["severity_end"]) if "severity_end" in f_cfg else None,
                        )
                    )

        return cls(snapshots=snapshots, fault_events=fault_events)


def find_telemetry_samples_in_window(
    telemetry_packets: List[Dict[str, Any]],
    start_time: float,
    end_time: float,
    time_key: str = "timestamp",
) -> List[Dict[str, Any]]:
    """Filter telemetry records falling within [start_time, end_time]."""
    return [p for p in telemetry_packets if start_time <= float(p.get(time_key, 0.0)) <= end_time]
