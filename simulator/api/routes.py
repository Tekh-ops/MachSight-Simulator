"""
simulator/api/routes.py
========================
FastAPI router for all REST control endpoints.

Endpoints
---------
POST /simulation/start        — start simulation (optionally load scenario)
POST /simulation/stop         — stop simulation
POST /simulation/pause        — pause simulation
POST /simulation/resume       — resume paused simulation
POST /simulation/reset        — reset to initial state

POST /control/throttle        — set PWM / direction / mode

POST /faults/inject           — inject a fault
POST /faults/clear            — clear fault(s)

GET  /simulation/status       — live simulation status (dev endpoint)
GET  /simulation/config       — show active configuration
GET  /simulation/ground-truth — ground truth (eval-only endpoint)

GET  /scenarios               — list available scenarios

WebSocket /ws/telemetry       — subscribe to live telemetry stream
"""

from __future__ import annotations

import asyncio
import uuid
import time
import logging
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, WebSocket, HTTPException, Depends
from fastapi.responses import JSONResponse

from simulator.api.models import (
    StartRequest,
    ThrottleRequest,
    FaultInjectRequest,
    FaultClearRequest,
    ScenarioLoadRequest,
)
from simulator.core.vehicle import VehicleMode
from simulator.scenarios.loader import list_scenarios, load_scenario
from simulator.faults import _build_fault_from_config

if TYPE_CHECKING:
    from simulator.core.simulation import SimulationEngine
    from simulator.telemetry.websocket import LocalBroadcaster, MachSightForwarder
    from simulator.evaluation.recorder import RunRecorder

logger = logging.getLogger(__name__)

router = APIRouter()

# These are injected into the router via dependency at app startup
_engine: Optional["SimulationEngine"] = None
_broadcaster: Optional["LocalBroadcaster"] = None
_forwarder: Optional["MachSightForwarder"] = None
_recorder: Optional["RunRecorder"] = None
_recording_enabled: bool = False


def inject_dependencies(engine, broadcaster, forwarder):
    global _engine, _broadcaster, _forwarder
    _engine = engine
    _broadcaster = broadcaster
    _forwarder = forwarder


def get_engine() -> "SimulationEngine":
    if _engine is None:
        raise RuntimeError("Engine not initialised")
    return _engine


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket):
    """Subscribe to the live telemetry stream.

    Receives every TelemetryMessage emitted by the simulator.
    Does NOT include ground truth or fault labels.
    """
    if _broadcaster is None:
        await websocket.close(code=1011)
        return
    await _broadcaster.subscribe(websocket)


# ── Simulation control ────────────────────────────────────────────────────────

@router.post("/simulation/start")
async def start_simulation(req: StartRequest):
    engine = get_engine()

    if engine.running:
        raise HTTPException(status_code=409, detail="Simulation already running")

    if req.scenario:
        try:
            scenario = load_scenario(req.scenario)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        engine.load_scenario(scenario, seed=req.seed)
    elif req.seed is not None:
        import random
        random.seed(req.seed)

    await engine.start()
    return {"status": "started", "scenario": engine.scenario_name}


@router.post("/simulation/stop")
async def stop_simulation():
    engine = get_engine()
    await engine.stop()
    return {"status": "stopped", "simulation_time_s": engine.sim_time}


@router.post("/simulation/pause")
async def pause_simulation():
    engine = get_engine()
    engine.pause()
    return {"status": "paused"}


@router.post("/simulation/resume")
async def resume_simulation():
    engine = get_engine()
    engine.resume()
    return {"status": "resumed"}


@router.post("/simulation/reset")
async def reset_simulation():
    engine = get_engine()
    if engine.running:
        await engine.stop()
    engine.reset()
    return {"status": "reset"}


# ── Control ───────────────────────────────────────────────────────────────────

@router.post("/control/throttle")
async def set_throttle(req: ThrottleRequest):
    engine = get_engine()
    engine.control.pwm_command = req.pwm

    if req.direction is not None:
        engine.control.direction = -1 if req.direction == "reverse" else 1

    if req.mode is not None:
        try:
            engine.control.mode = VehicleMode(req.mode.lower())
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown mode: {req.mode}")

    return {
        "pwm": engine.control.pwm_command,
        "direction": "reverse" if engine.control.direction == -1 else "forward",
        "mode": engine.control.mode.value,
    }


# ── Faults ────────────────────────────────────────────────────────────────────

@router.post("/faults/inject")
async def inject_fault(req: FaultInjectRequest):
    engine = get_engine()

    cfg = req.model_dump(exclude_none=True)
    # Convert start_s relative to current sim time
    cfg["start_s"] = engine.sim_time + req.start_s
    cfg["fault_id"] = str(uuid.uuid4())[:8]

    try:
        fault = _build_fault_from_config(cfg)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    fault_id = engine.fault_manager.register(fault)
    if req.start_s <= 0.0:
        fault.activate(engine.sim_time)
        fault.apply(
            engine.sim_time,
            engine.vehicle,
            engine.ultrasonic,
            engine.current_sensor,
            engine.rpm_sensor,
        )
    return {"status": "injected", "fault_id": fault_id, "type": req.type}


@router.post("/faults/clear")
async def clear_faults(req: FaultClearRequest):
    engine = get_engine()

    if req.fault_id is not None:
        cleared = engine.fault_manager.clear_fault(
            req.fault_id,
            engine.sim_time,
            engine.vehicle,
            engine.ultrasonic,
            engine.current_sensor,
            engine.rpm_sensor,
        )
        if not cleared:
            raise HTTPException(status_code=404, detail=f"Fault {req.fault_id!r} not found")
        return {"status": "cleared", "fault_id": req.fault_id}
    else:
        # Clear all
        for fid in list(engine.fault_manager._faults.keys()):
            engine.fault_manager.clear_fault(
                fid,
                engine.sim_time,
                engine.vehicle,
                engine.ultrasonic,
                engine.current_sensor,
                engine.rpm_sensor,
            )
        return {"status": "all_cleared"}


# ── Status / inspection ───────────────────────────────────────────────────────

@router.get("/simulation/status")
async def get_status():
    engine = get_engine()
    return engine.get_status()


@router.get("/simulation/config")
async def get_config():
    from simulator.config import settings
    return settings.model_dump()


@router.get("/simulation/ground-truth")
async def get_ground_truth():
    """DEV/EVAL ONLY: returns hidden ground-truth state.

    This endpoint MUST NOT be proxied to MachSight or exposed in production.
    It exists exclusively for evaluation, debugging, and dataset annotation.
    """
    engine = get_engine()
    return engine.get_ground_truth_snapshot()


@router.get("/scenarios")
async def get_scenarios():
    return {"scenarios": list_scenarios()}
