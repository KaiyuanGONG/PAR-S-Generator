from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.schemas_v2 import (  # noqa: E402
    CaseMetadataV2,
    FROZEN_PARS_V2_TO_PARD_BRIDGE_V1,
    FROZEN_PROJECTION_COORDINATES_V1,
    SchemaValidationError,
    load_evidence_registry,
    load_profile,
    validate_pars_v2_to_pard_bridge_v1,
    validate_projection_coordinates_v1,
)


CONFIG_DIR = REPO_ROOT / "configs"
REGISTRY_PATH = CONFIG_DIR / "evidence_registry_v2.json"
PROFILE_NAMES = (
    "population_tare_hcc_nopvi_v2.json",
    "population_broad_hcc_sensitivity_v2.json",
    "coverage_broad_hcc_v2.json",
    "stress_tare_hcc_v2.json",
    "negative_control_v2.json",
    "scanner_ge870_tcmma_v1.json",
)


def test_all_v2_configs_load_with_resolved_evidence() -> None:
    registry = load_evidence_registry(REGISTRY_PATH)
    profiles = [load_profile(CONFIG_DIR / name, registry) for name in PROFILE_NAMES]

    assert len(registry.entries) >= 10
    assert len({entry.evidence_id for entry in registry.entries.values()}) == len(registry.entries)
    assert {profile.profile_id for profile in profiles} == {Path(name).stem for name in PROFILE_NAMES}
    for profile in profiles:
        assert profile.parameters
        for parameter in profile.parameters.values():
            assert parameter.unit
            assert parameter.evidence_ids
            assert profile.profile_id in parameter.applies_to
            for evidence_id in parameter.evidence_ids:
                assert evidence_id in registry.entries
                assert registry.entries[evidence_id].source_type == parameter.source_type


def test_main_population_config_freezes_user_decisions_and_verified_marginals() -> None:
    registry = load_evidence_registry(REGISTRY_PATH)
    profile = load_profile(CONFIG_DIR / "population_tare_hcc_nopvi_v2.json", registry)

    assert profile.role == "population"
    assert profile.population_claim is True
    assert profile.value("explicit_pvi") is False
    assert profile.value("main_case_count") == 500
    assert profile.value("cirrhosis_prevalence") == pytest.approx(0.80)
    assert profile.value("cirrhosis_sensitivity_values") == [0.60, 0.90]
    assert profile.value("tumor_count_bins") == pytest.approx({"1": 0.324, "2-5": 0.224, ">5": 0.452})
    assert profile.value("dmax_bins") == pytest.approx({"10-<80_mm": 0.603, "80-200_mm": 0.397})
    assert profile.value("lobe_distribution") == pytest.approx({"unilobar": 0.587, "bilobar": 0.413})
    assert profile.value("dmax_min_mm") == 10
    assert profile.value("dmax_max_mm") == 200
    assert profile.value("confluent_required_above_mm") == 100
    assert profile.value("tumor_burden_fraction_max") == pytest.approx(0.70)


def test_non_population_profiles_cannot_masquerade_as_main_prevalence() -> None:
    registry = load_evidence_registry(REGISTRY_PATH)
    for name in PROFILE_NAMES[1:5]:
        profile = load_profile(CONFIG_DIR / name, registry)
        assert profile.population_claim is False

    negative = load_profile(CONFIG_DIR / "negative_control_v2.json", registry)
    assert negative.role == "negative"
    assert negative.value("case_count") == 50
    assert negative.value("tumor_count") == 0
    assert negative.value("population_weight") == 0

    stress = load_profile(CONFIG_DIR / "stress_tare_hcc_v2.json", registry)
    assert stress.value("dmax_range_mm") == [200, 215]


def test_scanner_profile_uses_pds_name_without_changing_geometry() -> None:
    registry = load_evidence_registry(REGISTRY_PATH)
    scanner = load_profile(CONFIG_DIR / "scanner_ge870_tcmma_v1.json", registry)

    assert scanner.role == "scanner"
    assert scanner.value("collimator_model") == "WEHR"
    assert scanner.value("collimator_catalog_number") == "H3906CM"
    assert scanner.value("collimator_legacy_aliases") == ["ge-legp"]
    assert scanner.value("hole_opening_mm") == pytest.approx(2.26)
    assert scanner.value("septal_thickness_mm") == pytest.approx(0.2)
    assert scanner.value("hole_length_mm") == pytest.approx(45.0)
    assert scanner.value("matrix") == [128, 128, 128]
    assert scanner.value("views") == 60
    assert scanner.value("activity_time_product_mbq_s") == pytest.approx(1704.0)
    assert scanner.value("rotation_direction") == "clockwise"
    assert scanner.value("projection_alignment_gate") == {
        "candidate_count": 480,
        "detector_downsample": 8,
        "bootstrap_candidate_count": 16,
        "bootstrap_iterations": 200,
        "bootstrap_seed": 20260714,
        "noise_relative_sd": 0.005,
        "minimum_score_margin": 0.005,
        "minimum_bootstrap_top1_frequency": 0.95,
        "minimum_case_top1_frequency": 1.0,
    }
    assert (
        validate_projection_coordinates_v1(scanner.value("projection_coordinates"))
        == FROZEN_PROJECTION_COORDINATES_V1
    )


