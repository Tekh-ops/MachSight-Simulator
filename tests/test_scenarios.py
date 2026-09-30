"""tests/test_scenarios.py — Scenario loading and validation tests."""

from __future__ import annotations

import pytest
from simulator.scenarios.loader import list_scenarios, load_scenario, discover_scenarios
from simulator.core.simulation import SimulationEngine


def test_list_scenarios_finds_builtins():
    scenarios = list_scenarios()
    expected = [
        "full_demo",
        "gradual_motor_drag",
        "healthy_cruise",
        "intermittent_fault",
        "obstacle_approach",
        "sensor_failures",
        "sudden_drivetrain_jam",
        "wheel_slip",
    ]
    for exp in expected:
        assert exp in scenarios, f"Expected scenario '{exp}' to be discovered"


def test_load_all_builtins_schema():
    """Verify that every built-in scenario loads valid YAML with expected keys."""
    for name in list_scenarios():
        data = load_scenario(name)
        assert "name" in data
        assert "duration_s" in data
        assert isinstance(data.get("faults", []), list)


def test_load_nonexistent_scenario_raises():
    with pytest.raises(KeyError, match="Scenario not found"):
        load_scenario("non_existent_scenario_12345")


@pytest.mark.asyncio
async def test_engine_loads_and_executes_scenario():
    engine = SimulationEngine()
    scenario = load_scenario("healthy_cruise")
    engine.load_scenario(scenario, seed=42)

    assert engine.scenario_name == "healthy_cruise"
    assert engine.scenario_duration_s == scenario["duration_s"]

    # Step a few ticks
    for _ in range(10):
        engine.step()

    assert engine.sim_time > 0.0
    status = engine.get_status()
    assert status["scenario"] == "healthy_cruise"
