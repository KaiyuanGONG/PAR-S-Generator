"""Atomic V2 case writing, family-first splitting and immutable dataset freeze."""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .provenance import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    json_compatible,
    resolve_relative_path,
    sha256_bytes,
    sha256_file,
    sha256_json,
)
from .seeds import SeedBundle
from .simind_postprocess import SimindCompletionError, audit_simind_completion
from .schemas_v2 import (
    FROZEN_LOADER_TRANSFORM_ID,
    PROJECTION_COORDINATE_CONTRACT_ID,
    ProjectionCoordinatesV1,
    SchemaValidationError,
    validate_projection_coordinates_v1,
)


CASE_SCHEMA_VERSION = "pars_syn_v2"
CASE_RECORD_SCHEMA_VERSION = "pars_case_record_v2"
SPLIT_PLAN_SCHEMA_VERSION = "pars_split_plan_v2"
DATASET_CONTRACT_SCHEMA_VERSION = "pars_dataset_contract_v2"
DATASET_FREEZE_SCHEMA_VERSION = "pars_dataset_freeze_v2"
DATASET_IDENTITY_FILENAME = "DATASET_IDENTITY.json"
SPLIT_PLAN_FILENAME = "SPLIT_PLAN.json"
CASE_MANIFEST_FILENAME = "case_manifest.jsonl"
DATASET_COMPLETE_FILENAME = "DATASET_COMPLETE.json"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SPLITS = ("train", "val", "test")
_DATASET_ROLES = ("main", "negative")
_SIMIND_QUARTET_SUFFIXES: Mapping[str, str] = {
    "projection_a00": "a00",
    "projection_mhd": "mhd",
    "projection_res": "res",
    "projection_spe": "spe",
}
_SIMIND_PROVENANCE_ARTIFACT = "simind_run_provenance"


class CaseWriteError(RuntimeError):
    pass


class DatasetFreezeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArraySpecV2:
    dtype: str
    unit: str
    semantic: str

    def to_dict(self) -> dict[str, str]:
        return {"dtype": self.dtype, "unit": self.unit, "semantic": self.semantic}


ARRAY_CONTRACT_V2: Mapping[str, ArraySpecV2] = {
    "activity_relative": ArraySpecV2("float32", "relative_concentration", "unnormalized liver and tumor uptake"),
    "activity_probability": ArraySpecV2("float32", "dimensionless_sum_1", "normalized source probability density"),
    "simind_source_weights": ArraySpecV2("float32", "histories_weight", "activity_probability times base histories per projection"),
    "mu_true_140kev": ArraySpecV2("float32", "cm^-1", "physical attenuation map permitted for SIMIND"),
    "mu_input_140kev": ArraySpecV2("float32", "cm^-1", "CT-like network input attenuation map"),
    "body_mask": ArraySpecV2("uint8", "binary_mask", "body support"),
    "liver_mask": ArraySpecV2("uint8", "binary_mask", "whole-liver support"),
    "liver_region_proxy": ArraySpecV2("uint8", "categorical_0_to_5", "Couinaud proxy labels"),
    "tumor_instance_mask": ArraySpecV2("uint16", "instance_labels", "zero background and contiguous lesion instances"),
    "tumor_union_mask": ArraySpecV2("uint8", "binary_mask", "union of all tumor instances"),
    "perfusion_mask": ArraySpecV2("uint8", "binary_mask", "injection territory proxy"),
}


_METADATA_FIELDS = {
    "seeds",
    "config_hashes",
    "patient",
    "target_metrics",
    "actual_metrics",
    "activity",
    "spatial",
    "acquisition",
    "physics",
    "simulation",
    "quality_control",
}


@dataclass(frozen=True)
class ArtifactRecordV2:
    relative_path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ArtifactRecordV2":
        if set(data) != {"relative_path", "size_bytes", "sha256"}:
            raise CaseWriteError("artifact record has invalid fields")
        relative_path = str(data["relative_path"])
        size_bytes = data["size_bytes"]
        digest = str(data["sha256"])
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise CaseWriteError("artifact size_bytes must be a non-negative integer")
        if not _SHA256.fullmatch(digest):
            raise CaseWriteError("artifact sha256 must be a lowercase SHA-256 digest")
        return cls(relative_path=relative_path, size_bytes=size_bytes, sha256=digest)


@dataclass(frozen=True)
class CasePayloadV2:
    case_id: str
    case_family_id: str
    profile_id: str
    dataset_id: str
    dataset_version: str
    dataset_role: str
    split: str
    population_weight: float
    sampling_probability: float
    arrays: Mapping[str, np.ndarray]
    metadata: Mapping[str, object]
    extra_artifacts: Mapping[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseRecordV2:
    case_id: str
    case_family_id: str
    profile_id: str
    dataset_id: str
    dataset_version: str
    dataset_role: str
    split: str
    population_weight: float
    sampling_probability: float
    split_plan_sha256: str
    projection_coordinate_contract_id: str
    loader_transform_id: str
    artifacts: Mapping[str, ArtifactRecordV2]
    schema_version: str = CASE_RECORD_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "case_family_id": self.case_family_id,
            "profile_id": self.profile_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "dataset_role": self.dataset_role,
            "split": self.split,
            "population_weight": self.population_weight,
            "sampling_probability": self.sampling_probability,
            "split_plan_sha256": self.split_plan_sha256,
            "projection_coordinate_contract_id": (
                self.projection_coordinate_contract_id
            ),
            "loader_transform_id": self.loader_transform_id,
            "artifacts": {
                name: artifact.to_dict()
                for name, artifact in sorted(self.artifacts.items())
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CaseRecordV2":
        allowed = {
            "schema_version",
            "case_id",
            "case_family_id",
            "profile_id",
            "dataset_id",
            "dataset_version",
            "dataset_role",
            "split",
            "population_weight",
            "sampling_probability",
            "split_plan_sha256",
            "projection_coordinate_contract_id",
            "loader_transform_id",
            "artifacts",
        }
        if set(data) != allowed or data.get("schema_version") != CASE_RECORD_SCHEMA_VERSION:
            raise CaseWriteError("case record does not match pars_case_record_v2 schema")
        raw_artifacts = data["artifacts"]
        if not isinstance(raw_artifacts, dict):
            raise CaseWriteError("case record artifacts must be an object")
        artifacts = {
            str(name): ArtifactRecordV2.from_dict(value)
            for name, value in raw_artifacts.items()
            if isinstance(value, dict)
        }
        if len(artifacts) != len(raw_artifacts):
            raise CaseWriteError("case record contains an invalid artifact")
        coordinate_contract_id = str(data["projection_coordinate_contract_id"])
        loader_transform_id = str(data["loader_transform_id"])
        if coordinate_contract_id != PROJECTION_COORDINATE_CONTRACT_ID:
            raise CaseWriteError(
                "case record projection_coordinate_contract_id is not frozen V2"
            )
        if loader_transform_id != FROZEN_LOADER_TRANSFORM_ID:
            raise CaseWriteError("case record loader_transform_id is not frozen V2")
        return cls(
            case_id=str(data["case_id"]),
            case_family_id=str(data["case_family_id"]),
            profile_id=str(data["profile_id"]),
            dataset_id=str(data["dataset_id"]),
            dataset_version=str(data["dataset_version"]),
            dataset_role=str(data["dataset_role"]),
            split=str(data["split"]),
            population_weight=float(data["population_weight"]),
            sampling_probability=float(data["sampling_probability"]),
            split_plan_sha256=str(data["split_plan_sha256"]),
            projection_coordinate_contract_id=coordinate_contract_id,
            loader_transform_id=loader_transform_id,
            artifacts=artifacts,
        )


@dataclass(frozen=True)
class SplitPlanV2:
    dataset_id: str
    profile_id: str
    global_seed: int
    ratios: Mapping[str, float]
    family_to_split: Mapping[str, str]
    family_seeds: Mapping[str, int]
    schema_version: str = SPLIT_PLAN_SCHEMA_VERSION

    def content_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "profile_id": self.profile_id,
            "global_seed": self.global_seed,
            "ratios": {name: float(self.ratios[name]) for name in _SPLITS},
            "family_to_split": dict(sorted(self.family_to_split.items())),
            "family_seeds": dict(sorted(self.family_seeds.items())),
        }

    @property
    def sha256(self) -> str:
        return sha256_json(self.content_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self.content_dict(), "sha256": self.sha256}


@dataclass(frozen=True)
class DatasetContractV2:
    output_root: Path
    dataset_id: str
    dataset_version: str
    dataset_role: str
    expected_case_ids: tuple[str, ...]
    allowed_profile_ids: tuple[str, ...]
    split_plan_sha256: str
    required_artifact_names: tuple[str, ...]
    projection_coordinate_contract_id: str = PROJECTION_COORDINATE_CONTRACT_ID
    loader_transform_id: str = FROZEN_LOADER_TRANSFORM_ID
    required_simulation_status: str = "complete"
    required_quality_status: str = "pass"
    schema_version: str = DATASET_CONTRACT_SCHEMA_VERSION

    def content_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "dataset_role": self.dataset_role,
            "expected_case_ids": sorted(self.expected_case_ids),
            "allowed_profile_ids": sorted(self.allowed_profile_ids),
            "split_plan_sha256": self.split_plan_sha256,
            "required_artifact_names": sorted(self.required_artifact_names),
            "projection_coordinate_contract_id": (
                self.projection_coordinate_contract_id
            ),
            "loader_transform_id": self.loader_transform_id,
            "required_simulation_status": self.required_simulation_status,
            "required_quality_status": self.required_quality_status,
        }

    @property
    def sha256(self) -> str:
        return sha256_json(self.content_dict())


