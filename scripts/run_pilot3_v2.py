"""Generate, run, atomically write and freeze the three-case PAR-S V2 pilot."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_dataset_v2 import (  # noqa: E402
    build_generation_plan,
    write_generation_plan,
)

from core.case_writer_v2 import (  # noqa: E402
    CasePayloadV2,
    DatasetContractV2,
    freeze_dataset,
    write_case_v2,
    write_split_plan,
)
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot_v2 import (  # noqa: E402
    PILOT_GATE_SCHEMA_VERSION,
    build_completed_metadata,
    load_pilot_plan,
    prepare_pilot_case,
    resolve_plan_path,
    simind_extra_artifacts,
    validate_boundary_rejections,
)
from core.provenance import atomic_write_json, sha256_file  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402
from core.seeds import SeedBundle  # noqa: E402
from core.simind_exec import SimindRunSpec, run_simind_case  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_pilot3")
DEFAULT_SIMIND_EXE = Path(r"D:\PFE-U\PAR-S-Generator\simind\simind.exe")
DEFAULT_SMC_DIR = Path(r"C:\simind\smc_dir")
REQUIRED_ARTIFACTS = (
    "phantom_npz",
    "metadata_json",
    "projection_a00",
    "projection_mhd",
    "projection_res",
    "projection_spe",
    "simind_run_provenance",
    "simind_source_bin",
    "simind_density_bin",
    "pilot_plan",
    "pilot_runtime",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic three-case Task-12 PAR-S V2 smoke pilot."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "pilot3_v2.json",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--simind-exe", type=Path, default=DEFAULT_SIMIND_EXE)
    parser.add_argument("--smc-dir", type=Path, default=DEFAULT_SMC_DIR)
    return parser


def _git_commit() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise RuntimeError(
            "pilot generation requires a clean Generator worktree; commit all "
            f"code/config changes first:\n{status.stdout.rstrip()}"
        )
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _relative_paths(plan: Mapping[str, object]) -> dict[str, Path]:
    return {
        key: resolve_plan_path(REPO_ROOT, plan[key], key)
        for key in (
            "profile_path",
            "scanner_path",
            "evidence_registry_path",
            "smc_path",
            "simind_ini_path",
        )
    }


def _require_runtime(args: argparse.Namespace, plan: Mapping[str, object]) -> None:
    if not args.simind_exe.is_file():
        raise FileNotFoundError(f"SIMIND executable not found: {args.simind_exe}")
    binary_digest = sha256_file(args.simind_exe)
    if binary_digest != plan["expected_simind_binary_sha256"]:
        raise RuntimeError(
            "SIMIND executable hash differs from the frozen pilot plan: "
            f"{binary_digest}"
        )
    if not args.smc_dir.is_dir():
        raise FileNotFoundError(f"SIMIND SMC_DIR not found: {args.smc_dir}")
    if args.output_root.exists():
        raise FileExistsError(
            f"pilot output root already exists; V2 pilots are never upgraded in place: "
            f"{args.output_root}"
        )
    work_root = args.output_root.parent / f"{args.output_root.name}_work"
    if work_root.exists():
        raise FileExistsError(
            f"pilot work root already exists; choose a fresh output name: {work_root}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = load_pilot_plan(args.config)
    paths = _relative_paths(plan)
    _require_runtime(args, plan)
    generator_commit = _git_commit()
    registry = load_evidence_registry(paths["evidence_registry_path"])
    profile = load_profile(paths["profile_path"], registry)
    scanner = load_profile(paths["scanner_path"], registry)
    boundary_results = validate_boundary_rejections(plan, profile)

    cases = plan["cases"]
    execution = plan["execution"]
    ratios = plan["split_ratios"]
    assert isinstance(cases, list)
    assert isinstance(execution, dict)
    assert isinstance(ratios, dict)
    split_plan, generation_plan = build_generation_plan(
        dataset_id=str(plan["dataset_id"]),
        dataset_version=str(plan["dataset_version"]),
        dataset_role=str(plan["dataset_role"]),
        profile_id=profile.profile_id,
        case_count=3,
        family_size=1,
        global_seed=int(plan["global_seed"]),
        ratios={key: float(value) for key, value in ratios.items()},
    )
    configured_pairs = [
        (str(case["case_id"]), str(case["case_family_id"])) for case in cases
    ]
    planned_pairs = [
        (str(entry["case_id"]), str(entry["case_family_id"]))
        for entry in generation_plan["entries"]
    ]
    if configured_pairs != planned_pairs:
        raise RuntimeError("pilot case/family IDs disagree with the immutable generation plan")
    rr_seeds = [
        SeedBundle.from_case(int(plan["global_seed"]), case_id).simind
        for case_id, _ in configured_pairs
    ]
    if len(set(rr_seeds)) != 3 or not all(1 <= seed <= 10_007 for seed in rr_seeds):
        raise RuntimeError("pilot SIMIND /RR allocation is not unique and practical")

    args.output_root.mkdir(parents=True, exist_ok=False)
    work_root = args.output_root.parent / f"{args.output_root.name}_work"
    work_root.mkdir(parents=True, exist_ok=False)
    write_split_plan(split_plan, args.output_root)
    write_generation_plan(generation_plan, args.output_root)
    runtime_document = {
        "schema_version": "pars_v2_pilot3_runtime_v1",
        "generator_git_commit": generator_commit,
        "pilot_plan_sha256": sha256_file(args.config),
        "profile_sha256": sha256_file(paths["profile_path"]),
        "scanner_sha256": sha256_file(paths["scanner_path"]),
        "evidence_registry_sha256": sha256_file(paths["evidence_registry_path"]),
        "smc_sha256": sha256_file(paths["smc_path"]),
        "simind_ini_sha256": sha256_file(paths["simind_ini_path"]),
        "simind_binary_sha256": sha256_file(args.simind_exe),
        "simind_executable": str(args.simind_exe.resolve()),
        "smc_dir": str(args.smc_dir.resolve()),
        "base_histories_per_projection": int(
            execution["base_histories_per_projection"]
        ),
        "nn_multiplier": int(execution["nn_multiplier"]),
        "rr_allocator": str(execution["rr_allocator"]),
        "rr_by_case": {
            case_id: rr for (case_id, _), rr in zip(configured_pairs, rr_seeds)
        },
        "timeout_seconds": float(execution["timeout_seconds"]),
        "boundary_gates": boundary_results,
    }
    atomic_write_json(args.output_root / "PILOT_RUNTIME.json", runtime_document)

    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    records = []
    case_summaries: list[dict[str, object]] = []
    entry_by_id = {
        str(entry["case_id"]): entry for entry in generation_plan["entries"]
    }
    for case in cases:
        case_id = str(case["case_id"])
        prepared = prepare_pilot_case(
            case,
            profile,
            grid,
            global_seed=int(plan["global_seed"]),
            base_histories=int(execution["base_histories_per_projection"]),
            work_dir=work_root / "inputs" / case_id,
        )
        run = run_simind_case(
            SimindRunSpec(
                case_id=case_id,
                simind_exe=args.simind_exe,
                smc_file=paths["smc_path"],
                simind_ini=paths["simind_ini_path"],
                source_bin=prepared.source_bin,
                density_bin=prepared.density_bin,
                output_root=work_root / "simind",
                rr_seed=prepared.seeds.simind,
                nn_multiplier=int(execution["nn_multiplier"]),
                expected_shape=(60, 128, 128),
                timeout_seconds=float(execution["timeout_seconds"]),
                environment_overrides={
                    "SMC_DIR": str(args.smc_dir.resolve()) + os.sep,
                },
            )
        )
        if not run.success:
            raise RuntimeError(
                f"{case_id}: SIMIND failed; diagnostics retained at {run.failure_dir}: "
                f"{run.error}"
            )
        metadata = build_completed_metadata(
            prepared,
            profile_path=paths["profile_path"],
            scanner_path=paths["scanner_path"],
            evidence_registry_path=paths["evidence_registry_path"],
            simind_ini_path=paths["simind_ini_path"],
            scanner=scanner,
            result=run,
            runtime_binding=runtime_document,
        )
        entry = entry_by_id[case_id]
        extra_artifacts = simind_extra_artifacts(prepared, run)
        extra_artifacts.update(
            {
                "pilot_plan": args.config.resolve(),
                "pilot_runtime": args.output_root / "PILOT_RUNTIME.json",
            }
        )
        payload = CasePayloadV2(
            case_id=case_id,
            case_family_id=str(entry["case_family_id"]),
            profile_id=profile.profile_id,
            dataset_id=str(plan["dataset_id"]),
            dataset_version=str(plan["dataset_version"]),
            dataset_role=str(plan["dataset_role"]),
            split=str(entry["split"]),
            population_weight=float(entry["population_weight"]),
            sampling_probability=float(entry["sampling_probability"]),
            arrays=prepared.arrays,
            metadata=metadata,
            extra_artifacts=extra_artifacts,
        )
        record = write_case_v2(payload, args.output_root)
        records.append(record)
        lesions = metadata["actual_metrics"]["tumors"]["lesions"]
        case_summaries.append(
            {
                "case_id": case_id,
                "split": record.split,
                "rr_seed": prepared.seeds.simind,
                "liver_morphology": prepared.patient.liver_morphology,
                "liver_fit_attempt": prepared.liver_fit_attempt,
                "liver_volume_ml": metadata["actual_metrics"]["liver"]["volume_ml"],
                "lesion_recist_3d_mm": [item["recist_3d_mm"] for item in lesions],
                "tumor_fraction_liver": metadata["actual_metrics"]["tumors"][
                    "tumor_union_fraction_liver"
                ],
                "injection_territory": metadata["activity"]["injection_territory"],
                "mismatch_challenge": metadata["activity"]["mismatch_challenge"],
                "projection_weight_sum": metadata["simulation"]["projection_stats"][
                    "projection_weight_sum"
                ],
                "simind_started_utc": run.started_utc,
                "simind_finished_utc": run.finished_utc,
                "status": "pass",
            }
        )
        atomic_write_json(
            work_root / "PROGRESS.json",
            {
                "status": "running",
                "completed_case_ids": [item["case_id"] for item in case_summaries],
                "case_summaries": case_summaries,
            },
        )

    audit_document = {
        "schema_version": PILOT_GATE_SCHEMA_VERSION,
        "status": "ready_for_dataset_freeze",
        "case_count": len(records),
        "case_summaries": case_summaries,
        "boundary_gates": boundary_results,
        "required_artifacts": list(REQUIRED_ARTIFACTS),
        "go_for_15_case_pilot": False,
        "reason": "Task 12 step 1 only; Generator and PAR-S_2 validators still required",
        "authority": "informational_only_not_a_gate_input",
    }
    atomic_write_json(args.output_root / "PILOT_GENERATION_AUDIT.json", audit_document)
    contract = DatasetContractV2(
        output_root=args.output_root,
        dataset_id=str(plan["dataset_id"]),
        dataset_version=str(plan["dataset_version"]),
        dataset_role=str(plan["dataset_role"]),
        expected_case_ids=tuple(case_id for case_id, _ in configured_pairs),
        allowed_profile_ids=(profile.profile_id,),
        split_plan_sha256=split_plan.sha256,
        required_artifact_names=REQUIRED_ARTIFACTS,
    )
    frozen = freeze_dataset(records, contract)
    atomic_write_json(
        work_root / "PROGRESS.json",
        {
            "status": "complete",
            "dataset_complete": frozen.to_dict(),
            "case_summaries": case_summaries,
        },
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "output_root": str(args.output_root.resolve()),
                "work_root": str(work_root.resolve()),
                "case_count": len(records),
                "manifest_sha256": frozen.manifest_sha256,
                "case_summaries": case_summaries,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
