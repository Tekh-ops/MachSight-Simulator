"""
tests/test_dataset_generator.py
===============================
Unit tests for the automated multi-run dataset generator and ground-truth isolation.
"""

import json
from pathlib import Path
import pytest

from simulator.evaluation.dataset_generator import DatasetGenerator
from simulator.evaluation.ground_truth import GroundTruthTimeline


def test_dataset_generation_structure(tmp_path: Path):
    """Verify dataset generation creates expected files and directory hierarchy."""
    generator = DatasetGenerator(output_dir=tmp_path)
    dataset_path = generator.generate(
        scenario_names=["healthy_cruise"],
        runs_per_scenario=2,
        base_seed=100,
        dataset_name="test_dataset",
        duration_override_s=2.0,
    )

    assert dataset_path.exists()
    assert (dataset_path / "manifest.json").exists()

    with (dataset_path / "manifest.json").open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["dataset_name"] == "test_dataset"
    assert manifest["total_runs"] == 2
    assert manifest["successful_runs"] == 2
    assert len(manifest["run_directories"]) == 2

    # Check each run directory
    for rel_dir in manifest["run_directories"]:
        run_dir = dataset_path / rel_dir
        assert (run_dir / "telemetry.jsonl").exists()
        assert (run_dir / "ground_truth.jsonl").exists()
        assert (run_dir / "metadata.json").exists()
        assert (run_dir / "scenario.yaml").exists()


def test_ground_truth_isolation_invariant(tmp_path: Path):
    """CRITICAL: Telemetry files MUST NOT leak ground truth fields."""
    generator = DatasetGenerator(output_dir=tmp_path)
    dataset_path = generator.generate(
        scenario_names=["sudden_drivetrain_jam"],
        runs_per_scenario=1,
        base_seed=42,
        duration_override_s=5.0,
    )

    run_dir = dataset_path / "sudden_drivetrain_jam" / "run_000000"
    telem_file = run_dir / "telemetry.jsonl"
    gt_file = run_dir / "ground_truth.jsonl"

    assert telem_file.exists()
    assert gt_file.exists()

    forbidden_fields = {"is_healthy", "active_faults", "active_fault_details", "wheel_slip", "mechanical_load"}

    with telem_file.open("r", encoding="utf-8") as f:
        for line in f:
            packet = json.loads(line)
            for field in forbidden_fields:
                assert field not in packet, f"Forbidden field '{field}' leaked into telemetry stream!"

    # In contrast, ground_truth.jsonl MUST have these fields
    with gt_file.open("r", encoding="utf-8") as f:
        first_gt = json.loads(f.readline())
        assert "is_healthy" in first_gt
        assert "active_faults" in first_gt
        assert "wheel_slip" in first_gt


def test_reproducibility_with_seed(tmp_path: Path):
    """Identical seed must yield identical telemetry and ground truth."""
    gen1 = DatasetGenerator(output_dir=tmp_path / "d1")
    path1 = gen1.generate(
        scenario_names=["healthy_cruise"],
        runs_per_scenario=1,
        base_seed=777,
        duration_override_s=3.0,
    )

    gen2 = DatasetGenerator(output_dir=tmp_path / "d2")
    path2 = gen2.generate(
        scenario_names=["healthy_cruise"],
        runs_per_scenario=1,
        base_seed=777,
        duration_override_s=3.0,
    )

    lines1 = (path1 / "healthy_cruise" / "run_000000" / "telemetry.jsonl").read_text(encoding="utf-8").strip().splitlines()
    lines2 = (path2 / "healthy_cruise" / "run_000000" / "telemetry.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines1) == len(lines2)
    for l1, l2 in zip(lines1, lines2):
        p1 = json.loads(l1)
        p2 = json.loads(l2)
        assert p1.get("current_a") == p2.get("current_a")
        assert p1.get("rpm") == p2.get("rpm")
        assert p1.get("distance_cm") == p2.get("distance_cm")
        assert p1.get("pwm_command") == p2.get("pwm_command")


def test_ground_truth_timeline_alignment(tmp_path: Path):
    """Test loading and querying GroundTruthTimeline from a generated run."""
    gen = DatasetGenerator(output_dir=tmp_path)
    dataset_path = gen.generate(
        scenario_names=["intermittent_fault"],
        runs_per_scenario=1,
        base_seed=42,
        duration_override_s=8.0,
    )

    run_dir = dataset_path / "intermittent_fault" / "run_000000"
    timeline = GroundTruthTimeline.load_from_run_dir(run_dir)

    assert timeline.has_faults
    assert len(timeline.fault_events) > 0

    fault = timeline.fault_events[0]
    assert fault.fault_type == "intermittent"
    assert timeline.is_fault_active_at(fault.start_time + 0.5)
    assert not timeline.is_fault_active_at(fault.start_time - 0.5)

    onset_state = timeline.get_state_at_onset("intermittent")
    assert onset_state is not None
    assert onset_state.sim_time >= fault.start_time
