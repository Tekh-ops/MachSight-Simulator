"""
simulator/core/simulation.py
=============================
SimulationEngine — the main real-time simulation loop.

Architecture:
    asyncio task runs the physics loop at cfg.sim_frequency_hz.
    Sensor readings are collected at their individual sampling rates.
    Completed TelemetryMessage objects are pushed to an asyncio.Queue
    that the WebSocket layer drains.

Simulation time is tracked independently of wall-clock time.
A sequence counter monotonically increases with each emitted telemetry packet.

Ground truth is maintained and never placed in TelemetryMessage objects.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from simulator.config import SimulatorConfig, settings as default_settings
from simulator.core.vehicle import VehicleState, VehicleDynamics, VehicleMode
from simulator.core.environment import Environment
from simulator.sensors.ultrasonic import UltrasonicSensor
from simulator.sensors.current import CurrentSensor
from simulator.sensors.rpm import RPMSensor
from simulator.faults.manager import FaultManager
from simulator.telemetry.schema import (
    TelemetryMessage,
    MachineInfo,
    OperatingState,
    SensorReadings,
    SensorStatus,
    SourceInfo,
)

logger = logging.getLogger(__name__)


class ControlState:
    """Mutable control input — modified by the REST API."""

    def __init__(self) -> None:
        self.pwm_command: int = 0
        self.direction: int = 1            # +1 forward, -1 reverse
        self.mode: VehicleMode = VehicleMode.IDLE


class SimulationEngine:
    """Core simulation engine.

    Usage::

        engine = SimulationEngine()
        engine.load_scenario(scenario_dict)
        await engine.start()
        ...
        await engine.stop()

    The engine publishes ready TelemetryMessage objects to ``self.telemetry_queue``.
    """

    def __init__(
        self,
        cfg: Optional[SimulatorConfig] = None,
        telemetry_queue: Optional["asyncio.Queue[TelemetryMessage]"] = None,
    ) -> None:
        self.cfg = cfg or default_settings
        self._telemetry_queue = telemetry_queue
        self._queue_loop = None

        # ── Sub-systems ───────────────────────────────────────────────────────
        self.vehicle = VehicleState()
        self.dynamics = VehicleDynamics(self.cfg)
        self.environment = Environment()
        self.fault_manager = FaultManager()

        # ── Sensors ───────────────────────────────────────────────────────────
        self.ultrasonic = UltrasonicSensor(
            sample_rate_hz=self.cfg.ultrasonic_sensor_hz,
            noise_sigma=self.cfg.ultrasonic_noise_sigma,
            min_cm=self.cfg.ultrasonic_min_cm,
            max_cm=self.cfg.ultrasonic_max_cm,
        )
        self.current_sensor = CurrentSensor(
            sample_rate_hz=self.cfg.current_sensor_hz,
            noise_sigma=self.cfg.current_noise_sigma,
        )
        self.rpm_sensor = RPMSensor(
            sample_rate_hz=self.cfg.rpm_sensor_hz,
            noise_sigma=self.cfg.rpm_noise_sigma,
        )

        # ── Control ───────────────────────────────────────────────────────────
        self.control = ControlState()

        # ── State ─────────────────────────────────────────────────────────────
        self.sim_time: float = 0.0      # internal simulation clock (seconds)
        self.sequence: int = 0
        self.running: bool = False
        self.paused: bool = False
        self.scenario_name: Optional[str] = None
        self.scenario_duration_s: Optional[float] = None

        # ── Ground truth buffer (ring buffer, max 10k entries) ────────────────
        self._ground_truth: List[dict] = []
        self._gt_max = 10_000

        # ── Task handle ───────────────────────────────────────────────────────
        self._task: Optional[asyncio.Task] = None

    @property
    def telemetry_queue(self) -> "asyncio.Queue[TelemetryMessage]":
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._telemetry_queue is None or (current_loop is not None and self._queue_loop != current_loop):
            self._telemetry_queue = asyncio.Queue(maxsize=1000)
            self._queue_loop = current_loop
        return self._telemetry_queue

    # ── Scenario loading ──────────────────────────────────────────────────────

    def load_scenario(self, scenario: dict, seed: Optional[int] = None) -> None:
        """Apply scenario configuration to the engine.

        Does NOT start the simulation; call start() separately.
        """
        self.reset()

        # Seed for reproducibility
        effective_seed = seed if seed is not None else scenario.get("seed", scenario.get("random_seed"))
        if effective_seed is not None:
            random.seed(effective_seed)
            logger.info("RNG seeded with: %d", effective_seed)

        self.scenario_name = scenario.get("name", "unnamed")
        self.scenario_duration_s = scenario.get("duration_seconds", scenario.get("duration_s"))

        # Driver profile
        from simulator.scenarios.driver import DriverProfile
        driver_cfg = scenario.get("driver")
        if driver_cfg:
            profile_name = driver_cfg.get("profile", "normal_cruise")
            self.driver_profile = DriverProfile(profile_name, driver_cfg)
        else:
            self.driver_profile = None

        # Vehicle initial state / legacy vehicle config
        initial_state = scenario.get("initial_state", {})
        if "position_m" in initial_state:
            self.vehicle.position_m = float(initial_state["position_m"])
        if "velocity_mps" in initial_state:
            self.vehicle.velocity_mps = float(initial_state["velocity_mps"])
        if "battery_soc" in initial_state:
            self.vehicle.battery_state = float(initial_state["battery_soc"])

        vehicle_cfg = scenario.get("vehicle", {})
        if self.driver_profile is None:
            self.control.pwm_command = int(vehicle_cfg.get("initial_pwm", 0))
            direction_str = vehicle_cfg.get("direction", "forward").lower()
            self.control.direction = -1 if direction_str == "reverse" else 1
            mode_str = vehicle_cfg.get("mode", "idle").lower()
            self.control.mode = VehicleMode(mode_str)
        else:
            cmd = self.driver_profile.get_command(0.0)
            self.control.pwm_command = cmd.pwm
            self.control.mode = cmd.mode
            self.control.direction = cmd.direction

        # Environment
        env_cfg = scenario.get("environment", {})
        obstacles = env_cfg.get("obstacles", [])
        self.environment = Environment.from_config(obstacles)

        # Faults (support start_time / start_s, duration_seconds / duration_s)
        for fault_cfg in scenario.get("faults", []):
            cfg = dict(fault_cfg)
            if "start_time" in cfg and "start_s" not in cfg:
                cfg["start_s"] = cfg["start_time"]
            if "duration_seconds" in cfg and "duration_s" not in cfg:
                cfg["duration_s"] = cfg["duration_seconds"]
            self.fault_manager.register_from_config(cfg)

        # Sensor config overrides
        sensor_cfg = scenario.get("sensors", {})
        if "ultrasonic" in sensor_cfg:
            u_cfg = sensor_cfg["ultrasonic"]
            if "noise_std_cm" in u_cfg:
                self.ultrasonic.noise_sigma = float(u_cfg["noise_std_cm"])
            elif "noise_sigma" in u_cfg:
                self.ultrasonic.noise_sigma = float(u_cfg["noise_sigma"])
        elif "ultrasonic_noise_sigma" in sensor_cfg:
            self.ultrasonic.noise_sigma = float(sensor_cfg["ultrasonic_noise_sigma"])

        if "current" in sensor_cfg:
            c_cfg = sensor_cfg["current"]
            if "noise_std_a" in c_cfg:
                self.current_sensor.noise_sigma = float(c_cfg["noise_std_a"])
            elif "noise_sigma" in c_cfg:
                self.current_sensor.noise_sigma = float(c_cfg["noise_sigma"])

        if "rpm" in sensor_cfg:
            r_cfg = sensor_cfg["rpm"]
            if "noise_std_rpm" in r_cfg:
                self.rpm_sensor.noise_sigma = float(r_cfg["noise_std_rpm"])
            elif "noise_sigma" in r_cfg:
                self.rpm_sensor.noise_sigma = float(r_cfg["noise_sigma"])

        logger.info("Scenario loaded: %s", self.scenario_name)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.paused = False
        self._task = asyncio.create_task(self._run_loop(), name="sim-loop")
        logger.info("Simulation started (scenario=%s)", self.scenario_name)

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Simulation stopped at t=%.2f s", self.sim_time)

    def pause(self) -> None:
        self.paused = True
        logger.info("Simulation paused at t=%.2f s", self.sim_time)

    def resume(self) -> None:
        self.paused = False
        logger.info("Simulation resumed at t=%.2f s", self.sim_time)

    def reset(self) -> None:
        """Reset to clean initial state."""
        self.vehicle = VehicleState()
        self.environment = Environment()
        self.fault_manager.clear_all()
        self.ultrasonic.clear_faults()
        self.current_sensor.clear_faults()
        self.rpm_sensor.clear_faults()
        self.ultrasonic.reset()
        self.current_sensor.reset()
        self.rpm_sensor.reset()
        self.control = ControlState()
        self.driver_profile = None
        self.sim_time = 0.0
        self.sequence = 0
        self._ground_truth.clear()
        self.scenario_name = None
        self.scenario_duration_s = None

    # ── Main simulation loop ──────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Real-time physics loop running at sim_frequency_hz."""
        dt = self.cfg.sim_dt
        wall_start = time.perf_counter()
        sim_target = 0.0          # next scheduled sim tick (wall time)

        while self.running:
            # ── Pace the loop to wall clock ──────────────────────────────────
            now = time.perf_counter() - wall_start
            if now < sim_target:
                await asyncio.sleep(sim_target - now)
            sim_target += dt

            if self.paused:
                # Advance wall target but not sim_time
                continue

            # ── Duration check ───────────────────────────────────────────────
            if (
                self.scenario_duration_s is not None
                and self.sim_time >= self.scenario_duration_s
            ):
                logger.info(
                    "Scenario '%s' completed at t=%.2f s",
                    self.scenario_name,
                    self.sim_time,
                )
                self.running = False
                break

            self.step(dt)

    def step(self, dt: Optional[float] = None) -> None:
        """Execute a single simulation step.

        Useful for both the live loop and synchronous testing/scenario replay.
        """
        step_dt = dt if dt is not None else self.cfg.sim_dt

        # ── 0. Update driver profile if active ───────────────────────────
        if getattr(self, "driver_profile", None) is not None:
            cmd = self.driver_profile.get_command(self.sim_time)
            self.control.pwm_command = cmd.pwm
            self.control.mode = cmd.mode
            self.control.direction = cmd.direction

        # ── 1. Sync control state into vehicle ───────────────────────────
        self.vehicle.pwm_command = self.control.pwm_command
        self.vehicle.direction = self.control.direction
        self.vehicle.mode = self.control.mode

        # ── 2. Apply fault manager (modifies vehicle + sensor flags) ──────
        #    First clear per-tick mechanical load injected by faults
        self.vehicle.mechanical_load = 0.0
        self.fault_manager.tick(
            self.sim_time,
            self.vehicle,
            self.ultrasonic,
            self.current_sensor,
            self.rpm_sensor,
        )

        # ── 3. Update vehicle dynamics ───────────────────────────────────
        extra_load = self.vehicle.mechanical_load
        wheel_slip = self.vehicle.wheel_slip if self.vehicle.wheel_slip > 0 else None
        self.vehicle = self.dynamics.update(
            self.vehicle,
            step_dt,
            extra_load=extra_load,
            wheel_slip_override=wheel_slip,
        )

        # ── 4. Update environment coupling ───────────────────────────────
        dist_m = self.environment.nearest_forward_obstacle_distance(
            self.vehicle.position_m
        )
        self.vehicle.obstacle_distance_m = dist_m if dist_m is not None else 999.0

        # ── 5. Sample sensors ────────────────────────────────────────────
        us_reading = self.ultrasonic.sample(dist_m, self.sim_time)
        cur_reading = self.current_sensor.sample(self.vehicle.motor_current, self.sim_time)
        rpm_reading = self.rpm_sensor.sample(self.vehicle.wheel_rpm, self.sim_time)

        # ── 6. Emit telemetry if any sensor fired ─────────────────────────
        if any(r is not None for r in [us_reading, cur_reading, rpm_reading]):
            msg = self._build_telemetry(us_reading, cur_reading, rpm_reading)
            try:
                self.telemetry_queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # Drop oldest in favour of freshness (consumer too slow)

        # ── 7. Ground truth log ───────────────────────────────────────────
        self._record_ground_truth()

        # ── 8. Advance simulation clock ───────────────────────────────────
        self.sim_time += step_dt

    # ── Telemetry builder ─────────────────────────────────────────────────────

    def _build_telemetry(self, us, cur, rpm) -> TelemetryMessage:
        """Construct a TelemetryMessage from the latest sensor samples.

        None values are propagated cleanly (MachSight defaults them to 0).
        IMPORTANT: no fault information is included.
        """
        self.sequence += 1
        return TelemetryMessage(
            timestamp=time.time(),
            sequence=self.sequence,
            machine=MachineInfo(
                machine_id=self.cfg.machine_id,
                asset_type=self.cfg.asset_type,
            ),
            operating_state=OperatingState(
                mode=self.vehicle.mode.value,
                pwm_command=self.vehicle.pwm_command,
            ),
            sensors=SensorReadings(
                distance_cm=us.value if us is not None else None,
                current_a=cur.value if cur is not None else None,
                wheel_rpm=rpm.value if rpm is not None else None,
            ),
            sensor_status=SensorStatus(
                distance=us.quality.value if us is not None else "ok",
                current=cur.quality.value if cur is not None else "ok",
                wheel_rpm=rpm.quality.value if rpm is not None else "ok",
            ),
            source=SourceInfo(
                source_type="simulator",
                transport="simulation",
                device_id=self.cfg.machine_id,
            ),
        )

    # ── Ground truth ──────────────────────────────────────────────────────────

    def _record_ground_truth(self) -> None:
        """Append a ground-truth record (never exposed via telemetry)."""
        active_fault_list = self.fault_manager.active_faults
        gt = {
            "sim_time": round(self.sim_time, 4),
            "wall_time": time.time(),
            "position_m": round(self.vehicle.position_m, 4),
            "velocity_mps": round(self.vehicle.velocity_mps, 4),
            "wheel_rpm_true": round(self.vehicle.wheel_rpm, 2),
            "motor_current_true": round(self.vehicle.motor_current, 3),
            "obstacle_distance_m": round(self.vehicle.obstacle_distance_m, 3),
            "battery_state": round(self.vehicle.battery_state, 4),
            "battery_voltage": round(self.vehicle.battery_voltage, 3),
            "wheel_slip": round(self.vehicle.wheel_slip, 4),
            "mechanical_load": round(self.vehicle.mechanical_load, 4),
            "is_healthy": len(active_fault_list) == 0,
            "active_faults": [f["type"] for f in active_fault_list],
            "active_fault_details": active_fault_list,
        }
        self._ground_truth.append(gt)
        if len(self._ground_truth) > self._gt_max:
            self._ground_truth = self._ground_truth[-self._gt_max :]

    def get_ground_truth_snapshot(self) -> dict:
        """Return latest ground truth (for /simulation/ground-truth endpoint)."""
        latest = self._ground_truth[-1] if self._ground_truth else {}
        return {
            "sim_time": self.sim_time,
            "latest_state": latest,
            "active_faults": self.fault_manager.all_faults_ground_truth,
        }

    def get_status(self) -> dict:
        """Return simulation status for /simulation/status."""
        return {
            "running": self.running,
            "paused": self.paused,
            "scenario": self.scenario_name,
            "simulation_time_s": round(self.sim_time, 3),
            "machine_id": self.cfg.machine_id,
            "mode": self.vehicle.mode.value,
            "pwm_command": self.vehicle.pwm_command,
            "position_m": round(self.vehicle.position_m, 3),
            "velocity_mps": round(self.vehicle.velocity_mps, 3),
            "wheel_rpm": round(self.vehicle.wheel_rpm, 2),
            "motor_current": round(self.vehicle.motor_current, 3),
            "battery_state_pct": round(self.vehicle.battery_state * 100, 1),
            "active_faults": self.fault_manager.active_faults,
            "sequence": self.sequence,
        }
