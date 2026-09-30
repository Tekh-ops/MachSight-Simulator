"""
simulator/config.py
====================
Centralised configuration using pydantic-settings.

All physical constants, server settings and sensor parameters live here.
Hard-coded magic numbers elsewhere in the codebase are explicitly forbidden;
import from this module instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorConfig(BaseSettings):
    """Complete simulator configuration.

    Values are read in priority order:
      1. Environment variables (prefixed with ``SIM_``)
      2. .env file in the project root
      3. Defaults listed below
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",          # No global prefix; individual fields use full names
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────────────────────
    sim_host: str = Field("0.0.0.0", alias="SIM_HOST")
    sim_port: int = Field(8765, alias="SIM_PORT")

    # ── Machine identity ──────────────────────────────────────────────────────
    machine_id: str = Field("rc-sim-01", alias="MACHINE_ID")
    asset_type: str = Field("rc_vehicle", alias="ASSET_TYPE")

    # ── Simulation engine ─────────────────────────────────────────────────────
    sim_frequency_hz: float = Field(100.0, alias="SIM_FREQUENCY_HZ")
    current_sensor_hz: float = Field(50.0, alias="CURRENT_SENSOR_HZ")
    rpm_sensor_hz: float = Field(20.0, alias="RPM_SENSOR_HZ")
    ultrasonic_sensor_hz: float = Field(10.0, alias="ULTRASONIC_SENSOR_HZ")

    # ── Vehicle physics ───────────────────────────────────────────────────────
    max_rpm: float = Field(400.0, alias="MAX_RPM")
    max_acceleration: float = Field(120.0, alias="MAX_ACCELERATION")    # RPM/s
    max_deceleration: float = Field(200.0, alias="MAX_DECELERATION")    # RPM/s
    rolling_resistance: float = Field(0.05, alias="ROLLING_RESISTANCE") # dimensionless
    motor_response_time: float = Field(0.25, alias="MOTOR_RESPONSE_TIME")  # seconds
    vehicle_mass_kg: float = Field(1.2, alias="VEHICLE_MASS_KG")
    wheel_radius_m: float = Field(0.033, alias="WHEEL_RADIUS_M")        # 33 mm
    pwm_min: int = Field(0, alias="PWM_MIN")
    pwm_max: int = Field(255, alias="PWM_MAX")

    # ── Battery ───────────────────────────────────────────────────────────────
    battery_nominal_v: float = Field(7.4, alias="BATTERY_NOMINAL_V")
    battery_capacity_mah: float = Field(2000.0, alias="BATTERY_CAPACITY_MAH")

    # ── Sensor noise ──────────────────────────────────────────────────────────
    ultrasonic_noise_sigma: float = Field(1.2, alias="ULTRASONIC_NOISE_SIGMA")
    current_noise_sigma: float = Field(0.05, alias="CURRENT_NOISE_SIGMA")
    rpm_noise_sigma: float = Field(3.0, alias="RPM_NOISE_SIGMA")

    # ── Ultrasonic limits ─────────────────────────────────────────────────────
    ultrasonic_min_cm: float = Field(2.0, alias="ULTRASONIC_MIN_CM")
    ultrasonic_max_cm: float = Field(400.0, alias="ULTRASONIC_MAX_CM")

    # ── Network imperfections ─────────────────────────────────────────────────
    network_packet_loss: float = Field(0.0, alias="NETWORK_PACKET_LOSS")
    network_latency_ms: float = Field(0.0, alias="NETWORK_LATENCY_MS")
    network_jitter_ms: float = Field(0.0, alias="NETWORK_JITTER_MS")

    # ── MachSight upstream target ─────────────────────────────────────────────
    machsight_ws_url: str = Field(
        "ws://localhost:8000/telemetry/ingest",
        alias="MACHSIGHT_WS_URL",
    )

    # ── Logging / recording ───────────────────────────────────────────────────
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    record_runs: bool = Field(False, alias="RECORD_RUNS")
    runs_dir: Path = Field(Path("runs"), alias="RUNS_DIR")

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def sim_dt(self) -> float:
        """Simulation timestep in seconds."""
        return 1.0 / self.sim_frequency_hz

    @property
    def pwm_range(self) -> int:
        """Total PWM range."""
        return self.pwm_max - self.pwm_min


# Module-level singleton — import this everywhere
settings = SimulatorConfig()
