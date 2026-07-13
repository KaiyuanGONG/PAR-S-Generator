from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SOURCE_TYPES = {
    "literature_population",
    "local_clinical",
    "physics_standard",
    "engineering_prior",
    "coverage_sampling",
    "stress_test",
}
PROFILE_ROLES = {"population", "sensitivity", "coverage", "stress", "negative", "scanner"}


class SchemaValidationError(ValueError):
    pass


def _require_object(value: Any, context: str) -> dict:
    if not isinstance(value, dict):
        raise SchemaValidationError(f"{context} must be a JSON object")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SchemaValidationError(f"{context} has unknown fields: {unknown}")


def _require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{context} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class EvidenceEntryV2:
    evidence_id: str
    title: str
    source_type: str
    verification_status: str
    locator: str
    supports: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class EvidenceRegistryV2:
    registry_id: str
    entries: Mapping[str, EvidenceEntryV2]
    schema_version: str = "pars_evidence_registry_v2"


@dataclass(frozen=True)
class ParameterSpecV2:
    name: str
    value: Any
    unit: str
    source_type: str
    evidence_ids: tuple[str, ...]
    applies_to: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class PopulationProfileV2:
    profile_id: str
    role: str
    population_claim: bool
    description: str
    parameters: Mapping[str, ParameterSpecV2]
    parent_profile_id: str | None = None
    schema_version: str = "pars_profile_v2"

    def value(self, name: str) -> Any:
        try:
            return self.parameters[name].value
        except KeyError as exc:
            raise KeyError(f"Profile {self.profile_id!r} has no parameter {name!r}") from exc


@dataclass(frozen=True)
class PatientSampleV2:
    case_id: str
    sex: str
    age_years: float
    height_cm: float
    weight_kg: float
    bmi: float
    liver_morphology: str
    evidence_types: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LiverTargetV2:
    volume_ml: float
    lr_mm: float
    ap_mm: float
    si_mm: float
    left_fraction: float
    centroid_mm: tuple[float, float, float]
    morphology: str
    s1_3_to_s4_8_ratio: float
    caudate_fraction: float
    surface_roughness_target: float
    surface_field_amplitude: float
    caudate_enabled: bool
    evidence_types: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TumorTargetV2:
    lesion_id: str
    dmax_mm: float
    axis_ratios: tuple[float, float]
    lobe: str
    morphology: str
    evidence_types: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityTargetV2:
    injection_territory: str
    activity_pattern: str
    tnr_mean: float
    heterogeneous: bool
    mismatch_challenge: bool = False


@dataclass(frozen=True)
class AcquisitionProfileV2:
    matrix: tuple[int, int, int]
    voxel_size_mm: float
    views: int
    starting_angle_deg: float
    rotation_direction: str
    orbit_cm: float
    energy_window_kev: tuple[float, float]


@dataclass(frozen=True)
class CaseMetadataV2:
    case_id: str
    case_family_id: str
    profile_id: str
    seeds: Mapping[str, int] = field(default_factory=dict)
    target_metrics: Mapping[str, Any] = field(default_factory=dict)
    actual_metrics: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "pars_syn_v2"


def load_evidence_registry(path: str | Path) -> EvidenceRegistryV2:
    path = Path(path)
    data = _require_object(json.loads(path.read_text(encoding="utf-8")), "evidence registry")
    _reject_unknown(data, {"schema_version", "registry_id", "entries"}, "evidence registry")
    if data.get("schema_version") != "pars_evidence_registry_v2":
        raise SchemaValidationError("evidence registry schema_version must be pars_evidence_registry_v2")
    registry_id = _require_string(data.get("registry_id"), "registry_id")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise SchemaValidationError("evidence registry entries must be a non-empty list")

    entries: dict[str, EvidenceEntryV2] = {}
    allowed = {"evidence_id", "title", "source_type", "verification_status", "locator", "supports", "notes"}
    for index, raw in enumerate(raw_entries):
        raw = _require_object(raw, f"evidence entry {index}")
        _reject_unknown(raw, allowed, f"evidence entry {index}")
        evidence_id = _require_string(raw.get("evidence_id"), f"evidence entry {index}.evidence_id")
        if evidence_id in entries:
            raise SchemaValidationError(f"duplicate evidence_id: {evidence_id}")
        source_type = _require_string(raw.get("source_type"), f"{evidence_id}.source_type")
        if source_type not in SOURCE_TYPES:
            raise SchemaValidationError(f"{evidence_id} has invalid source_type {source_type!r}")
        supports = raw.get("supports")
        if not isinstance(supports, list) or not supports or not all(isinstance(item, str) and item for item in supports):
            raise SchemaValidationError(f"{evidence_id}.supports must be a non-empty string list")
        entries[evidence_id] = EvidenceEntryV2(
            evidence_id=evidence_id,
            title=_require_string(raw.get("title"), f"{evidence_id}.title"),
            source_type=source_type,
            verification_status=_require_string(raw.get("verification_status"), f"{evidence_id}.verification_status"),
            locator=_require_string(raw.get("locator"), f"{evidence_id}.locator"),
            supports=tuple(supports),
            notes=str(raw.get("notes", "")),
        )
    return EvidenceRegistryV2(registry_id=registry_id, entries=entries)


