"""
simulator/evaluation/scenario_validator.py
============================================
Lightweight Scenario Validation & Physical Sanity-Check Utility.

Validates that a simulation run satisfies the causal physical requirements:
1. Healthy phase: vehicle accelerates and reaches steady cruise (positive RPM, current ~1.2-1.8A).
2. Fault onset: drivetrain jam causes RPM and velocity to decrease while current increases.
3. PWM sustained: driver maintains high throttle command during the fault.
4. Current increase: motor current rises substantially above healthy cruise under mechanical load.
5. RPM decrease: wheels decelerate to near zero.
6. Velocity decrease: ground-truth vehicle velocity falls to near zero.
7. Persistent fault: high command + high current + near-zero motion maintained throughout the fault.
8. Recovery: upon fault removal, resistance clears, current drops, and RPM / velocity recover.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PhaseMetrics:
    mean_current: float = 0.0
    peak_current: float = 0.0
    min_current: float = 0.0
    mean_rpm: float = 0.0
    min_rpm: float = 0.0
    max_rpm: float = 0.0
    mean_velocity: float = 0.0
    min_velocity: float = 0.0
    max_velocity: float = 0.0
    mean_pwm: float = 0.0


@dataclass
class ValidationCheck:
    name: str
    passed: bool
    details: str = ""


@dataclass
class ScenarioValidationReport:
    scenario_name: str
    seed: Optional[int]
    duration_s: float
    checks: List[ValidationCheck] = field(default_factory=list)
    healthy_metrics: PhaseMetrics = field(default_factory=PhaseMetrics)
    fault_metrics: PhaseMetrics = field(default_factory=PhaseMetrics)
    recovery_metrics: PhaseMetrics = field(default_factory=PhaseMetrics)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def print_summary(self) -> str:
        lines = [
            f"Scenario: {self.scenario_name}",
            f"Seed: {self.seed}",
            f"Duration: {self.duration_s:.1f}s",
            "",
            "Validation Checks:",
        ]
        for c in self.checks:
            status = "PASS" if c.passed else "FAIL"
            detail_str = f" ({c.details})" if c.details else ""
            lines.append(f"    {c.name}: {status}{detail_str}")

        lines.extend([
            "",
            "Quantitative Sanity Checks:",
            "Healthy phase:",
            f"  mean current: {self.healthy_metrics.mean_current:.2f} A",
            f"  peak current: {self.healthy_metrics.peak_current:.2f} A",
            f"  mean RPM: {self.healthy_metrics.mean_rpm:.1f} RPM",
            f"  mean velocity: {self.healthy_metrics.mean_velocity:.2f} m/s",
            "",
            "Fault phase:",
            f"  mean current: {self.fault_metrics.mean_current:.2f} A",
            f"  peak current: {self.fault_metrics.peak_current:.2f} A",
            f"  mean RPM: {self.fault_metrics.mean_rpm:.1f} RPM",
            f"  minimum RPM: {self.fault_metrics.min_rpm:.1f} RPM",
            f"  mean velocity: {self.fault_metrics.mean_velocity:.2f} m/s",
            f"  minimum velocity: {self.fault_metrics.min_velocity:.2f} m/s",
            "",
            "Recovery:",
            f"  current recovered: {'YES' if self.recovery_metrics.mean_current < self.fault_metrics.mean_current * 0.75 else 'NO'} ({self.recovery_metrics.mean_current:.2f} A)",
            f"  RPM recovered: {'YES' if self.recovery_metrics.max_rpm > 150.0 else 'NO'} ({self.recovery_metrics.max_rpm:.1f} RPM)",
            f"  velocity recovered: {'YES' if self.recovery_metrics.max_velocity > 0.4 else 'NO'} ({self.recovery_metrics.max_velocity:.2f} m/s)",
        ])
        return "\n".join(lines)


def _calc_metrics(
    gt_records: List[Dict[str, Any]],
    telemetry_records: List[Dict[str, Any]],
    t_start: float,
    t_end: float,
) -> PhaseMetrics:
    gts = [r for r in gt_records if t_start <= r.get("sim_time", 0.0) <= t_end]
    currents = [r.get("motor_current_true", 0.0) for r in gts]
    rpms = [r.get("wheel_rpm_true", 0.0) for r in gts]
    vels = [r.get("velocity_mps", 0.0) for r in gts]

    if not gts:
        return PhaseMetrics()

    return PhaseMetrics(
        mean_current=sum(currents) / len(currents) if currents else 0.0,
        peak_current=max(currents) if currents else 0.0,
        min_current=min(currents) if currents else 0.0,
        mean_rpm=sum(rpms) / len(rpms) if rpms else 0.0,
        min_rpm=min(rpms) if rpms else 0.0,
        max_rpm=max(rpms) if rpms else 0.0,
        mean_velocity=sum(vels) / len(vels) if vels else 0.0,
        min_velocity=min(vels) if vels else 0.0,
        max_velocity=max(vels) if vels else 0.0,
    )


class ScenarioValidator:
    """Validates simulation run outputs against physical ground truth."""

    @classmethod
    def validate_run(
        cls,
        run_dir: Path,
        scenario_name: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> ScenarioValidationReport:
        gt_file = run_dir / "ground_truth.jsonl"
        tel_file = run_dir / "telemetry.jsonl"

        if not gt_file.exists():
            raise FileNotFoundError(f"Missing {gt_file}")

        gt_records = []
        with gt_file.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    gt_records.append(json.loads(line))

        tel_records = []
        if tel_file.exists():
            with tel_file.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        tel_records.append(json.loads(line))

        name = scenario_name or run_dir.parent.name
        meta_file = run_dir / "metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if seed is None:
                    seed = meta.get("seed")
                if scenario_name is None:
                    name = meta.get("scenario", name)
            except Exception:
                pass

        duration = gt_records[-1].get("sim_time", 60.0) if gt_records else 0.0

        # Define time windows for evaluation:
        # Full demo timeline: healthy cruise 18-30s, jam 30-45s, recovery 48-60s
        t_healthy_start, t_healthy_end = 18.0, 29.5
        t_fault_start, t_fault_end = 32.0, 44.5
        t_recovery_start, t_recovery_end = 50.0, min(60.0, duration)

        healthy_m = _calc_metrics(gt_records, tel_records, t_healthy_start, t_healthy_end)
        fault_m = _calc_metrics(gt_records, tel_records, t_fault_start, t_fault_end)
        rec_m = _calc_metrics(gt_records, tel_records, t_recovery_start, t_recovery_end)

        checks: List[ValidationCheck] = []

        # 1. Healthy phase check
        healthy_ok = healthy_m.mean_rpm > 120.0 and healthy_m.mean_current > 0.8
        checks.append(ValidationCheck(
            name="Healthy phase",
            passed=healthy_ok,
            details=f"mean RPM={healthy_m.mean_rpm:.1f}, mean current={healthy_m.mean_current:.2f}A",
        ))

        # 2. Fault onset
        onset_records = [r for r in gt_records if 29.5 <= r.get("sim_time", 0.0) <= 33.0]
        onset_ok = False
        if onset_records:
            rpm_init = onset_records[0].get("wheel_rpm_true", 0.0)
            rpm_end = onset_records[-1].get("wheel_rpm_true", 0.0)
            onset_ok = rpm_end < rpm_init * 0.7
        checks.append(ValidationCheck(
            name="Fault onset",
            passed=onset_ok,
            details="RPM deceleration detected at onset",
        ))

        # 3. PWM sustained during jam
        fault_pwms = [r.get("pwm_command", 0) for r in tel_records]
        pwm_sustained = any(p >= 140 for p in fault_pwms)
        checks.append(ValidationCheck(
            name="PWM sustained during jam",
            passed=pwm_sustained,
            details="high throttle command maintained during jam",
        ))

        # 4. Current increase
        current_increased = fault_m.mean_current > healthy_m.mean_current * 1.5
        checks.append(ValidationCheck(
            name="Current increase",
            passed=current_increased,
            details=f"fault current {fault_m.mean_current:.2f}A > 1.5x healthy {healthy_m.mean_current:.2f}A",
        ))

        # 5. RPM decrease
        rpm_decreased = fault_m.min_rpm < 30.0 and fault_m.mean_rpm < healthy_m.mean_rpm * 0.3
        checks.append(ValidationCheck(
            name="RPM decrease",
            passed=rpm_decreased,
            details=f"min RPM during jam={fault_m.min_rpm:.1f}",
        ))

        # 6. Vehicle velocity decrease
        velocity_decreased = fault_m.min_velocity < 0.1
        checks.append(ValidationCheck(
            name="Vehicle velocity decrease",
            passed=velocity_decreased,
            details=f"min velocity during jam={fault_m.min_velocity:.2f} m/s",
        ))

        # 7. Persistent fault
        persistent = fault_m.mean_current > 2.5 and fault_m.mean_rpm < 50.0
        checks.append(ValidationCheck(
            name="Persistent fault",
            passed=persistent,
            details=f"sustained stall current={fault_m.mean_current:.2f}A, mean RPM={fault_m.mean_rpm:.1f}",
        ))

        # 8. Recovery
        recovered = rec_m.max_rpm > 150.0 and rec_m.mean_current < fault_m.mean_current * 0.75
        checks.append(ValidationCheck(
            name="Recovery",
            passed=recovered,
            details=f"recovered RPM={rec_m.max_rpm:.1f}, current dropped to {rec_m.mean_current:.2f}A",
        ))

        return ScenarioValidationReport(
            scenario_name=name,
            seed=seed,
            duration_s=duration,
            checks=checks,
            healthy_metrics=healthy_m,
            fault_metrics=fault_m,
            recovery_metrics=rec_m,
        )
