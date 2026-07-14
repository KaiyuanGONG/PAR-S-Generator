"""Run three reproducible full-physics SIMIND coordinate fixtures.

The fixture activity is deliberately sparse and asymmetric while attenuation is
zero.  This isolates geometry/storage alignment without bypassing the frozen
SIMIND physics configuration or the production C-order voxel-source writer.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Mapping

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.interfile_writer import (  # noqa: E402
    write_attenuation_map_v2,
    write_voxel_source,
)
from core.provenance import atomic_write_json, sha256_file  # noqa: E402
from core.schemas_v2 import FROZEN_PROJECTION_COORDINATES_V1  # noqa: E402
from core.simind_exec import (  # noqa: E402
    SIMIND_PROTOCOL_NAME_V2,
    SimindRunResult,
    SimindRunSpec,
    run_simind_case,
)
from core.simind_postprocess import audit_simind_completion  # noqa: E402


PLAN_SCHEMA = "pars_projection_coordinate_fixtures_v2"
DESCRIPTOR_SCHEMA = "pars_projection_alignment_cases_v1"
COMPLETE_SCHEMA = "pars_projection_coordinate_fixtures_complete_v2"
SHAPE_ZYX = (128, 128, 128)
PROJECTION_SHAPE_VYX = (60, 128, 128)
BASE_HISTORIES = 80_000
NN_MULTIPLIER = 5
RR_MAXIMUM = 10_007
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

DEFAULT_OUTPUT_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\projection_coordinate_fixtures_v2"
)
DEFAULT_SIMIND_EXE = Path(r"D:\PFE-U\PAR-S-Generator\simind\simind.exe")
DEFAULT_SMC_DIR = Path(r"C:\simind\smc_dir")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen three-case SIMIND projection-coordinate fixtures."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "projection_coordinate_fixtures_v2.json",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--simind-exe", type=Path, default=DEFAULT_SIMIND_EXE)
    parser.add_argument("--smc-dir", type=Path, default=DEFAULT_SMC_DIR)
    return parser


def load_fixture_plan(path: str | Path) -> dict[str, object]:
    plan_path = Path(path)
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read fixture plan: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != PLAN_SCHEMA:
        raise ValueError(f"fixture schema_version must be {PLAN_SCHEMA}")
    required = {
        "schema_version",
        "shape_zyx",
        "expected_projection_shape_vyx",
        "base_histories_per_projection",
        "nn_multiplier",
        "rr_maximum",
        "timeout_seconds",
        "smc_path",
        "smc_sha256",
        "simind_ini_path",
        "simind_ini_sha256",
        "expected_simind_binary_sha256",
        "projection_coordinates",
        "cases",
    }
    if set(raw) != required:
        raise ValueError("fixture plan fields do not match the frozen schema")
    if tuple(raw["shape_zyx"]) != SHAPE_ZYX:
        raise ValueError(f"shape_zyx must be {SHAPE_ZYX}")
    if tuple(raw["expected_projection_shape_vyx"]) != PROJECTION_SHAPE_VYX:
        raise ValueError(
            f"expected_projection_shape_vyx must be {PROJECTION_SHAPE_VYX}"
        )
    if raw["base_histories_per_projection"] != BASE_HISTORIES:
        raise ValueError(f"base histories must be {BASE_HISTORIES}")
    if raw["nn_multiplier"] != NN_MULTIPLIER:
        raise ValueError(
            "coordinate fixtures require /NN=5 (80k base x 5 ~= 400k photons/view)"
        )
    if raw["rr_maximum"] != RR_MAXIMUM:
        raise ValueError(f"rr_maximum must be {RR_MAXIMUM}")
    if raw["projection_coordinates"] != FROZEN_PROJECTION_COORDINATES_V1.to_dict():
        raise ValueError("projection_coordinates drifted from the frozen contract")
    timeout = raw["timeout_seconds"]
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout_seconds must be positive")

    cases = raw["cases"]
    if not isinstance(cases, list) or len(cases) != 3:
        raise ValueError("fixture plan must contain exactly three cases")
    case_ids: list[str] = []
    rr_values: list[int] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"case_id", "rr_seed", "spots"}:
            raise ValueError("fixture case fields must be case_id, rr_seed and spots")
        case_id = case["case_id"]
        rr_seed = case["rr_seed"]
        spots = case["spots"]
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            raise ValueError("fixture case_id is invalid")
        if not isinstance(rr_seed, int) or isinstance(rr_seed, bool) or not 1 <= rr_seed <= RR_MAXIMUM:
            raise ValueError("fixture rr_seed must be an integer in [1, 10007]")
        if not isinstance(spots, list) or not 3 <= len(spots) <= 8:
            raise ValueError("each fixture requires three to eight sparse spots")
        centers: list[tuple[int, int, int]] = []
        weights: list[float] = []
        for spot in spots:
            if not isinstance(spot, dict) or set(spot) != {
                "index_zyx",
                "radius_vox",
                "relative_weight",
            }:
                raise ValueError("spot fields must be index_zyx/radius_vox/relative_weight")
            center = spot["index_zyx"]
            radius = spot["radius_vox"]
            weight = spot["relative_weight"]
            if (
                not isinstance(center, list)
                or len(center) != 3
                or any(not isinstance(value, int) or isinstance(value, bool) for value in center)
                or not isinstance(radius, int)
                or isinstance(radius, bool)
                or not 3 <= radius <= 5
                or any(value - radius < 0 or value + radius >= 128 for value in center)
                or not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or float(weight) <= 0
            ):
                raise ValueError("spot geometry/weight violates the sparse fixture contract")
            centers.append(tuple(center))
            weights.append(float(weight))
        if len(set(centers)) != len(centers) or len(set(weights)) != len(weights):
            raise ValueError("spot centers and weights must be unique for asymmetry")
        case_ids.append(case_id)
        rr_values.append(rr_seed)
    if len(set(case_ids)) != 3 or len(set(rr_values)) != 3:
        raise ValueError("fixture case IDs and /RR values must be unique")
    return raw


def _resolve_repo_file(value: object, expected_hash: object, field: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f"{field} must be a repository-relative path")
    path = (REPO_ROOT / value).resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{field} escapes the repository") from exc
    if not path.is_file():
        raise FileNotFoundError(f"{field} not found: {path}")
    if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
        raise ValueError(f"{field} hash differs from the frozen fixture plan")
    return path


def _git_identity() -> dict[str, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise RuntimeError(
            "coordinate calibration requires a clean committed Generator worktree:\n"
            + status.stdout.rstrip()
        )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {"generator_git_commit": commit, "generator_git_tree": tree}


def _write_deterministic_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for key in sorted(arrays):
            buffer = io.BytesIO()
            np.lib.format.write_array(buffer, np.asarray(arrays[key]), allow_pickle=False)
            info = zipfile.ZipInfo(f"{key}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, buffer.getvalue(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def prepare_fixture_case(
    case: Mapping[str, object],
    case_dir: Path,
) -> dict[str, object]:
    case_id = str(case["case_id"])
    case_dir.mkdir(parents=True, exist_ok=False)
    activity = np.zeros(SHAPE_ZYX, dtype=np.float32)
    zz, yy, xx = np.ogrid[:128, :128, :128]
    for spot in case["spots"]:  # type: ignore[index]
        center = tuple(int(value) for value in spot["index_zyx"])
        radius = int(spot["radius_vox"])
        support = (
            (zz - center[0]) ** 2
            + (yy - center[1]) ** 2
            + (xx - center[2]) ** 2
            <= radius**2
        )
        activity[support] = float(spot["relative_weight"])
    source = write_voxel_source(
        activity,
        case_dir / case_id,
        base_histories=BASE_HISTORIES,
    )
    mu_true = np.zeros(SHAPE_ZYX, dtype=np.float32)
    density = write_attenuation_map_v2(
        mu_true,
        case_dir / case_id,
        semantic_key="mu_true_140kev",
    )
    source_weights = np.fromfile(source.path, dtype="<f4").reshape(SHAPE_ZYX)
    phantom_path = case_dir / "phantom.npz"
    _write_deterministic_npz(
        phantom_path,
        {
            "simind_source_weights": source_weights,
            "mu_true_140kev": mu_true,
        },
    )
    return {
        "case_id": case_id,
        "rr_seed": int(case["rr_seed"]),
        "phantom_npz": phantom_path,
        "source_bin": source.path,
        "density_bin": density.path,
        "source_sum": float(source_weights.sum(dtype=np.float64)),
        "nonzero_voxels": int(np.count_nonzero(source_weights)),
    }


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _collect_success(
    prepared: Mapping[str, object],
    result: SimindRunResult,
    output_root: Path,
) -> tuple[dict[str, str], dict[str, object]]:
    case_id = str(prepared["case_id"])
    if not result.success or result.final_dir is None or result.exit_code != 0:
        raise RuntimeError(
            f"{case_id}: SIMIND failed; diagnostics retained at {result.failure_dir}: {result.error}"
        )
    final_dir = Path(result.final_dir)
    output_stem = final_dir / case_id
    audit = audit_simind_completion(
        output_stem,
        expected_shape=PROJECTION_SHAPE_VYX,
        exit_code=0,
    )
    provenance_path = final_dir / "run_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if (
        provenance.get("status") != "complete"
        or provenance.get("case_id") != case_id
        or provenance.get("rr_seed") != prepared["rr_seed"]
        or provenance.get("nn_multiplier") != NN_MULTIPLIER
        or provenance.get("completion_audit") != audit.to_dict()
    ):
        raise RuntimeError(f"{case_id}: SIMIND provenance does not bind the fixture run")
    descriptor_case = {
        "case_id": case_id,
        "phantom_npz": _relative(Path(prepared["phantom_npz"]), output_root),
        "projection_a00": _relative(output_stem.with_suffix(".a00"), output_root),
        "projection_mhd": _relative(output_stem.with_suffix(".mhd"), output_root),
    }
    record = {
        "case_id": case_id,
        "rr_seed": prepared["rr_seed"],
        "nn_multiplier": NN_MULTIPLIER,
        "base_histories_per_projection": BASE_HISTORIES,
        "source_sum": prepared["source_sum"],
        "nonzero_voxels": prepared["nonzero_voxels"],
        "phantom_npz": descriptor_case["phantom_npz"],
        "phantom_npz_sha256": sha256_file(Path(prepared["phantom_npz"])),
        "source_bin": _relative(Path(prepared["source_bin"]), output_root),
        "source_bin_sha256": sha256_file(Path(prepared["source_bin"])),
        "density_bin": _relative(Path(prepared["density_bin"]), output_root),
        "density_bin_sha256": sha256_file(Path(prepared["density_bin"])),
        "run_provenance": _relative(provenance_path, output_root),
        "run_provenance_sha256": sha256_file(provenance_path),
        "quartet_sha256": dict(audit.sha256),
    }
    return descriptor_case, record


def run_fixture_suite(
    *,
    config_path: Path,
    output_root: Path,
    simind_exe: Path,
    smc_dir: Path,
) -> dict[str, object]:
    plan = load_fixture_plan(config_path)
    smc_path = _resolve_repo_file(plan["smc_path"], plan["smc_sha256"], "smc_path")
    ini_path = _resolve_repo_file(
        plan["simind_ini_path"], plan["simind_ini_sha256"], "simind_ini_path"
    )
    if not simind_exe.is_file():
        raise FileNotFoundError(f"SIMIND executable not found: {simind_exe}")
    if sha256_file(simind_exe) != plan["expected_simind_binary_sha256"]:
        raise ValueError("SIMIND executable hash differs from the frozen fixture plan")
    if not smc_dir.is_dir():
        raise FileNotFoundError(f"SIMIND SMC_DIR not found: {smc_dir}")
    git_identity = _git_identity()
    if output_root.exists():
        raise FileExistsError(f"fixture output root must be fresh: {output_root}")
    output_root.mkdir(parents=True, exist_ok=False)

    context = {
        "schema_version": "pars_projection_coordinate_fixture_runtime_v2",
        **git_identity,
        "config_sha256": sha256_file(config_path),
        "smc_sha256": sha256_file(smc_path),
        "simind_ini_sha256": sha256_file(ini_path),
        "simind_binary_sha256": sha256_file(simind_exe),
        "shape_zyx": list(SHAPE_ZYX),
        "expected_projection_shape_vyx": list(PROJECTION_SHAPE_VYX),
        "base_histories_per_projection": BASE_HISTORIES,
        "nn_multiplier": NN_MULTIPLIER,
        "timeout_seconds": float(plan["timeout_seconds"]),
    }
    context_path = output_root / "RUN_CONTEXT.json"
    atomic_write_json(context_path, context)

    descriptor_cases: list[dict[str, str]] = []
    records: list[dict[str, object]] = []
    for case in plan["cases"]:  # type: ignore[assignment]
        prepared = prepare_fixture_case(case, output_root / "cases" / case["case_id"])
        result = run_simind_case(
            SimindRunSpec(
                case_id=str(case["case_id"]),
                simind_exe=simind_exe,
                smc_file=smc_path,
                simind_ini=ini_path,
                source_bin=Path(prepared["source_bin"]),
                density_bin=Path(prepared["density_bin"]),
                output_root=output_root / "simind",
                rr_seed=int(case["rr_seed"]),
                nn_multiplier=NN_MULTIPLIER,
                expected_shape=PROJECTION_SHAPE_VYX,
                timeout_seconds=float(plan["timeout_seconds"]),
                environment_overrides={"SMC_DIR": str(smc_dir.resolve()) + os.sep},
            )
        )
        descriptor_case, record = _collect_success(prepared, result, output_root)
        descriptor_cases.append(descriptor_case)
        records.append(record)

    descriptor = {
        "schema_version": DESCRIPTOR_SCHEMA,
        "projection_coordinates": FROZEN_PROJECTION_COORDINATES_V1.to_dict(),
        "cases": descriptor_cases,
    }
    descriptor_path = output_root / "projection_alignment_cases_v1.json"
    atomic_write_json(descriptor_path, descriptor)
    complete = {
        "schema_version": COMPLETE_SCHEMA,
        "status": "complete",
        "case_count": 3,
        "run_context": context_path.name,
        "run_context_sha256": sha256_file(context_path),
        "descriptor": descriptor_path.name,
        "descriptor_sha256": sha256_file(descriptor_path),
        "cases": records,
    }
    atomic_write_json(output_root / "COMPLETE.json", complete)
    return complete


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    complete = run_fixture_suite(
        config_path=args.config.resolve(),
        output_root=args.output_root.resolve(),
        simind_exe=args.simind_exe.resolve(),
        smc_dir=args.smc_dir.resolve(),
    )
    print(json.dumps(complete, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
