"""
tests/test_replay.py
====================
Unit tests for the TelemetryReplaySource hardware abstraction.
"""

import json
from pathlib import Path
import pytest

from simulator.evaluation.dataset_generator import DatasetGenerator
from simulator.evaluation.replay import TelemetryReplaySource
from simulator.telemetry.schema import TelemetryMessage


@pytest.mark.asyncio
async def test_replay_source_streams_valid_telemetry(tmp_path: Path):
    """Verify TelemetryReplaySource produces valid TelemetryMessage objects without leaks."""
    gen = DatasetGenerator(output_dir=tmp_path)
    ds_path = gen.generate(
        scenario_names=["healthy_cruise"],
        runs_per_scenario=1,
        base_seed=123,
        duration_override_s=2.0,
    )
    telem_file = ds_path / "healthy_cruise" / "run_000000" / "telemetry.jsonl"

    source = TelemetryReplaySource(
        telemetry_file=telem_file,
        speed_multiplier=0.0,  # instantaneous
        loop=False,
    )

    received_messages = []
    has_distance = False
    has_current = False
    has_rpm = False

    async for msg in source.telemetry_stream():
        assert isinstance(msg, TelemetryMessage)
        assert msg.schema_version == 2
        # At least one sensor reading should be present in each packet
        assert (
            msg.sensors.distance_cm is not None
            or msg.sensors.current_a is not None
            or msg.sensors.wheel_rpm is not None
        )
        if msg.sensors.distance_cm is not None:
            has_distance = True
        if msg.sensors.current_a is not None:
            has_current = True
        if msg.sensors.wheel_rpm is not None:
            has_rpm = True

        # Telemetry contract verification
        d = msg.to_machsight_dict()
        assert "is_healthy" not in d
        assert "wheel_slip" not in d
        received_messages.append(msg)

    assert len(received_messages) > 10
    assert has_distance, "Stream should contain distance readings"
    assert has_current, "Stream should contain current readings"
    assert has_rpm, "Stream should contain RPM readings"
    # Verify sequence monotonicity
    seqs = [m.sequence for m in received_messages]
    assert seqs == sorted(seqs)


@pytest.mark.asyncio
async def test_replay_nonexistent_file_raises():
    """Verify error raised on invalid path."""
    with pytest.raises(FileNotFoundError):
        TelemetryReplaySource(telemetry_file=Path("/nonexistent/telemetry.jsonl"))
