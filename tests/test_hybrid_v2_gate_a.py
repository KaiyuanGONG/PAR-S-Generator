from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.phantom_generator import PhantomConfig, PhantomGenerator  # noqa: E402
from pipeline.qc import assess_gate_a_v2_population, phantom_qc  # noqa: E402
from pipeline.runner import PipelineConfig, PipelineRunner  # noqa: E402


def _v2_config() -> PhantomConfig:
    return PhantomConfig(
        anatomy_model="v2_population",
        global_seed=260819001,
        n_cases=1,
        v2_max_liver_shape_attempts=16,
    )


def test_hybrid_case_preserves_master_npz_lesion_and_physical_mu_contract(tmp_path: Path) -> None:
    generator = PhantomGenerator(_v2_config())
    first = generator.generate_one(1)
    second = generator.generate_one(1)
    first.save(tmp_path)

    with np.load(tmp_path / "case_0001.npz") as payload:
        assert set(payload.files) == {
            "activity",
            "mu_map",
            "liver_mask",
            "left_mask",
            "right_mask",
            "tumor_masks",
        }
        assert payload["mu_map"].dtype == np.float32
        for key in payload.files:
            expected = (
                np.stack(first.tumor_masks, axis=0)
                if key == "tumor_masks"
                else getattr(first, key)
            )
            replay = (
                np.stack(second.tumor_masks, axis=0)
                if key == "tumor_masks"
                else getattr(second, key)
            )
            assert np.array_equal(payload[key], expected)
            assert np.array_equal(expected, replay)

    metadata = json.loads((tmp_path / "case_0001_meta.json").read_text(encoding="utf-8"))
    assert metadata["v2"]["contracts"]["tumor_generator_v2_imported"] is False
    assert metadata["v2"]["attenuation"]["physical_map_key"] == "mu_true_140kev"
    assert metadata["v2"]["attenuation"]["ct_like_map_saved_to_npz"] is False
    assert metadata["v2"] == second.v2_metadata
    assert metadata["tumors"] == second.tumor_metadata
    assert not np.isclose(metadata["v2"]["liver"]["target"]["left_fraction"], 0.35)

    occupied = np.zeros_like(first.liver_mask)
    for lesion, record in zip(first.tumor_masks, first.tumor_metadata):
        lower, upper = record["sampled_size_bin_mm"]
        actual = record["effective_diameter_mm"]
        assert lower <= actual <= upper
        assert not np.any(lesion & ~first.liver_mask)
        assert not np.any(lesion & occupied)
        occupied |= lesion
        assert record["placement_stratum"] in {
            "central",
            "subcapsular",
            "capacity_fallback_margin_relaxed",
        }

    qc = phantom_qc(tmp_path / "case_0001.npz", tmp_path / "case_0001_meta.json")
    assert qc["status"] == "passed", qc["failures"]
    assert qc["v2"]["shape_quality_status"] == "pass"
    assert qc["v2"]["torso_qc_passed"] is True


