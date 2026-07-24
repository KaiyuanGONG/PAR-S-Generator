#!/usr/bin/env python
"""Run the fail-closed automatic acceptance for the frozen Task13 Formal550."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    DatasetFreezeRecordV2,
    load_case_record_v2,
)
from core.provenance import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    resolve_relative_path,
    sha256_file,
)


DEFAULT_CAMPAIGN_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1")
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1_qa")
DEFAULT_PARS2_ROOT = Path(r"D:\PFE-U\PAR\.worktrees\PAR-S_2-task12")
DEFAULT_COORDINATE_REPORT = Path(
    r"D:\PFE-U\PAR\outputs\task12e_linux_qa_v3"
    r"\linux_projection_coordinate_report.json"
)
CAMPAIGN_SCHEMA = "pars_v2_task13_formal550_complete_v1"
AUTOMATIC_SCHEMA = "pars_v2_task13_formal550_automatic_acceptance_v1"
GENERATOR_GATE_SCHEMA = "formal550_generator_gate_v1"
PROGRESS_SCHEMA = "pars_v2_task13_formal550_acceptance_progress_v1"
EXPECTED_PROJECTION_SHAPE = (60, 128, 128)
MAXIMUM_VIEW_SUM_RATIO = 80.0
COORDINATE_CONTRACT_ID = "pars_simind_v8_xcat_zyx_sar_v1"
LOADER_TRANSFORM_ID = (
    "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
)
ROLE_CONTRACTS: Mapping[str, Mapping[str, object]] = {
    "main": {
        "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2",
        "profile_id": "population_tare_hcc_nopvi_v2",
        "case_count": 500,
        "case_prefix": "case",
        "split_counts": {"train": 400, "val": 50, "test": 50},
    },
    "negative": {
        "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
        "profile_id": "negative_control_v2",
        "case_count": 50,
        "case_prefix": "negative",
        "split_counts": {"train": 0, "val": 0, "test": 50},
    },
}


class Formal550AcceptanceError(RuntimeError):
    """Raised when formal acceptance evidence is absent or inconsistent."""


@dataclass(frozen=True)
class AcceptanceConfig:
    python_executable: Path
    generator_root: Path
    pars2_root: Path
    campaign_root: Path
    qa_root: Path
    coordinate_report: Path

    def resolved(self) -> "AcceptanceConfig":
        return AcceptanceConfig(
            python_executable=self.python_executable.resolve(),
            generator_root=self.generator_root.resolve(),
            pars2_root=self.pars2_root.resolve(),
            campaign_root=self.campaign_root.resolve(),
            qa_root=self.qa_root.resolve(),
            coordinate_report=self.coordinate_report.resolve(),
        )


@dataclass(frozen=True)
class StageCommand:
    name: str
    command: tuple[str, ...]
    cwd: Path
    script_path: Path
    output_paths: tuple[Path, ...]
    accepted_return_codes: tuple[int, ...] = (0,)
    expected_status_by_return_code: tuple[tuple[int, str], ...] = ()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json_snapshot(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], str]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Formal550AcceptanceError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise Formal550AcceptanceError(f"{label} must be a JSON object")
    return value, hashlib.sha256(payload).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _read_json_snapshot(path, label)[0]


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_stage_commands(config: AcceptanceConfig) -> list[StageCommand]:
    """Build the two exact PAR-S_2 loader commands."""

    cfg = config.resolved()
    loader = cfg.pars2_root / "scripts" / "validate_synthetic_dataset.py"
    stages: list[StageCommand] = []
    for role in ("main", "negative"):
        expected = str(ROLE_CONTRACTS[role]["case_count"])
        gate = cfg.qa_root / f"{role}_loader_gate.json"
        markdown = cfg.qa_root / f"{role}_loader_gate.md"
        alignment = cfg.qa_root / f"{role}_loader_alignment.json"
        stages.append(
            StageCommand(
                name=f"formal550_{role}_loader_gate",
                command=(
                    str(cfg.python_executable),
                    str(loader),
                    "--dataset-root",
                    str(cfg.campaign_root / role),
                    "--expected-count",
                    expected,
                    "--gate-json",
                    str(gate),
                    "--gate-markdown",
                    str(markdown),
                    "--alignment-json",
                    str(alignment),
                ),
                cwd=cfg.pars2_root,
                script_path=loader,
                output_paths=(gate, markdown, alignment),
                accepted_return_codes=(0, 1),
                expected_status_by_return_code=((0, "pass"), (1, "fail")),
            )
        )
    return stages


def _validate_campaign_documents(
    campaign: Mapping[str, Any],
    main_marker: Mapping[str, Any],
    negative_marker: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate the exact Task13 campaign-to-role marker join."""

    if (
        campaign.get("schema_version") != CAMPAIGN_SCHEMA
        or campaign.get("status") != "complete"
        or campaign.get("campaign")
        != {"dataset_id": "PAR-S-V2-FORMAL550", "dataset_version": "2.0.0"}
        or campaign.get("case_count") != 550
        or campaign.get("role_case_counts") != {"main": 500, "negative": 50}
    ):
        raise Formal550AcceptanceError("campaign identity/count binding mismatch")
    datasets = campaign.get("datasets")
    if not isinstance(datasets, Mapping) or set(datasets) != {"main", "negative"}:
        raise Formal550AcceptanceError("campaign dataset binding mismatch")

    result: dict[str, dict[str, Any]] = {}
    for role, marker in (("main", main_marker), ("negative", negative_marker)):
        contract = ROLE_CONTRACTS[role]
        raw_binding = datasets.get(role)
        expected_binding = {
            "relative_root": role,
            "manifest_sha256": marker.get("manifest_sha256"),
        }
        if raw_binding != expected_binding:
            raise Formal550AcceptanceError(
                f"{role} campaign manifest binding mismatch"
            )
        try:
            parsed = DatasetFreezeRecordV2.from_dict(marker)
        except Exception as exc:
            raise Formal550AcceptanceError(
                f"{role} completion marker is invalid: {exc}"
            ) from exc
        if (
            parsed.dataset_id != contract["dataset_id"]
            or parsed.dataset_version != "2.0.0"
            or parsed.dataset_role != role
            or parsed.case_count != contract["case_count"]
        ):
            raise Formal550AcceptanceError(f"{role} role identity/count mismatch")
        if Counter(parsed.split_counts) != Counter(contract["split_counts"]):
            raise Formal550AcceptanceError(f"{role} split count mismatch")
        if (
            parsed.projection_coordinate_contract_id != COORDINATE_CONTRACT_ID
            or parsed.loader_transform_id != LOADER_TRANSFORM_ID
        ):
            raise Formal550AcceptanceError(
                f"{role} frozen coordinate binding mismatch"
            )
        result[role] = {
            "dataset_id": parsed.dataset_id,
            "manifest_sha256": parsed.manifest_sha256,
            "case_count": parsed.case_count,
            "split_counts": dict(parsed.split_counts),
        }
    return result