def test_projection_coordinate_schema_rejects_implicit_or_unknown_transforms() -> None:
    valid = FROZEN_PROJECTION_COORDINATES_V1.to_dict()
    assert validate_projection_coordinates_v1(valid) == FROZEN_PROJECTION_COORDINATES_V1

    wrong_loader = dict(valid, loader_transform_id="flip_some_axes")
    with pytest.raises(SchemaValidationError, match="frozen PAR-S coordinate"):
        validate_projection_coordinates_v1(wrong_loader)

    unknown = dict(valid, implicit_transform=True)
    with pytest.raises(SchemaValidationError, match="unknown fields"):
        validate_projection_coordinates_v1(unknown)


def test_pars_v2_to_pard_bridge_freezes_keys_world_frame_and_dvf_semantics() -> None:
    frozen = FROZEN_PARS_V2_TO_PARD_BRIDGE_V1.to_dict()
    assert validate_pars_v2_to_pard_bridge_v1(frozen) == FROZEN_PARS_V2_TO_PARD_BRIDGE_V1
    assert frozen["source_array_keys"] == [
        "activity_probability",
        "mu_true_140kev",
        "liver_mask",
        "tumor_instance_mask",
    ]
    assert frozen["source_world_frame_id"] == "pars_v2_centered_sar_world_v1"
    assert frozen["canonical_world_basis"] == "RAS_mm"
    assert frozen["dvf_direction"] == "ref_to_phase"
    assert frozen["dvf_units"] == "mm"
    assert frozen["dvf_domain"] == "reference_grid"
    assert frozen["dvf_layout"] == "ZYX3"
    assert "component_order" in frozen["required_dvf_fields"]
    assert "dynamic_case_family_id" in frozen["required_dvf_fields"]
    assert "target_phase_id" in frozen["required_dvf_fields"]
    assert "phase_id" in frozen["required_grid_fields"]
    assert frozen["mask_interpolation"] == "deterministic_forward_nearest"

    for key, wrong in (
        ("dvf_direction", "phase_to_ref"),
        ("dvf_units", "voxel"),
        ("dvf_domain", "phase_grid"),
        ("source_orientation_code", "RAS"),
    ):
        mutated = dict(frozen, **{key: wrong})
        with pytest.raises(SchemaValidationError, match="frozen PAR-S V2 to PAR-D"):
            validate_pars_v2_to_pard_bridge_v1(mutated)

    with pytest.raises(SchemaValidationError, match="unknown fields"):
        validate_pars_v2_to_pard_bridge_v1(dict(frozen, infer_from_shape=True))


def test_schema_rejects_unknown_fields_invalid_probabilities_and_missing_evidence(tmp_path: Path) -> None:
    registry = load_evidence_registry(REGISTRY_PATH)
    valid = json.loads((CONFIG_DIR / "population_tare_hcc_nopvi_v2.json").read_text(encoding="utf-8"))

    invalid_unknown = dict(valid, surprise=True)
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_text(json.dumps(invalid_unknown), encoding="utf-8")
    with pytest.raises(SchemaValidationError, match="unknown fields"):
        load_profile(unknown_path, registry)

    invalid_probability = json.loads(json.dumps(valid))
    invalid_probability["parameters"]["cirrhosis_prevalence"]["value"] = 1.2
    probability_path = tmp_path / "probability.json"
    probability_path.write_text(json.dumps(invalid_probability), encoding="utf-8")
    with pytest.raises(SchemaValidationError, match="probability"):
        load_profile(probability_path, registry)

    invalid_evidence = json.loads(json.dumps(valid))
    invalid_evidence["parameters"]["cirrhosis_prevalence"]["evidence_ids"] = ["missing-id"]
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(invalid_evidence), encoding="utf-8")
    with pytest.raises(SchemaValidationError, match="missing-id"):
        load_profile(evidence_path, registry)


def test_case_metadata_has_frozen_v2_schema_name() -> None:
    metadata = CaseMetadataV2(case_id="case_0001", case_family_id="family_0001", profile_id="population_tare_hcc_nopvi_v2")
    assert metadata.schema_version == "pars_syn_v2"
