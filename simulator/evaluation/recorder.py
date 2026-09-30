"""
simulator/evaluation/recorder.py
==================================
Run recorder: persists telemetry and ground-truth to disk.

Output layout::

    runs/
        2026-09-30_14-30-00/
            telemetry.jsonl      ← one TelemetryMessage per line (no ground truth)
            ground_truth.jsonl   ← hidden simulation state per sim tick
            scenario.yaml        ← the scenario that was run
            metadata.json        ← run summary

IMPORTANT: telemetry.jsonl must NEVER contain fault labels or hidden state.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

if TYPE_CHECKING:
    from simulator.telemetry.schema import TelemetryMessage

logger = logging.getLogger(__name__)


class RunRecorder:
    """Records a simulation run to disk."""

    def __init__(self, runs_dir: Path, scenario_name: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._run_dir = runs_dir / f"{ts}_{scenario_name}"
        self._run_dir.mkdir(parents=True, exist_ok=True)

        self._telemetry_path = self._run_dir / "telemetry.jsonl"
        self._gt_path = self._run_dir / "ground_truth.jsonl"
        self._meta_path = self._run_dir / "metadata.json"
        self._scenario_path = self._run_dir / "scenario.yaml"

        self._telemetry_file = self._telemetry_path.open("w", encoding="utf-8")
        self._gt_file = self._gt_path.open("w", encoding="utf-8")

        self._start_time = time.time()
        self._telemetry_count = 0
        self._gt_count = 0

        logger.info("Recording run to: %s", self._run_dir)

    def record_telemetry(self, msg: "TelemetryMessage") -> None:
        """Write one telemetry packet to telemetry.jsonl.

        Uses to_machsight_dict() to ensure ground-truth is excluded.
        """
        row = msg.to_machsight_dict()
        self._telemetry_file.write(json.dumps(row) + "\n")
        self._telemetry_count += 1

    def record_ground_truth(self, gt: dict) -> None:
        """Write one ground-truth snapshot to ground_truth.jsonl."""
        self._gt_file.write(json.dumps(gt) + "\n")
        self._gt_count += 1

    def save_scenario(self, scenario_dict: dict) -> None:
        """Persist the scenario YAML."""
        with self._scenario_path.open("w", encoding="utf-8") as f:
            yaml.dump(scenario_dict, f, default_flow_style=False)

    def finalise(self, engine_status: dict) -> None:
        """Close files and write metadata.json."""
        self._telemetry_file.close()
        self._gt_file.close()

        metadata = {
            "run_dir": str(self._run_dir),
            "start_time": self._start_time,
            "end_time": time.time(),
            "duration_s": round(time.time() - self._start_time, 2),
            "telemetry_packets": self._telemetry_count,
            "ground_truth_records": self._gt_count,
            "scenario": engine_status.get("scenario"),
            "machine_id": engine_status.get("machine_id"),
        }
        with self._meta_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        logger.info(
            "Run recorded: %d telemetry packets, %d ground-truth records → %s",
            self._telemetry_count,
            self._gt_count,
            self._run_dir,
        )

    @property
    def run_dir(self) -> Path:
        return self._run_dir
