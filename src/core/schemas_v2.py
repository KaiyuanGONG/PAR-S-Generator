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


PROJECTION_COORDINATE_SCHEMA_VERSION = "pars_projection_coordinates_v1"
PROJECTION_COORDINATE_CONTRACT_ID = "pars_simind_v8_xcat_zyx_sar_v1"
PARS_DETECTOR_AXIS_CONTRACT = (
    "pars_detector_v_plus_source_z__u_plus_rotated_source_y_v1"
)
FROZEN_LOADER_TRANSFORM_ID = (
    "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
)
PARS_V2_TO_PARD_BRIDGE_SCHEMA_VERSION = "pars_v2_to_pard_reference_bridge_v1"
PARS_V2_SOURCE_WORLD_FRAME_ID = "pars_v2_centered_sar_world_v1"


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
    orientation_deg_zyx: tuple[float, float, float] = (0.0, 0.0, 0.0)
    subcapsular: bool = False
    primitive_count: int = 1
    target_rank: int = 1
    count_bin: str = ""
    dmax_bin: str = ""
    within_bin_assumption: bool = False
    evidence_types: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityTargetV2:
    injection_territory: str
    activity_pattern: str
    tnr_mean: float
    heterogeneous: bool
    mismatch_challenge: bool = False
    sector_proxy_label: int | None = None
    lesion_tnr_means: Mapping[int, float] = field(default_factory=dict)
    lesion_heterogeneous: Mapping[int, bool] = field(default_factory=dict)
    within_patient_correlation_assumption: str = "unknown_not_assumed_independent"
    evidence_types: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectionCoordinatesV1:
    """Frozen bridge from Generator arrays through SIMIND to PAR-S projections.

    The source arrays remain in their established ``(Z, Y, X)`` storage order.
    ``SAR`` names the positive physical direction represented by those three
    array components: superior, anterior and right, respectively.  It is not a
    claim that the diagonal array affine is a standard RAS affine.
    """

    schema_version: str
    coordinate_contract_id: str
    simind_starting_angle_deg: float
    projector_starting_angle_deg: float
    rotation_direction: str
    simind_to_projector_angle_offset_deg: float
    detector_axis_contract: str
    loader_transform_id: str
    source_index_order: str
    source_positive_directions: str
    simind_basis_from_source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "coordinate_contract_id": self.coordinate_contract_id,
            "simind_starting_angle_deg": self.simind_starting_angle_deg,
            "projector_starting_angle_deg": self.projector_starting_angle_deg,
            "rotation_direction": self.rotation_direction,
            "simind_to_projector_angle_offset_deg": (
                self.simind_to_projector_angle_offset_deg
            ),
            "detector_axis_contract": self.detector_axis_contract,
            "loader_transform_id": self.loader_transform_id,
            "source_index_order": self.source_index_order,
            "source_positive_directions": self.source_positive_directions,
            "simind_basis_from_source": self.simind_basis_from_source,
        }


FROZEN_PROJECTION_COORDINATES_V1 = ProjectionCoordinatesV1(
    schema_version=PROJECTION_COORDINATE_SCHEMA_VERSION,
    coordinate_contract_id=PROJECTION_COORDINATE_CONTRACT_ID,
    simind_starting_angle_deg=180.0,
    projector_starting_angle_deg=90.0,
    rotation_direction="clockwise",
    simind_to_projector_angle_offset_deg=-90.0,
    detector_axis_contract=PARS_DETECTOR_AXIS_CONTRACT,
    loader_transform_id=FROZEN_LOADER_TRANSFORM_ID,
    source_index_order="ZYX",
    source_positive_directions="SAR",
    simind_basis_from_source="Xsim=-Zsrc;Ysim=+Xsrc;Zsim=-Ysrc",
)


@dataclass(frozen=True)
class ParsV2ToPardBridgeV1:
    """Frozen semantic contract for materializing a PAR-D reference phase.

    PAR-S does not produce a DVF.  The DVF fields below describe what the
    downstream bridge must require from an XCAT/DVF provider; none may be
    inferred from array shape.  ``world_frame_id`` and an explicit registration
    are owned by the bridge because PAR-S and XCAT are independent anatomies.
    """

    schema_version: str
    source_schema_version: str
    source_world_frame_id: str
    canonical_world_basis: str
    source_array_keys: tuple[str, ...]
    source_spatial_keys: tuple[str, ...]
    required_grid_fields: tuple[str, ...]
    lesion_center_path: str
    activity_target_key: str
    mu_target_key: str
    organ_target_key: str
    lesion_target_key: str
    family_target_key: str
    source_reference_phase: str
    target_reference_phase: str
    source_axis_order: str
    source_orientation_code: str
    dvf_direction: str
    dvf_units: str
    dvf_domain: str
    dvf_layout: str
    required_dvf_fields: tuple[str, ...]
    activity_interpolation: str
    mu_interpolation: str
    mask_interpolation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_schema_version": self.source_schema_version,
            "source_world_frame_id": self.source_world_frame_id,
            "canonical_world_basis": self.canonical_world_basis,
            "source_array_keys": list(self.source_array_keys),
            "source_spatial_keys": list(self.source_spatial_keys),
            "required_grid_fields": list(self.required_grid_fields),
            "lesion_center_path": self.lesion_center_path,
            "activity_target_key": self.activity_target_key,
            "mu_target_key": self.mu_target_key,
            "organ_target_key": self.organ_target_key,
            "lesion_target_key": self.lesion_target_key,
            "family_target_key": self.family_target_key,
            "source_reference_phase": self.source_reference_phase,
            "target_reference_phase": self.target_reference_phase,
            "source_axis_order": self.source_axis_order,
            "source_orientation_code": self.source_orientation_code,
            "dvf_direction": self.dvf_direction,
            "dvf_units": self.dvf_units,
            "dvf_domain": self.dvf_domain,
            "dvf_layout": self.dvf_layout,
            "required_dvf_fields": list(self.required_dvf_fields),
            "activity_interpolation": self.activity_interpolation,
            "mu_interpolation": self.mu_interpolation,
            "mask_interpolation": self.mask_interpolation,
        }


