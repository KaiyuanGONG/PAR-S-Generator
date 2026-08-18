"""Checksum freeze and evidence extraction for the current 500-case dataset."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from pipeline.contracts import (
    LEGACY_PAR_S2_PROJECTION_TRANSFORM,
    assign_fixed_splits,
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    utc_now,
    write_jsonl,
)
from pipeline.qc import phantom_qc, validate_projection_artifacts


LEGACY_DATASET_ID = "legacy-v1-weighted-mc"


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "min": float(arr.min()),
        "q05": float(np.quantile(arr, 0.05)),
        "q25": float(np.quantile(arr, 0.25)),
        "median": float(np.median(arr)),
        "q75": float(np.quantile(arr, 0.75)),
        "q95": float(np.quantile(arr, 0.95)),
        "max": float(arr.max()),
    }


def refresh_legacy_aggregate(destination: Path) -> dict:
    """Add cross-case overlap/containment evidence from the frozen JSONL only."""
    destination = Path(destination)
    summary_path = destination / "qc_summary.json"
    cases_path = destination / "cases.jsonl"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    empty = outside = overlap = 0
    overlap_cases = 0
    overlap_voxels: list[float] = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        case_overlaps = 0
        for tumor in case.get("qc", {}).get("tumors", []):
            empty += int(tumor.get("volume_vox", 0) == 0)
            outside += int(tumor.get("outside_liver_vox", 0) > 0)
            overlap_value = int(tumor.get("overlap_previous_vox", 0))
            if overlap_value > 0:
                overlap += 1
                case_overlaps += 1
                overlap_voxels.append(float(overlap_value))
        overlap_cases += int(case_overlaps > 0)
    summary["lesion_integrity"] = {
        "empty_mask_count": empty,
        "outside_liver_count": outside,
        "overlap_with_previous_lesion_count": overlap,
        "cases_with_overlap": overlap_cases,
        "overlap_voxels_when_present": _quantiles(overlap_voxels),
    }
    atomic_write_json(summary_path, summary)
    return summary


def freeze_legacy_dataset(
    *,
    phantom_dir: Path,
    projection_dir: Path,
    destination: Path,
    simind_exe: Path,
    smc_file: Path,
    split_seed: int = 42,
    progress=None,
    refresh_manifest: bool = False,
) -> Path:
    """Freeze by reference; source data is never copied, written, or renamed."""
    phantom_dir = Path(phantom_dir).resolve()
    projection_dir = Path(projection_dir).resolve()
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        existing_run = destination / "run.json"
        safe_refresh = False
        if refresh_manifest and existing_run.is_file():
            existing = json.loads(existing_run.read_text(encoding="utf-8"))
            safe_refresh = existing.get("dataset_id") == LEGACY_DATASET_ID
        if not safe_refresh:
            raise FileExistsError(f"Legacy freeze destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    npz_by_id = {path.stem: path for path in phantom_dir.glob("case_*.npz")}
    a00_by_id = {path.stem: path for path in projection_dir.glob("case_*.a00")}
    if set(npz_by_id) != set(a00_by_id):
        missing_projection = sorted(set(npz_by_id) - set(a00_by_id))
        missing_phantom = sorted(set(a00_by_id) - set(npz_by_id))
        raise RuntimeError(
            f"Legacy pairing mismatch: no projection={missing_projection[:5]}, "
            f"no phantom={missing_phantom[:5]}"
        )
    case_ids = sorted(npz_by_id)
    if len(case_ids) != 500:
        raise RuntimeError(f"Expected exactly 500 legacy cases, found {len(case_ids)}")
    splits = assign_fixed_splits(case_ids, seed=split_seed)

    records: list[dict] = []
    liver_volumes: list[float] = []
    left_ratios: list[float] = []
    lesion_nominal: list[float] = []
    lesion_actual: list[float] = []
    lesion_ratio: list[float] = []
    lesion_ratio_by_mode: dict[str, list[float]] = {}
    lesion_tnr: list[float] = []
    lesion_surface_contact = 0
    lesion_count = 0
    projection_noninteger: list[float] = []
    projection_nonzero: list[float] = []
    support_patterns: Counter[str] = Counter()
    phantom_failures: Counter[str] = Counter()
    projection_failures: Counter[str] = Counter()
    perfusion_modes: Counter[str] = Counter()

    for position, case_id in enumerate(case_ids, 1):
        npz = npz_by_id[case_id]
        meta_path = phantom_dir / f"{case_id}_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        pqc = phantom_qc(npz, meta_path)
        a00 = a00_by_id[case_id]
        proj_qc = validate_projection_artifacts(a00)

        liver_volumes.append(float(pqc["metrics"]["liver_volume_ml"]))
        left_ratios.append(float(pqc["metrics"]["left_ratio"]))
        perfusion_modes[str(meta.get("perfusion_mode", "unknown"))] += 1
        nominal = [float(value) for value in meta.get("tumor_diameters_mm", [])]
        actual = [float(tumor["effective_diameter_mm"]) for tumor in pqc["metrics"]["tumors"]]
        modes = [str(value) for value in meta.get("tumor_modes", [])]
        lesion_nominal.extend(nominal)
        lesion_actual.extend(actual)
        for lesion_index, (nominal_value, actual_value) in enumerate(zip(nominal, actual, strict=False)):
            if nominal_value > 0:
                ratio = actual_value / nominal_value
                lesion_ratio.append(ratio)
                mode = modes[lesion_index] if lesion_index < len(modes) else "unknown"
                lesion_ratio_by_mode.setdefault(mode, []).append(ratio)
        lesion_tnr.extend(
            float(tumor["tnr_from_saved_activity"])
            for tumor in pqc["metrics"]["tumors"]
            if tumor.get("tnr_from_saved_activity") is not None
        )
        lesion_count += len(actual)
        lesion_surface_contact += sum(
            tumor["surface_margin_mm"] <= 1e-6 for tumor in pqc["metrics"]["tumors"]
        )
        phantom_failures.update(pqc["failures"])
        projection_failures.update(proj_qc["failures"])
        metrics = proj_qc.get("metrics", {})
        projection_noninteger.append(float(metrics.get("noninteger_positive_fraction", 0.0)))
        projection_nonzero.append(float(metrics.get("nonzero_fraction", 0.0)))
        support_key = json.dumps(
            {"rows": metrics.get("support_rows", []), "cols": metrics.get("support_cols", [])},
            separators=(",", ":"),
        )
        support_patterns[support_key] += 1

        artifacts = {}
        for suffix in (".a00", ".mhd", ".res", ".spe"):
            path = projection_dir / f"{case_id}{suffix}"
            if path.exists():
                artifacts[suffix[1:]] = {
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
        records.append(
            {
                "case_id": case_id,
                "phantom_id": case_id,
                "split": splits[case_id],
                "seed": meta.get("seed"),
                "phantom": {
                    "npz": str(npz),
                    "npz_sha256": sha256_file(npz),
                    "meta": str(meta_path),
                    "meta_sha256": sha256_file(meta_path),
                    "shape": pqc["metrics"]["shape"],
                    "activity_dtype": pqc["metrics"]["activity_dtype"],
                    "mu_dtype": pqc["metrics"]["mu_dtype"],
                    "mu_semantic": "claimed_linear_attenuation_coefficient",
                    "mu_unit": "cm^-1",
                    "mu_contract_status": "unverified_after_failed_simind_analytic_attenuation_control",
                },
                "projection": artifacts,
                "projection_contract": {
                    "shape": proj_qc.get("metrics", {}).get("shape"),
                    "dtype": proj_qc.get("metrics", {}).get("dtype"),
                    "canonical_transform": LEGACY_PAR_S2_PROJECTION_TRANSFORM,
                    "interpretation": "weighted_mc_expectation_like_output",
                },
                "qc": {
                    "phantom_status": pqc["status"],
                    "projection_status": proj_qc["status"],
                    "actual_lesion_diameters_mm": actual,
                    "nominal_lesion_diameters_mm": nominal,
                    "surface_contact_count": sum(
                        tumor["surface_margin_mm"] <= 1e-6 for tumor in pqc["metrics"]["tumors"]
                    ),
                    "left_ratio": pqc["metrics"]["left_ratio"],
                    "liver_volume_ml": pqc["metrics"]["liver_volume_ml"],
                    "tumors": [
                        {**tumor, "mode": modes[index] if index < len(modes) else "unknown"}
                        for index, tumor in enumerate(pqc["metrics"]["tumors"])
                    ],
                },
            }
        )
        if progress and (position == 1 or position % 10 == 0 or position == len(case_ids)):
            progress(position, len(case_ids), case_id)

    cases_path = destination / "cases.jsonl"
    write_jsonl(cases_path, records)
    checksum_lines: list[str] = []
    for record in records:
        checksum_lines.append(f"{record['phantom']['npz_sha256']}  {record['phantom']['npz']}")
        checksum_lines.append(f"{record['phantom']['meta_sha256']}  {record['phantom']['meta']}")
        for artifact in record["projection"].values():
            checksum_lines.append(f"{artifact['sha256']}  {artifact['path']}")
    atomic_write_text(destination / "file_inventory.sha256", "\n".join(checksum_lines) + "\n")
    split_payload = {
        "algorithm": "sorted case IDs + numpy.default_rng(seed).permutation",
        "seed": split_seed,
        "fractions": [0.8, 0.1, 0.1],
        "assignment_unit": "phantom_id",
        "counts": dict(Counter(splits.values())),
        "splits": {
            name: sorted(case_id for case_id, split in splits.items() if split == name)
            for name in ("train", "val", "test")
        },
    }
    atomic_write_json(destination / "splits.json", split_payload)

    decoded_patterns = []
    for key, count in support_patterns.most_common():
        decoded_patterns.append({"count": count, **json.loads(key)})
    qc_summary = {
        "dataset_id": LEGACY_DATASET_ID,
        "case_count": len(records),
        "all_projection_artifacts_strong_qc_passed": not projection_failures,
        "projection_failures": dict(projection_failures),
        "phantom_structural_failures": dict(phantom_failures),
        "liver_volume_ml": _quantiles(liver_volumes),
        "left_ratio": _quantiles(left_ratios),
        "perfusion_modes": dict(perfusion_modes),
        "lesion_count": lesion_count,
        "nominal_lesion_diameter_mm": _quantiles(lesion_nominal),
        "actual_effective_lesion_diameter_mm": _quantiles(lesion_actual),
        "actual_to_nominal_diameter_ratio": _quantiles(lesion_ratio),
        "actual_to_nominal_diameter_ratio_by_mode": {
            mode: _quantiles(values) for mode, values in sorted(lesion_ratio_by_mode.items())
        },
        "tnr_from_saved_activity": _quantiles(lesion_tnr),
        "lesion_surface_contact_count": lesion_surface_contact,
        "lesion_surface_contact_fraction": lesion_surface_contact / max(lesion_count, 1),
        "projection_nonzero_fraction": _quantiles(projection_nonzero),
        "projection_noninteger_positive_fraction": _quantiles(projection_noninteger),
        "projection_support_patterns": decoded_patterns,
        "interpretation_limits": [
            "Single-realization spatial variation is not a repeated-sampling Fano-factor estimate.",
            "The weighted non-integer projection can be used as a non-negative data fidelity target, but not claimed as raw Poisson counts.",
            "The attenuation input contract remains unverified after the Flag-15 readback passed but the analytic transmission control failed.",
            "The legacy 128x128 native-detector aperture was quantified as 31.488 cm square; the current GE-specific candidate is 160x208, but legacy files remain frozen.",
        ],
    }
    atomic_write_json(destination / "qc_summary.json", qc_summary)
    qc_summary = refresh_legacy_aggregate(destination)

    first_res = projection_dir / f"{case_ids[0]}.res"
    first_command = None
    for line in first_res.read_text(encoding="utf-8", errors="replace").splitlines():
        if "Command:" in line:
            first_command = line.split("Command:", 1)[1].strip()
            break
    run = {
        "contract_version": 1,
        "dataset_id": LEGACY_DATASET_ID,
        "created_utc": utc_now(),
        "freeze_mode": "read_only_checksum_reference",
        "source_phantom_dir": str(phantom_dir),
        "source_projection_dir": str(projection_dir),
        "case_count": len(records),
        "cases_manifest": "cases.jsonl",
        "cases_manifest_sha256": sha256_file(cases_path),
        "file_inventory": "file_inventory.sha256",
        "file_inventory_sha256": sha256_file(destination / "file_inventory.sha256"),
        "split_manifest": "splits.json",
        "qc_summary": "qc_summary.json",
        "qc_summary_sha256": sha256_file(destination / "qc_summary.json"),
        "scope": "synthetic_liver_spect_current_protocol_only",
        "classification": "legacy_weighted_mc_expectation_like_output",
        "noise_contract": "no_separate_observation_realization",
        "canonical_projection_transform": LEGACY_PAR_S2_PROJECTION_TRANSFORM,
        "production_command_evidence_first_case": first_command,
        "simind_binary": {
            "path": str(Path(simind_exe).resolve()),
            "sha256": sha256_file(Path(simind_exe)),
            "provenance_note": "Bundled binary checksum; historical .res command records simind path but does not itself prove binary identity.",
        },
        "smc": {
            "path": str(Path(smc_file).resolve()),
            "sha256": sha256_file(Path(smc_file)),
            "provenance_note": "Current bundled SMC checksum; historical .res embeds configuration summary but exact historical file identity is not independently proven.",
        },
        "known_ambiguities": [
            "Folder name says 60Mbq20s while SMC Index-25 is 1704 (compatible with 60 MBq x 28.4 s, not 20 s).",
            "Attenuation /FD unit/semantic is not verified by .ict readback.",
            "Output matrix 128 yields a 31.5 cm square at 0.246 cm/pixel; 160/208 behavior is not yet controlled.",
            "Legacy lesion metadata records nominal/requested size from the pre-fix generator.",
        ],
    }
    atomic_write_json(destination / "run.json", run)
    return destination