def _validate_coordinate_gate(document: Mapping[str, Any]) -> dict[str, Any]:
    classification = document.get("report_classification")
    freeze = document.get("freeze_gate")
    coordinates = document.get("projection_coordinates")
    if (
        document.get("schema_version") != "pars_projection_alignment_report_v1"
        or not isinstance(classification, Mapping)
        or classification.get("schema_version") != "projection_coordinate_gate_v2"
        or classification.get("role") != "projection-coordinate-gate"
        or classification.get("blocking") is not True
        or classification.get("transform_uniqueness_required") is not True
        or not isinstance(freeze, Mapping)
        or freeze.get("passed") is not True
        or freeze.get("frozen_transform_recovered") is not True
        or not isinstance(coordinates, Mapping)
        or coordinates.get("coordinate_contract_id") != COORDINATE_CONTRACT_ID
        or coordinates.get("loader_transform_id") != LOADER_TRANSFORM_ID
    ):
        raise Formal550AcceptanceError(
            "coordinate evidence is not the frozen blocking pass gate"
        )
    return {
        "schema_version": "projection_coordinate_gate_v2",
        "status": "pass",
        "blocking": True,
    }


def _projection_metrics(projection: np.ndarray) -> dict[str, Any]:
    """Compute frozen absolute-scale projection gates for one case."""

    values = np.asarray(projection)
    if values.shape != EXPECTED_PROJECTION_SHAPE:
        raise Formal550AcceptanceError(
            f"projection shape must be {EXPECTED_PROJECTION_SHAPE}, got {values.shape}"
        )
    if not np.isfinite(values).all() or np.any(values < 0):
        raise Formal550AcceptanceError(
            "projection contains non-finite or negative bins"
        )
    per_view = np.asarray(
        values.sum(axis=(1, 2), dtype=np.float64), dtype=np.float64
    )
    if np.any(per_view <= 0):
        raise Formal550AcceptanceError(
            "projection requires positive support in every view"
        )
    total = float(per_view.sum(dtype=np.float64))
    positive_fraction = (values > 0).mean(axis=(1, 2), dtype=np.float64)
    outer = np.zeros(values.shape[1:], dtype=bool)
    outer[:8, :] = True
    outer[-8:, :] = True
    outer[:, :8] = True
    outer[:, -8:] = True
    outer_fraction = float(values[:, outer].sum(dtype=np.float64) / total)
    detector_y, detector_x = np.indices(values.shape[1:], dtype=np.float64)
    centroid_y = (
        (values * detector_y[None]).sum(axis=(1, 2), dtype=np.float64) / per_view
    )
    centroid_x = (
        (values * detector_x[None]).sum(axis=(1, 2), dtype=np.float64) / per_view
    )
    ratio = float(per_view.max() / per_view.min())
    minimum_positive = float(positive_fraction.min())
    guard = 4.0
    upper = EXPECTED_PROJECTION_SHAPE[1] - 1 - guard
    gates = {
        "shape_60x128x128": True,
        "finite_nonnegative": True,
        "positive_total_and_all_view_support": math.isfinite(total) and total > 0,
        "minimum_positive_bin_fraction": minimum_positive >= 0.001,
        "outer_8px_count_fraction": outer_fraction <= 0.01,
        "detector_centroid_guard_band": all(
            guard <= float(value) <= upper
            for bounds in (
                (centroid_y.min(), centroid_y.max()),
                (centroid_x.min(), centroid_x.max()),
            )
            for value in bounds
        ),
        "view_sum_ratio_at_most_80": math.isfinite(ratio)
        and ratio <= MAXIMUM_VIEW_SUM_RATIO,
    }
    return {
        "shape": list(values.shape),
        "projection_weight_sum": total,
        "view_sum_cv": float(per_view.std() / per_view.mean()),
        "view_sum_ratio": ratio,
        "minimum_positive_bin_fraction_per_view": minimum_positive,
        "outer_8px_count_fraction": outer_fraction,
        "detector_centroid_y_range_px": [
            float(centroid_y.min()),
            float(centroid_y.max()),
        ],
        "detector_centroid_x_range_px": [
            float(centroid_x.min()),
            float(centroid_x.max()),
        ],
        "per_view": per_view,
        "gates": gates,
    }


