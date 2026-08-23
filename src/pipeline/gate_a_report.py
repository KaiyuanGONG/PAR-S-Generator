"""Gate A anatomy-only reports and deterministic replay evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from core.phantom_generator import PhantomGenerator
from pipeline.contracts import atomic_write_json, atomic_write_text, sha256_file


REQUIRED_NPZ_KEYS = (
    "activity",
    "mu_map",
    "liver_mask",
    "left_mask",
    "right_mask",
    "tumor_masks",
)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _array_sha256(array: np.ndarray) -> str:
    values = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(values.dtype).encode("ascii"))
    digest.update(json.dumps(list(values.shape), separators=(",", ":")).encode("ascii"))
    digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


def _implementation_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _reproducibility_rows(cases: list[dict], config) -> list[dict[str, Any]]:
    if len(cases) < 5:
        raise ValueError("Gate A reproducibility requires at least five cases")
    positions = sorted(set(int(value) for value in np.linspace(0, len(cases) - 1, 5)))
    if len(positions) < 5:
        raise RuntimeError("Unable to select five distinct reproducibility cases")
    generator = PhantomGenerator(config.phantom)
    rows: list[dict[str, Any]] = []
    for position in positions:
        record = cases[position]
        numeric_id = int(record["case_id"].rsplit("_", 1)[1])
        replay = generator.generate_one(numeric_id)
        replay_arrays = {
            "activity": replay.activity,
            "mu_map": replay.mu_map,
            "liver_mask": replay.liver_mask,
            "left_mask": replay.left_mask,
            "right_mask": replay.right_mask,
            "tumor_masks": (
                np.stack(replay.tumor_masks, axis=0)
                if replay.tumor_masks
                else np.zeros((0, *replay.volume_shape), dtype=bool)
            ),
        }
        with np.load(record["phantom"]["npz"]) as saved:
            array_checks = {
                key: {
                    "equal": bool(np.array_equal(np.asarray(saved[key]), replay_arrays[key])),
                    "saved_sha256": _array_sha256(np.asarray(saved[key])),
                    "replay_sha256": _array_sha256(replay_arrays[key]),
                }
                for key in REQUIRED_NPZ_KEYS
            }
        saved_meta = json.loads(Path(record["phantom"]["meta"]).read_text(encoding="utf-8"))
        deterministic_metadata_equal = (
            saved_meta.get("v2") == replay.v2_metadata
            and saved_meta.get("tumors") == replay.tumor_metadata
            and saved_meta.get("tumor_diameters_mm") == replay.tumor_diameters_mm
            and saved_meta.get("tumor_nominal_diameters_mm") == replay.tumor_nominal_diameters_mm
            and saved_meta.get("perfusion_mode") == replay.perfusion_mode
        )
        passed = all(row["equal"] for row in array_checks.values()) and deterministic_metadata_equal
        rows.append(
            {
                "case_id": record["case_id"],
                "recorded_seed": int(record["seed"]),
                "replay_seed": int(replay.seed),
                "arrays": array_checks,
                "deterministic_metadata_equal": deterministic_metadata_equal,
                "status": "passed" if passed else "failed",
                "excluded_nondeterministic_field": "generation_time_s",
            }
        )
    return rows


def _csv_rows(cases: list[dict]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in cases:
        qc = json.loads(Path(record["qc"]["phantom"]["path"]).read_text(encoding="utf-8"))
        v2 = qc.get("v2") or {}
        tumors = qc.get("metrics", {}).get("tumors", [])
        for tumor in tumors:
            rows.append(
                {
                    "case_id": record["case_id"],
                    "split": record["split"],
                    "morphology": v2.get("morphology"),
                    "target_liver_volume_ml": v2.get("target_volume_ml"),
                    "actual_liver_volume_ml": v2.get("actual_volume_ml"),
                    "target_left_fraction": v2.get("target_left_fraction"),
                    "actual_left_fraction": v2.get("actual_left_fraction"),
                    "caudate_enabled": v2.get("caudate_enabled"),
                    "n_tumors": qc.get("metrics", {}).get("n_tumors"),
                    "lesion_index": tumor.get("index"),
                    "effective_diameter_mm": tumor.get("effective_diameter_mm"),
                    "sampled_size_bin_mm": json.dumps(tumor.get("sampled_size_bin_mm")),
                    "placement_stratum": tumor.get("placement_stratum"),
                    "surface_margin_mm": tumor.get("surface_margin_mm"),
                    "outside_liver_vox": tumor.get("outside_liver_vox"),
                    "overlap_previous_vox": tumor.get("overlap_previous_vox"),
                    "case_qc_status": qc.get("status"),
                }
            )
    return rows


def _markdown(report: dict[str, Any]) -> str:
    summary = report["qc_summary"]
    v2 = summary.get("v2_population", {})
    lesion = summary.get("lesion_distributions", {})
    gate = summary.get("gate_a_population_acceptance", {})
    checks = "\n".join(
        f"- {row['status'].upper()}: `{row['name']}`"
        for row in gate.get("checks", [])
    )
    repro = "\n".join(
        f"- {row['case_id']}: {row['status']} (seed {row['recorded_seed']})"
        for row in report["reproducibility"]
    )
    differences = "\n".join(
        f"- **{row['component']}**: {row['integration']}"
        for row in report["implementation_differences"]
    )
    return f"""# Gate A V2 anatomy-only pilot

