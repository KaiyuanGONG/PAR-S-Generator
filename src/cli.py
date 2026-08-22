"""Command-line entry point for PAR-S synthetic-data preparation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.phantom_generator import PhantomConfig
from pipeline.contracts import EMPIRICAL_OBSERVATION_PROTOCOL_STATUS, atomic_write_json
from pipeline.experiments import (
    EXPERIMENT_NAMES,
    analyze_experiment,
    execute_prepared_experiment,
    experiment_summary,
    prepare_all_experiments,
    prepare_experiment,
)
from pipeline.legacy import freeze_legacy_dataset
from pipeline.pilot import select_from_run
from pipeline.runner import PipelineConfig, PipelineRunner


def _load_config(path: Path) -> PipelineConfig:
    return PipelineConfig.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _cmd_init(args) -> int:
    phantom = PhantomConfig(n_cases=args.cases, output_dir="managed_by_pipeline")
    config = PipelineConfig(
        run_id=args.run_id,
        runs_root=args.runs_root,
        phantom=phantom,
        simulation_mode=args.mode,
        create_poisson_observation=True,
        observation_policy="empirical_total_counts",
        observation_protocol_status=EMPIRICAL_OBSERVATION_PROTOCOL_STATUS,
    )
    atomic_write_json(Path(args.output), config.to_dict())
    print(Path(args.output).resolve())
    return 0


def _cmd_run(args) -> int:
    config = _load_config(Path(args.config))
    if config.simulation_mode == "execute" and not args.allow_simind_execution:
        raise SystemExit(
            "Refusing to launch SIMIND. Re-run with --allow-simind-execution after reviewing run config and commands."
        )
    runner = PipelineRunner(config, resume=args.resume)
    # A prepared-only run intentionally has no projection expectation and
    # therefore cannot be finalized.  It should still complete successfully
    # as a reviewable command/input package without requiring a special flag.
    should_finalize = not args.no_finalize and (
        config.simulation_mode != "prepare"
        or config.execution_scope == "anatomy_only_gate_a"
    )
    result = runner.run_all(finalize=should_finalize)
    print(json.dumps({"run": str(runner.layout.root), "finalized": result.get("finalized", False)}, indent=2))
    return 0


def _cmd_inspect(args) -> int:
    runner = PipelineRunner.open(Path(args.run))
    payload = runner.ledger.load()
    payload["case_count"] = len(runner.ledger.read_cases())
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_select_pilot(args) -> int:
    output = Path(args.output) if args.output else None
    result = select_from_run(Path(args.run), args.count, output)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_experiment(args) -> int:
    destination = Path(args.destination)
    if args.name == "all":
        roots = prepare_all_experiments(
            destination,
            simind_exe=Path(args.simind_exe),
            smc_file=Path(args.smc),
        )
    else:
        roots = [
            prepare_experiment(
                args.name,
                destination,
                simind_exe=Path(args.simind_exe),
                smc_file=Path(args.smc),
            )
        ]
    print(json.dumps([experiment_summary(root) for root in roots], indent=2))
    return 0


def _cmd_freeze(args) -> int:
    def progress(current, total, case_id):
        print(f"[{current}/{total}] {case_id}")

    root = freeze_legacy_dataset(
        phantom_dir=Path(args.phantom_dir),
        projection_dir=Path(args.projection_dir),
        destination=Path(args.destination),
        simind_exe=Path(args.simind_exe),
        smc_file=Path(args.smc),
        progress=progress,
        refresh_manifest=args.refresh,
    )
    print(root)
    return 0


def _cmd_analyze_experiment(args) -> int:
    print(json.dumps(analyze_experiment(Path(args.experiment)), indent=2))
    return 0


def _cmd_execute_experiment(args) -> int:
    if not args.allow_simind_execution:
        raise SystemExit(
            "Refusing to launch SIMIND. Re-run with --allow-simind-execution after reviewing commands.json."
        )
    result = execute_prepared_experiment(Path(args.experiment), resume=args.resume)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "completed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="par-s-data",
        description="Prepare, simulate, quality-control and finalize synthetic liver SPECT datasets.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Write an editable effective pipeline configuration")
    init.add_argument("--run-id", required=True)
    init.add_argument("--runs-root", default="runs")
    init.add_argument("--cases", type=int, default=2)
    init.add_argument("--mode", choices=("prepare", "mock", "execute"), default="prepare")
    init.add_argument("--output", required=True)
    init.set_defaults(func=_cmd_init)

    run = sub.add_parser("run", help="Run or resume the single data-preparation pipeline")
    run.add_argument("--config", required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--no-finalize", action="store_true")
    run.add_argument("--allow-simind-execution", action="store_true")
    run.set_defaults(func=_cmd_run)

    inspect = sub.add_parser("inspect", help="Print run state without modifying it")
    inspect.add_argument("--run", required=True)
    inspect.set_defaults(func=_cmd_inspect)

    select_pilot = sub.add_parser(
        "select-pilot",
        help="Select a deterministic representative subset from a QC-complete phantom run",
    )
    select_pilot.add_argument("--run", required=True)
    select_pilot.add_argument("--count", type=int, default=10)
    select_pilot.add_argument("--output")
    select_pilot.set_defaults(func=_cmd_select_pilot)

    experiment = sub.add_parser("prepare-experiment", help="Prepare a blocking physics experiment; never execute it")
    experiment.add_argument("--name", choices=(*EXPERIMENT_NAMES, "all"), required=True)
    experiment.add_argument("--destination", default="experiments")
    experiment.add_argument("--simind-exe", default="simind/simind.exe")
    experiment.add_argument("--smc", default="simind/ge870_czt.smc")
    experiment.set_defaults(func=_cmd_experiment)

    analyze = sub.add_parser("analyze-experiment", help="Analyze available prepared-experiment outputs")
    analyze.add_argument("--experiment", required=True)
    analyze.set_defaults(func=_cmd_analyze_experiment)

    execute_experiment = sub.add_parser(
        "execute-experiment",
        help="Execute one reviewed experiment sequentially with logs, QC and hashes",
    )
    execute_experiment.add_argument("--experiment", required=True)
    execute_experiment.add_argument("--allow-simind-execution", action="store_true")
    execute_experiment.add_argument("--resume", action="store_true")
    execute_experiment.set_defaults(func=_cmd_execute_experiment)

    freeze = sub.add_parser("freeze-legacy", help="Checksum-freeze the existing 500 cases by read-only reference")
    freeze.add_argument("--phantom-dir", default="output/syn3d_noNoise")
    freeze.add_argument("--projection-dir", default="output/SPECT_60Mbq20s")
    freeze.add_argument("--destination", default="manifests/legacy-v1-weighted-mc")
    freeze.add_argument("--simind-exe", default="simind/simind.exe")
    freeze.add_argument("--smc", default="simind/ge870_czt.smc")
    freeze.add_argument("--refresh", action="store_true", help="Refresh only an existing verified legacy manifest")
    freeze.set_defaults(func=_cmd_freeze)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
