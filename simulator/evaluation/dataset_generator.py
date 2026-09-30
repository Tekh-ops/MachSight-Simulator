"""
simulator/evaluation/dataset_generator.py
==========================================
Automated multi-run dataset generation facility for benchmark datasets.

Produces:
    datasets/<dataset_id>/
        manifest.json
        <scenario_name>/
            run_000001/
                telemetry.jsonl      (clean canonical telemetry without ground truth)
                ground_truth.jsonl   (hidden simulation physics and fault labels)
                scenario.yaml        (exact scenario definition)
                metadata.json        (run metadata and RNG seed)
            run_000002/
                ...
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from simulator.config import settings
from simulator.core.simulation import SimulationEngine
from simulator.evaluation.recorder import RunRecorder
from simulator.scenarios.loader import discover_scenarios, load_scenario

logger = logging.getLogger(__name__)


@dataclass
class DatasetManifest:
    """Detailed provenance and statistical manifest for a generated dataset."""
    dataset_id: str
    dataset_version: str = "1.0.0"
    creation_timestamp: float = field(default_factory=time.time)
    simulator_version: str = "0.1.0"
    telemetry_schema_version: int = 2
    scenario_set: List[str] = field(default_factory=list)
    number_of_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    seeds: List[int] = field(default_factory=list)
    fault_distribution: Dict[str, int] = field(default_factory=dict)
    run_directories: List[str] = field(default_factory=list)
    configuration_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["dataset_name"] = self.dataset_id
        d["total_runs"] = self.number_of_runs
        d["created_iso"] = datetime.fromtimestamp(self.creation_timestamp, tz=timezone.utc).isoformat()
        return d


class DatasetGenerator:
    """Orchestrates headless multi-run dataset generation."""

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self.output_base = output_dir or Path("datasets")

    def generate(
        self,
        scenario_names: List[str],
        runs_per_scenario: int = 1,
        base_seed: int = 42,
        dataset_name: Optional[str] = None,
        duration_override_s: Optional[float] = None,
    ) -> Path:
        """Generate a complete multi-run dataset for the specified scenarios."""
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        ds_id = dataset_name or f"dataset_{ts_str}"
        target_dir = self.output_base / ds_id
        target_dir.mkdir(parents=True, exist_ok=True)

        manifest = DatasetManifest(
            dataset_id=ds_id,
            scenario_set=scenario_names,
            number_of_runs=len(scenario_names) * runs_per_scenario,
        )

        global_run_idx = 0
        current_seed = base_seed

        for scen_name in scenario_names:
            scenario_data = load_scenario(scen_name)
            scen_dir = target_dir / scen_name
            scen_dir.mkdir(parents=True, exist_ok=True)

            for r_idx in range(runs_per_scenario):
                run_id = f"run_{r_idx:06d}"
                run_dir = scen_dir / run_id
                run_dir.mkdir(parents=True, exist_ok=True)

                run_seed = current_seed
                current_seed += 1
                manifest.seeds.append(run_seed)

                logger.info("Generating %s / %s (seed=%d)...", scen_name, run_id, run_seed)

                try:
                    self._execute_single_run(
                        scenario_data=scenario_data,
                        run_dir=run_dir,
                        seed=run_seed,
                        duration_s=duration_override_s,
                    )
                    manifest.successful_runs += 1
                    manifest.run_directories.append(str(run_dir.relative_to(target_dir)))

                    # Count fault types in this run
                    for f in scenario_data.get("faults", []):
                        ft = f.get("type", "unknown")
                        manifest.fault_distribution[ft] = manifest.fault_distribution.get(ft, 0) + 1

                except Exception as exc:
                    logger.error("Failed run %s / %s: %s", scen_name, run_id, exc, exc_info=True)
                    manifest.failed_runs += 1
                    err_file = run_dir / "error.txt"
                    err_file.write_text(f"Generation failure: {exc}\n", encoding="utf-8")

                global_run_idx += 1

        # Calculate configuration hash for reproducibility validation
        config_dump = json.dumps({"scenarios": scenario_names, "base_seed": base_seed, "runs": runs_per_scenario})
        manifest.configuration_hash = hashlib.sha256(config_dump.encode("utf-8")).hexdigest()[:16]

        # Save manifest.json
        manifest_file = target_dir / "manifest.json"
        with manifest_file.open("w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2)

        logger.info("Dataset generated: %s (%d successful runs)", target_dir, manifest.successful_runs)
        return target_dir

    def _execute_single_run(
        self,
        scenario_data: dict,
        run_dir: Path,
        seed: int,
        duration_s: Optional[float] = None,
    ) -> None:
        """Run headless synchronous simulation and stream directly to disk."""
        engine = SimulationEngine(cfg=settings)
        engine.load_scenario(scenario_data, seed=seed)

        target_duration = duration_s if duration_s is not None else (engine.scenario_duration_s or 60.0)

        telemetry_file = (run_dir / "telemetry.jsonl").open("w", encoding="utf-8")
        gt_file = (run_dir / "ground_truth.jsonl").open("w", encoding="utf-8")

        start_wall = time.time()
        packet_count = 0
        gt_count = 0

        # Save scenario.yaml copy
        with (run_dir / "scenario.yaml").open("w", encoding="utf-8") as f:
            yaml.dump(scenario_data, f, default_flow_style=False)

        # Step synchronously until target duration reached
        dt = engine.cfg.sim_dt
        while engine.sim_time < target_duration:
            engine.step(dt)

            # Drain any emitted telemetry
            while not engine.telemetry_queue.empty():
                msg = engine.telemetry_queue.get_nowait()
                telemetry_file.write(json.dumps(msg.to_machsight_dict()) + "\n")
                packet_count += 1

            # Record latest ground-truth snapshot
            gt_snapshot = engine.get_ground_truth_snapshot()
            latest = gt_snapshot.get("latest_state", {})
            gt_file.write(json.dumps(latest) + "\n")
            gt_count += 1

        telemetry_file.close()
        gt_file.close()

        # Save metadata.json
        meta = {
            "scenario": engine.scenario_name,
            "seed": seed,
            "duration_s": round(engine.sim_time, 2),
            "wall_time_s": round(time.time() - start_wall, 2),
            "packets_emitted": packet_count,
            "ground_truth_records": gt_count,
            "faults": scenario_data.get("faults", []),
        }
        with (run_dir / "metadata.json").open("w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
