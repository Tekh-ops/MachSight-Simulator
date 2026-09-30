"""
simulator/api/models.py
========================
Pydantic request/response models for the REST control API.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class StartRequest(BaseModel):
    scenario: Optional[str] = Field(None, description="Scenario name to load")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    record: bool = Field(False, description="Record this run to disk")


class ThrottleRequest(BaseModel):
    pwm: int = Field(..., ge=0, le=255, description="PWM command value 0–255")
    direction: Optional[str] = Field(
        None,
        description="'forward' or 'reverse'",
        pattern="^(forward|reverse)$",
    )
    mode: Optional[str] = Field(
        None,
        description="Vehicle operating mode",
    )


class FaultInjectRequest(BaseModel):
    type: str = Field(..., description="Fault type identifier")
    start_s: float = Field(0.0, description="Seconds from now (0 = immediate)")
    duration_s: Optional[float] = Field(None, description="Fault duration in seconds; None = permanent")
    severity: float = Field(0.5, ge=0.0, le=1.0, description="Fault severity 0–1")
    severity_end: Optional[float] = Field(None, ge=0.0, le=1.0, description="Ending severity for ramping")
    # Type-specific parameters
    stuck_value_cm: Optional[float] = Field(None, description="For ultrasonic_stuck: fixed reading (cm)")
    bias_cm: Optional[float] = Field(None, description="For ultrasonic_bias: offset (cm)")
    bias_a: Optional[float] = Field(None, description="For current_bias: offset (A)")
    on_seconds: Optional[float] = Field(2.0, description="For intermittent: ON duration")
    off_seconds: Optional[float] = Field(3.0, description="For intermittent: OFF duration")
    inner_fault: Optional[dict] = Field(None, description="For intermittent: inner fault config")


class FaultClearRequest(BaseModel):
    fault_id: Optional[str] = Field(None, description="Clear specific fault by ID (None = clear all)")


class ScenarioLoadRequest(BaseModel):
    name: str = Field(..., description="Scenario name")
    seed: Optional[int] = Field(None, description="Random seed override")


class StatusResponse(BaseModel):
    running: bool
    paused: bool
    scenario: Optional[str]
    simulation_time_s: float
    machine_id: str
    mode: str
    pwm_command: int
    active_faults: List[dict]
    sequence: int
