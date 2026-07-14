"""Run non-destructive NN=5 alignment companions for the frozen V2 pilot.

The Task 11 uniqueness thresholds were calibrated with 80,000 base source
histories and ``/NN=5``.  The formal three-case Task 12 smoke dataset is
deliberately ``/NN=1`` and must never be overwritten.  This runner reuses the
frozen phantom/source/density bytes, writes a separate SIMIND result tree, and
emits a standard PAR-S alignment descriptor only after all three companions
complete successfully.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.provenance import atomic_write_json, sha256_file  # noqa: E402
from core.schemas_v2 import FROZEN_PROJECTION_COORDINATES_V1  # noqa: E402
from core.simind_exec import SimindRunSpec, run_simind_case  # noqa: E402


DESCRIPTOR_SCHEMA = "pars_projection_alignment_cases_v1"
PLAN_SCHEMA = "pars_v2_pilot3_alignment_companion_plan_v1"
COMPLETE_SCHEMA = "pars_v2_pilot3_alignment_companion_complete_v1"
ALIGNMENT_NN_MULTIPLIER = 5
BASE_HISTORIES_PER_PROJECTION = 80_000
EXPECTED_CASE_IDS = ("case_00000", "case_00001", "case_00002")
EXPECTED_SHAPE = (60, 128, 128)
DEFAULT_DATASET_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_pilot3_r2")
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\pars_v2_pilot3_r2_alignment_nn5"
)
DEFAULT_SIMIND_EXE = Path(r"D:\PFE-U\PAR-S-Generator\simind\simind.exe")
DEFAULT_SMC_DIR = Path(r"C:\simind\smc_dir")


@dataclass(frozen=True)
class FrozenAlignmentCase:
    case_id: str
    phantom_npz: Path
    source_bin: Path
    density_bin: Path
    metadata_json: Path
    rr_seed: int
    binary_sha256: str
    smc_sha256: str
    simind_ini_sha256: str


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _inside(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} relative_path must be a non-empty string")
    root = root.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes the frozen dataset") from exc
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    return path


def _artifact(
    dataset_root: Path,
    record: Mapping[str, object],
    name: str,
) -> Path:
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, Mapping) or name not in artifacts:
        raise ValueError(f"{record.get('case_id')}: missing artifact {name}")
    raw = artifacts[name]
    if not isinstance(raw, Mapping):
        raise ValueError(f"{record.get('case_id')}: invalid artifact {name}")
    path = _inside(dataset_root, raw.get("relative_path"), name)
    expected_hash = raw.get("sha256")
    if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
        raise ValueError(f"{record.get('case_id')}: {name} hash mismatch")
    return path


def load_frozen_alignment_cases(
    dataset_root: str | Path,
) -> tuple[list[FrozenAlignmentCase], dict[str, object]]:
    """Verify the frozen NN=1 pilot and return immutable companion inputs."""

    root = Path(dataset_root).resolve()
    marker_path = root / "DATASET_COMPLETE.json"
    manifest_path = root / "case_manifest.jsonl"
    marker = _read_json(marker_path, "dataset completion marker")
    if marker.get("status") != "complete" or marker.get("case_count") != 3:
        raise ValueError("alignment companion requires a complete three-case dataset")
    if marker.get("manifest_relative_path") != "case_manifest.jsonl":
        raise ValueError("dataset completion marker names an unexpected manifest")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest is missing: {manifest_path}")
    manifest_sha256 = sha256_file(manifest_path)
    if marker.get("manifest_sha256") != manifest_sha256:
        raise ValueError("frozen manifest hash does not match DATASET_COMPLETE.json")
    if (
        marker.get("projection_coordinate_contract_id")
        != FROZEN_PROJECTION_COORDINATES_V1.coordinate_contract_id
        or marker.get("loader_transform_id")
        != FROZEN_PROJECTION_COORDINATES_V1.loader_transform_id
    ):
        raise ValueError("frozen dataset uses an unexpected projection contract")

    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid manifest JSON at line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"manifest line {line_number} must be an object")
        records.append(value)
    if tuple(sorted(str(record.get("case_id")) for record in records)) != EXPECTED_CASE_IDS:
        raise ValueError("frozen manifest case IDs do not match the pilot contract")

    cases: list[FrozenAlignmentCase] = []
    for record in sorted(records, key=lambda value: str(value["case_id"])):
        case_id = str(record["case_id"])
        metadata_path = _artifact(root, record, "metadata_json")
        metadata = _read_json(metadata_path, f"{case_id} metadata")
        physics = metadata.get("physics")
        simulation = metadata.get("simulation")
        acquisition = metadata.get("acquisition")
        if not all(
            isinstance(value, Mapping)
            for value in (physics, simulation, acquisition)
        ):
            raise ValueError(f"{case_id}: incomplete physics/simulation/acquisition")
        assert isinstance(physics, Mapping)
        assert isinstance(simulation, Mapping)
        assert isinstance(acquisition, Mapping)
        if physics.get("base_histories_per_projection") != BASE_HISTORIES_PER_PROJECTION:
            raise ValueError(f"{case_id}: unexpected base histories")
        if physics.get("nn_multiplier") != 1:
            raise ValueError(f"{case_id}: source pilot is not the frozen NN=1 smoke")
        rr_seed = physics.get("rr_seed")
        if not isinstance(rr_seed, int) or isinstance(rr_seed, bool) or not 1 <= rr_seed <= 10_007:
            raise ValueError(f"{case_id}: invalid practical /RR seed")
        coordinates = acquisition.get("projection_coordinates")
        if coordinates != FROZEN_PROJECTION_COORDINATES_V1.to_dict():
            raise ValueError(f"{case_id}: projection coordinate metadata drift")
        cases.append(
            FrozenAlignmentCase(
                case_id=case_id,
                phantom_npz=_artifact(root, record, "phantom_npz"),
                source_bin=_artifact(root, record, "simind_source_bin"),
                density_bin=_artifact(root, record, "simind_density_bin"),
                metadata_json=metadata_path,
                rr_seed=rr_seed,
                binary_sha256=str(simulation.get("binary_sha256", "")),
                smc_sha256=str(simulation.get("smc_snapshot_sha256", "")),
                simind_ini_sha256=str(
                    simulation.get("simind_ini_snapshot_sha256", "")
                ),
            )
        )
    if len({case.rr_seed for case in cases}) != len(cases):
        raise ValueError("pilot /RR seeds are not unique")
    return cases, {
        "dataset_root": str(root),
        "dataset_id": marker.get("dataset_id"),
        "dataset_version": marker.get("dataset_version"),
        "manifest_sha256": manifest_sha256,
        "completion_marker_sha256": sha256_file(marker_path),
    }


def _clean_git_commit() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise RuntimeError("alignment companion requires a clean Generator worktree")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return head.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run NN=5 coordinate companions without modifying the frozen pilot."
    )
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--simind-exe", type=Path, default=DEFAULT_SIMIND_EXE)
    parser.add_argument("--smc-dir", type=Path, default=DEFAULT_SMC_DIR)
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be positive")
    if args.output_root.exists():
        raise FileExistsError("alignment companion output is immutable; choose a fresh root")
    cases, dataset_binding = load_frozen_alignment_cases(args.dataset_root)
    simind_exe = args.simind_exe.resolve()
    smc_path = (REPO_ROOT / "simind" / "ge870_czt.smc").resolve()
    simind_ini = (REPO_ROOT / "configs" / "simind_v2.ini").resolve()
    if not simind_exe.is_file() or not smc_path.is_file() or not simind_ini.is_file():
        raise FileNotFoundError("SIMIND executable, frozen SMC or simind.ini is missing")
    if not args.smc_dir.is_dir():
        raise FileNotFoundError(f"SIMIND SMC_DIR is missing: {args.smc_dir}")
    binary_sha256 = sha256_file(simind_exe)
    smc_sha256 = sha256_file(smc_path)
    ini_sha256 = sha256_file(simind_ini)
    for case in cases:
        if (
            case.binary_sha256 != binary_sha256
            or case.smc_sha256 != smc_sha256
            or case.simind_ini_sha256 != ini_sha256
        ):
            raise ValueError(f"{case.case_id}: companion runtime hash drift")
    generator_commit = _clean_git_commit()

    args.output_root.mkdir(parents=True, exist_ok=False)
    plan = {
        "schema_version": PLAN_SCHEMA,
        **dataset_binding,
        "generator_git_commit": generator_commit,
        "base_histories_per_projection": BASE_HISTORIES_PER_PROJECTION,
        "nn_multiplier": ALIGNMENT_NN_MULTIPLIER,
        "purpose": "match_the_actual_Task11_400k_history_threshold_baseline",
        "simind_binary_sha256": binary_sha256,
        "smc_sha256": smc_sha256,
        "simind_ini_sha256": ini_sha256,
        "case_rr": {case.case_id: case.rr_seed for case in cases},
    }
    atomic_write_json(args.output_root / "ALIGNMENT_COMPANION_PLAN.json", plan)

    descriptor_cases: list[dict[str, str]] = []
    result_records: list[dict[str, object]] = []
    for case in cases:
        result = run_simind_case(
            SimindRunSpec(
                case_id=case.case_id,
                simind_exe=simind_exe,
                smc_file=smc_path,
                simind_ini=simind_ini,
                source_bin=case.source_bin,
                density_bin=case.density_bin,
                output_root=args.output_root / "simind",
                rr_seed=case.rr_seed,
                nn_multiplier=ALIGNMENT_NN_MULTIPLIER,
                expected_shape=EXPECTED_SHAPE,
                timeout_seconds=float(args.timeout_seconds),
                environment_overrides={
                    "SMC_DIR": str(args.smc_dir.resolve()) + os.sep,
                },
            )
        )
        if not result.success or result.final_dir is None:
            raise RuntimeError(
                f"{case.case_id}: NN=5 companion failed; retained at "
                f"{result.failure_dir}: {result.error}"
            )
        stem = Path(result.final_dir) / case.case_id
        descriptor_cases.append(
            {
                "case_id": case.case_id,
                "phantom_npz": str(case.phantom_npz),
                "projection_a00": str(stem.with_suffix(".a00")),
                "projection_mhd": str(stem.with_suffix(".mhd")),
            }
        )
        result_records.append(
            {
                "case_id": case.case_id,
                "rr_seed": case.rr_seed,
                "started_utc": result.started_utc,
                "finished_utc": result.finished_utc,
                "a00_sha256": sha256_file(stem.with_suffix(".a00")),
                "mhd_sha256": sha256_file(stem.with_suffix(".mhd")),
                "res_sha256": sha256_file(stem.with_suffix(".res")),
                "spe_sha256": sha256_file(stem.with_suffix(".spe")),
                "run_provenance_sha256": sha256_file(
                    Path(result.final_dir) / "run_provenance.json"
                ),
            }
        )

    descriptor = {
        "schema_version": DESCRIPTOR_SCHEMA,
        "projection_coordinates": FROZEN_PROJECTION_COORDINATES_V1.to_dict(),
        "cases": descriptor_cases,
    }
    descriptor_path = args.output_root / "projection_alignment_cases.json"
    atomic_write_json(descriptor_path, descriptor)
    complete = {
        "schema_version": COMPLETE_SCHEMA,
        "status": "complete",
        **dataset_binding,
        "generator_git_commit": generator_commit,
        "nn_multiplier": ALIGNMENT_NN_MULTIPLIER,
        "descriptor_path": str(descriptor_path.resolve()),
        "descriptor_sha256": sha256_file(descriptor_path),
        "case_results": result_records,
    }
    atomic_write_json(args.output_root / "ALIGNMENT_COMPANION_COMPLETE.json", complete)
    print(json.dumps(complete, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
