"""Run, resume, atomically publish and freeze the 15-case Task-12 QA pilot."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_dataset_v2 import build_generation_plan, write_generation_plan  # noqa: E402

from core.case_writer_v2 import (  # noqa: E402
    DATASET_COMPLETE_FILENAME,
    CasePayloadV2,
    DatasetContractV2,
    freeze_dataset,
    write_case_v2,
    write_split_plan,
)
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot15_v2 import (  # noqa: E402
    PILOT15_CASE_COUNT,
    PILOT15_COVERAGE_LABEL,
    PILOT15_GATE_SCHEMA,
    PILOT15_PREFLIGHT_SCHEMA,
    PILOT15_PROGRESS_SCHEMA,
    PILOT15_RUNTIME_SCHEMA,
    classify_run_root,
    load_completed_records,
    next_attempt_dir,
    require_pilot15_coverage,
)
from core.pilot_v2 import (  # noqa: E402
    build_completed_metadata,
    load_pilot_plan,
    prepare_pilot_case,
    resolve_plan_path,
    simind_extra_artifacts,
    validate_boundary_rejections,
)
from core.provenance import (  # noqa: E402
    atomic_write_json,
    resolve_relative_path,
    sha256_file,
)
from core.reproducibility_v2 import (  # noqa: E402
    FrozenPreflightInputV2,
    capture_generator_source_binding,
    capture_python_runtime,
    load_and_validate_preflight_input_bundle,
    prove_preflight_byte_identity,
)
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402
from core.simind_exec import (  # noqa: E402
    SIMIND_PROTOCOL_NAME_V2,
    SimindRunResult,
    SimindRunSpec,
    run_simind_case,
)
from core.simind_postprocess import audit_simind_completion  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_pilot15")
DEFAULT_PREFLIGHT_REPORT = Path(
    r"D:\PFE-U\PAR\outputs\pars_v2_pilot15_preflight\PREFLIGHT.json"
)
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
    "pilot_preflight",
    "pilot_input_bundle",
    "preflight_byte_identity",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "pilot15_v2.json",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--preflight-report",
        type=Path,
        default=DEFAULT_PREFLIGHT_REPORT,
    )
    parser.add_argument("--simind-exe", type=Path, default=DEFAULT_SIMIND_EXE)
    parser.add_argument("--smc-dir", type=Path, default=DEFAULT_SMC_DIR)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--max-cases",
        type=int,
        help="Optional safe batch limit. An incomplete batch exits 3 and resumes with --resume.",
    )
    return parser


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


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
            "pilot15 execution requires a clean Generator worktree; commit all changes:\n"
            + status.stdout.rstrip()
        )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _resolve_paths(plan: Mapping[str, object]) -> dict[str, Path]:
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


def _validate_runtime_dependencies(
    args: argparse.Namespace,
    plan: Mapping[str, object],
) -> None:
    if not args.simind_exe.is_file():
        raise FileNotFoundError(f"SIMIND executable not found: {args.simind_exe}")
    if sha256_file(args.simind_exe) != plan["expected_simind_binary_sha256"]:
        raise RuntimeError("SIMIND executable hash differs from pilot15 plan")
    if not args.smc_dir.is_dir():
        raise FileNotFoundError(f"SIMIND SMC_DIR not found: {args.smc_dir}")
    if args.max_cases is not None and args.max_cases < 1:
        raise ValueError("--max-cases must be positive")


def _validate_preflight(
    path: Path,
    *,
    commit: str,
    config_path: Path,
    plan: Mapping[str, object],
    paths: Mapping[str, Path],
    simind_exe: Path,
    smc_dir: Path,
    python_runtime: Mapping[str, object],
    generator_source: Mapping[str, object],
) -> dict[str, object]:
    report = _read_json(path, "pilot15 preflight report")
    if report.get("schema_version") != PILOT15_PREFLIGHT_SCHEMA:
        raise RuntimeError("pilot15 preflight schema mismatch")
    if report.get("status") != "pass" or report.get("formal_runner_eligible") is not True:
        raise RuntimeError("pilot15 preflight is not formally eligible")
    if report.get("simind_launched") is not False:
        raise RuntimeError("pilot15 preflight incorrectly claims a SIMIND launch")
    expected = {
        "generator_git_commit": commit,
        "pilot_plan_sha256": sha256_file(config_path),
        "profile_sha256": sha256_file(paths["profile_path"]),
        "scanner_sha256": sha256_file(paths["scanner_path"]),
        "evidence_registry_sha256": sha256_file(paths["evidence_registry_path"]),
    }
    drifted = [name for name, value in expected.items() if report.get(name) != value]
    runtime = report.get("runtime")
    if not isinstance(runtime, Mapping):
        raise RuntimeError("pilot15 preflight runtime evidence is missing")
    if runtime.get("simind_binary_sha256") != plan["expected_simind_binary_sha256"]:
        drifted.append("simind_binary_sha256")
    if runtime.get("smc_sha256") != sha256_file(paths["smc_path"]):
        drifted.append("smc_sha256")
    if runtime.get("simind_ini_sha256") != sha256_file(paths["simind_ini_path"]):
        drifted.append("simind_ini_sha256")
    if runtime.get("simind_executable") != str(simind_exe.resolve()):
        drifted.append("simind_executable")
    if runtime.get("smc_dir") != str(smc_dir.resolve()):
        drifted.append("smc_dir")
    if report.get("python_runtime") != dict(python_runtime):
        drifted.append("python_runtime")
    if report.get("generator_source") != dict(generator_source):
        drifted.append("generator_source")
    if not isinstance(report.get("input_bundle"), Mapping):
        drifted.append("input_bundle")
    observed_ids = [
        str(item.get("case_id"))
        for item in report.get("cases", [])
        if isinstance(item, Mapping)
    ]
    expected_ids = [str(case["case_id"]) for case in plan["cases"]]
    if observed_ids != expected_ids:
        drifted.append("case_ids")
    if drifted:
        raise RuntimeError(f"pilot15 preflight bindings drifted: {sorted(set(drifted))}")
    return report


def _runtime_document(
    args: argparse.Namespace,
    plan: Mapping[str, object],
    paths: Mapping[str, Path],
    *,
    commit: str,
    preflight_path: Path,
    coverage: Mapping[str, object],
    boundary_gates: object,
    python_runtime: Mapping[str, object],
    generator_source: Mapping[str, object],
    input_bundle: Mapping[str, object],
) -> dict[str, object]:
    execution = plan["execution"]
    rr_by_case = {
        str(case["case_id"]): int(coverage["rr_by_case"][str(case["case_id"])])
        for case in plan["cases"]
    }
    return {
        "schema_version": PILOT15_RUNTIME_SCHEMA,
        "generator_git_commit": commit,
        "pilot_plan_sha256": sha256_file(args.config),
        "pilot_preflight_sha256": sha256_file(preflight_path),
        "profile_sha256": sha256_file(paths["profile_path"]),
        "scanner_sha256": sha256_file(paths["scanner_path"]),
        "evidence_registry_sha256": sha256_file(paths["evidence_registry_path"]),
        "smc_sha256": sha256_file(paths["smc_path"]),
        "simind_ini_sha256": sha256_file(paths["simind_ini_path"]),
        "simind_binary_sha256": sha256_file(args.simind_exe),
        "simind_executable": str(args.simind_exe.resolve()),
        "smc_dir": str(args.smc_dir.resolve()),
        "base_histories_per_projection": int(execution["base_histories_per_projection"]),
        "nn_multiplier": int(execution["nn_multiplier"]),
        "max_parallel": 1,
        "rr_allocator": str(execution["rr_allocator"]),
        "rr_by_case": rr_by_case,
        "timeout_seconds": float(execution["timeout_seconds"]),
        "boundary_gates": boundary_gates,
        "python_runtime": dict(python_runtime),
        "generator_source": dict(generator_source),
        "preflight_input_bundle": dict(input_bundle),
        "preflight_to_run_input_contract": {
            "comparison": "sha256_and_size_exact",
            "formal_simind_input": "frozen_preflight_bundle_bytes",
            "regenerated_input_action": "prove_identity_then_discard",
            "mismatch_action": "fail_before_simind_launch",
        },
        "resume_contract": {
            "completed_case_action": "verify_hashes_and_skip",
            "completed_simind_action": "verify_provenance_and_reuse",
            "failed_attempt_action": "retain_and_allocate_new_attempt",
            "progress_schema": PILOT15_PROGRESS_SCHEMA,
        },
    }


def _load_or_write_runtime(output_root: Path, expected: Mapping[str, object], state: str) -> None:
    path = output_root / "PILOT_RUNTIME.json"
    if state == "fresh":
        atomic_write_json(path, expected)
        return
    existing = _read_json(path, "pilot15 runtime")
    if existing != dict(expected):
        raise RuntimeError("pilot15 runtime binding changed; resume is forbidden")


def _bind_preflight_inputs(
    prepared: object,
    frozen: FrozenPreflightInputV2,
    *,
    evidence_path: Path,
) -> tuple[object, Path]:
    prove_preflight_byte_identity(
        generated_source=prepared.source_bin,
        generated_density=prepared.density_bin,
        frozen=frozen,
        generated_arrays=prepared.arrays,
        evidence_path=evidence_path,
    )
    return (
        replace(
            prepared,
            source_bin=frozen.source_path,
            density_bin=frozen.density_path,
        ),
        evidence_path,
    )


def _summary_from_record(record: object, output_root: Path) -> dict[str, object]:
    metadata_path = resolve_relative_path(
        record.artifacts["metadata_json"].relative_path,
        output_root,
    )
    metadata = _read_json(metadata_path, f"{record.case_id} metadata")
    provenance_path = resolve_relative_path(
        record.artifacts["simind_run_provenance"].relative_path,
        output_root,
    )
    provenance = _read_json(provenance_path, f"{record.case_id} SIMIND provenance")
    return {
        "case_id": record.case_id,
        "split": record.split,
        "rr_seed": metadata["physics"]["rr_seed"],
        "liver_morphology": metadata["patient"]["liver_morphology"],
        "liver_fit_attempt": metadata["quality_control"]["liver_fit_attempt"],
        "liver_volume_ml": metadata["actual_metrics"]["liver"]["volume_ml"],
        "lesion_recist_3d_mm": [
            item["recist_3d_mm"] for item in metadata["actual_metrics"]["tumors"]["lesions"]
        ],
        "tumor_fraction_liver": metadata["actual_metrics"]["tumors"][
            "tumor_union_fraction_liver"
        ],
        "injection_territory": metadata["activity"]["injection_territory"],
        "mismatch_challenge": metadata["activity"]["mismatch_challenge"],
        "projection_weight_sum": metadata["simulation"]["projection_stats"][
            "projection_weight_sum"
        ],
        "simind_started_utc": provenance["started_utc"],
        "simind_finished_utc": provenance["finished_utc"],
        "status": "pass",
    }


def _reuse_completed_simind(
    spec: SimindRunSpec,
) -> SimindRunResult | None:
    final_dir = spec.output_root / SIMIND_PROTOCOL_NAME_V2 / spec.case_id
    if not final_dir.exists():
        return None
    provenance = _read_json(final_dir / "run_provenance.json", "completed SIMIND provenance")
    audit = audit_simind_completion(
        final_dir / spec.case_id,
        expected_shape=spec.expected_shape,
        exit_code=int(provenance.get("exit_code", -1)),
    )
    expected = {
        "status": "complete",
        "case_id": spec.case_id,
        "rr_seed": spec.rr_seed,
        "nn_multiplier": spec.nn_multiplier,
        "binary_sha256": sha256_file(spec.simind_exe),
    }
    drifted = [name for name, value in expected.items() if provenance.get(name) != value]
    inputs = provenance.get("inputs")
    if not isinstance(inputs, Mapping):
        drifted.append("inputs")
    else:
        if inputs.get("source_sha256") != sha256_file(spec.source_bin):
            drifted.append("source_sha256")
        if inputs.get("density_sha256") != sha256_file(spec.density_bin):
            drifted.append("density_sha256")
    if drifted:
        raise RuntimeError(
            f"{spec.case_id}: completed SIMIND output cannot be reused; drifted={drifted}"
        )
    return SimindRunResult(
        case_id=spec.case_id,
        success=True,
        exit_code=0,
        command=tuple(str(value) for value in provenance["command"]),
        expected_shape=spec.expected_shape,
        started_utc=str(provenance["started_utc"]),
        finished_utc=str(provenance["finished_utc"]),
        final_dir=final_dir,
        output_hashes=dict(audit.sha256),
    )


def _progress(
    work_root: Path,
    *,
    status: str,
    records: list[object],
    summaries: list[dict[str, object]],
    current_case_id: str | None = None,
    error: str | None = None,
    dataset_complete: object | None = None,
) -> None:
    document: dict[str, object] = {
        "schema_version": PILOT15_PROGRESS_SCHEMA,
        "status": status,
        "completed_case_ids": [record.case_id for record in records],
        "completed_count": len(records),
        "total_count": PILOT15_CASE_COUNT,
        "remaining_count": PILOT15_CASE_COUNT - len(records),
        "case_summaries": summaries,
    }
    if current_case_id is not None:
        document["current_case_id"] = current_case_id
    if error is not None:
        document["error"] = error
    if dataset_complete is not None:
        document["dataset_complete"] = dataset_complete
    atomic_write_json(work_root / "PROGRESS.json", document)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = load_pilot_plan(args.config)
    paths = _resolve_paths(plan)
    _validate_runtime_dependencies(args, plan)
    commit = _git_commit()
    python_runtime = capture_python_runtime()
    generator_source = capture_generator_source_binding(REPO_ROOT)
    if generator_source.get("git_commit") != commit:
        raise RuntimeError("generator source binding commit changed during runner startup")
    if generator_source.get("worktree_clean") is not True:
        raise RuntimeError("formal runner requires a clean bound Generator source tree")
    registry = load_evidence_registry(paths["evidence_registry_path"])
    profile = load_profile(paths["profile_path"], registry)
    scanner = load_profile(paths["scanner_path"], registry)
    coverage = require_pilot15_coverage(plan, profile)
    boundary_gates = validate_boundary_rejections(plan, profile)
    preflight = _validate_preflight(
        args.preflight_report.resolve(),
        commit=commit,
        config_path=args.config.resolve(),
        plan=plan,
        paths=paths,
        simind_exe=args.simind_exe,
        smc_dir=args.smc_dir,
        python_runtime=python_runtime,
        generator_source=generator_source,
    )
    split_plan, generation_plan = build_generation_plan(
        dataset_id=str(plan["dataset_id"]),
        dataset_version=str(plan["dataset_version"]),
        dataset_role=str(plan["dataset_role"]),
        profile_id=profile.profile_id,
        case_count=PILOT15_CASE_COUNT,
        family_size=1,
        global_seed=int(plan["global_seed"]),
        ratios={key: float(value) for key, value in plan["split_ratios"].items()},
    )
    configured_pairs = [
        (str(case["case_id"]), str(case["case_family_id"]))
        for case in plan["cases"]
    ]
    planned_pairs = [
        (str(entry["case_id"]), str(entry["case_family_id"]))
        for entry in generation_plan["entries"]
    ]
    if configured_pairs != planned_pairs:
        raise RuntimeError("pilot15 case identities disagree with immutable generation plan")
    expected_ids = [case_id for case_id, _ in configured_pairs]
    input_bundle_reference = preflight.get("input_bundle")
    if not isinstance(input_bundle_reference, Mapping):
        raise RuntimeError("pilot15 preflight input bundle binding is missing")
    preflight_inputs = load_and_validate_preflight_input_bundle(
        args.preflight_report.resolve(),
        input_bundle_reference,
        expected_case_ids=expected_ids,
        case_summaries=[
            item for item in preflight.get("cases", []) if isinstance(item, Mapping)
        ],
    )
    work_root = args.output_root.parent / f"{args.output_root.name}_work"
    state = classify_run_root(args.output_root, work_root, resume=args.resume)
    if state == "fresh":
        args.output_root.mkdir(parents=True, exist_ok=False)
        work_root.mkdir(parents=True, exist_ok=False)
    write_split_plan(split_plan, args.output_root)
    write_generation_plan(generation_plan, args.output_root)
    runtime_document = _runtime_document(
        args,
        plan,
        paths,
        commit=commit,
        preflight_path=args.preflight_report.resolve(),
        coverage=coverage,
        boundary_gates=boundary_gates,
        python_runtime=python_runtime,
        generator_source=generator_source,
        input_bundle=input_bundle_reference,
    )
    if runtime_document["pilot_preflight_sha256"] != sha256_file(args.preflight_report):
        raise RuntimeError("pilot15 preflight changed during runner initialization")
    if preflight.get("split_plan_sha256") != split_plan.sha256:
        raise RuntimeError("pilot15 preflight split plan binding mismatch")
    if preflight.get("generation_plan_sha256") != generation_plan["sha256"]:
        raise RuntimeError("pilot15 preflight generation plan binding mismatch")
    _load_or_write_runtime(args.output_root, runtime_document, state)

    records = load_completed_records(args.output_root, expected_ids)
    summaries = [_summary_from_record(record, args.output_root) for record in records]
    entry_by_id = {
        str(entry["case_id"]): entry for entry in generation_plan["entries"]
    }
    if (args.output_root / DATASET_COMPLETE_FILENAME).exists():
        contract = DatasetContractV2(
            output_root=args.output_root,
            dataset_id=str(plan["dataset_id"]),
            dataset_version=str(plan["dataset_version"]),
            dataset_role=str(plan["dataset_role"]),
            expected_case_ids=tuple(expected_ids),
            allowed_profile_ids=(profile.profile_id,),
            split_plan_sha256=split_plan.sha256,
            required_artifact_names=REQUIRED_ARTIFACTS,
        )
        frozen = freeze_dataset(records, contract)
        _progress(
            work_root,
            status="complete",
            records=records,
            summaries=summaries,
            dataset_complete=frozen.to_dict(),
        )
        print(json.dumps({"status": "already_complete", "case_count": len(records)}))
        return 0

    completed_ids = {record.case_id for record in records}
    pending = [case for case in plan["cases"] if str(case["case_id"]) not in completed_ids]
    processed_this_run = 0
    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    execution = plan["execution"]
    for case in pending:
        if args.max_cases is not None and processed_this_run >= args.max_cases:
            _progress(work_root, status="paused", records=records, summaries=summaries)
            print(json.dumps({"status": "paused", "completed_count": len(records)}))
            return 3
        case_id = str(case["case_id"])
        _progress(
            work_root,
            status="running",
            records=records,
            summaries=summaries,
            current_case_id=case_id,
        )
        try:
            attempt_dir = next_attempt_dir(work_root, case_id)
            prepared = prepare_pilot_case(
                case,
                profile,
                grid,
                global_seed=int(plan["global_seed"]),
                base_histories=int(execution["base_histories_per_projection"]),
                work_dir=attempt_dir,
                coverage_label=PILOT15_COVERAGE_LABEL,
            )
            prepared, byte_identity_path = _bind_preflight_inputs(
                prepared,
                preflight_inputs[case_id],
                evidence_path=attempt_dir / "PREFLIGHT_BYTE_IDENTITY.json",
            )
            spec = SimindRunSpec(
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
                environment_overrides={"SMC_DIR": str(args.smc_dir.resolve()) + os.sep},
            )
            run = _reuse_completed_simind(spec) or run_simind_case(spec)
            if not run.success:
                raise RuntimeError(
                    f"SIMIND failed; diagnostics={run.failure_dir}; error={run.error}"
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
                    "pilot_preflight": args.preflight_report.resolve(),
                    "pilot_input_bundle": preflight_inputs[
                        case_id
                    ].bundle_manifest_path,
                    "preflight_byte_identity": byte_identity_path,
                }
            )
            record = write_case_v2(
                CasePayloadV2(
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
                ),
                args.output_root,
            )
            records.append(record)
            records.sort(key=lambda item: item.case_id)
            summaries = [_summary_from_record(item, args.output_root) for item in records]
            processed_this_run += 1
            _progress(
                work_root,
                status="running",
                records=records,
                summaries=summaries,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _progress(
                work_root,
                status="failed",
                records=records,
                summaries=summaries,
                current_case_id=case_id,
                error=error,
            )
            print(json.dumps({"status": "failed", "case_id": case_id, "error": error}))
            return 1

    audit_document = {
        "schema_version": PILOT15_GATE_SCHEMA,
        "status": "ready_for_dataset_freeze",
        "case_count": len(records),
        "case_summaries": summaries,
        "coverage": coverage,
        "boundary_gates": boundary_gates,
        "required_artifacts": list(REQUIRED_ARTIFACTS),
        "go_for_50_case_pilot": False,
        "reason": "15-case post-generation validation, visualization and manual QA still required",
        "authority": "informational_only_not_a_gate_input",
    }
    atomic_write_json(args.output_root / "PILOT_GENERATION_AUDIT.json", audit_document)
    contract = DatasetContractV2(
        output_root=args.output_root,
        dataset_id=str(plan["dataset_id"]),
        dataset_version=str(plan["dataset_version"]),
        dataset_role=str(plan["dataset_role"]),
        expected_case_ids=tuple(expected_ids),
        allowed_profile_ids=(profile.profile_id,),
        split_plan_sha256=split_plan.sha256,
        required_artifact_names=REQUIRED_ARTIFACTS,
    )
    frozen = freeze_dataset(records, contract)
    _progress(
        work_root,
        status="complete",
        records=records,
        summaries=summaries,
        dataset_complete=frozen.to_dict(),
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "output_root": str(args.output_root.resolve()),
                "work_root": str(work_root.resolve()),
                "case_count": len(records),
                "manifest_sha256": frozen.manifest_sha256,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
