"""
simulator/__main__.py
======================
CLI entry point for MachSight-Simulator.

Supports:
1. Live Server Mode:
       python -m simulator
       python -m simulator --scenario healthy_cruise
       python -m simulator --record

2. Automated Dataset Generation:
       python -m simulator generate-dataset --scenarios healthy_cruise,sudden_drivetrain_jam --runs 5
       python -m simulator --generate-dataset --scenarios all --runs 3

3. Benchmark Suite Runner & Evaluation:
       python -m simulator benchmark --suite benchmark_v1
       python -m simulator benchmark --suite regression_ci --evaluate-machsight

4. Telemetry Replay:
       python -m simulator replay --file datasets/my_ds/run_001/telemetry.jsonl --speed 2.0
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator",
        description="MachSight-Simulator — Physics-inspired RC-car digital twin & evaluation suite",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # ── Subcommand: generate-dataset ──────────────────────────────────────────
    gen_p = subparsers.add_parser("generate-dataset", help="Generate multi-run synthetic telemetry datasets")
    gen_p.add_argument("--scenarios", default="all", help="Comma-separated scenario names, or 'all'")
    gen_p.add_argument("--runs", "--runs-per-scenario", type=int, default=1, dest="runs_per_scenario", help="Runs per scenario")
    gen_p.add_argument("--output-dir", type=Path, default=Path("datasets"), help="Output dataset base directory")
    gen_p.add_argument("--name", default=None, help="Name of the dataset collection")
    gen_p.add_argument("--seed", type=int, default=42, help="Base random seed")
    gen_p.add_argument("--duration", type=float, default=None, help="Duration override in seconds")

    # ── Subcommand: benchmark ─────────────────────────────────────────────────
    bench_p = subparsers.add_parser("benchmark", help="Execute declarative benchmark suite")
    bench_p.add_argument("--suite", default="regression_ci", help="Benchmark suite name or YAML path")
    bench_p.add_argument("--runs", "--runs-per-scenario", type=int, default=None, dest="runs_per_scenario", help="Runs per scenario override")
    bench_p.add_argument("--seed", type=int, default=None, help="Random seed override")
    bench_p.add_argument("--duration", type=float, default=None, help="Duration override in seconds")
    bench_p.add_argument("--results-dir", type=Path, default=Path("results"), help="Results output directory")
    bench_p.add_argument("--evaluate-machsight", action="store_true", help="Connect and evaluate against live MachSight backend")
    bench_p.add_argument("--machsight-url", default="http://localhost:8000", help="MachSight backend base URL")

    # ── Subcommand: replay ───────────────────────────────────────────────────
    rep_p = subparsers.add_parser("replay", help="Replay pre-recorded telemetry stream")
    rep_p.add_argument("--file", type=Path, required=True, help="Path to telemetry.jsonl file")
    rep_p.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier (0 = instant)")
    rep_p.add_argument("--target-ws", default=None, help="Forward replayed telemetry to target WebSocket URL")

    # ── Subcommand: validate ─────────────────────────────────────────────────
    val_p = subparsers.add_parser("validate", help="Validate causal and physical correctness of a simulation run")
    val_p.add_argument("--run", type=Path, default=None, help="Path to run directory containing ground_truth.jsonl")
    val_p.add_argument("--scenario", default="full_demo", help="Scenario to run headlessly and validate (default: full_demo)")
    val_p.add_argument("--seed", type=int, default=42, help="Random seed for validation run")

    # ── Top-level / Server flags ──────────────────────────────────────────────
    parser.add_argument("--scenario", metavar="NAME", default=None, help="Load and run a named scenario on startup")
    parser.add_argument("--seed", type=int, default=None, metavar="INT", help="Random seed for reproducible scenario playback")
    parser.add_argument("--list-scenarios", action="store_true", help="Print available scenario names and exit")
    parser.add_argument("--list-benchmarks", action="store_true", help="Print available benchmark suites and exit")
    parser.add_argument("--record", action="store_true", help="Record live run to runs/<timestamp>/")
    parser.add_argument("--host", default=None, metavar="HOST", help="Override server host")
    parser.add_argument("--port", type=int, default=None, metavar="PORT", help="Override server port")
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Override log level")

    return parser


def handle_generate_dataset(args: argparse.Namespace) -> None:
    from simulator.evaluation.dataset_generator import DatasetGenerator
    from simulator.scenarios.loader import list_scenarios

    if args.scenarios == "all":
        scen_list = list_scenarios()
    else:
        scen_list = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    print(f"Generating dataset for {len(scen_list)} scenarios ({args.runs_per_scenario} runs each)...")
    gen = DatasetGenerator(output_dir=args.output_dir)
    out_dir = gen.generate(
        scenario_names=scen_list,
        runs_per_scenario=args.runs_per_scenario,
        base_seed=args.seed,
        dataset_name=args.name,
        duration_override_s=args.duration,
    )
    print(f"Dataset generated successfully at: {out_dir}")


def handle_benchmark(args: argparse.Namespace) -> None:
    from simulator.benchmarks.benchmark import BenchmarkRunner
    from simulator.integration import MachSightClient, MachSightEvaluator

    evaluator = None
    if args.evaluate_machsight:
        print(f"Connecting to MachSight backend at {args.machsight_url}...")
        client = MachSightClient(base_url=args.machsight_url)
        evaluator = MachSightEvaluator(client=client)

    runner = BenchmarkRunner(results_dir=args.results_dir)
    report = runner.run_benchmark(
        suite_name_or_dict=args.suite,
        runs_per_scenario_override=args.runs_per_scenario,
        seed_override=args.seed,
        duration_override_s=args.duration,
        machsight_evaluator=evaluator,
    )
    print("\n" + report.generate_markdown())


def handle_replay(args: argparse.Namespace) -> None:
    from simulator.evaluation.replay import TelemetryReplaySource

    async def _do_replay():
        source = TelemetryReplaySource(
            telemetry_file=args.file,
            speed_multiplier=args.speed,
            loop=False,
        )
        print(f"Replaying {args.file} (speed: {args.speed}x)...")
        count = 0
        async for msg in source.telemetry_stream():
            count += 1
            if count % 50 == 0:
                print(f"  Emitted {count} packets (current RPM={msg.rpm.rpm_raw:.0f}, Distance={msg.distance.distance_cm:.1f}cm)")
        print(f"Replay complete: {count} packets emitted.")

    asyncio.run(_do_replay())


def handle_validate(args: argparse.Namespace) -> None:
    from simulator.evaluation.scenario_validator import ScenarioValidator
    from simulator.evaluation.dataset_generator import DatasetGenerator
    import tempfile

    if args.run is not None:
        report = ScenarioValidator.validate_run(args.run)
    else:
        with tempfile.TemporaryDirectory() as tmp_dir:
            gen = DatasetGenerator(output_dir=Path(tmp_dir))
            ds_path = gen.generate(
                scenario_names=[args.scenario],
                runs_per_scenario=1,
                base_seed=args.seed,
            )
            run_dir = ds_path / args.scenario / "run_000000"
            report = ScenarioValidator.validate_run(run_dir, scenario_name=args.scenario, seed=args.seed)

    print()
    print(report.print_summary())
    print()
    if not report.all_passed:
        sys.exit(1)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ── List utilities ────────────────────────────────────────────────────────
    if args.list_scenarios:
        from simulator.scenarios.loader import list_scenarios
        names = list_scenarios()
        print("Available scenarios:")
        for name in names:
            print(f"  {name}")
        sys.exit(0)

    if args.list_benchmarks:
        from simulator.benchmarks.benchmark import list_benchmark_suites
        names = list_benchmark_suites()
        print("Available benchmark suites:")
        for name in names:
            print(f"  {name}")
        sys.exit(0)

    # ── Subcommand dispatch ───────────────────────────────────────────────────
    if args.command == "generate-dataset":
        handle_generate_dataset(args)
        sys.exit(0)
    elif args.command == "benchmark":
        handle_benchmark(args)
        sys.exit(0)
    elif args.command == "replay":
        handle_replay(args)
        sys.exit(0)
    elif args.command == "validate":
        handle_validate(args)
        sys.exit(0)

    # ── Apply CLI overrides to settings ──────────────────────────────────────
    from simulator.config import settings

    if args.host is not None:
        settings.sim_host = args.host
    if args.port is not None:
        settings.sim_port = args.port
    if args.log_level is not None:
        settings.log_level = args.log_level

    # ── Validate scenario exists before starting server ───────────────────────
    if args.scenario is not None:
        from simulator.scenarios.loader import list_scenarios
        available = list_scenarios()
        if args.scenario not in available:
            print(
                f"Error: scenario '{args.scenario}' not found.\n"
                f"Available: {', '.join(available) if available else 'none'}"
            )
            sys.exit(1)

    # ── Print startup banner & run server ─────────────────────────────────────
    _print_banner(args)
    from simulator.main import run_server

    cli_args = {
        "scenario": args.scenario,
        "seed": args.seed,
        "record": args.record,
    }
    run_server(cli_args)


def _print_banner(args: argparse.Namespace) -> None:
    from simulator.config import settings
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║          MachSight-Simulator  v0.1.0                 ║")
    print("║  Physics-inspired RC-car Digital Twin & Evaluator    ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"  Machine ID  : {settings.machine_id}")
    print(f"  Server      : http://{settings.sim_host}:{settings.sim_port}")
    print(f"  Telemetry WS: ws://{settings.sim_host}:{settings.sim_port}/ws/telemetry")
    print(f"  MachSight   : {settings.machsight_ws_url}")
    if args.scenario:
        print(f"  Scenario    : {args.scenario}")
    if args.seed is not None:
        print(f"  Seed        : {args.seed}")
    if args.record:
        print(f"  Recording   : runs/")
    print()


if __name__ == "__main__":
    main()
