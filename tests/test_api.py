"""tests/test_api.py — REST API endpoint tests."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from simulator.main import create_app
from simulator.core.simulation import SimulationEngine


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_get_status(client):
    res = client.get("/simulation/status")
    assert res.status_code == 200
    data = res.json()
    assert "running" in data
    assert "simulation_time_s" in data
    assert "active_faults" in data


def test_get_scenarios(client):
    res = client.get("/scenarios")
    assert res.status_code == 200
    data = res.json()
    assert "scenarios" in data
    assert "healthy_cruise" in data["scenarios"]


def test_throttle_control(client):
    res = client.post("/control/throttle", json={"pwm": 150, "direction": "forward", "mode": "forward"})
    assert res.status_code == 200
    data = res.json()
    assert data["pwm"] == 150
    assert data["direction"] == "forward"
    assert data["mode"] == "forward"


def test_inject_and_clear_fault(client):
    # Inject drivetrain jam
    res = client.post(
        "/faults/inject",
        json={
            "type": "drivetrain_jam",
            "start_s": 0.0,
            "duration_s": 10.0,
            "severity": 0.8,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "injected"
    fault_id = data["fault_id"]

    # Verify status reflects injected fault
    status_res = client.get("/simulation/status")
    assert status_res.status_code == 200
    assert any(f["fault_id"] == fault_id for f in status_res.json()["active_faults"])

    # Clear specific fault
    clear_res = client.post("/faults/clear", json={"fault_id": fault_id})
    assert clear_res.status_code == 200
    assert clear_res.json()["status"] == "cleared"


def test_ground_truth_endpoint(client):
    res = client.get("/simulation/ground-truth")
    assert res.status_code == 200
    data = res.json()
    assert "latest_state" in data