@dataclass(frozen=True)
class DatasetFreezeRecordV2:
    dataset_id: str
    dataset_version: str
    dataset_role: str
    case_count: int
    split_counts: Mapping[str, int]
    manifest_relative_path: str
    manifest_sha256: str
    split_plan_sha256: str
    contract_sha256: str
    required_artifact_names: tuple[str, ...]
    projection_coordinate_contract_id: str
    loader_transform_id: str
    frozen_utc: str
    status: str = "complete"
    schema_version: str = DATASET_FREEZE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "dataset_role": self.dataset_role,
            "case_count": self.case_count,
            "split_counts": dict(self.split_counts),
            "manifest_relative_path": self.manifest_relative_path,
            "manifest_sha256": self.manifest_sha256,
            "split_plan_sha256": self.split_plan_sha256,
            "contract_sha256": self.contract_sha256,
            "required_artifact_names": list(self.required_artifact_names),
            "projection_coordinate_contract_id": (
                self.projection_coordinate_contract_id
            ),
            "loader_transform_id": self.loader_transform_id,
            "frozen_utc": self.frozen_utc,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "DatasetFreezeRecordV2":
        allowed = {
            "schema_version",
            "status",
            "dataset_id",
            "dataset_version",
            "dataset_role",
            "case_count",
            "split_counts",
            "manifest_relative_path",
            "manifest_sha256",
            "split_plan_sha256",
            "contract_sha256",
            "required_artifact_names",
            "projection_coordinate_contract_id",
            "loader_transform_id",
            "frozen_utc",
        }
        if set(data) != allowed or data.get("schema_version") != DATASET_FREEZE_SCHEMA_VERSION:
            raise DatasetFreezeError("completion marker has invalid schema")
        split_counts = data["split_counts"]
        artifacts = data["required_artifact_names"]
        if not isinstance(split_counts, dict) or not isinstance(artifacts, list):
            raise DatasetFreezeError("completion marker fields have invalid types")
        if data.get("status") != "complete":
            raise DatasetFreezeError("completion marker status must be complete")
        if (
            data.get("projection_coordinate_contract_id")
            != PROJECTION_COORDINATE_CONTRACT_ID
            or data.get("loader_transform_id") != FROZEN_LOADER_TRANSFORM_ID
        ):
            raise DatasetFreezeError(
                "completion marker projection coordinate contract is not frozen V2"
            )
        return cls(
            dataset_id=str(data["dataset_id"]),
            dataset_version=str(data["dataset_version"]),
            dataset_role=str(data["dataset_role"]),
            case_count=int(data["case_count"]),
            split_counts={str(key): int(value) for key, value in split_counts.items()},
            manifest_relative_path=str(data["manifest_relative_path"]),
            manifest_sha256=str(data["manifest_sha256"]),
            split_plan_sha256=str(data["split_plan_sha256"]),
            contract_sha256=str(data["contract_sha256"]),
            required_artifact_names=tuple(str(value) for value in artifacts),
            projection_coordinate_contract_id=str(
                data["projection_coordinate_contract_id"]
            ),
            loader_transform_id=str(data["loader_transform_id"]),
            frozen_utc=str(data["frozen_utc"]),
            status=str(data["status"]),
        )


def _safe_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise CaseWriteError(f"{name} must be a filesystem-safe non-empty identifier")
    return value