- Status: **{report['status']}**
- Implementation commit: `{report['implementation_commit']}`
- Effective config SHA256: `{report['effective_config_sha256']}`
- Cases: {summary.get('passed_case_count')}/{summary.get('case_count')} hard-QC passed
- Lesions: {summary.get('lesion_count')}
- Outside-liver voxels: {summary.get('containment_outside_voxels')}
- Overlap voxels: {summary.get('overlap_voxels')}
- Morphology counts: `{json.dumps(v2.get('morphology_counts', {}), sort_keys=True)}`
- Caudate enabled: {v2.get('caudate_enabled_count')}
- Actual liver volume distribution (mL): `{json.dumps(v2.get('actual_volume_ml', {}), sort_keys=True)}`
- V2 left-fraction distribution: `{json.dumps(v2.get('actual_left_fraction', {}), sort_keys=True)}`
- Effective lesion diameter bins: `{json.dumps(lesion.get('diameter_bins', {}), sort_keys=True)}`
- Placement strata: `{json.dumps(lesion.get('placement_strata', {}), sort_keys=True)}`

## Implementation differences

{differences}

## Gate checks

{checks}

## Bitwise replay

{repro}

`dataset_manifest.json` contains the byte size and SHA256 for every packaged
NPZ, metadata, QC and report artifact. `gate_a_failures.json` is empty only
when all hard checks and all five replays pass.
"""


def write_gate_a_reports(run_root: Path, cases: list[dict], config) -> dict[str, Any]:
    run_root = Path(run_root)
    qc_summary_path = run_root / "qc" / "phantom_qc_summary.json"
    qc_summary = json.loads(qc_summary_path.read_text(encoding="utf-8"))
    reproducibility = _reproducibility_rows(cases, config)
    gate = qc_summary.get("gate_a_population_acceptance", {})
    failures: list[dict[str, Any]] = []
    for case_id in qc_summary.get("failed_cases", []):
        failures.append({"kind": "case_or_population", "case_id": case_id})
    for check in gate.get("checks", []):
        if check.get("status") != "passed":
            failures.append({"kind": "gate_check", "name": check.get("name"), "evidence": check})
    for replay in reproducibility:
        if replay["status"] != "passed":
            failures.append({"kind": "reproducibility", "case_id": replay["case_id"], "evidence": replay})

    repo_root = Path(__file__).resolve().parents[2]
    effective_config = config.to_dict()
    source_paths = [
        "src/core/phantom_generator.py",
        "src/core/hybrid_v2_adapter.py",
        "src/core/anatomy_v2.py",
        "src/core/attenuation_model_v2.py",
        "src/core/liver_geometry.py",
        "src/core/liver_regions.py",
        "src/core/measurements.py",
        "src/core/population_sampler.py",
        "src/core/schemas_v2.py",
        "src/core/seeds.py",
        "src/pipeline/qc.py",
        "src/pipeline/runner.py",
        "src/pipeline/gate_a_report.py",
        "src/cli.py",
        "configs/gate_a_v2_master_100.json",
        "configs/evidence_registry_v2.json",
        "configs/population_tare_hcc_nopvi_v2.json",
        "docs/reports/gate_a_v2_dependency_contract_map.md",
    ]
    implementation_differences = [
        {
            "component": "population_and_liver",
            "integration": (
                "V2 evidence-backed patient sampling, normal/cirrhotic morphology, "
                "large-volume liver, caudate and five-region geometry are adapted to "
                "the frozen master left/right array contract."
            ),
        },
        {
            "component": "activity_and_attenuation",
            "integration": (
                "Master perfusion/activity semantics use an independent V2 child seed; "
                "only V2 physical mu_true_140kev is exposed as master mu_map, while the "
                "CT-like input map is hash-traced but not saved or exported."
            ),
        },
        {
            "component": "lesions",
            "integration": (
                "The frozen master measured-size, liver-containment, zero-overlap, "
                "margin and labelled capacity-fallback implementation remains authoritative; "
                "tumor_generator_v2 is absent and not imported."
            ),
        },
        {
            "component": "execution_boundary",
            "integration": (
                "PipelineRunner packages anatomy/QC/NPZ/metadata/manifest only; SIMIND, "
                "GPU, E-CAL, observation, training, sealed evaluation and Formal550 are "
                "not executed."
            ),
        },
        {
            "component": "population_gate",
            "integration": (
                "V2 source shape and torso hard QC replace only the legacy 900-1900 mL "
                "and fixed-left-ratio anatomy checks; all frozen master lesion hard checks "
                "remain enforced without threshold relaxation."
            ),
        },
    ]
    report = {
        "schema_version": "pars_gate_a_v2_report_v1",
        "status": "passed" if not failures and gate.get("status") == "passed" else "failed",
        "implementation_commit": _implementation_commit(repo_root),
        "effective_config": effective_config,
        "effective_config_sha256": _canonical_sha256(effective_config),
        "source_sha256": {path: sha256_file(repo_root / path) for path in source_paths},
        "implementation_differences": implementation_differences,
        "qc_summary_path": qc_summary_path.relative_to(run_root).as_posix(),
        "qc_summary_sha256": sha256_file(qc_summary_path),
        "qc_summary": qc_summary,
        "reproducibility": reproducibility,
        "artifact_sha256": {
            record["case_id"]: {
                "npz": record["phantom"]["npz_sha256"],
                "metadata": record["phantom"]["meta_sha256"],
                "qc": record["qc"]["phantom"]["sha256"],
            }
            for record in cases
        },
        "failures": failures,
    }
    atomic_write_json(run_root / "gate_a_report.json", report)
    atomic_write_json(
        run_root / "gate_a_failures.json",
        {
            "schema_version": "pars_gate_a_v2_failures_v1",
            "status": "passed" if not failures else "failed",
            "failure_count": len(failures),
            "failures": failures,
        },
    )
    rows = _csv_rows(cases)
    csv_path = run_root / "gate_a_report.csv"
    fieldnames = list(rows[0]) if rows else []
    if fieldnames:
        temporary = csv_path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(csv_path)
    else:
        atomic_write_text(csv_path, "")
    atomic_write_text(run_root / "gate_a_report.md", _markdown(report))
    if report["status"] != "passed":
        raise RuntimeError("Gate A report contains blocking failures")
    return report
