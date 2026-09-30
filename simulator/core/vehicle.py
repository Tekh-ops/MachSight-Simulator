"""
simulator/core/vehicle.py
==========================
Internal vehicle state — the *ground truth* representation of the RC car.

PHYSICS MODEL ASSUMPTIONS
--------------------------
1. Single-axis (1-D) linear motion; turning is architecturally reserved.
2. Wheel RPM → velocity via rolling contact:
       v = (RPM / 60) * 2π * R_wheel
3. Motor torque is approximated from normalized PWM duty cycle:
       τ_available = τ_max * duty_cycle * battery_factor
4. Net torque drives RPM through first-order lag (motor_response_time τ):
       RPM_target  = (duty_cycle * max_rpm) * battery_factor * load_factor
       dRPM/dt     ≈ (RPM_target - RPM) / τ   (clamped to max_accel/decel)
5. Current is modelled from power balance:
       I ≈ I_idle + k_torque * torque_demand + k_load * mechanical_load
6. Battery discharges proportionally to current draw; voltage sag is linear.
7. Wheel slip is detected when vehicle velocity is anomalously low relative
   to expected wheel surface speed.

All constants are injected from SimulatorConfig to keep this file testable
without a running server.
"""

from __future__ import annotations

import dataclasses
import enum
import math
from typing import Optional

from simulator.config import SimulatorConfig, settings as default_settings


class VehicleMode(str, enum.Enum):
    IDLE = "idle"
    FORWARD = "forward"
    REVERSE = "reverse"
    BRAKING = "braking"
    COASTING = "coasting"
    TURNING_LEFT = "turning_left"
    TURNING_RIGHT = "turning_right"
    STOPPED = "stopped"


@dataclasses.dataclass
class VehicleState:
    """Complete internal ground-truth state of the simulated RC vehicle.

    These variables are NEVER transmitted directly to MachSight.
    Sensor models derive their observable outputs from this state.
    """

    # ── Control inputs ────────────────────────────────────────────────────────
    pwm_command: int = 0
    direction: int = 1            # +1 = forward, -1 = reverse
    mode: VehicleMode = VehicleMode.IDLE

    # ── Dynamics ─────────────────────────────────────────────────────────────
    position_m: float = 0.0
    velocity_mps: float = 0.0
    wheel_rpm: float = 0.0        # true internal RPM (before sensor noise)
    target_rpm: float = 0.0

    # ── Motor / drivetrain ────────────────────────────────────────────────────
    motor_torque: float = 0.0     # N·m (dimensionless model units)
    motor_current: float = 0.0    # A (true, before sensor noise)
    throttle_duty: float = 0.0    # 0.0–1.0 normalised from PWM

    # ── Load / resistance ─────────────────────────────────────────────────────
    mechanical_load: float = 0.0  # extra resistance 0.0–1.0 (faults add here)
    rolling_resistance: float = 0.05

    # ── Battery ───────────────────────────────────────────────────────────────
    battery_voltage: float = 7.4  # V
    battery_state: float = 1.0    # 0.0 (dead) – 1.0 (full)

    # ── Environment coupling ──────────────────────────────────────────────────
    obstacle_distance_m: float = 10.0   # m to nearest obstacle

    # ── Wheel slip ────────────────────────────────────────────────────────────
    wheel_slip: float = 0.0       # 0.0 = no slip, 1.0 = full slip