def _finite_number(value: object, name: str, *, lower: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise CaseWriteError(f"{name} must be finite")
    result = float(value)
    if lower is not None and result < lower:
        raise CaseWriteError(f"{name} must be >= {lower}")
    return result


def _require_mapping(container: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = container.get(key)
    if not isinstance(value, Mapping):
        raise CaseWriteError(f"metadata.{key} must be an object")
    return value


def _require_keys(value: Mapping[str, object], required: set[str], name: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise CaseWriteError(f"{name} missing required fields: {missing}")


def _require_exact_keys(
    value: Mapping[str, object],
    required: set[str],
    name: str,
) -> None:
    _require_keys(value, required, name)
    unknown = sorted(set(value) - required)
    if unknown:
        raise CaseWriteError(f"{name} has unknown fields: {unknown}")


def _validate_coordinate_metadata(
    metadata: Mapping[str, object],
) -> ProjectionCoordinatesV1:
    """Validate the source-array and projection bridge without touching bytes."""

    spatial = _require_mapping(metadata, "spatial")
    if spatial.get("axis_order") != "ZYX" or spatial.get("orientation_code") != "SAR":
        raise CaseWriteError(
            "metadata.spatial must identify ZYX array components with positive "
            "directions SAR; the diagonal array affine is not standard RAS"
        )

    acquisition = _require_mapping(metadata, "acquisition")
    raw_contract = acquisition.get("projection_coordinates")
    try:
        contract = validate_projection_coordinates_v1(
            raw_contract,
            context="metadata.acquisition.projection_coordinates",
        )
    except SchemaValidationError as exc:
        raise CaseWriteError(str(exc)) from exc
    starting_angle = _finite_number(
        acquisition.get("starting_angle_deg"),
        "metadata.acquisition.starting_angle_deg",
    )
    if not math.isclose(
        starting_angle,
        contract.simind_starting_angle_deg,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise CaseWriteError(
            "metadata.acquisition.starting_angle_deg must match the frozen "
            "SIMIND coordinate contract"
        )
    if acquisition.get("rotation_direction") != contract.rotation_direction:
        raise CaseWriteError(
            "metadata.acquisition.rotation_direction must match the frozen "
            "projection coordinate contract"
        )
    return contract


def _finite_sequence(
    value: object,
    name: str,
    *,
    length: int,
    lower: float | None = None,
) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise CaseWriteError(f"{name} must contain exactly {length} values")
    return tuple(
        _finite_number(item, f"{name}[{index}]", lower=lower)
        for index, item in enumerate(value)
    )


def _validate_liver_metrics(
    metrics: Mapping[str, object],
    *,
    actual: bool,
) -> None:
    required = {
        "volume_ml",
        "extent_mm_zyx",
        "centroid_world_mm",
        "left_fraction",
        "s1_3_to_s4_8_ratio",
        "caudate_fraction",
        "surface_roughness",
    }
    if actual:
        required |= {"surface_area_mm2", "sphericity"}
    _require_keys(metrics, required, "actual liver metrics" if actual else "target liver metrics")
    _finite_number(metrics["volume_ml"], "liver volume_ml", lower=0)
    _finite_sequence(metrics["extent_mm_zyx"], "liver extent_mm_zyx", length=3, lower=0)
    _finite_sequence(metrics["centroid_world_mm"], "liver centroid_world_mm", length=3)
    left = _finite_number(metrics["left_fraction"], "liver left_fraction", lower=0)
    caudate = _finite_number(metrics["caudate_fraction"], "liver caudate_fraction", lower=0)
    if left > 1 or caudate > left:
        raise CaseWriteError("liver fractions are invalid")
    _finite_number(metrics["s1_3_to_s4_8_ratio"], "liver s1_3_to_s4_8_ratio", lower=0)
    _finite_number(metrics["surface_roughness"], "liver surface_roughness", lower=0)
    if actual:
        _finite_number(metrics["surface_area_mm2"], "liver surface_area_mm2", lower=0)
        sphericity = _finite_number(metrics["sphericity"], "liver sphericity", lower=0)
        if sphericity > 1.0 + 1e-6:
            raise CaseWriteError("liver sphericity must not exceed 1")


def _validate_path_lengths(metrics: Mapping[str, object], *, views: int = 60) -> None:
    _require_keys(
        metrics,
        {"angles_deg", "body", "liver", "support_definition"},
        "metadata.actual_metrics.path_lengths",
    )
    _finite_sequence(metrics["angles_deg"], "path length angles_deg", length=views)
    for mask_name in ("body", "liver"):
        values = metrics[mask_name]
        if not isinstance(values, list) or len(values) != views:
            raise CaseWriteError(f"path length {mask_name} must contain one record per view")
        for index, raw in enumerate(values):
            if not isinstance(raw, Mapping):
                raise CaseWriteError(f"path length {mask_name}[{index}] must be an object")
            _require_keys(raw, {"mean_mm", "p05_mm", "p50_mm", "p95_mm"}, f"path length {mask_name}[{index}]")
            mean = _finite_number(raw["mean_mm"], "path mean_mm", lower=0)
            p05 = _finite_number(raw["p05_mm"], "path p05_mm", lower=0)
            p50 = _finite_number(raw["p50_mm"], "path p50_mm", lower=0)
            p95 = _finite_number(raw["p95_mm"], "path p95_mm", lower=0)
            if not p05 <= p50 <= p95 or not p05 <= mean <= p95:
                raise CaseWriteError("path length quantiles/mean are inconsistent")


def _validate_tumor_metrics(
    target: Mapping[str, object],
    actual: Mapping[str, object],
) -> None:
    _require_keys(target, {"count_bin", "dmax_bin", "lobe_extent"}, "target tumor metrics")
    _require_keys(
        actual,
        {
            "count_bin",
            "realized_count",
            "lobe_extent",
            "tumor_union_fraction_liver",
            "tumor_union_fraction_perfused",
            "lesions",
        },
        "actual tumor metrics",
    )
    count = actual["realized_count"]
    lesions = actual["lesions"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise CaseWriteError("actual tumor realized_count must be a non-negative integer")
    if not isinstance(lesions, list) or len(lesions) != count:
        raise CaseWriteError("actual tumor lesions must match realized_count")
    for fraction_name in ("tumor_union_fraction_liver", "tumor_union_fraction_perfused"):
        fraction = _finite_number(actual[fraction_name], fraction_name, lower=0)
        if fraction > 1:
            raise CaseWriteError(f"{fraction_name} must not exceed 1")
    required_lesion = {
        "instance_id",
        "center_world_mm",
        "normalized_liver_coordinate_zyx",
        "liver_region_proxy",
        "capsule_clearance_mm",
        "recist_3d_mm",
        "principal_axes_mm",
        "equivalent_diameter_mm",
        "volume_ml",
        "sphericity",
        "morphology",
        "necrotic_fraction",
        "tnr_mean",
        "tnr_max",
    }
    instance_ids: list[int] = []
    for index, lesion in enumerate(lesions):
        if not isinstance(lesion, Mapping):
            raise CaseWriteError(f"actual tumor lesion {index} must be an object")
        _require_keys(lesion, required_lesion, f"actual tumor lesion {index}")
        instance_id = lesion["instance_id"]
        if not isinstance(instance_id, int) or isinstance(instance_id, bool) or instance_id < 1:
            raise CaseWriteError("lesion instance_id must be a positive integer")
        instance_ids.append(instance_id)
        _finite_sequence(lesion["center_world_mm"], "lesion center_world_mm", length=3)
        normalized = _finite_sequence(
            lesion["normalized_liver_coordinate_zyx"],
            "lesion normalized_liver_coordinate_zyx",
            length=3,
        )
        if any(value < 0 or value > 1 for value in normalized):
            raise CaseWriteError("normalized liver coordinates must be within [0, 1]")
        if lesion["liver_region_proxy"] not in {1, 2, 3, 4, 5}:
            raise CaseWriteError("lesion liver_region_proxy must be 1..5")
        for name in (
            "capsule_clearance_mm",
            "recist_3d_mm",
            "equivalent_diameter_mm",
            "volume_ml",
            "sphericity",
            "necrotic_fraction",
            "tnr_mean",
            "tnr_max",
        ):
            _finite_number(lesion[name], f"lesion {name}", lower=0)
        _finite_sequence(lesion["principal_axes_mm"], "lesion principal_axes_mm", length=3, lower=0)
        if float(lesion["sphericity"]) > 1.0 + 1e-6:
            raise CaseWriteError("lesion sphericity must not exceed 1")
        if not 0 <= float(lesion["necrotic_fraction"]) <= 1:
            raise CaseWriteError("lesion necrotic_fraction must be within [0, 1]")
        if float(lesion["tnr_max"]) < float(lesion["tnr_mean"]):
            raise CaseWriteError("lesion tnr_max must be at least tnr_mean")
    if instance_ids != list(range(1, count + 1)):
        raise CaseWriteError("metadata lesion instance_ids must be contiguous from 1")


def _validate_metadata(payload: CasePayloadV2, shape: tuple[int, int, int]) -> dict[str, object]:
    metadata = dict(payload.metadata)
    unknown = sorted(set(metadata) - _METADATA_FIELDS)
    missing = sorted(_METADATA_FIELDS - set(metadata))
    if unknown:
        raise CaseWriteError(f"unknown metadata fields: {unknown}")
    if missing:
        raise CaseWriteError(f"missing metadata fields: {missing}")

    seeds = _require_mapping(metadata, "seeds")
    seed_fields = {"global_seed", "patient", "liver", "tumor", "activity", "mu", "simind"}
    if set(seeds) != seed_fields or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in seeds.values()
    ):
        raise CaseWriteError(f"metadata.seeds must contain exact integer fields {sorted(seed_fields)}")
    expected_seeds = SeedBundle.from_case(int(seeds["global_seed"]), payload.case_id)
    if any(seeds[name] != expected_seeds.child_seeds[name] for name in expected_seeds.child_seeds):
        raise CaseWriteError(
            "metadata child seeds must be derived from global_seed and case_id"
        )

    hashes = _require_mapping(metadata, "config_hashes")
    hash_fields = {
        "evidence_registry_sha256",
        "population_config_sha256",
        "scanner_config_sha256",
        "simind_ini_sha256",
    }
    if set(hashes) != hash_fields or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in hashes.values()):
        raise CaseWriteError(f"metadata.config_hashes must contain exact SHA-256 fields {sorted(hash_fields)}")

    patient = _require_mapping(metadata, "patient")
    _require_keys(
        patient,
        {"sex", "age_years", "height_cm", "weight_kg", "bmi", "liver_morphology", "evidence_types"},
        "metadata.patient",
    )
    if patient["liver_morphology"] not in {"normal", "cirrhotic"}:
        raise CaseWriteError("metadata.patient.liver_morphology must be normal or cirrhotic")

    target_metrics = _require_mapping(metadata, "target_metrics")
    actual_metrics = _require_mapping(metadata, "actual_metrics")
    _require_keys(target_metrics, {"liver", "tumors"}, "metadata.target_metrics")
    _require_keys(actual_metrics, {"liver", "path_lengths", "tumors"}, "metadata.actual_metrics")
    target_liver = target_metrics["liver"]
    actual_liver = actual_metrics["liver"]
    target_tumors = target_metrics["tumors"]
    actual_tumors = actual_metrics["tumors"]
    if not all(
        isinstance(value, Mapping)
        for value in (target_liver, actual_liver, target_tumors, actual_tumors)
    ):
        raise CaseWriteError("target/actual liver and tumor metrics must be objects")
    _validate_liver_metrics(target_liver, actual=False)
    _validate_liver_metrics(actual_liver, actual=True)
    _validate_tumor_metrics(target_tumors, actual_tumors)
    path_lengths = actual_metrics["path_lengths"]
    if not isinstance(path_lengths, Mapping):
        raise CaseWriteError("metadata.actual_metrics.path_lengths must be an object")
    _validate_path_lengths(path_lengths)

    activity = _require_mapping(metadata, "activity")
    _require_keys(
        activity,
        {
            "injection_territory",
            "activity_pattern",
            "perfused_volume_ml",
            "injection_tumor_coverage_fraction",
            "tumor_volume_fraction_perfused",
            "mismatch_challenge",
        },
        "metadata.activity",
    )
    _finite_number(activity["perfused_volume_ml"], "activity perfused_volume_ml", lower=0)
    for name in ("injection_tumor_coverage_fraction", "tumor_volume_fraction_perfused"):
        fraction = _finite_number(activity[name], f"activity {name}", lower=0)
        if fraction > 1:
            raise CaseWriteError(f"activity {name} must not exceed 1")
    if not isinstance(activity["mismatch_challenge"], bool):
        raise CaseWriteError("activity mismatch_challenge must be boolean")

    spatial = _require_mapping(metadata, "spatial")
    _require_exact_keys(
        spatial,
        {"affine_4x4", "world_origin_mm", "orientation_code", "axis_order", "reference_phase", "dvf_convention", "dvf_units"},
        "metadata.spatial",
    )
    affine = np.asarray(spatial["affine_4x4"], dtype=np.float64)
    origin = np.asarray(spatial["world_origin_mm"], dtype=np.float64)
    if affine.shape != (4, 4) or not np.isfinite(affine).all() or origin.shape != (3,) or not np.isfinite(origin).all():
        raise CaseWriteError("metadata.spatial affine/origin are invalid")
    if not np.allclose(affine[:3, 3], origin, atol=1e-8):
        raise CaseWriteError("metadata.spatial.world_origin_mm must equal affine translation")
    if (
        spatial["axis_order"] != "ZYX"
        or spatial["orientation_code"] != "SAR"
        or spatial["reference_phase"] != "end_expiration"
    ):
        raise CaseWriteError(
            "metadata.spatial must use ZYX components, SAR positive directions, "
            "and end_expiration"
        )
    if spatial["dvf_convention"] != "ref_to_phase" or spatial["dvf_units"] != "mm":
        raise CaseWriteError("metadata.spatial must freeze ref_to_phase DVF in mm")

    acquisition = _require_mapping(metadata, "acquisition")
    _require_exact_keys(
        acquisition,
        {
            "matrix",
            "voxel_size_mm",
            "views",
            "starting_angle_deg",
            "rotation_direction",
            "orbit_cm",
            "energy_window_kev",
            "projection_coordinates",
        },
        "metadata.acquisition",
    )
    if tuple(acquisition["matrix"]) != shape:
        raise CaseWriteError("metadata.acquisition.matrix must match array shape")
    if acquisition["views"] != 60:
        raise CaseWriteError("metadata.acquisition.views must be 60")
    coordinate_contract = _validate_coordinate_metadata(metadata)
    expected_path_angles = np.asarray(
        [
            (
                coordinate_contract.projector_starting_angle_deg
                + index * 360.0 / int(acquisition["views"])
            )
            % 360.0
            for index in range(int(acquisition["views"]))
        ],
        dtype=np.float64,
    )
    actual_path_angles = np.asarray(path_lengths["angles_deg"], dtype=np.float64)
    if not np.allclose(actual_path_angles, expected_path_angles, rtol=0.0, atol=1e-9):
        raise CaseWriteError(
            "metadata.actual_metrics.path_lengths.angles_deg must use the frozen "
            "PAR-S common-projector angles"
        )

    physics = _require_mapping(metadata, "physics")
    _require_keys(
        physics,
        {
            "base_histories_per_projection",
            "activity_mbq",
            "time_per_projection_s",
            "smc_index25",
            "nn_multiplier",
            "rr_seed",
            "hepatic_only",
            "lung_shunt_fraction",
            "extrahepatic_uptake",
        },
        "metadata.physics",
    )
    base_histories = _finite_number(physics["base_histories_per_projection"], "base histories", lower=1)
    activity_mbq = _finite_number(physics["activity_mbq"], "activity_mbq", lower=0)
    time_s = _finite_number(physics["time_per_projection_s"], "time_per_projection_s", lower=0)
    smc_index25 = _finite_number(physics["smc_index25"], "smc_index25", lower=0)
    if not math.isclose(activity_mbq * time_s, smc_index25, rel_tol=0.0, abs_tol=1e-6):
        raise CaseWriteError("smc_index25 must equal activity_mbq times time_per_projection_s")
    if physics["rr_seed"] != seeds["simind"]:
        raise CaseWriteError("physics.rr_seed must equal the simind child seed")
    if physics["hepatic_only"] is not True or physics["lung_shunt_fraction"] != 0 or physics["extrahepatic_uptake"] is not False:
        raise CaseWriteError("V2 main contract requires hepatic-only activity with zero shunt and no extrahepatic uptake")

    simulation = _require_mapping(metadata, "simulation")
    quality = _require_mapping(metadata, "quality_control")
    _require_keys(simulation, {"status"}, "metadata.simulation")
    _require_keys(quality, {"status", "failed_gates"}, "metadata.quality_control")
    if not isinstance(quality["failed_gates"], list) or not all(
        isinstance(value, str) for value in quality["failed_gates"]
    ):
        raise CaseWriteError("metadata.quality_control.failed_gates must be a string list")
    if simulation["status"] == "complete":
        _require_keys(
            simulation,
            {
                "exit_code",
                "command",
                "simind_version",
                "binary_sha256",
                "smc_snapshot_sha256",
                "simind_ini_snapshot_sha256",
                "input_sha256",
                "output_sha256",
                "projection_stats",
                "completion_status",
            },
            "metadata.simulation",
        )
        if simulation["exit_code"] != 0 or simulation["completion_status"] != "complete":
            raise CaseWriteError("completed simulation requires exit_code=0 and completion_status=complete")
        if not isinstance(simulation["command"], list) or not simulation["command"] or not all(
            isinstance(value, str) and value for value in simulation["command"]
        ):
            raise CaseWriteError("simulation command must be a non-empty string list")
        for name in ("binary_sha256", "smc_snapshot_sha256", "simind_ini_snapshot_sha256"):
            if not isinstance(simulation[name], str) or not _SHA256.fullmatch(simulation[name]):
                raise CaseWriteError(f"simulation {name} must be a SHA-256 digest")
        if simulation["simind_ini_snapshot_sha256"] != hashes["simind_ini_sha256"]:
            raise CaseWriteError("SIMIND ini snapshot hash must match config provenance")
        for group_name in ("input_sha256", "output_sha256"):
            group = simulation[group_name]
            if not isinstance(group, Mapping) or not group or any(
                not isinstance(value, str) or not _SHA256.fullmatch(value)
                for value in group.values()
            ):
                raise CaseWriteError(f"simulation {group_name} must be a non-empty SHA-256 map")
        if set(simulation["output_sha256"]) != {"a00", "mhd", "res", "spe"}:
            raise CaseWriteError("simulation output_sha256 must contain the SIMIND quartet")
        projection = simulation["projection_stats"]
        if not isinstance(projection, Mapping):
            raise CaseWriteError("simulation projection_stats must be an object")
        _require_keys(
            projection,
            {"view_count", "projection_weight_sum", "projection_per_view_weight_sum", "finite"},
            "simulation projection_stats",
        )
        if projection["view_count"] != 60 or projection["finite"] is not True:
            raise CaseWriteError("simulation projection must contain 60 finite views")
        _finite_number(projection["projection_weight_sum"], "projection_weight_sum", lower=0)
        _finite_sequence(
            projection["projection_per_view_weight_sum"],
            "projection_per_view_weight_sum",
            length=60,
            lower=0,
        )
    elif simulation["status"] not in {"pending", "failed"}:
        raise CaseWriteError("simulation status must be pending, failed, or complete")

    try:
        compatible = json_compatible(metadata)
        canonical_json_bytes(compatible)
    except (TypeError, ValueError) as exc:
        raise CaseWriteError(f"metadata is not strict JSON: {exc}") from exc
    compatible["physics"]["base_histories_per_projection"] = int(base_histories)
    return compatible


def _validate_arrays(payload: CasePayloadV2) -> tuple[dict[str, np.ndarray], tuple[int, int, int]]:
    expected = set(ARRAY_CONTRACT_V2)
    actual = set(payload.arrays)
    if actual != expected:
        raise CaseWriteError(
            f"required array keys mismatch; missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )
    arrays: dict[str, np.ndarray] = {}
    shape: tuple[int, int, int] | None = None
    for key, spec in ARRAY_CONTRACT_V2.items():
        array = np.asarray(payload.arrays[key])
        if array.dtype != np.dtype(spec.dtype):
            raise CaseWriteError(f"{key} must have dtype {spec.dtype}, got {array.dtype}")
        if array.ndim != 3:
            raise CaseWriteError(f"{key} must be a 3D array")
        if shape is None:
            shape = tuple(int(value) for value in array.shape)
        elif array.shape != shape:
            raise CaseWriteError(f"{key} shape must match {shape}")
        arrays[key] = np.ascontiguousarray(array)
    assert shape is not None

    floats = ("activity_relative", "activity_probability", "simind_source_weights", "mu_true_140kev", "mu_input_140kev")
    for key in floats:
        if not np.isfinite(arrays[key]).all() or np.any(arrays[key] < 0):
            raise CaseWriteError(f"{key} must be finite and non-negative")
    for key in ("body_mask", "liver_mask", "tumor_union_mask", "perfusion_mask"):
        if not np.isin(arrays[key], (0, 1)).all():
            raise CaseWriteError(f"{key} must contain only 0 and 1")
    if not np.isin(arrays["liver_region_proxy"], range(6)).all():
        raise CaseWriteError("liver_region_proxy must contain labels 0..5")

    body = arrays["body_mask"] > 0
    liver = arrays["liver_mask"] > 0
    tumor_instances = arrays["tumor_instance_mask"]
    tumor_union = arrays["tumor_union_mask"] > 0
    perfusion = arrays["perfusion_mask"] > 0
    if not body.any() or not liver.any() or np.any(liver & ~body):
        raise CaseWriteError("liver_mask must be non-empty and contained in body_mask")
    if not np.array_equal(arrays["liver_region_proxy"] > 0, liver):
        raise CaseWriteError("liver_region_proxy must exactly partition liver_mask")
    if np.any((tumor_instances > 0) & ~liver):
        raise CaseWriteError("tumor instances must be completely contained in liver")
    if not np.array_equal(tumor_instances > 0, tumor_union):
        raise CaseWriteError("tumor_union_mask must equal tumor_instance_mask > 0")
    if np.any(perfusion & ~liver) or not perfusion.any():
        raise CaseWriteError("perfusion_mask must be non-empty and contained in liver")
    instance_ids = np.unique(tumor_instances[tumor_instances > 0])
    if len(instance_ids) and not np.array_equal(instance_ids, np.arange(1, len(instance_ids) + 1, dtype=np.uint16)):
        raise CaseWriteError("tumor instance labels must be contiguous from 1")

    if np.any(arrays["activity_relative"][~liver] != 0) or np.any(arrays["activity_probability"][~liver] != 0):
        raise CaseWriteError("activity arrays must be zero outside liver")
    probability_sum = float(arrays["activity_probability"].sum(dtype=np.float64))
    if not math.isclose(probability_sum, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise CaseWriteError(f"activity_probability must sum to 1, got {probability_sum}")
    for key in ("mu_true_140kev", "mu_input_140kev"):
        if np.any(arrays[key][~body] != 0):
            raise CaseWriteError(f"{key} must be zero outside body_mask")

    return arrays, shape


def _validate_payload(payload: CasePayloadV2) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    if not isinstance(payload, CasePayloadV2):
        raise CaseWriteError("case must be CasePayloadV2")
    for name in ("case_id", "case_family_id", "profile_id", "dataset_id"):
        _safe_identifier(getattr(payload, name), name)
    if not isinstance(payload.dataset_version, str) or not payload.dataset_version.strip():
        raise CaseWriteError("dataset_version must be a non-empty string")
    if payload.dataset_role not in _DATASET_ROLES:
        raise CaseWriteError(f"dataset_role must be one of {_DATASET_ROLES}")
    if payload.split not in _SPLITS:
        raise CaseWriteError(f"split must be one of {_SPLITS}")
    weight = _finite_number(payload.population_weight, "population_weight", lower=0)
    probability = _finite_number(payload.sampling_probability, "sampling_probability", lower=0)
    if probability > 1:
        raise CaseWriteError("sampling_probability must not exceed 1")
    if payload.dataset_role == "negative" and weight != 0:
        raise CaseWriteError("negative datasets require population_weight=0")

    arrays, shape = _validate_arrays(payload)
    has_tumor = bool(np.any(arrays["tumor_instance_mask"] > 0))
    if payload.dataset_role == "main" and not has_tumor:
        raise CaseWriteError("main dataset cases require at least one tumor instance")
    if payload.dataset_role == "negative" and has_tumor:
        raise CaseWriteError("negative dataset cases must not contain tumor instances")
    metadata = _validate_metadata(payload, shape)
    instance_ids = np.unique(
        arrays["tumor_instance_mask"][arrays["tumor_instance_mask"] > 0]
    )
    metadata_tumors = metadata["actual_metrics"]["tumors"]
    if int(metadata_tumors["realized_count"]) != len(instance_ids):
        raise CaseWriteError(
            "metadata actual tumor realized_count must match tumor_instance_mask"
        )
    base_histories = float(metadata["physics"]["base_histories_per_projection"])
    expected_weights = arrays["activity_probability"] * base_histories
    if not np.allclose(arrays["simind_source_weights"], expected_weights, rtol=2e-6, atol=2e-5):
        raise CaseWriteError("simind_source_weights must equal activity_probability times base histories")
    histories_sum = float(arrays["simind_source_weights"].sum(dtype=np.float64))
    if not math.isclose(histories_sum, base_histories, rel_tol=2e-6, abs_tol=1e-3):
        raise CaseWriteError("simind_source_weights must sum to base histories per projection")
    return arrays, metadata


def _metadata_document(payload: CasePayloadV2, metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": CASE_SCHEMA_VERSION,
        "dataset_version": payload.dataset_version,
        "dataset_id": payload.dataset_id,
        "dataset_role": payload.dataset_role,
        "case_id": payload.case_id,
        "case_family_id": payload.case_family_id,
        "profile_id": payload.profile_id,
        "split": payload.split,
        "population_weight": float(payload.population_weight),
        "sampling_probability": float(payload.sampling_probability),
        "array_contract": {
            key: spec.to_dict() for key, spec in ARRAY_CONTRACT_V2.items()
        },
        **metadata,
    }


def _write_deterministic_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    """Write an NPZ whose bytes do not depend on wall-clock ZIP timestamps."""
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for key in sorted(arrays):
            buffer = io.BytesIO()
            np.lib.format.write_array(buffer, np.asarray(arrays[key]), allow_pickle=False)
            info = zipfile.ZipInfo(f"{key}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, buffer.getvalue(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _identity_document(payload: CasePayloadV2) -> dict[str, object]:
    return {
        "schema_version": "pars_dataset_identity_v2",
        "dataset_id": payload.dataset_id,
        "dataset_version": payload.dataset_version,
        "dataset_role": payload.dataset_role,
    }


def _ensure_identity(root: Path, payload: CasePayloadV2) -> None:
    path = root / DATASET_IDENTITY_FILENAME
    expected = _identity_document(payload)
    if path.exists():
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaseWriteError(f"invalid dataset identity: {exc}") from exc
        if actual != expected:
            raise CaseWriteError("dataset identity mismatch for output root")
    else:
        atomic_write_json(path, expected)


def _load_split_plan(path: Path) -> SplitPlanV2:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetFreezeError(f"cannot read split plan: {exc}") from exc
    allowed = {
        "schema_version",
        "dataset_id",
        "profile_id",
        "global_seed",
        "ratios",
        "family_to_split",
        "family_seeds",
        "sha256",
    }
    if set(data) != allowed or data.get("schema_version") != SPLIT_PLAN_SCHEMA_VERSION:
        raise DatasetFreezeError("split plan has invalid schema")
    try:
        plan = SplitPlanV2(
            dataset_id=str(data["dataset_id"]),
            profile_id=str(data["profile_id"]),
            global_seed=int(data["global_seed"]),
            ratios={str(key): float(value) for key, value in data["ratios"].items()},
            family_to_split={str(key): str(value) for key, value in data["family_to_split"].items()},
            family_seeds={str(key): int(value) for key, value in data["family_seeds"].items()},
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise DatasetFreezeError(f"split plan has invalid field types: {exc}") from exc
    if data["sha256"] != plan.sha256:
        raise DatasetFreezeError("split plan SHA-256 does not match its content")
    if set(plan.ratios) != set(_SPLITS) or any(
        not math.isfinite(value) or value < 0 for value in plan.ratios.values()
    ) or not math.isclose(sum(plan.ratios.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise DatasetFreezeError("split plan ratios are invalid")
    if not plan.family_to_split or set(plan.family_seeds) != set(plan.family_to_split):
        raise DatasetFreezeError("split plan family assignments and seeds do not pair")
    if any(split not in _SPLITS for split in plan.family_to_split.values()):
        raise DatasetFreezeError("split plan contains an invalid split label")
    expected_family_seeds = {
        family: SeedBundle.from_case(plan.global_seed, family).case_seed
        for family in plan.family_to_split
    }
    if plan.family_seeds != expected_family_seeds:
        raise DatasetFreezeError(
            "split plan family seeds must be derived from global_seed and case_family_id"
        )
    return plan


def build_split_plan(
    case_family_ids: Sequence[str],
    *,
    dataset_id: str,
    profile_id: str,
    global_seed: int,
    ratios: Mapping[str, float] | None = None,
) -> SplitPlanV2:
    """Assign complete families before generation, with deterministic exact quotas."""
    _safe_identifier(dataset_id, "dataset_id")
    _safe_identifier(profile_id, "profile_id")
    if not isinstance(global_seed, int) or isinstance(global_seed, bool) or global_seed < 0:
        raise DatasetFreezeError("global_seed must be a non-negative integer")
    families = tuple(str(value) for value in case_family_ids)
    if not families or len(set(families)) != len(families):
        raise DatasetFreezeError("case_family_ids must be non-empty and unique")
    for family in families:
        _safe_identifier(family, "case_family_id")
    ratio_values = dict(ratios or {"train": 0.8, "val": 0.1, "test": 0.1})
    if set(ratio_values) != set(_SPLITS):
        raise DatasetFreezeError(f"ratios must contain exactly {_SPLITS}")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or value < 0 for value in ratio_values.values()):
        raise DatasetFreezeError("split ratios must be finite and non-negative")
    total_ratio = float(sum(ratio_values.values()))
    if not math.isclose(total_ratio, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise DatasetFreezeError("split ratios must sum to 1")

    raw_counts = {name: float(ratio_values[name]) * len(families) for name in _SPLITS}
    counts = {name: int(math.floor(raw_counts[name])) for name in _SPLITS}
    remainder = len(families) - sum(counts.values())
    order = sorted(_SPLITS, key=lambda name: (-(raw_counts[name] - counts[name]), _SPLITS.index(name)))
    for name in order[:remainder]:
        counts[name] += 1

    def ordering(family: str) -> str:
        return sha256_bytes(f"pars-split-v2|{dataset_id}|{global_seed}|{family}".encode("utf-8"))

    ordered = sorted(families, key=lambda family: (ordering(family), family))
    assignments: dict[str, str] = {}
    cursor = 0
    for split in _SPLITS:
        for family in ordered[cursor : cursor + counts[split]]:
            assignments[family] = split
        cursor += counts[split]
    seeds = {
        family: SeedBundle.from_case(global_seed, family).case_seed
        for family in sorted(families)
    }
    return SplitPlanV2(
        dataset_id=dataset_id,
        profile_id=profile_id,
        global_seed=global_seed,
        ratios={name: float(ratio_values[name]) for name in _SPLITS},
        family_to_split=dict(sorted(assignments.items())),
        family_seeds=seeds,
    )


def write_split_plan(plan: SplitPlanV2, output_root: str | Path) -> Path:
    if not isinstance(plan, SplitPlanV2):
        raise DatasetFreezeError("plan must be SplitPlanV2")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / SPLIT_PLAN_FILENAME
    if path.exists():
        existing = _load_split_plan(path)
        if existing != plan:
            raise DatasetFreezeError("immutable split plan already exists with different content")
        return path
    cases = root / "cases"
    if cases.exists() and any(cases.iterdir()):
        raise DatasetFreezeError("split plan must be written before any case generation")
    atomic_write_json(path, plan.to_dict())
    return path


def _artifact(path: Path, root: Path) -> ArtifactRecordV2:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise CaseWriteError(f"artifact path escapes dataset root: {path}") from exc
    return ArtifactRecordV2(
        relative_path=relative,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
    )


def _verify_record_artifacts(record: CaseRecordV2, root: Path) -> None:
    mandatory = {"phantom_npz", "metadata_json"}
    if not mandatory.issubset(record.artifacts):
        raise DatasetFreezeError(f"case {record.case_id} is missing mandatory artifacts")
    for name, artifact in record.artifacts.items():
        relative = Path(artifact.relative_path)
        expected_prefix = Path("cases") / record.case_id
        if relative.is_absolute() or relative.parts[:2] != expected_prefix.parts:
            raise DatasetFreezeError(
                f"case {record.case_id} artifact {name} is not inside its own case directory"
            )
        if name == "phantom_npz" and relative != expected_prefix / "phantom.npz":
            raise DatasetFreezeError(f"case {record.case_id} phantom path violates pairing contract")
        if name == "metadata_json" and relative != expected_prefix / "metadata.json":
            raise DatasetFreezeError(f"case {record.case_id} metadata path violates pairing contract")
        try:
            path = resolve_relative_path(artifact.relative_path, root)
        except ValueError as exc:
            raise DatasetFreezeError(str(exc)) from exc
        if not path.is_file():
            raise DatasetFreezeError(f"case {record.case_id} artifact {name} is missing")
        if path.stat().st_size != artifact.size_bytes:
            raise DatasetFreezeError(f"case {record.case_id} artifact {name} size mismatch")
        actual = sha256_file(path)
        if actual != artifact.sha256:
            raise DatasetFreezeError(f"case {record.case_id} artifact {name} SHA-256 mismatch")


def load_case_record_v2(
    path: str | Path,
    *,
    dataset_root: str | Path,
    verify_hashes: bool = True,
) -> CaseRecordV2:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseWriteError(f"cannot read case record: {exc}") from exc
    if not isinstance(raw, dict):
        raise CaseWriteError("case record must be a JSON object")
    record = CaseRecordV2.from_dict(raw)
    if verify_hashes:
        try:
            _verify_record_artifacts(record, Path(dataset_root))
        except DatasetFreezeError as exc:
            raise CaseWriteError(str(exc)) from exc
    return record


def _resume_matches(
    payload: CasePayloadV2,
    arrays: Mapping[str, np.ndarray],
    metadata_document: Mapping[str, object],
    record: CaseRecordV2,
    root: Path,
) -> bool:
    coordinate_contract = _validate_coordinate_metadata(payload.metadata)
    identity = (
        payload.case_id,
        payload.case_family_id,
        payload.profile_id,
        payload.dataset_id,
        payload.dataset_version,
        payload.dataset_role,
        payload.split,
        float(payload.population_weight),
        float(payload.sampling_probability),
        coordinate_contract.coordinate_contract_id,
        coordinate_contract.loader_transform_id,
    )
    recorded = (
        record.case_id,
        record.case_family_id,
        record.profile_id,
        record.dataset_id,
        record.dataset_version,
        record.dataset_role,
        record.split,
        record.population_weight,
        record.sampling_probability,
        record.projection_coordinate_contract_id,
        record.loader_transform_id,
    )
    if identity != recorded:
        return False
    metadata_path = resolve_relative_path(record.artifacts["metadata_json"].relative_path, root)
    if json.loads(metadata_path.read_text(encoding="utf-8")) != metadata_document:
        return False
    npz_path = resolve_relative_path(record.artifacts["phantom_npz"].relative_path, root)
    with np.load(npz_path, allow_pickle=False) as existing:
        if set(existing.files) != set(arrays):
            return False
        if any(not np.array_equal(existing[key], arrays[key]) for key in arrays):
            return False
    extras = set(record.artifacts) - {"phantom_npz", "metadata_json"}
    if extras != set(payload.extra_artifacts):
        return False
    return all(
        sha256_file(payload.extra_artifacts[name]) == record.artifacts[name].sha256
        for name in extras
    )


def _extra_artifact_destination_names(
    extra_artifacts: Mapping[str, Path],
) -> dict[str, str]:
    """Choose safe copy names while preserving the SIMIND quartet pairing."""

    names = set(extra_artifacts)
    quartet_names = set(_SIMIND_QUARTET_SUFFIXES)
    touched = names & (quartet_names | {_SIMIND_PROVENANCE_ARTIFACT})
    if touched and not (quartet_names | {_SIMIND_PROVENANCE_ARTIFACT}).issubset(names):
        missing = sorted(
            (quartet_names | {_SIMIND_PROVENANCE_ARTIFACT}) - names
        )
        raise CaseWriteError(
            "SIMIND projection quartet and run provenance are inseparable; "
            f"missing={missing}"
        )

    destinations: dict[str, str] = {}
    if quartet_names.issubset(names):
        quartet_sources = {
            name: Path(extra_artifacts[name]) for name in quartet_names
        }
        for name, suffix in _SIMIND_QUARTET_SUFFIXES.items():
            source = quartet_sources[name]
            if not source.is_file():
                raise CaseWriteError(f"extra artifact does not exist: {source}")
            if source.suffix.casefold() != f".{suffix}":
                raise CaseWriteError(
                    f"{name} must reference a .{suffix} file, got {source.name}"
                )
        stems = {source.stem.casefold() for source in quartet_sources.values()}
        if len(stems) != 1:
            raise CaseWriteError("SIMIND quartet source files must share one stem")
        stem = quartet_sources["projection_a00"].stem
        _safe_identifier(stem, "SIMIND quartet stem")

        try:
            mhd_lines = quartet_sources["projection_mhd"].read_text(
                encoding="ascii", errors="strict"
            ).splitlines()
        except (OSError, UnicodeError) as exc:
            raise CaseWriteError(f"SIMIND MHD cannot be read: {exc}") from exc
        element_values = [
            value.strip()
            for line in mhd_lines
            if "=" in line
            for key, value in [line.split("=", 1)]
            if key.strip().casefold() == "elementdatafile"
        ]
        if len(element_values) != 1:
            raise CaseWriteError("SIMIND MHD must contain exactly one ElementDataFile")
        element_name = element_values[0].replace("\\", "/").split("/")[-1]
        if element_name.casefold() != quartet_sources["projection_a00"].name.casefold():
            raise CaseWriteError(
                "SIMIND MHD ElementDataFile must name the paired A00 before copying"
            )
        destinations.update(
            {name: quartet_sources[name].name for name in quartet_names}
        )

    for name, source_value in sorted(extra_artifacts.items()):
        _safe_identifier(name, "artifact name")
        if name in destinations:
            continue
        source = Path(source_value)
        if not source.is_file():
            raise CaseWriteError(f"extra artifact does not exist: {source}")
        destinations[name] = f"{name}{''.join(source.suffixes)}"

    if len(set(value.casefold() for value in destinations.values())) != len(destinations):
        raise CaseWriteError("extra artifact destination names collide")
    return destinations


def write_case_v2(
    case: CasePayloadV2,
    output_root: str | Path,
    *,
    resume: bool = False,
) -> CaseRecordV2:
    """Validate and atomically publish one immutable V2 case directory."""
    root = Path(output_root)
    final_dir = root / "cases" / str(getattr(case, "case_id", "invalid"))
    if (root / DATASET_COMPLETE_FILENAME).exists() and not (resume and final_dir.is_dir()):
        raise CaseWriteError("dataset is already frozen; late case writes are forbidden")
    arrays, metadata = _validate_payload(case)
    metadata_document = _metadata_document(case, metadata)
    root.mkdir(parents=True, exist_ok=True)

    split_path = root / SPLIT_PLAN_FILENAME
    if not split_path.is_file():
        raise CaseWriteError("SPLIT_PLAN.json must be frozen before case generation")
    try:
        split_plan = _load_split_plan(split_path)
    except DatasetFreezeError as exc:
        raise CaseWriteError(str(exc)) from exc
    if split_plan.dataset_id != case.dataset_id or split_plan.profile_id != case.profile_id:
        raise CaseWriteError("case identity does not match split plan")
    if int(metadata["seeds"]["global_seed"]) != split_plan.global_seed:
        raise CaseWriteError("case global_seed does not match the immutable split plan")
    if split_plan.family_to_split.get(case.case_family_id) != case.split:
        raise CaseWriteError("case split does not match pre-generated family split plan")
    _ensure_identity(root, case)

    record_path = final_dir / "case_record.json"
    if final_dir.exists():
        if not resume:
            raise CaseWriteError(f"duplicate case_id: {case.case_id}")
        try:
            record = load_case_record_v2(record_path, dataset_root=root, verify_hashes=True)
            if not _resume_matches(case, arrays, metadata_document, record, root):
                raise CaseWriteError(f"resume content mismatch for case {case.case_id}")
            return record
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise CaseWriteError(f"resume content mismatch for case {case.case_id}: {exc}") from exc

    staging_root = root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    temporary = staging_root / f"{case.case_id}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir(parents=False, exist_ok=False)
        npz_path = temporary / "phantom.npz"
        metadata_path = temporary / "metadata.json"
        _write_deterministic_npz(npz_path, arrays)
        metadata_path.write_bytes(canonical_json_bytes(metadata_document))

        final_relative_base = Path("cases") / case.case_id
        artifacts: dict[str, ArtifactRecordV2] = {
            "phantom_npz": ArtifactRecordV2(
                (final_relative_base / "phantom.npz").as_posix(),
                npz_path.stat().st_size,
                sha256_file(npz_path),
            ),
            "metadata_json": ArtifactRecordV2(
                (final_relative_base / "metadata.json").as_posix(),
                metadata_path.stat().st_size,
                sha256_file(metadata_path),
            ),
        }
        if case.extra_artifacts:
            artifact_dir = temporary / "artifacts"
            artifact_dir.mkdir()
            destination_names = _extra_artifact_destination_names(
                case.extra_artifacts
            )
            for name, source_value in sorted(case.extra_artifacts.items()):
                _safe_identifier(name, "artifact name")
                if name in artifacts:
                    raise CaseWriteError(f"reserved artifact name: {name}")
                source = Path(source_value)
                destination = artifact_dir / destination_names[name]
                shutil.copyfile(source, destination)
                artifacts[name] = ArtifactRecordV2(
                    (final_relative_base / "artifacts" / destination.name).as_posix(),
                    destination.stat().st_size,
                    sha256_file(destination),
                )

        coordinate_contract = _validate_coordinate_metadata(metadata)
        record = CaseRecordV2(
            case_id=case.case_id,
            case_family_id=case.case_family_id,
            profile_id=case.profile_id,
            dataset_id=case.dataset_id,
            dataset_version=case.dataset_version,
            dataset_role=case.dataset_role,
            split=case.split,
            population_weight=float(case.population_weight),
            sampling_probability=float(case.sampling_probability),
            split_plan_sha256=split_plan.sha256,
            projection_coordinate_contract_id=(
                coordinate_contract.coordinate_contract_id
            ),
            loader_transform_id=coordinate_contract.loader_transform_id,
            artifacts=artifacts,
        )
        (temporary / "case_record.json").write_bytes(canonical_json_bytes(record.to_dict()))
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, final_dir)
        return record
    except CaseWriteError:
        raise
    except Exception as exc:
        raise CaseWriteError(f"atomic case write failed: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if staging_root.exists() and not any(staging_root.iterdir()):
            staging_root.rmdir()


def _validate_record_set(
    records: Sequence[CaseRecordV2],
    *,
    split_plan_sha256: str,
) -> tuple[CaseRecordV2, ...]:
    items = tuple(records)
    if not items:
        raise DatasetFreezeError("at least one case record is required")
    case_ids = [record.case_id for record in items]
    if len(set(case_ids)) != len(case_ids):
        raise DatasetFreezeError("duplicate case_id in manifest records")
    identities = {
        (record.dataset_id, record.dataset_version, record.dataset_role)
        for record in items
    }
    if len(identities) != 1:
        raise DatasetFreezeError("records mix dataset identity")
    family_split: dict[str, str] = {}
    for record in items:
        if record.split_plan_sha256 != split_plan_sha256:
            raise DatasetFreezeError(f"case {record.case_id} split plan hash mismatch")
        if (
            record.projection_coordinate_contract_id
            != PROJECTION_COORDINATE_CONTRACT_ID
            or record.loader_transform_id != FROZEN_LOADER_TRANSFORM_ID
        ):
            raise DatasetFreezeError(
                f"case {record.case_id} record uses an unfrozen projection contract"
            )
        previous = family_split.setdefault(record.case_family_id, record.split)
        if previous != record.split:
            raise DatasetFreezeError(
                f"case_family_id {record.case_family_id!r} appears in multiple splits"
            )
    return tuple(sorted(items, key=lambda record: record.case_id))


def _manifest_bytes(records: Sequence[CaseRecordV2]) -> bytes:
    return b"".join(canonical_json_bytes(record.to_dict()) for record in records)


def write_case_manifest(
    records: Sequence[CaseRecordV2],
    output_root: str | Path,
    *,
    split_plan_sha256: str,
) -> Path:
    """Write a canonical record/artifact pairing manifest, never silently replace it."""
    root = Path(output_root)
    ordered = _validate_record_set(records, split_plan_sha256=split_plan_sha256)
    content = _manifest_bytes(ordered)
    path = root / CASE_MANIFEST_FILENAME
    if path.exists():
        if path.read_bytes() != content:
            raise DatasetFreezeError("immutable case manifest already exists with different content")
        return path
    atomic_write_bytes(path, content)
    return path


def _read_case_metadata(record: CaseRecordV2, root: Path) -> Mapping[str, object]:
    path = resolve_relative_path(record.artifacts["metadata_json"].relative_path, root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetFreezeError(f"case {record.case_id} metadata cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise DatasetFreezeError(f"case {record.case_id} metadata must be an object")
    return data


def _read_json_artifact(
    record: CaseRecordV2,
    root: Path,
    artifact_name: str,
) -> Mapping[str, object]:
    path = resolve_relative_path(record.artifacts[artifact_name].relative_path, root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetFreezeError(
            f"case {record.case_id} {artifact_name} cannot be read: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise DatasetFreezeError(
            f"case {record.case_id} {artifact_name} must be a JSON object"
        )
    return value


def _command_switch(command: object, prefix: str, case_id: str) -> int:
    if not isinstance(command, list) or not all(
        isinstance(value, str) for value in command
    ):
        raise DatasetFreezeError(f"case {case_id} SIMIND command is invalid")
    values = [value for value in command if value.upper().startswith(prefix)]
    if len(values) != 1:
        raise DatasetFreezeError(
            f"case {case_id} SIMIND command must contain exactly one {prefix} switch"
        )
    try:
        return int(values[0].split(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise DatasetFreezeError(
            f"case {case_id} SIMIND command has an invalid {prefix} switch"
        ) from exc


def _snapshot_hash(value: object, case_id: str, name: str) -> str:
    if not isinstance(value, Mapping):
        raise DatasetFreezeError(f"case {case_id} provenance {name} is invalid")
    digest = value.get("sha256")
    snapshot = value.get("snapshot")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise DatasetFreezeError(
            f"case {case_id} provenance {name} SHA-256 is invalid"
        )
    if not isinstance(snapshot, str) or sha256_bytes(snapshot.encode("utf-8")) != digest:
        raise DatasetFreezeError(
            f"case {case_id} provenance {name} snapshot does not match its SHA-256"
        )
    return digest


def _audit_simind_case_binding(
    record: CaseRecordV2,
    root: Path,
    metadata: Mapping[str, object],
) -> tuple[object, ...] | None:
    """Re-audit copied bytes and bind every formal-success identity field."""

    quartet_names = set(_SIMIND_QUARTET_SUFFIXES)
    all_names = quartet_names | {_SIMIND_PROVENANCE_ARTIFACT}
    present = set(record.artifacts) & all_names
    if not present:
        return None
    missing = all_names - set(record.artifacts)
    if missing:
        raise DatasetFreezeError(
            f"case {record.case_id} has an incomplete SIMIND evidence set: {sorted(missing)}"
        )

    paths = {
        name: resolve_relative_path(record.artifacts[name].relative_path, root)
        for name in quartet_names
    }
    a00_path = paths["projection_a00"]
    output_stem = a00_path.with_suffix("")
    for name, suffix in _SIMIND_QUARTET_SUFFIXES.items():
        if paths[name].resolve() != output_stem.with_suffix(f".{suffix}").resolve():
            raise DatasetFreezeError(
                f"case {record.case_id} SIMIND quartet does not share one paired stem"
            )

    provenance = _read_json_artifact(
        record, root, _SIMIND_PROVENANCE_ARTIFACT
    )
    if (
        provenance.get("schema_version") != "pars_simind_run_v2"
        or provenance.get("status") != "complete"
        or provenance.get("case_id") != record.case_id
        or provenance.get("exit_code") != 0
        or not isinstance(provenance.get("protocol_name"), str)
        or not provenance.get("protocol_name")
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND provenance is not a formal successful run"
        )
    expected_shape_raw = provenance.get("expected_shape")
    if not isinstance(expected_shape_raw, list) or len(expected_shape_raw) != 3 or any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in expected_shape_raw
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND expected_shape is invalid"
        )
    expected_shape = tuple(expected_shape_raw)
    try:
        audit = audit_simind_completion(
            output_stem,
            expected_shape=expected_shape,
            exit_code=0,
        )
    except (SimindCompletionError, ValueError) as exc:
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND quartet strict audit failed: {exc}"
        ) from exc
    if provenance.get("completion_audit") != audit.to_dict():
        raise DatasetFreezeError(
            f"case {record.case_id} stored SIMIND completion audit does not match copied bytes"
        )

    simulation = metadata.get("simulation")
    physics = metadata.get("physics")
    hashes = metadata.get("config_hashes")
    if not all(isinstance(value, Mapping) for value in (simulation, physics, hashes)):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND metadata binding fields are invalid"
        )
    assert isinstance(simulation, Mapping)
    assert isinstance(physics, Mapping)
    assert isinstance(hashes, Mapping)

    rr_seed = provenance.get("rr_seed")
    nn_multiplier = provenance.get("nn_multiplier")
    if (
        not isinstance(rr_seed, int)
        or isinstance(rr_seed, bool)
        or not 1 <= rr_seed <= 2_147_483_646
        or not isinstance(nn_multiplier, int)
        or isinstance(nn_multiplier, bool)
        or nn_multiplier <= 0
        or rr_seed != physics.get("rr_seed")
        or nn_multiplier != physics.get("nn_multiplier")
        or simulation.get("exit_code") != provenance.get("exit_code")
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND exit/RR/NN provenance binding mismatch"
        )
    timeout_seconds = provenance.get("timeout_seconds")
    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND timeout provenance is invalid"
        )
    for command in (provenance.get("command"), simulation.get("command")):
        if (
            _command_switch(command, "/RR:", record.case_id) != rr_seed
            or _command_switch(command, "/NN:", record.case_id) != nn_multiplier
        ):
            raise DatasetFreezeError(
                f"case {record.case_id} SIMIND command RR/NN binding mismatch"
            )

    binary_digest = provenance.get("binary_sha256")
    smc_digest = _snapshot_hash(provenance.get("smc"), record.case_id, "smc")
    ini_digest = _snapshot_hash(
        provenance.get("simind_ini"), record.case_id, "simind_ini"
    )
    if (
        not isinstance(binary_digest, str)
        or not _SHA256.fullmatch(binary_digest)
        or binary_digest != simulation.get("binary_sha256")
        or smc_digest != simulation.get("smc_snapshot_sha256")
        or ini_digest != simulation.get("simind_ini_snapshot_sha256")
        or ini_digest != hashes.get("simind_ini_sha256")
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND binary/SMC/INI provenance binding mismatch"
        )

    inputs = provenance.get("inputs")
    metadata_inputs = simulation.get("input_sha256")
    if not isinstance(inputs, Mapping) or not isinstance(metadata_inputs, Mapping) or {
        "source": inputs.get("source_sha256"),
        "density": inputs.get("density_sha256"),
    } != dict(metadata_inputs):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND input hash provenance binding mismatch"
        )

    metadata_outputs = simulation.get("output_sha256")
    artifact_outputs = {
        suffix: record.artifacts[name].sha256
        for name, suffix in _SIMIND_QUARTET_SUFFIXES.items()
    }
    if (
        not isinstance(metadata_outputs, Mapping)
        or dict(metadata_outputs) != dict(audit.sha256)
        or artifact_outputs != dict(audit.sha256)
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND output hash provenance binding mismatch"
        )

    projection_stats = simulation.get("projection_stats")
    if not isinstance(projection_stats, Mapping):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND projection statistics are invalid"
        )
    projections = np.memmap(
        a00_path, dtype="<f4", mode="r", shape=expected_shape
    )
    per_view = np.asarray(
        projections.sum(axis=(1, 2), dtype=np.float64), dtype=np.float64
    )
    del projections
    stored_per_view = np.asarray(
        projection_stats.get("projection_per_view_weight_sum"), dtype=np.float64
    )
    if (
        projection_stats.get("view_count") != expected_shape[0]
        or projection_stats.get("finite") is not True
        or not math.isclose(
            float(projection_stats.get("projection_weight_sum", math.nan)),
            audit.projection_sum,
            rel_tol=1e-9,
            abs_tol=1e-5,
        )
        or stored_per_view.shape != per_view.shape
        or not np.allclose(stored_per_view, per_view, rtol=1e-9, atol=1e-5)
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} SIMIND projection statistics do not match copied A00"
        )

    required_config_fields = {
        "evidence_registry_sha256",
        "population_config_sha256",
        "scanner_config_sha256",
        "simind_ini_sha256",
    }
    if set(hashes) != required_config_fields or any(
        not isinstance(hashes[name], str) or not _SHA256.fullmatch(hashes[name])
        for name in required_config_fields
    ):
        raise DatasetFreezeError(
            f"case {record.case_id} config hash set is invalid"
        )
    return (
        nn_multiplier,
        tuple((name, hashes[name]) for name in sorted(required_config_fields)),
        binary_digest,
        smc_digest,
        ini_digest,
        provenance.get("protocol_name"),
        expected_shape,
        None if timeout_seconds is None else float(timeout_seconds),
    )


def _validate_contract(contract: DatasetContractV2) -> None:
    if not isinstance(contract, DatasetContractV2):
        raise DatasetFreezeError("contract must be DatasetContractV2")
    if contract.dataset_role not in _DATASET_ROLES:
        raise DatasetFreezeError("contract dataset_role is invalid")
    if not contract.expected_case_ids or len(set(contract.expected_case_ids)) != len(contract.expected_case_ids):
        raise DatasetFreezeError("contract expected_case_ids must be non-empty and unique")
    if not contract.allowed_profile_ids or not contract.required_artifact_names:
        raise DatasetFreezeError("contract must declare profiles and required artifacts")
    if not _SHA256.fullmatch(contract.split_plan_sha256):
        raise DatasetFreezeError("contract split_plan_sha256 is invalid")
    if (
        contract.projection_coordinate_contract_id
        != PROJECTION_COORDINATE_CONTRACT_ID
        or contract.loader_transform_id != FROZEN_LOADER_TRANSFORM_ID
    ):
        raise DatasetFreezeError(
            "dataset contract must use the frozen PAR-S projection coordinates"
        )


def freeze_dataset(
    records: Sequence[CaseRecordV2],
    contract: DatasetContractV2,
) -> DatasetFreezeRecordV2:
    """Verify the exact case set and all bytes, then write completion marker last."""
    _validate_contract(contract)
    root = Path(contract.output_root)
    marker_path = root / DATASET_COMPLETE_FILENAME
    manifest_path = root / CASE_MANIFEST_FILENAME

    ordered = _validate_record_set(records, split_plan_sha256=contract.split_plan_sha256)
    expected = set(contract.expected_case_ids)
    actual = {record.case_id for record in ordered}
    if actual != expected:
        raise DatasetFreezeError(
            f"expected case set mismatch; missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )

    split_path = root / SPLIT_PLAN_FILENAME
    if not split_path.is_file():
        raise DatasetFreezeError("SPLIT_PLAN.json is missing")
    plan = _load_split_plan(split_path)
    if plan.sha256 != contract.split_plan_sha256 or plan.dataset_id != contract.dataset_id:
        raise DatasetFreezeError("dataset contract does not match split plan")

    identities = {
        (record.dataset_id, record.dataset_version, record.dataset_role)
        for record in ordered
    }
    wanted_identity = (contract.dataset_id, contract.dataset_version, contract.dataset_role)
    if identities != {wanted_identity}:
        raise DatasetFreezeError("records mix or violate dataset identity")

    required_artifacts = set(contract.required_artifact_names)
    family_split: dict[str, str] = {}
    simind_signature: tuple[object, ...] | None = None
    simind_case_count = 0
    for record in ordered:
        if record.profile_id not in contract.allowed_profile_ids:
            raise DatasetFreezeError(f"case {record.case_id} uses disallowed profile")
        if (
            record.projection_coordinate_contract_id
            != contract.projection_coordinate_contract_id
            or record.loader_transform_id != contract.loader_transform_id
        ):
            raise DatasetFreezeError(
                f"case {record.case_id} manifest record violates the dataset "
                "projection coordinate contract"
            )
        if plan.family_to_split.get(record.case_family_id) != record.split:
            raise DatasetFreezeError(f"case {record.case_id} violates family split plan")
        prior = family_split.setdefault(record.case_family_id, record.split)
        if prior != record.split:
            raise DatasetFreezeError(f"case_family_id {record.case_family_id!r} appears in multiple splits")
        missing_artifacts = required_artifacts - set(record.artifacts)
        if missing_artifacts:
            raise DatasetFreezeError(f"case {record.case_id} missing required artifacts: {sorted(missing_artifacts)}")
        if contract.dataset_role == "negative" and record.population_weight != 0:
            raise DatasetFreezeError("negative dataset records require population_weight=0")
        _verify_record_artifacts(record, root)
        metadata = _read_case_metadata(record, root)
        identity_fields = {
            "case_id": record.case_id,
            "case_family_id": record.case_family_id,
            "profile_id": record.profile_id,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "dataset_role": record.dataset_role,
            "split": record.split,
        }
        if any(metadata.get(key) != value for key, value in identity_fields.items()):
            raise DatasetFreezeError(f"case {record.case_id} metadata/manifest pairing mismatch")
        try:
            coordinate_contract = _validate_coordinate_metadata(metadata)
        except CaseWriteError as exc:
            raise DatasetFreezeError(
                f"case {record.case_id} projection coordinate metadata is invalid: {exc}"
            ) from exc
        if (
            coordinate_contract.coordinate_contract_id
            != contract.projection_coordinate_contract_id
            or coordinate_contract.loader_transform_id != contract.loader_transform_id
            or coordinate_contract.coordinate_contract_id
            != record.projection_coordinate_contract_id
            or coordinate_contract.loader_transform_id != record.loader_transform_id
        ):
            raise DatasetFreezeError(
                f"case {record.case_id} violates the dataset projection coordinate contract"
            )
        simulation = metadata.get("simulation")
        quality = metadata.get("quality_control")
        if not isinstance(simulation, dict) or simulation.get("status") != contract.required_simulation_status:
            raise DatasetFreezeError(f"case {record.case_id} simulation status is not freeze-ready")
        if not isinstance(quality, dict) or quality.get("status") != contract.required_quality_status:
            raise DatasetFreezeError(f"case {record.case_id} quality status is not freeze-ready")
        current_signature = _audit_simind_case_binding(record, root, metadata)
        if current_signature is not None:
            simind_case_count += 1
            if simind_signature is None:
                simind_signature = current_signature
            elif current_signature != simind_signature:
                raise DatasetFreezeError(
                    f"case {record.case_id} violates cross-case NN/scanner/config/SIMIND consistency"
                )

    if simind_case_count not in {0, len(ordered)}:
        raise DatasetFreezeError(
            "dataset mixes cases with and without complete SIMIND evidence"
        )

    expected_manifest = _manifest_bytes(ordered)
    if marker_path.exists():
        try:
            marker_data = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetFreezeError(f"invalid completion marker: {exc}") from exc
        frozen = DatasetFreezeRecordV2.from_dict(marker_data)
        if frozen.contract_sha256 != contract.sha256:
            raise DatasetFreezeError("existing completion marker belongs to a different contract")
        if not manifest_path.is_file() or manifest_path.read_bytes() != expected_manifest:
            raise DatasetFreezeError("frozen manifest content does not match supplied records")
        if sha256_file(manifest_path) != frozen.manifest_sha256:
            raise DatasetFreezeError("frozen manifest SHA-256 mismatch")
        return frozen

    if manifest_path.exists() and manifest_path.read_bytes() != expected_manifest:
        raise DatasetFreezeError("pre-existing manifest does not match validated records")
    if not manifest_path.exists():
        write_case_manifest(ordered, root, split_plan_sha256=contract.split_plan_sha256)
    manifest_digest = sha256_file(manifest_path)
    split_counts = {split: sum(record.split == split for record in ordered) for split in _SPLITS}
    frozen = DatasetFreezeRecordV2(
        dataset_id=contract.dataset_id,
        dataset_version=contract.dataset_version,
        dataset_role=contract.dataset_role,
        case_count=len(ordered),
        split_counts=split_counts,
        manifest_relative_path=CASE_MANIFEST_FILENAME,
        manifest_sha256=manifest_digest,
        split_plan_sha256=contract.split_plan_sha256,
        contract_sha256=contract.sha256,
        required_artifact_names=tuple(sorted(contract.required_artifact_names)),
        projection_coordinate_contract_id=(
            contract.projection_coordinate_contract_id
        ),
        loader_transform_id=contract.loader_transform_id,
        frozen_utc=datetime.now(timezone.utc).isoformat(),
    )
    atomic_write_json(marker_path, frozen.to_dict())
    return frozen
