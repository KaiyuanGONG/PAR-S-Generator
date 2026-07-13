from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_validation_module():
    path = REPO_ROOT / "scripts" / "validate_task3_liver_v2.py"
    spec = importlib.util.spec_from_file_location("validate_task3_liver_v2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_population_report_is_deterministic_evidence_aware_and_gated() -> None:
    module = _load_validation_module()
    profile = module.load_main_profile(REPO_ROOT)

    first = module.build_population_statistics(profile, sample_count=2000, seed=20260713)
    second = module.build_population_statistics(profile, sample_count=2000, seed=20260713)

    assert first == second
    assert first["sample_count"] == 2000
    assert first["profile_id"] == profile.profile_id
    assert first["expected"]["male_fraction"]["source_type"] == "literature_population"
    assert first["expected"]["cirrhosis_fraction"]["source_type"] == "engineering_prior"
    assert first["expected"]["liver_extent_mm_zyx"]["source_type"] == "literature_population"
    assert first["observed"]["bbox_fill_fraction"]["sd"] >= 0.008
    assert first["checks"]["banned_upper_limit_equation_used"] is False
    assert all(first["gates"].values())


def test_representative_selector_is_deterministic_balanced_and_covers_quantile_edges() -> None:
    module = _load_validation_module()
    profile = module.load_main_profile(REPO_ROOT)

    first = module.select_representative_targets(profile, seed=20260714)
    second = module.select_representative_targets(profile, seed=20260714)

    assert [item.patient.case_id for item in first] == [item.patient.case_id for item in second]
    assert len(first) == 14
    assert len({item.patient.case_id for item in first}) == 14
    core = [item for item in first if not item.selection_role.startswith("stress-")]
    assert {
        (morphology, enabled): sum(
            item.target.morphology == morphology and item.target.caudate_enabled is enabled
            for item in core
        )
        for morphology in ("normal", "cirrhotic")
        for enabled in (False, True)
    } == {
        ("normal", False): 3,
        ("normal", True): 3,
        ("cirrhotic", False): 3,
        ("cirrhotic", True): 3,
    }
    by_role = {item.selection_role: item for item in first}
    assert {
        "stress-cirrhotic-caudate-upper",
        "stress-cirrhotic-left-upper",
    } <= set(by_role)
    assert all(
        by_role["joint-size-p10"].features[name]
        <= by_role["joint-size-p10"].selection_thresholds[name]
        for name in ("volume_ml", "si_mm", "ap_mm", "lr_mm")
    )
    assert all(
        by_role["joint-size-p90"].features[name]
        >= by_role["joint-size-p90"].selection_thresholds[name]
        for name in ("volume_ml", "si_mm", "ap_mm", "lr_mm")
    )
    for role, feature, threshold, lower in (
        ("left-p05", "left_fraction", "left_fraction_p05", True),
        ("left-p95", "left_fraction", "left_fraction_p95", False),
        ("shape-u-p05", "shape_u", "shape_u_p05", True),
        ("shape-u-p95", "shape_u", "shape_u_p95", False),
        ("shape-v-p05", "shape_v", "shape_v_p05", True),
        ("shape-v-p95", "shape_v", "shape_v_p95", False),
    ):
        observed = by_role[role].features[feature]
        edge = by_role[role].selection_thresholds[threshold]
        assert observed <= edge if lower else observed >= edge


def test_voxel_report_gates_complete_composed_shape_not_only_size() -> None:
    module = _load_validation_module()
    profile = module.load_main_profile(REPO_ROOT)

    report = module.build_voxel_validation(profile, seed=20260714)

    assert report["case_count"] == 14
    assert report["gates"]["selection_roles_complete"] is True
    assert report["gates"]["selection_strata_balanced"] is True
    assert report["gates"]["selection_joint_size_edges"] is True
    assert report["gates"]["selection_left_and_shape_edges"] is True
    assert report["gates"]["fixed_production_stress_edges"] is True
    assert report["gates"]["controlled_cirrhotic_roughness"] is True
    assert report["aggregate"]["controlled_morphology_pair"]["roughness_delta"] >= 0.01
    assert report["gates"]["all_shape_quality"] is True
    for row in report["cases"]:
        quality = row["actual"]["shape_quality"]
        assert row["gates"]["shape_quality"] is True
        assert quality["status"] == "pass"
        assert all(quality["gates"].values())
        assert quality["lobe_overlap_fraction"] >= 0.05
        assert 0.01 <= quality["dome_removed_fraction"] <= 0.35
        assert 0.005 <= quality["fossa_removed_fraction"] <= 0.12
        assert quality["gates"]["left_lobe_tapers_laterally"] is True
        assert quality["gates"]["caudate_changes_outer_geometry"] is True
        assert quality["gates"]["caudate_outer_is_s1"] is True