FROZEN_PARS_V2_TO_PARD_BRIDGE_V1 = ParsV2ToPardBridgeV1(
    schema_version=PARS_V2_TO_PARD_BRIDGE_SCHEMA_VERSION,
    source_schema_version="pars_syn_v2",
    source_world_frame_id=PARS_V2_SOURCE_WORLD_FRAME_ID,
    canonical_world_basis="RAS_mm",
    source_array_keys=(
        "activity_probability",
        "mu_true_140kev",
        "liver_mask",
        "tumor_instance_mask",
    ),
    source_spatial_keys=(
        "affine_4x4",
        "world_origin_mm",
        "orientation_code",
        "axis_order",
        "reference_phase",
        "dvf_convention",
        "dvf_units",
    ),
    required_grid_fields=(
        "shape_zyx",
        "affine_4x4",
        "world_origin_mm",
        "orientation_code",
        "axis_order",
        "reference_phase",
        "world_frame_id",
        "phase_id",
    ),
    lesion_center_path="actual_metrics.tumors.lesions[].center_world_mm",
    activity_target_key="activity_ref",
    mu_target_key="mu_ref",
    organ_target_key="organ_ref",
    lesion_target_key="lesion_ref",
    family_target_key="dynamic_case_family_id",
    source_reference_phase="end_expiration",
    target_reference_phase="phase_0",
    source_axis_order="ZYX",
    source_orientation_code="SAR",
    dvf_direction="ref_to_phase",
    dvf_units="mm",
    dvf_domain="reference_grid",
    dvf_layout="ZYX3",
    required_dvf_fields=(
        "values",
        "reference_grid",
        "phase_grid",
        "dynamic_case_family_id",
        "reference_phase_id",
        "target_phase_id",
        "direction",
        "units",
        "component_order",
        "domain",
        "layout",
    ),
    activity_interpolation="mass_conserving_trilinear",
    mu_interpolation="trilinear",
    mask_interpolation="deterministic_forward_nearest",
)


def validate_pars_v2_to_pard_bridge_v1(
    value: Any,
    *,
    context: str = "pars_v2_to_pard_bridge",
) -> ParsV2ToPardBridgeV1:
    """Reject any drift from the frozen cross-repository bridge contract."""

    raw = _require_object(value, context)
    expected = FROZEN_PARS_V2_TO_PARD_BRIDGE_V1.to_dict()
    _reject_unknown(raw, set(expected), context)
    missing = sorted(set(expected) - set(raw))
    if missing:
        raise SchemaValidationError(f"{context} is missing fields: {missing}")
    mismatches = sorted(name for name in expected if raw[name] != expected[name])
    if mismatches:
        raise SchemaValidationError(
            f"{context} does not match the frozen PAR-S V2 to PAR-D bridge; "
            f"mismatched fields: {mismatches}"
        )
    return FROZEN_PARS_V2_TO_PARD_BRIDGE_V1


def validate_projection_coordinates_v1(
    value: Any,
    *,
    context: str = "projection_coordinates",
) -> ProjectionCoordinatesV1:
    """Parse the coordinate object and reject every non-frozen alternative."""

    raw = _require_object(value, context)
    required = set(FROZEN_PROJECTION_COORDINATES_V1.to_dict())
    _reject_unknown(raw, required, context)
    missing = sorted(required - set(raw))
    if missing:
        raise SchemaValidationError(f"{context} is missing fields: {missing}")

    string_fields = (
        "schema_version",
        "coordinate_contract_id",
        "rotation_direction",
        "detector_axis_contract",
        "loader_transform_id",
        "source_index_order",
        "source_positive_directions",
        "simind_basis_from_source",
    )
    strings = {
        name: _require_string(raw[name], f"{context}.{name}")
        for name in string_fields
    }

    numbers: dict[str, float] = {}
    for name in (
        "simind_starting_angle_deg",
        "projector_starting_angle_deg",
        "simind_to_projector_angle_offset_deg",
    ):
        item = raw[name]
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
        ):
            raise SchemaValidationError(f"{context}.{name} must be finite")
        numbers[name] = float(item)

    parsed = ProjectionCoordinatesV1(
        **strings,
        **numbers,
    )
    if parsed != FROZEN_PROJECTION_COORDINATES_V1:
        expected = FROZEN_PROJECTION_COORDINATES_V1.to_dict()
        actual = parsed.to_dict()
        mismatches = sorted(
            name for name in expected if actual[name] != expected[name]
        )
        raise SchemaValidationError(
            f"{context} does not match the frozen PAR-S coordinate contract; "
            f"mismatched fields: {mismatches}"
        )
    return parsed


@dataclass(frozen=True)
class AcquisitionProfileV2:
    matrix: tuple[int, int, int]
    voxel_size_mm: float
    views: int
    starting_angle_deg: float
    rotation_direction: str
    orbit_cm: float
    energy_window_kev: tuple[float, float]
    projection_coordinates: ProjectionCoordinatesV1


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
