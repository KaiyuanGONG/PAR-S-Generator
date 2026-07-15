"""Run a real one-case, no-SIMIND Task-12C byte-reproducibility fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot15_v2 import PILOT15_COVERAGE_LABEL  # noqa: E402
from core.pilot_v2 import (  # noqa: E402
    load_pilot_plan,
    prepare_pilot_case,
    resolve_plan_path,
)
from core.provenance import atomic_write_bytes, atomic_write_json, sha256_file  # noqa: E402
from core.reproducibility_v2 import (  # noqa: E402
    array_manifest,
    capture_generator_source_binding,
    capture_python_runtime,
    load_and_validate_preflight_input_bundle,
    prove_preflight_byte_identity,
    write_preflight_input_bundle,
)
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


SCHEMA_VERSION = "pars_v2_task12c_reproducibility_fixture_v1"
DEFAULT_OUTPUT_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12c_fixture_v2")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "pilot15_v2.json",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--case-id", default="case_00000")
    parser.add_argument("--allow-dirty", action="store_true")
    return parser


def _markdown(report: dict[str, object]) -> str:
    return "\n".join(
        [
            "# PAR-S V2 Task 12C reproducibility fixture",
            "",
            f"- Status: **{str(report['status']).upper()}**",
            f"- Formal eligible: `{report['formal_eligible']}`",
            f"- Case: `{report['case_id']}`",
            f"- Python/Conda binding: `{report['python_runtime']['binding_sha256']}`",
            f"- Generator source binding: `{report['generator_source']['binding_sha256']}`",
            f"- Input bundle: `{report['input_bundle']['manifest_sha256']}`",
            f"- Source/density byte identity: `{report['byte_identity']['status']}`",
            f"- All phantom arrays byte-identical: `{report['all_arrays_byte_identical']}`",
            "- SIMIND launched: `false`",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output_root.resolve()
    if output.exists():
        raise FileExistsError(f"fixture output root already exists: {output}")

    plan = load_pilot_plan(args.config)
    cases = {
        str(case["case_id"]): case
        for case in plan["cases"]
        if isinstance(case, dict)
    }
    if args.case_id not in cases:
        raise ValueError(f"fixture case not found in plan: {args.case_id}")
    paths = {
        key: resolve_plan_path(REPO_ROOT, plan[key], key)
        for key in ("profile_path", "scanner_path", "evidence_registry_path")
    }
    registry = load_evidence_registry(paths["evidence_registry_path"])
    profile = load_profile(paths["profile_path"], registry)
    scanner = load_profile(paths["scanner_path"], registry)
    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    python_runtime_before = capture_python_runtime()
    generator_source_before = capture_generator_source_binding(REPO_ROOT)
    if not generator_source_before["worktree_clean"] and not args.allow_dirty:
        raise RuntimeError("formal Task-12C fixture requires a clean Generator worktree")

    output.mkdir(parents=True, exist_ok=False)
    case = cases[args.case_id]
    execution = plan["execution"]
    first = prepare_pilot_case(
        case,
        profile,
        grid,
        global_seed=int(plan["global_seed"]),
        base_histories=int(execution["base_histories_per_projection"]),
        work_dir=output / "cases" / args.case_id,
        coverage_label=PILOT15_COVERAGE_LABEL,
    )
    summaries = [
        {
            "case_id": args.case_id,
            "source_sha256": sha256_file(first.source_bin),
            "density_sha256": sha256_file(first.density_bin),
            "array_manifest": array_manifest(first.arrays),
        }
    ]
    input_bundle = write_preflight_input_bundle(output, summaries)
    frozen = load_and_validate_preflight_input_bundle(
        output / "TASK12C_REPRODUCIBILITY.json",
        input_bundle,
        expected_case_ids=[args.case_id],
        case_summaries=summaries,
    )[args.case_id]
    second = prepare_pilot_case(
        case,
        profile,
        grid,
        global_seed=int(plan["global_seed"]),
        base_histories=int(execution["base_histories_per_projection"]),
        work_dir=output / "regenerated" / args.case_id,
        coverage_label=PILOT15_COVERAGE_LABEL,
    )
    byte_identity = prove_preflight_byte_identity(
        generated_source=second.source_bin,
        generated_density=second.density_bin,
        frozen=frozen,
        generated_arrays=second.arrays,
        evidence_path=output / "PREFLIGHT_BYTE_IDENTITY.json",
    )
    first_arrays = array_manifest(first.arrays)
    second_arrays = array_manifest(second.arrays)
    arrays_identical = first_arrays == second_arrays
    python_runtime_after = capture_python_runtime()
    generator_source_after = capture_generator_source_binding(REPO_ROOT)
    runtime_stable = python_runtime_before == python_runtime_after
    source_stable = generator_source_before == generator_source_after
    conda_prefix_aligned = bool(
        python_runtime_before["conda"]["prefix_matches_python_prefix"]
    )
    formal_eligible = (
        bool(generator_source_before["worktree_clean"])
        and conda_prefix_aligned
        and not args.allow_dirty
    )
    status = (
        "pass"
        if byte_identity["status"] == "pass"
        and arrays_identical
        and runtime_stable
        and source_stable
        else "fail"
    )
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "formal_eligible": formal_eligible and status == "pass",
        "case_id": args.case_id,
        "simind_launched": False,
        "pilot_plan_sha256": sha256_file(args.config),
        "python_runtime": python_runtime_before,
        "python_runtime_stable_within_fixture": runtime_stable,
        "conda_prefix_aligned_with_python_prefix": conda_prefix_aligned,
        "generator_source": generator_source_before,
        "generator_source_stable_within_fixture": source_stable,
        "input_bundle": input_bundle,
        "byte_identity": byte_identity,
        "first_array_manifest": first_arrays,
        "second_array_manifest": second_arrays,
        "all_arrays_byte_identical": arrays_identical,
    }
    atomic_write_json(output / "TASK12C_REPRODUCIBILITY.json", report)
    atomic_write_bytes(
        output / "TASK12C_REPRODUCIBILITY.md",
        _markdown(report).encode("utf-8"),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["formal_eligible"] else (0 if args.allow_dirty and status == "pass" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
