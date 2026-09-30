"""
simulator/main.py
==================
FastAPI application entry point.

Lifecycle:
    startup  → SimulationEngine created, background forwarding task started
    runtime  → REST API + WebSocket running, forwarding loop drains telemetry queue
    shutdown → engine stopped, recorder finalised, forwarder closed
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from simulator.config import settings
from simulator.core.simulation import SimulationEngine
from simulator.telemetry.source import SimulatorTelemetrySource
from simulator.telemetry.websocket import broadcaster, forwarder
from simulator.evaluation.recorder import RunRecorder
from simulator.api.routes import router, inject_dependencies

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── Global engine singleton ────────────────────────────────────────────────────
engine = SimulationEngine(cfg=settings)


async def _forwarding_loop(
    recorder: Optional[RunRecorder] = None,
) -> None:
    """Drain telemetry queue → broadcast to local subscribers → forward to MachSight."""
    source = SimulatorTelemetrySource(engine.telemetry_queue)
    await source.start()

    while True:
        try:
            msg = await asyncio.wait_for(engine.telemetry_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break

        # Broadcast to local WebSocket subscribers (dashboards / tests)
        await broadcaster.broadcast(msg)

        # Forward to MachSight
        await forwarder.forward(msg)

        # Optionally record to disk
        if recorder is not None:
            recorder.record_telemetry(msg)
            # Grab latest ground truth and record it too
            gt = engine.get_ground_truth_snapshot()
            recorder.record_ground_truth(gt.get("latest_state", {}))

        engine.telemetry_queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager."""
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info(
        "MachSight-Simulator starting on %s:%d",
        settings.sim_host,
        settings.sim_port,
    )

    # Resolve recorder from CLI args (stored on app.state)
    recorder: Optional[RunRecorder] = None
    scenario_data: Optional[dict] = None

    cli_args = getattr(app.state, "cli_args", {})
    scenario_name = cli_args.get("scenario")
    seed = cli_args.get("seed")
    should_record = cli_args.get("record", settings.record_runs)

    if scenario_name:
        from simulator.scenarios.loader import load_scenario
        scenario_data = load_scenario(scenario_name)
        engine.load_scenario(scenario_data, seed=seed)
        await engine.start()

    if should_record:
        from simulator.evaluation.recorder import RunRecorder
        runs_dir = settings.runs_dir
        runs_dir.mkdir(parents=True, exist_ok=True)
        recorder = RunRecorder(runs_dir, engine.scenario_name or "manual")
        if scenario_data:
            recorder.save_scenario(scenario_data)

    forwarding_task = asyncio.create_task(_forwarding_loop(recorder))

    yield  # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down simulator...")
    forwarding_task.cancel()
    try:
        await forwarding_task
    except asyncio.CancelledError:
        pass

    if engine.running:
        await engine.stop()

    await forwarder.close()

    if recorder is not None:
        recorder.finalise(engine.get_status())

    logger.info("Simulator shut down cleanly.")


def create_app(cli_args: Optional[dict] = None) -> FastAPI:
    """Factory function so tests can create fresh app instances."""
    app = FastAPI(
        title="MachSight-Simulator",
        description="Physics-inspired RC-car digital twin and fault injection server",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    # Store CLI args on app state for lifespan access
    app.state.cli_args = cli_args or {}

    # Inject shared singletons into routes
    inject_dependencies(engine, broadcaster, forwarder)

    return app


def run_server(cli_args: Optional[dict] = None) -> None:
    """Start the uvicorn server."""
    app = create_app(cli_args)
    uvicorn.run(
        app,
        host=settings.sim_host,
        port=settings.sim_port,
        log_level=settings.log_level.lower(),
    )