def _validate_probability(name: str, value: Any, unit: str) -> None:
    if unit in {"probability", "fraction"}:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
            raise SchemaValidationError(f"{name} probability must be finite and within [0, 1]")
    elif unit == "probability_values":
        if not isinstance(value, list) or not value:
            raise SchemaValidationError(f"{name} probability_values must be a non-empty list")
        if any(not isinstance(item, (int, float)) or isinstance(item, bool) or not 0 <= item <= 1 for item in value):
            raise SchemaValidationError(f"{name} probability_values must be within [0, 1]")
    elif unit == "probability_distribution":
        if not isinstance(value, dict) or not value:
            raise SchemaValidationError(f"{name} probability_distribution must be a non-empty object")
        values = list(value.values())
        if any(not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item) or not 0 <= item <= 1 for item in values):
            raise SchemaValidationError(f"{name} probability_distribution values must be within [0, 1]")
        if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise SchemaValidationError(f"{name} probability_distribution must sum to 1")


def load_profile(path: str | Path, registry: EvidenceRegistryV2 | None = None) -> PopulationProfileV2:
    path = Path(path)
    data = _require_object(json.loads(path.read_text(encoding="utf-8")), f"profile {path}")
    allowed = {"schema_version", "profile_id", "role", "population_claim", "description", "parent_profile_id", "parameters"}
    _reject_unknown(data, allowed, f"profile {path.name}")
    if data.get("schema_version") != "pars_profile_v2":
        raise SchemaValidationError(f"{path.name} schema_version must be pars_profile_v2")
    profile_id = _require_string(data.get("profile_id"), f"{path.name}.profile_id")
    role = _require_string(data.get("role"), f"{profile_id}.role")
    if role not in PROFILE_ROLES:
        raise SchemaValidationError(f"{profile_id} has invalid role {role!r}")
    population_claim = data.get("population_claim")
    if not isinstance(population_claim, bool):
        raise SchemaValidationError(f"{profile_id}.population_claim must be boolean")
    if population_claim and role != "population":
        raise SchemaValidationError(f"{profile_id} cannot make a population claim with role {role}")
    raw_parameters = _require_object(data.get("parameters"), f"{profile_id}.parameters")
    if not raw_parameters:
        raise SchemaValidationError(f"{profile_id}.parameters must not be empty")

    parameters: dict[str, ParameterSpecV2] = {}
    parameter_allowed = {"value", "unit", "source_type", "evidence_ids", "applies_to", "notes"}
    for name, raw in raw_parameters.items():
        name = _require_string(name, f"{profile_id} parameter name")
        raw = _require_object(raw, f"{profile_id}.{name}")
        _reject_unknown(raw, parameter_allowed, f"{profile_id}.{name}")
        unit = _require_string(raw.get("unit"), f"{profile_id}.{name}.unit")
        source_type = _require_string(raw.get("source_type"), f"{profile_id}.{name}.source_type")
        if source_type not in SOURCE_TYPES:
            raise SchemaValidationError(f"{profile_id}.{name} has invalid source_type {source_type!r}")
        evidence_ids = raw.get("evidence_ids")
        applies_to = raw.get("applies_to")
        if not isinstance(evidence_ids, list) or not evidence_ids or not all(isinstance(item, str) and item for item in evidence_ids):
            raise SchemaValidationError(f"{profile_id}.{name}.evidence_ids must be a non-empty string list")
        if not isinstance(applies_to, list) or profile_id not in applies_to:
            raise SchemaValidationError(f"{profile_id}.{name}.applies_to must include {profile_id}")
        value = raw.get("value")
        _validate_probability(name, value, unit)
        if registry is not None:
            for evidence_id in evidence_ids:
                if evidence_id not in registry.entries:
                    raise SchemaValidationError(f"{profile_id}.{name} references missing evidence_id {evidence_id}")
                evidence_type = registry.entries[evidence_id].source_type
                if evidence_type != source_type:
                    raise SchemaValidationError(
                        f"{profile_id}.{name} source_type {source_type} does not match {evidence_id} ({evidence_type})"
                    )
        parameters[name] = ParameterSpecV2(
            name=name,
            value=value,
            unit=unit,
            source_type=source_type,
            evidence_ids=tuple(evidence_ids),
            applies_to=tuple(applies_to),
            notes=str(raw.get("notes", "")),
        )
    parent_profile_id = data.get("parent_profile_id")
    if parent_profile_id is not None:
        parent_profile_id = _require_string(parent_profile_id, f"{profile_id}.parent_profile_id")
    return PopulationProfileV2(
        profile_id=profile_id,
        role=role,
        population_claim=population_claim,
        description=_require_string(data.get("description"), f"{profile_id}.description"),
        parent_profile_id=parent_profile_id,
        parameters=parameters,
    )
