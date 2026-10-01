"""
simulator/scenarios/driver.py
==============================
Driver profile engine for realistic throttle and operational regime control.

Supported Profiles:
    - idle: Vehicle stays in IDLE mode with PWM=0.
    - slow_cruise: Steady low-speed cruise (PWM ~90).
    - normal_cruise: Steady medium-speed cruise (PWM ~160).
    - high_load: High throttle operation (PWM ~230) challenging motor/battery limits.
    - stop_and_go: Dynamic urban profile alternating between acceleration, cruising,
      and active braking / stopping.
    - custom: User-defined stepped timeline of [time_s, pwm, mode].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from simulator.core.vehicle import VehicleMode

logger = logging.getLogger(__name__)


class DriverProfileType(str, Enum):
    IDLE = "idle"
    SLOW_CRUISE = "slow_cruise"
    NORMAL_CRUISE = "normal_cruise"
    HIGH_LOAD = "high_load"
    STOP_AND_GO = "stop_and_go"
    FULL_DEMO = "full_demo"
    DIAGNOSTIC_DEMO = "diagnostic_demo"
    CUSTOM = "custom"


@dataclass
class DriverCommand:
    """Throttle and mode command issued by the driver at a specific tick."""
    pwm: int
    mode: VehicleMode
    direction: int = 1  # +1 forward, -1 reverse


class DriverProfile:
    """Translates profile configuration into dynamic per-tick commands."""

    def __init__(self, profile_type: str = "normal_cruise", config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        try:
            self.profile_type = DriverProfileType(profile_type.lower())
        except ValueError:
            logger.warning("Unknown driver profile '%s', falling back to normal_cruise", profile_type)
            self.profile_type = DriverProfileType.NORMAL_CRUISE

        # Custom timeline if provided: list of (time_s, pwm, mode_str)
        self._timeline: List[tuple[float, int, VehicleMode]] = []
        if self.profile_type == DriverProfileType.CUSTOM:
            for step in self.config.get("timeline", []):
                t = float(step.get("time_s", 0.0))
                pwm = int(step.get("pwm", 0))
                mode = VehicleMode(step.get("mode", "forward").lower())
                self._timeline.append((t, pwm, mode))
            self._timeline.sort(key=lambda x: x[0])

    def get_command(self, sim_time: float) -> DriverCommand:
        """Compute the driver command for the given simulation time."""
        if self.profile_type == DriverProfileType.IDLE:
            return DriverCommand(pwm=0, mode=VehicleMode.IDLE)

        elif self.profile_type == DriverProfileType.SLOW_CRUISE:
            pwm = int(self.config.get("pwm", 90))
            return DriverCommand(pwm=pwm, mode=VehicleMode.FORWARD)

        elif self.profile_type == DriverProfileType.NORMAL_CRUISE:
            pwm = int(self.config.get("pwm", 160))
            return DriverCommand(pwm=pwm, mode=VehicleMode.FORWARD)

        elif self.profile_type == DriverProfileType.HIGH_LOAD:
            pwm = int(self.config.get("pwm", 230))
            return DriverCommand(pwm=pwm, mode=VehicleMode.FORWARD)

        elif self.profile_type in (DriverProfileType.FULL_DEMO, DriverProfileType.DIAGNOSTIC_DEMO):
            # Polished industrial demo timeline:
            # 0–8s:   idle / initialization (PWM 0, mode IDLE)
            # 8–18s:  acceleration (PWM ramps to cruise, mode FORWARD)
            # 18s+:   sustained command through cruise, jam, recovery (PWM target, mode FORWARD)
            target_pwm = int(self.config.get("pwm", 160))
            if sim_time < 8.0:
                return DriverCommand(pwm=0, mode=VehicleMode.IDLE)
            elif sim_time < 14.0:
                ramp_pct = (sim_time - 8.0) / 6.0
                cmd_pwm = int(target_pwm * ramp_pct)
                return DriverCommand(pwm=max(40, cmd_pwm), mode=VehicleMode.FORWARD)
            else:
                return DriverCommand(pwm=target_pwm, mode=VehicleMode.FORWARD)

        elif self.profile_type == DriverProfileType.STOP_AND_GO:
            # 16-second repeating cycle:
            # 0–6s:   forward cruise (PWM 150)
            # 6–9s:   braking (PWM 0, mode BRAKING)
            # 9–11s:  stopped/idle (PWM 0, mode STOPPED)
            # 11–16s: gentle acceleration (PWM 120)
            cycle_time = sim_time % 16.0
            if cycle_time < 6.0:
                return DriverCommand(pwm=150, mode=VehicleMode.FORWARD)
            elif cycle_time < 9.0:
                return DriverCommand(pwm=0, mode=VehicleMode.BRAKING)
            elif cycle_time < 11.0:
                return DriverCommand(pwm=0, mode=VehicleMode.STOPPED)
            else:
                return DriverCommand(pwm=120, mode=VehicleMode.FORWARD)

        elif self.profile_type == DriverProfileType.CUSTOM:
            if not self._timeline:
                return DriverCommand(pwm=100, mode=VehicleMode.FORWARD)
            current_pwm = self._timeline[0][1]
            current_mode = self._timeline[0][2]
            for t, pwm, mode in self._timeline:
                if sim_time >= t:
                    current_pwm = pwm
                    current_mode = mode
                else:
                    break
            return DriverCommand(pwm=current_pwm, mode=current_mode)

        return DriverCommand(pwm=150, mode=VehicleMode.FORWARD)