def _negative_semantics_gates(
    record: Any,
    metadata: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
) -> dict[str, bool]:
    try:
        tumors = metadata["actual_metrics"]["tumors"]
        activity = metadata["activity"]
        union = np.asarray(arrays["tumor_union_mask"])
        instances = np.asarray(arrays["tumor_instance_mask"])
        zero_metadata = (
            tumors["realized_count"] == 0
            and float(tumors["tumor_union_fraction_liver"]) == 0.0
            and float(tumors["tumor_union_fraction_perfused"]) == 0.0
            and tumors["lesions"] == []
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Formal550AcceptanceError(
            "negative tumor semantics metadata is malformed"
        ) from exc
    return {
        "test_only_split": record.split == "test",
        "zero_population_weight": float(record.population_weight) == 0.0,
        "negative_profile": record.profile_id == "negative_control_v2",
        "zero_tumor_metadata": zero_metadata,
        "zero_tumor_masks": not np.any(union) and not np.any(instances),
        "not_mismatch_challenge": activity.get("mismatch_challenge") is False,
    }


def _artifact_path(dataset_root: Path, record: Any, name: str) -> Path:
    try:
        relative = record.artifacts[name].relative_path
    except KeyError as exc:
        raise Formal550AcceptanceError(
            f"{record.case_id}: required artifact {name} is missing"
        ) from exc
    try:
        return resolve_relative_path(relative, dataset_root)
    except ValueError as exc:
        raise Formal550AcceptanceError(
            f"{record.case_id}: artifact {name} escapes the role dataset"
        ) from exc


def _audit_case(
    dataset_root: Path,
    record: Any,
    *,
    role: str,
) -> dict[str, Any]:
    metadata = _read_json(
        _artifact_path(dataset_root, record, "metadata_json"),
        f"{record.case_id} metadata",
    )
    provenance = _read_json(
        _artifact_path(dataset_root, record, "simind_run_provenance"),
        f"{record.case_id} SIMIND provenance",
    )
    if provenance.get("expected_shape") != list(EXPECTED_PROJECTION_SHAPE):
        raise Formal550AcceptanceError(
            f"{record.case_id}: projection provenance shape mismatch"
        )
    projection_path = _artifact_path(dataset_root, record, "projection_a00")
    expected_size = math.prod(EXPECTED_PROJECTION_SHAPE) * np.dtype("<f4").itemsize
    if projection_path.stat().st_size != expected_size:
        raise Formal550AcceptanceError(
            f"{record.case_id}: projection byte size mismatch"
        )
    projection_map = np.memmap(
        projection_path,
        dtype="<f4",
        mode="r",
        shape=EXPECTED_PROJECTION_SHAPE,
    )
    try:
        metrics = _projection_metrics(projection_map)
    finally:
        del projection_map
    with np.load(
        _artifact_path(dataset_root, record, "phantom_npz"),
        allow_pickle=False,
    ) as archive:
        arrays = {name: archive[name].copy() for name in archive.files}
    float_arrays = (
        "activity_probability",
        "activity_relative",
        "mu_input_140kev",
        "mu_true_140kev",
        "simind_source_weights",
    )
    if any(name not in arrays for name in float_arrays):
        raise Formal550AcceptanceError(
            f"{record.case_id}: phantom array contract is incomplete"
        )
    common = {
        "artifact_hashes": True,
        "arrays_finite": all(np.isfinite(arrays[name]).all() for name in float_arrays),
        "arrays_nonnegative": all(np.all(arrays[name] >= 0) for name in float_arrays),
        **metrics["gates"],
    }
    simulation = metadata.get("simulation")
    if not isinstance(simulation, Mapping):
        raise Formal550AcceptanceError(
            f"{record.case_id}: simulation metadata is malformed"
        )
    stored = simulation.get("projection_stats")
    if not isinstance(stored, Mapping):
        raise Formal550AcceptanceError(
            f"{record.case_id}: projection statistics are missing"
        )
    stored_per_view = np.asarray(
        stored.get("projection_per_view_weight_sum"), dtype=np.float64
    )
    common["projection_metadata_binding"] = (
        math.isclose(
            float(stored.get("projection_weight_sum", math.nan)),
            float(metrics["projection_weight_sum"]),
            rel_tol=1e-9,
            abs_tol=1e-5,
        )
        and stored_per_view.shape == np.asarray(metrics["per_view"]).shape
        and np.allclose(
            stored_per_view,
            np.asarray(metrics["per_view"]),
            rtol=1e-9,
            atol=1e-5,
        )
    )
    if role == "negative":
        gates = {**common, **_negative_semantics_gates(record, metadata, arrays)}
    else:
        try:
            tumors = metadata["actual_metrics"]["tumors"]
            activity = metadata["activity"]
            tumor_mask = np.asarray(arrays["tumor_union_mask"])
            instance_mask = np.asarray(arrays["tumor_instance_mask"])
        except (KeyError, TypeError) as exc:
            raise Formal550AcceptanceError(
                f"{record.case_id}: main tumor semantics are malformed"
            ) from exc
        gates = {
            **common,
            "main_profile": record.profile_id
            == "population_tare_hcc_nopvi_v2",
            "unit_population_weight": float(record.population_weight) == 1.0,
            "nonempty_tumor": (
                int(tumors.get("realized_count", 0)) > 0
                and bool(np.any(tumor_mask))
                and bool(np.any(instance_mask))
                and bool(tumors.get("lesions"))
            ),
            "not_mismatch_challenge": activity.get("mismatch_challenge") is False,
        }
    return {
        "case_id": record.case_id,
        "dataset_role": role,
        "split": record.split,
        "status": "pass" if all(gates.values()) else "fail",
        "gates": gates,
        "projection_weight_sum": metrics["projection_weight_sum"],
        "view_sum_cv": metrics["view_sum_cv"],
        "view_sum_ratio": metrics["view_sum_ratio"],
        "minimum_positive_bin_fraction_per_view": metrics[
            "minimum_positive_bin_fraction_per_view"
        ],
        "outer_8px_count_fraction": metrics["outer_8px_count_fraction"],
    }


def _manifest_rows(
    dataset_root: Path,
    marker: DatasetFreezeRecordV2,
    *,
    role: str,
) -> tuple[dict[str, Any], ...]:
    manifest = resolve_relative_path(marker.manifest_relative_path, dataset_root)
    if not manifest.is_file() or sha256_file(manifest) != marker.manifest_sha256:
        raise Formal550AcceptanceError(f"{role} manifest SHA-256 mismatch")
    try:
        raw_rows = [
            json.loads(line)
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not all(isinstance(row, dict) for row in raw_rows):
            raise TypeError("manifest rows must be objects")
        rows = tuple(dict(row) for row in raw_rows)
        case_ids = tuple(str(row["case_id"]) for row in rows)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise Formal550AcceptanceError(f"{role} manifest is malformed") from exc
    contract = ROLE_CONTRACTS[role]
    expected = tuple(
        f"{contract['case_prefix']}_{index:05d}"
        for index in range(int(contract["case_count"]))
    )
    if case_ids != expected:
        raise Formal550AcceptanceError(
            f"{role} manifest does not contain the canonical ordered case set"
        )
    return rows


def _require_manifest_record_binding(
    record: Any,
    manifest_row: Mapping[str, Any],
) -> None:
    """Reject path-swapped records even when their requested case ID matches."""

    if record.to_dict() != dict(manifest_row):
        raise Formal550AcceptanceError(
            f"{record.case_id}: loaded record does not equal its manifest record"
        )


def _audit_role_dataset(
    dataset_root: Path,
    marker_document: Mapping[str, Any],
    *,
    role: str,
) -> list[dict[str, Any]]:
    try:
        marker = DatasetFreezeRecordV2.from_dict(marker_document)
    except Exception as exc:
        raise Formal550AcceptanceError(
            f"{role} completion marker is invalid: {exc}"
        ) from exc
    manifest_rows = _manifest_rows(dataset_root, marker, role=role)
    rows: list[dict[str, Any]] = []
    for manifest_row in manifest_rows:
        case_id = str(manifest_row["case_id"])
        try:
            record = load_case_record_v2(
                dataset_root / "cases" / case_id / "case_record.json",
                dataset_root=dataset_root,
                verify_hashes=True,
            )
        except Exception as exc:
            raise Formal550AcceptanceError(
                f"{case_id}: record/artifact verification failed: {exc}"
            ) from exc
        _require_manifest_record_binding(record, manifest_row)
        if (
            record.case_id != case_id
            or record.dataset_id != ROLE_CONTRACTS[role]["dataset_id"]
            or record.dataset_version != "2.0.0"
            or record.dataset_role != role
            or record.projection_coordinate_contract_id
            != COORDINATE_CONTRACT_ID
            or record.loader_transform_id != LOADER_TRANSFORM_ID
            or record.split not in ROLE_CONTRACTS[role]["split_counts"]
            or not set(marker.required_artifact_names).issubset(record.artifacts)
        ):
            raise Formal550AcceptanceError(
                f"{case_id}: record role/artifact/coordinate binding mismatch"
            )
        rows.append(_audit_case(dataset_root, record, role=role))
    if Counter(row["split"] for row in rows) != Counter(
        ROLE_CONTRACTS[role]["split_counts"]
    ):
        raise Formal550AcceptanceError(f"{role} audited split counts mismatch")
    return rows


def select_focus_cases(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select stable attention and per-role extrema with deduplicated reasons."""

    reasons: dict[tuple[str, str], set[str]] = {}

    def add(row: Mapping[str, Any], reason: str) -> None:
        key = (str(row["dataset_role"]), str(row["case_id"]))
        reasons.setdefault(key, set()).add(reason)

    for row in rows:
        if row.get("status") != "pass":
            add(row, "automatic_gate_attention")
    for role in ("main", "negative"):
        role_rows = sorted(
            (row for row in rows if row.get("dataset_role") == role),
            key=lambda row: str(row["case_id"]),
        )
        if not role_rows:
            continue
        for field, label in (
            ("projection_weight_sum", "projection_total"),
            ("view_sum_ratio", "view_sum_ratio"),
        ):
            add(min(role_rows, key=lambda row: (float(row[field]), str(row["case_id"]))),
                f"minimum_{label}")
            add(max(role_rows, key=lambda row: (float(row[field]), str(row["case_id"]))),
                f"maximum_{label}")
    return [
        {
            "case_id": case_id,
            "dataset_role": role,
            "reasons": sorted(values),
        }
        for (role, case_id), values in sorted(
            reasons.items(), key=lambda item: (item[0][1], item[0][0])
        )
    ]


def _numeric_summary(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    if not len(array) or not np.isfinite(array).all():
        raise Formal550AcceptanceError("statistical summary values are invalid")
    return {
        "min": float(array.min()),
        "median": float(np.median(array)),
        "mean": float(array.mean()),
        "max": float(array.max()),
    }


def audit_formal550(
    campaign_root: Path,
    qa_root: Path,
) -> dict[str, Any]:
    """Audit all 550 immutable records, artifacts and projections."""

    root = Path(campaign_root).resolve()
    output = Path(qa_root).resolve()
    campaign = _read_json(root / "FORMAL550_COMPLETE.json", "campaign marker")
    role_markers = {
        role: _read_json(
            root / role / "DATASET_COMPLETE.json",
            f"{role} completion marker",
        )
        for role in ("main", "negative")
    }
    bindings = _validate_campaign_documents(
        campaign, role_markers["main"], role_markers["negative"]
    )
    rows = [
        row
        for role in ("main", "negative")
        for row in _audit_role_dataset(
            root / role,
            role_markers[role],
            role=role,
        )
    ]
    report = {
        "schema_version": GENERATOR_GATE_SCHEMA,
        "status": "pass" if all(row["status"] == "pass" for row in rows) else "fail",
        "generated_utc": _utc_now(),
        "case_count": len(rows),
        "role_case_counts": dict(Counter(row["dataset_role"] for row in rows)),
        "dataset_manifests": {
            role: binding["manifest_sha256"] for role, binding in bindings.items()
        },
        "split_counts": {
            role: dict(Counter(row["split"] for row in rows if row["dataset_role"] == role))
            for role in ("main", "negative")
        },
        "thresholds": {"maximum_view_sum_ratio": MAXIMUM_VIEW_SUM_RATIO},
        "projection_statistics": {
            role: {
                field: _numeric_summary(
                    [
                        float(row[field])
                        for row in rows
                        if row["dataset_role"] == role
                    ]
                )
                for field in (
                    "projection_weight_sum",
                    "view_sum_cv",
                    "view_sum_ratio",
                    "minimum_positive_bin_fraction_per_view",
                    "outer_8px_count_fraction",
                )
            }
            for role in ("main", "negative")
        },
        "passed_case_count": sum(row["status"] == "pass" for row in rows),
        "failed_case_ids": [
            row["case_id"] for row in rows if row["status"] != "pass"
        ],
        "focus_cases": select_focus_cases(rows),
        "cases": rows,
    }
    output.mkdir(parents=True, exist_ok=True)
    gate_path = output / "generator_gate.json"
    atomic_write_json(gate_path, report)
    atomic_write_bytes(
        output / "generator_gate.md",
        (_generator_markdown(report) + "\n").encode("utf-8"),
    )
    return report


def _generator_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Task13 Formal550 generator projection/statistical gate",
            "",
            f"- Status: **{str(report['status']).upper()}**",
            f"- Cases: `{report['case_count']}`",
            f"- Roles: `{json.dumps(report['role_case_counts'], sort_keys=True)}`",
            f"- Passed cases: `{report['passed_case_count']}`",
            f"- Maximum per-view ratio: `{MAXIMUM_VIEW_SUM_RATIO}`",
            f"- Failed case IDs: `{json.dumps(report['failed_case_ids'])}`",
            "",
            "All case records and artifact hashes were verified before projection "
            "and semantic gates were evaluated.",
        ]
    )


def evidence_row(
    gate_id: str,
    path: Path,
    *,
    snapshot: tuple[Mapping[str, Any], str] | None = None,
) -> dict[str, Any]:
    """Create one blocking evidence row bound to the source file bytes."""

    if snapshot is None:
        report, digest = _read_json_snapshot(Path(path), gate_id)
    else:
        report, digest = snapshot
    if gate_id == "projection_coordinate_gate_v2":
        normalized = _validate_coordinate_gate(report)
        schema = normalized["schema_version"]
        status = normalized["status"]
    else:
        schema = report.get("schema_version")
        status = report.get("status")
    if not isinstance(schema, str) or status not in {"pass", "fail"}:
        raise Formal550AcceptanceError(f"{gate_id} has invalid schema/status")
    return {
        "gate_id": gate_id,
        "schema_version": schema,
        "status": status,
        "blocking": True,
        "path": str(Path(path).resolve()),
        "sha256": digest,
    }


def _loader_binding(
    report: Mapping[str, Any],
    *,
    role: str,
    binding: Mapping[str, Any],
    dataset_root: Path,
) -> None:
    root = Path(dataset_root).resolve()
    observed = report.get("observed_count", report.get("case_count"))
    status = report.get("status")
    if (
        report.get("schema_version") != "pars_v2_synthetic_dataset_gate_v1"
        or status not in {"pass", "fail"}
        or report.get("dataset_root") != str(root)
        or report.get("manifest_path") != str(
            (root / "case_manifest.jsonl").resolve()
        )
        or report.get("completion_marker_path")
        != str((root / "DATASET_COMPLETE.json").resolve())
        or report.get("expected_count") != binding["case_count"]
    ):
        raise Formal550AcceptanceError(f"{role} loader invocation binding mismatch")
    identity = {
        "dataset_id": binding["dataset_id"],
        "dataset_version": "2.0.0",
        "dataset_role": role,
        "manifest_sha256": binding["manifest_sha256"],
    }
    if status == "pass" and (
        observed != binding["case_count"]
        or any(report.get(key) != value for key, value in identity.items())
    ):
        raise Formal550AcceptanceError(
            f"{role} loader campaign/manifest/count binding mismatch"
        )
    if status == "fail" and any(
        report.get(key) is not None and report.get(key) != value
        for key, value in identity.items()
    ):
        raise Formal550AcceptanceError(
            f"{role} failed loader report is bound to another dataset"
        )


def build_final_summary(config: AcceptanceConfig) -> dict[str, Any]:
    """Bind the four authoritative reports and aggregate fail closed."""

    cfg = config.resolved()
    campaign = _read_json(
        cfg.campaign_root / "FORMAL550_COMPLETE.json", "campaign marker"
    )
    markers = {
        role: _read_json(
            cfg.campaign_root / role / "DATASET_COMPLETE.json",
            f"{role} completion marker",
        )
        for role in ("main", "negative")
    }
    bindings = _validate_campaign_documents(
        campaign, markers["main"], markers["negative"]
    )
    generator_path = cfg.qa_root / "generator_gate.json"
    loader_paths = {
        role: cfg.qa_root / f"{role}_loader_gate.json"
        for role in ("main", "negative")
    }
    generator_snapshot = _read_json_snapshot(
        generator_path, "Formal550 generator gate"
    )
    generator = generator_snapshot[0]
    if (
        generator.get("schema_version") != GENERATOR_GATE_SCHEMA
        or generator.get("case_count") != 550
        or generator.get("role_case_counts") != {"main": 500, "negative": 50}
        or generator.get("dataset_manifests")
        != {
            role: bindings[role]["manifest_sha256"]
            for role in ("main", "negative")
        }
    ):
        raise Formal550AcceptanceError("generator gate campaign binding mismatch")
    loader_snapshots = {
        role: _read_json_snapshot(path, f"{role} loader gate")
        for role, path in loader_paths.items()
    }
    for role, path in loader_paths.items():
        _loader_binding(
            loader_snapshots[role][0],
            role=role,
            binding=bindings[role],
            dataset_root=cfg.campaign_root / role,
        )
    coordinate_snapshot = _read_json_snapshot(
        cfg.coordinate_report, "projection coordinate gate"
    )
    _validate_coordinate_gate(coordinate_snapshot[0])
    rows = [
        evidence_row(
            "formal550_generator_gate_v1",
            generator_path,
            snapshot=generator_snapshot,
        ),
        evidence_row(
            "formal550_main_loader_gate_v1",
            loader_paths["main"],
            snapshot=loader_snapshots["main"],
        ),
        evidence_row(
            "formal550_negative_loader_gate_v1",
            loader_paths["negative"],
            snapshot=loader_snapshots["negative"],
        ),
        evidence_row(
            "projection_coordinate_gate_v2",
            cfg.coordinate_report,
            snapshot=coordinate_snapshot,
        ),
    ]
    passed = all(row["status"] == "pass" for row in rows)
    return {
        "schema_version": AUTOMATIC_SCHEMA,
        "status": "pass" if passed else "fail",
        "automatic_gate_passed": passed,
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "gate_rows": rows,
        "notebook_authority": "informational_read_only",
    }


def _stage_can_resume(stage: StageCommand, state: Mapping[str, object]) -> bool:
    if (
        state.get("status") != "complete"
        or state.get("return_code") not in stage.accepted_return_codes
        or state.get("command") != list(stage.command)
        or not stage.script_path.is_file()
        or state.get("script_sha256") != sha256_file(stage.script_path)
    ):
        return False
    expected_status = dict(stage.expected_status_by_return_code).get(
        state.get("return_code")
    )
    if expected_status is not None and (
        state.get("formal_result_status") != expected_status
    ):
        return False
    outputs = state.get("outputs")
    if not isinstance(outputs, Mapping) or not outputs:
        return False
    for output in stage.output_paths:
        resolved = str(output.resolve())
        if (
            not output.is_file()
            or not isinstance(outputs.get(resolved), str)
            or sha256_file(output) != outputs[resolved]
        ):
            return False
    return set(outputs) == {str(path.resolve()) for path in stage.output_paths}


def _stage_state(stage: StageCommand, return_code: int) -> dict[str, Any]:
    accepted = return_code in stage.accepted_return_codes
    outputs: dict[str, str] = {}
    formal_status: str | None = None
    if accepted:
        for path in stage.output_paths:
            if not path.is_file():
                raise Formal550AcceptanceError(
                    f"{stage.name} did not create required output: {path}"
                )
            outputs[str(path.resolve())] = sha256_file(path)
        expected = dict(stage.expected_status_by_return_code).get(return_code)
        if expected is not None:
            formal_status = _read_json(
                stage.output_paths[0], f"{stage.name} report"
            ).get("status")
            if formal_status != expected:
                raise Formal550AcceptanceError(
                    f"{stage.name} exit {return_code} requires status {expected}"
                )
    state = {
        "status": "complete" if accepted else "failed",
        "command": list(stage.command),
        "command_sha256": _json_sha256(list(stage.command)),
        "cwd": str(stage.cwd),
        "script_path": str(stage.script_path),
        "script_sha256": sha256_file(stage.script_path),
        "return_code": return_code,
        "outputs": outputs,
    }
    if formal_status is not None:
        state["formal_result_status"] = formal_status
    return state


def _run_stage(stage: StageCommand, logs_root: Path) -> int:
    logs_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    with (logs_root / f"{stage.name}.stdout.log").open("wb") as stdout, (
        logs_root / f"{stage.name}.stderr.log"
    ).open("wb") as stderr:
        completed = subprocess.run(
            list(stage.command),
            cwd=stage.cwd,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    return completed.returncode


def _remove_stage_outputs(stage: StageCommand) -> None:
    for path in stage.output_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise Formal550AcceptanceError(
                f"cannot remove stale {stage.name} output: {path}"
            ) from exc


def _validate_config(config: AcceptanceConfig) -> AcceptanceConfig:
    cfg = config.resolved()
    try:
        cfg.qa_root.relative_to(cfg.campaign_root)
    except ValueError:
        pass
    else:
        raise Formal550AcceptanceError(
            "QA root must be outside the frozen campaign root"
        )
    for path, label in (
        (cfg.python_executable, "Python executable"),
        (cfg.generator_root, "Generator root"),
        (cfg.pars2_root, "PAR-S_2 root"),
        (cfg.campaign_root / "FORMAL550_COMPLETE.json", "campaign marker"),
        (cfg.campaign_root / "main", "main dataset"),
        (cfg.campaign_root / "negative", "negative dataset"),
        (cfg.coordinate_report, "coordinate report"),
    ):
        if not path.exists():
            raise Formal550AcceptanceError(f"{label} does not exist: {path}")
    for stage in build_stage_commands(cfg):
        if not stage.script_path.is_file():
            raise Formal550AcceptanceError(
                f"PAR-S_2 loader script is missing: {stage.script_path}"
            )
    return cfg


def _write_progress(
    path: Path,
    config: AcceptanceConfig,
    stages: Mapping[str, Mapping[str, Any]],
    *,
    status: str,
    current_stage: str | None = None,
    error: str | None = None,
) -> None:
    campaign = _read_json(
        config.campaign_root / "FORMAL550_COMPLETE.json", "campaign marker"
    )
    atomic_write_json(
        path,
        {
            "schema_version": PROGRESS_SCHEMA,
            "status": status,
            "updated_utc": _utc_now(),
            "campaign": campaign.get("campaign"),
            "case_count": campaign.get("case_count"),
            "role_case_counts": campaign.get("role_case_counts"),
            "current_stage": current_stage,
            "error": error,
            "stages": dict(stages),
        },
    )


def _markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Task13 Formal550 automatic acceptance",
        "",
        f"- Status: **{str(summary['status']).upper()}**",
        f"- Automatic gate passed: `{summary['automatic_gate_passed']}`",
        f"- Cases: `{summary['case_count']}`",
        f"- Roles: `{json.dumps(summary['role_case_counts'], sort_keys=True)}`",
        "- Notebook authority: `informational_read_only`",
        "",
        "## SHA-bound formal evidence",
        "",
        "| Gate | Status | SHA-256 |",
        "|---|---:|---|",
    ]
    for row in summary["gate_rows"]:
        lines.append(
            f"| `{row['gate_id']}` | **{str(row['status']).upper()}** | "
            f"`{row['sha256']}` |"
        )
    return "\n".join(lines)


def run_acceptance_pipeline(
    config: AcceptanceConfig,
    *,
    resume: bool,
) -> dict[str, Any]:
    requested = config.resolved()
    requested_automatic_json = (
        requested.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    )
    requested_automatic_markdown = (
        requested.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.md"
    )
    if resume:
        requested_automatic_json.unlink(missing_ok=True)
        requested_automatic_markdown.unlink(missing_ok=True)
    cfg = _validate_config(config)
    cfg.qa_root.mkdir(parents=True, exist_ok=True)
    progress_path = cfg.qa_root / "PROGRESS.json"
    if progress_path.is_file():
        progress = _read_json(progress_path, "acceptance progress")
        if progress.get("schema_version") != PROGRESS_SCHEMA:
            raise Formal550AcceptanceError("existing progress schema mismatch")
        raw_states = progress.get("stages")
        states = dict(raw_states) if isinstance(raw_states, Mapping) else {}
        if not resume:
            raise Formal550AcceptanceError(
                "acceptance progress exists; rerun with --resume"
            )
    else:
        states: dict[str, Mapping[str, Any]] = {}
    logs = cfg.qa_root / "logs"
    automatic_json = (
        cfg.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    )
    automatic_markdown = (
        cfg.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.md"
    )
    automatic_json.unlink(missing_ok=True)
    automatic_markdown.unlink(missing_ok=True)

    generator_path = cfg.qa_root / "generator_gate.json"
    generator_markdown = cfg.qa_root / "generator_gate.md"
    _write_progress(
        progress_path,
        cfg,
        states,
        status="running",
        current_stage="formal550_generator_gate",
    )
    generator_path.unlink(missing_ok=True)
    generator_markdown.unlink(missing_ok=True)
    try:
        audit_formal550(cfg.campaign_root, cfg.qa_root)
    except Exception as exc:
        _write_progress(
            progress_path,
            cfg,
            states,
            status="failed",
            current_stage="formal550_generator_gate",
            error=str(exc),
        )
        raise
    states["formal550_generator_gate"] = {
        "status": "complete",
        "return_code": 0,
        "outputs": {
            str(generator_path.resolve()): sha256_file(generator_path),
            str(generator_markdown.resolve()): sha256_file(generator_markdown),
        },
    }

    for stage in build_stage_commands(cfg):
        _write_progress(
            progress_path,
            cfg,
            states,
            status="running",
            current_stage=stage.name,
        )
        try:
            _remove_stage_outputs(stage)
            return_code = _run_stage(stage, logs)
            state = _stage_state(stage, return_code)
        except Exception as exc:
            _write_progress(
                progress_path,
                cfg,
                states,
                status="failed",
                current_stage=stage.name,
                error=str(exc),
            )
            raise
        states[stage.name] = state
        if return_code not in stage.accepted_return_codes:
            error = f"{stage.name} failed with exit code {return_code}; see {logs}"
            _write_progress(
                progress_path,
                cfg,
                states,
                status="failed",
                current_stage=stage.name,
                error=error,
            )
            raise Formal550AcceptanceError(error)

    try:
        summary = build_final_summary(cfg)
        atomic_write_bytes(
            automatic_markdown,
            (_markdown(summary) + "\n").encode("utf-8"),
        )
        atomic_write_json(automatic_json, summary)
    except Exception as exc:
        automatic_json.unlink(missing_ok=True)
        automatic_markdown.unlink(missing_ok=True)
        _write_progress(
            progress_path,
            cfg,
            states,
            status="failed",
            current_stage="automatic_acceptance_summary",
            error=str(exc),
        )
        raise
    states["automatic_acceptance_summary"] = {
        "status": "complete",
        "return_code": 0,
        "outputs": {
            str(automatic_json.resolve()): sha256_file(automatic_json),
            str(automatic_markdown.resolve()): sha256_file(automatic_markdown),
        },
    }
    _write_progress(progress_path, cfg, states, status="complete")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT
    )
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    parser.add_argument("--pars2-root", type=Path, default=DEFAULT_PARS2_ROOT)
    parser.add_argument(
        "--coordinate-report", type=Path, default=DEFAULT_COORDINATE_REPORT
    )
    parser.add_argument(
        "--python-executable", type=Path, default=Path(sys.executable)
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = AcceptanceConfig(
        python_executable=args.python_executable,
        generator_root=REPO_ROOT,
        pars2_root=args.pars2_root,
        campaign_root=args.campaign_root,
        qa_root=args.qa_root,
        coordinate_report=args.coordinate_report,
    )
    try:
        summary = run_acceptance_pipeline(config, resume=args.resume)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "fail", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": summary["status"],
                "automatic_gate_passed": summary["automatic_gate_passed"],
                "qa_root": str(args.qa_root.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["automatic_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