def test_tumor_generator_v2_is_not_present_or_imported() -> None:
    assert not (REPO_ROOT / "src" / "core" / "tumor_generator_v2.py").exists()
    for relative in ("src/core/phantom_generator.py", "src/core/hybrid_v2_adapter.py"):
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8-sig"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(name.endswith("tumor_generator_v2") for name in imports)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"phantom": PhantomConfig(anatomy_model="legacy", n_cases=100)}, "anatomy_model"),
        ({"phantom": PhantomConfig(anatomy_model="v2_population", n_cases=99)}, "exactly 100"),
        (
            {
                "phantom": PhantomConfig(anatomy_model="v2_population", n_cases=100),
                "simulation_mode": "execute",
            },
            "never executes or mocks SIMIND",
        ),
        (
            {
                "phantom": PhantomConfig(anatomy_model="v2_population", n_cases=100),
                "create_poisson_observation": True,
            },
            "cannot create observations",
        ),
    ],
)
def test_anatomy_only_scope_fails_closed(overrides, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        PipelineConfig(
            run_id="invalid-gate-a",
            runs_root="unused",
            execution_scope="anatomy_only_gate_a",
            **overrides,
        )


def test_anatomy_only_packager_has_no_simulation_export_or_observation_stage() -> None:
    source = inspect.getsource(PipelineRunner.package_anatomy_only)
    assert "self.run_phantom_qc()" in source
    assert "write_gate_a_reports" in source
    for forbidden in (
        "self.export(",
        "self.prepare_simind(",
        "self.simulate_or_mock(",
        "self.create_observations(",
        "export_run_figures(",
    ):
        assert forbidden not in source


def test_v2_runtime_provenance_covers_adapter_sources_and_population_inputs(
    tmp_path: Path,
) -> None:
    config = PipelineConfig(
        run_id="v2-provenance",
        runs_root=str(tmp_path),
        execution_scope="anatomy_only_gate_a",
        phantom=PhantomConfig(anatomy_model="v2_population", n_cases=100),
    )
    runner = PipelineRunner(config)
    provenance = runner.ledger.load()["provenance"]
    assert {
        "core/anatomy_v2.py",
        "core/attenuation_model_v2.py",
        "core/hybrid_v2_adapter.py",
        "core/liver_geometry.py",
        "core/liver_regions.py",
        "core/measurements.py",
        "core/population_sampler.py",
        "core/schemas_v2.py",
        "core/seeds.py",
        "pipeline/gate_a_report.py",
        "pipeline/provenance.py",
    } <= set(provenance["software_sha256"])
    assert set(provenance["v2_inputs"]) == {
        "population_profile",
        "evidence_registry",
    }
    assert all(
        len(record["sha256"]) == 64
        for record in provenance["v2_inputs"].values()
    )


def test_anatomy_only_packager_records_an_early_failure_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = PipelineConfig(
        run_id="v2-failure-evidence",
        runs_root=str(tmp_path),
        execution_scope="anatomy_only_gate_a",
        phantom=PhantomConfig(anatomy_model="v2_population", n_cases=100),
    )
    runner = PipelineRunner(config)

    def fail_qc() -> list[dict]:
        raise RuntimeError("synthetic evidence failure")

    monkeypatch.setattr(runner, "run_phantom_qc", fail_qc)
    with pytest.raises(RuntimeError, match="synthetic evidence failure"):
        runner.package_anatomy_only()
    failure_list = json.loads(
        (runner.layout.root / "gate_a_failures.json").read_text(encoding="utf-8")
    )
    assert failure_list["status"] == "failed"
    assert failure_list["failure_count"] == 1
    assert failure_list["failures"][0]["generated_case_count"] == 0


def test_anatomy_only_ledger_finalizes_without_entering_export_or_simulation(
    tmp_path: Path,
) -> None:
    config = PipelineConfig(
        run_id="v2-scope-finalize",
        runs_root=str(tmp_path),
        execution_scope="anatomy_only_gate_a",
        phantom=PhantomConfig(anatomy_model="v2_population", n_cases=100),
    )
    runner = PipelineRunner(config)
    for stage in ("generate", "phantom_qc", "package"):
        runner.ledger.update_stage(stage, "passed")
    state = runner.ledger.finalize(package_sha256="a" * 64)
    assert state["finalized"] is True
    assert set(state["stages"]) == {"generate", "phantom_qc", "package"}


def test_anatomy_only_ledger_refuses_a_prohibited_stage(
    tmp_path: Path,
) -> None:
    config = PipelineConfig(
        run_id="v2-scope-prohibited",
        runs_root=str(tmp_path),
        execution_scope="anatomy_only_gate_a",
        phantom=PhantomConfig(anatomy_model="v2_population", n_cases=100),
    )
    runner = PipelineRunner(config)
    for stage in ("generate", "phantom_qc", "package", "export"):
        runner.ledger.update_stage(stage, "passed")
    with pytest.raises(RuntimeError, match="prohibited stages were entered: export"):
        runner.ledger.finalize()


def test_gate_a_reuses_master_lesion_checks_without_legacy_anatomy_substitution() -> None:
    summary = {
        "case_count": 100,
        "passed_case_count": 100,
        "containment_outside_voxels": 0,
        "overlap_voxels": 0,
        "lesion_count": 100,
        "case_distributions": {
            "liver_volume_ml": {"min": 780.0, "max": 2200.0},
            "left_ratio": {"min": 0.16, "max": 0.54},
            "tumor_count": {str(value): 20 for value in range(1, 6)},
        },
        "lesion_distributions": {
            "sampled_size_bins": {
                "10_to_20_mm": 45,
                "20_to_40_mm": 40,
                "40_to_60_mm": 15,
            },
            "diameter_bins": {
                "10_to_lt20_mm": 45,
                "20_to_lt40_mm": 40,
                "40_to_60_mm": 15,
                "outside_10_to_60_mm": 0,
            },
            "morphology_modes": {"ellipsoid": 70, "spiculated": 30},
            "target_contrast": {"min": 2.0, "max": 8.0},
            "placement_strata": {"central": 98, "capacity_fallback_margin_relaxed": 2},
            "central_surface_margin_mm": {"min": 4.42},
        },
        "v2_population": {
            "case_count": 100,
            "profile_identities": ["population_tare_hcc_nopvi_v2@" + "a" * 64],
            "evidence_registry_identities": ["registry@" + "b" * 64],
            "profile_contracts": [json.dumps({
                "cirrhosis_prevalence": 0.8,
                "liver_volume_range_ml": [775.0, 2300.0],
                "left_fraction_reference": {"median": 0.31, "range": [0.15, 0.45]},
            }, sort_keys=True, separators=(",", ":"))],
            "target_volume_ml": {"min": 775.0, "max": 2250.0},
            "large_volume_above_legacy_1900_ml_count": 5,
            "morphology_counts": {"normal": 20, "cirrhotic": 80},
            "caudate_enabled_count": 90,
            "target_left_fraction": {"min": 0.15, "max": 0.55},
            "left_fraction_error": {"count": 100},
            "volume_relative_error": {"count": 100},
            "all_shape_quality_passed": True,
            "all_torso_qc_passed": True,
            "child_seed_unique_counts": {
                name: 100 for name in ("patient", "liver", "tumor", "activity", "mu", "simind")
            },
        },
    }
    result = assess_gate_a_v2_population(
        summary,
        size_bins_mm=[[10, 20], [20, 40], [40, 60]],
        size_probabilities=[0.45, 0.40, 0.15],
        tumor_count_min=1,
        tumor_count_max=5,
        mode_probabilities={"ellipsoid": 0.7, "spiculated": 0.3},
        target_contrast_range=(2.0, 8.0),
        central_margin_mm=4.42,
    )
    names = {row["name"] for row in result["checks"]}
    assert result["status"] == "passed", result["checks"]
    assert "liver_volume_design_envelope_ml" not in names
    assert "cantlie_left_ratio" not in names
    assert "lesion_containment_and_nonoverlap" in names
    assert "effective_diameters_match_declared_strata" in names
    assert result["legacy_anatomy_checks_not_applied"] == [
        "cantlie_left_ratio",
        "liver_volume_design_envelope_ml",
    ]