class VehicleDynamics:
    """Stateless physics functions applied each simulation tick.

    Each method takes the current state and dt, returns a *new* VehicleState.
    The simulation engine calls update() once per tick.

    Parameters
    ----------
    cfg : SimulatorConfig
        Injected configuration; defaults to the module-level singleton.
    """

    # ── Current model constants ───────────────────────────────────────────────
    # I = I_idle + k_torque * torque + k_load * load
    I_IDLE = 0.15          # A — no-load idle current
    K_TORQUE = 1.8         # A per unit torque
    K_LOAD = 2.2           # A per unit mechanical load

    # ── Battery discharge model ───────────────────────────────────────────────
    # Capacity converted from mAh to A·s: C_As = capacity_mah * 3.6
    # Discharge per tick: ΔSoC = I * dt / C_As
    BATTERY_VOLTAGE_SLOPE = -1.5  # V per unit SoC drop (linear sag)

    def __init__(self, cfg: Optional[SimulatorConfig] = None) -> None:
        self.cfg = cfg or default_settings

    # ── PWM → throttle ────────────────────────────────────────────────────────

    def pwm_to_duty(self, pwm: int) -> float:
        """Normalise PWM command to [0, 1] duty cycle."""
        clamped = max(self.cfg.pwm_min, min(self.cfg.pwm_max, pwm))
        return (clamped - self.cfg.pwm_min) / self.cfg.pwm_range

    # ── Target RPM ────────────────────────────────────────────────────────────

    def compute_target_rpm(
        self,
        duty: float,
        battery_factor: float,
        load_factor: float,
        mode: VehicleMode,
    ) -> float:
        """Map throttle duty to target RPM.

        Higher load → lower achievable RPM at the same throttle.
        Battery depletion also reduces max RPM.
        """
        if mode in (VehicleMode.IDLE, VehicleMode.STOPPED):
            return 0.0
        if mode == VehicleMode.BRAKING:
            return 0.0

        base_rpm = duty * self.cfg.max_rpm * battery_factor
        # Severe load (e.g. drivetrain jam) severely impedes motor rotation
        return base_rpm * max(0.05, 1.0 - 0.85 * load_factor)

    # ── RPM update (first-order lag) ──────────────────────────────────────────

    def update_rpm(
        self,
        current_rpm: float,
        target_rpm: float,
        dt: float,
        mode: VehicleMode,
        wheel_slip_factor: float = 0.0,
    ) -> float:
        """Apply first-order lag toward target RPM.

        Acceleration and deceleration are independently clamped so the
        vehicle decelerates faster than it accelerates (more realistic).
        """
        if mode == VehicleMode.BRAKING:
            # Active braking — faster deceleration
            decel = self.cfg.max_deceleration * 2.0
            new_rpm = max(0.0, current_rpm - decel * dt)
            return new_rpm

        # First-order lag: dRPM/dt = (target - current) / τ
        error = target_rpm - current_rpm
        rate = error / max(self.cfg.motor_response_time, 1e-6)

        # Clamp rate to physical limits
        if rate > 0:
            rate = min(rate, self.cfg.max_acceleration)
        else:
            rate = max(rate, -self.cfg.max_deceleration)

        new_rpm = current_rpm + rate * dt

        # Wheel slip: motor spins but vehicle doesn't move proportionally
        # The RPM itself stays high; velocity coupling is reduced elsewhere.
        # No RPM clamp change needed for slip.

        return max(0.0, new_rpm)

    # ── Velocity from RPM ─────────────────────────────────────────────────────

    def rpm_to_velocity(
        self,
        rpm: float,
        direction: int,
        wheel_slip: float = 0.0,
    ) -> float:
        """Convert wheel RPM to linear vehicle velocity (m/s).

        When wheel_slip > 0 the wheel spins faster than the vehicle moves.
        v = (1 - slip) * (rpm / 60) * 2π * R
        """
        circumference = 2.0 * math.pi * self.cfg.wheel_radius_m
        no_slip_v = (rpm / 60.0) * circumference
        effective_v = no_slip_v * (1.0 - wheel_slip)
        return direction * effective_v

    # ── Current from torque and load ──────────────────────────────────────────

    def compute_current(
        self,
        duty: float,
        mechanical_load: float,
        battery_factor: float,
    ) -> float:
        """Estimate motor current from operating point.

        Model:
            I = I_idle + k_torque * (duty * battery_factor)
                       + k_load   * mechanical_load
        """
        torque_component = self.K_TORQUE * duty * battery_factor
        load_component = self.K_LOAD * mechanical_load
        return max(0.0, self.I_IDLE + torque_component + load_component)

    # ── Battery ───────────────────────────────────────────────────────────────

    def update_battery(
        self,
        state: VehicleState,
        current_a: float,
        dt: float,
    ) -> tuple[float, float]:
        """Return updated (battery_state, battery_voltage).

        State-of-charge depletes at rate I/C_As.
        Voltage sags linearly from nominal.
        """
        capacity_as = self.cfg.battery_capacity_mah * 3.6
        delta_soc = (current_a * dt) / capacity_as
        new_soc = max(0.0, state.battery_state - delta_soc)
        new_voltage = (
            self.cfg.battery_nominal_v
            + self.BATTERY_VOLTAGE_SLOPE * (1.0 - new_soc)
        )
        return new_soc, max(0.0, new_voltage)

    # ── Master update ─────────────────────────────────────────────────────────

    def update(
        self,
        state: VehicleState,
        dt: float,
        extra_load: float = 0.0,
        wheel_slip_override: Optional[float] = None,
    ) -> VehicleState:
        """Apply one simulation tick.

        Parameters
        ----------
        state        : current VehicleState
        dt           : timestep seconds
        extra_load   : additional mechanical resistance from fault engine
        wheel_slip_override : if not None, forces wheel_slip to this value
        """
        # --- Throttle duty cycle -----------------------------------------
        duty = self.pwm_to_duty(state.pwm_command)
        state.throttle_duty = duty

        # --- Battery factor (0.5 at fully depleted) ----------------------
        battery_factor = 0.5 + 0.5 * state.battery_state

        # --- Total mechanical load (rolling resistance + faults) ---------
        total_load = min(1.0, state.rolling_resistance + extra_load)
        state.mechanical_load = total_load

        # --- Target RPM --------------------------------------------------
        load_factor = total_load  # [0, 1]
        target_rpm = self.compute_target_rpm(
            duty, battery_factor, load_factor, state.mode
        )
        state.target_rpm = target_rpm

        # --- Wheel slip --------------------------------------------------
        if wheel_slip_override is not None:
            state.wheel_slip = wheel_slip_override

        # --- Update wheel RPM (first-order lag) --------------------------
        state.wheel_rpm = self.update_rpm(
            state.wheel_rpm,
            target_rpm,
            dt,
            state.mode,
            state.wheel_slip,
        )

        # --- Motor torque (proportional to current demand) ---------------
        state.motor_torque = duty * battery_factor * (1.0 - 0.3 * total_load)

        # --- Motor current -----------------------------------------------
        state.motor_current = self.compute_current(duty, total_load, battery_factor)

        # --- Battery update ----------------------------------------------
        state.battery_state, state.battery_voltage = self.update_battery(
            state, state.motor_current, dt
        )

        # --- Velocity & position -----------------------------------------
        state.velocity_mps = self.rpm_to_velocity(
            state.wheel_rpm, state.direction, state.wheel_slip
        )
        state.position_m += state.velocity_mps * dt

        return state
