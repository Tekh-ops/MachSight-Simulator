"""
MachSight-Simulator
===================
Physics-inspired RC-car digital twin and fault injection server.

Package layout
--------------
simulator/
    config.py         — Pydantic-Settings configuration model
    main.py           — FastAPI + WebSocket entry point
    api/              — REST control API + WebSocket server
    core/             — Vehicle state, physics, environment
    sensors/          — Sensor models (ultrasonic, current, RPM)
    faults/           — Fault injection engine
    scenarios/        — Scenario loader + YAML definitions
    telemetry/        — Canonical schema + TelemetrySource abstraction
    evaluation/       — Ground-truth logger + run recorder
"""
