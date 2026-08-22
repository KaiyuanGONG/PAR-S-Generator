import json

import pytest

from core.windows_v1 import GENERATION_PROFILE, WindowsV1Config, WindowsV1ConfigError
from core.seeds import SeedBundle
from pipeline.runner import PipelineConfig, PipelineRunner


def _mixed_controls(seed=314159):
    return WindowsV1Config.from_dict(
        {
            "cohort": {"mode": "mixed", "positive_cases": 1, "negative_cases": 1},
            "lesions": {
                "tumor_count_min": 1,
                "tumor_count_max": 1,
                "territory_policy": "whole_liver",
            },
            "seed": seed,
        }
    )


def test_windows_v1_pipeline_config_round_trips_without_unlocking_physics(tmp_path):
    config = PipelineConfig.for_windows_v1(
        run_id="roundtrip",
        runs_root=str(tmp_path),
        windows_v1=_mixed_controls(),
        simulation_mode="prepare",
    )

    payload = config.to_dict()
    restored = PipelineConfig.from_dict(payload)

    assert restored.to_dict() == payload
    assert restored.schema_version == "windows_v1"
    assert restored.generation_profile == GENERATION_PROFILE
    assert restored.runtime_backend == "windows_native"
    assert restored.phantom.anatomy_model == "v2_population"
    assert restored.phantom.activity_model == "limited_v1"
    assert restored.phantom.n_cases == 2


def test_windows_v1_pipeline_rejects_physics_tampering_on_reload(tmp_path):
    config = PipelineConfig.for_windows_v1(
        run_id="tamper",
        runs_root=str(tmp_path),
        windows_v1=_mixed_controls(),
    )
    payload = config.to_dict()
    payload["phantom"]["voxel_size_mm"] = 5.0

    with pytest.raises((ValueError, WindowsV1ConfigError), match="locked|authoritative|phantom"):
        PipelineConfig.from_dict(payload)


def test_windows_v1_pipeline_rejects_unknown_nested_phantom_field(tmp_path):
    config = PipelineConfig.for_windows_v1(
        run_id="unknown",
        runs_root=str(tmp_path),
        windows_v1=_mixed_controls(),
    )
    payload = config.to_dict()
    payload["phantom"]["silent_typo"] = 123

    with pytest.raises((ValueError, WindowsV1ConfigError), match="unknown"):
        PipelineConfig.from_dict(payload)


@pytest.mark.parametrize("nn", [0, 1_000_001, 1.5, True])
def test_pipeline_nn_rejects_non_positive_non_integer_or_excessive_values(tmp_path, nn):
    with pytest.raises(ValueError, match="nn_multiplier"):
        PipelineConfig.for_windows_v1(
            run_id=f"nn-{nn}",
            runs_root=str(tmp_path),
            windows_v1=_mixed_controls(),
            nn_multiplier=nn,
        )


def test_mixed_pipeline_persists_positive_and_true_negative_roles(tmp_path):
    config = PipelineConfig.for_windows_v1(
        run_id="mixed-roles",
        runs_root=str(tmp_path),
        windows_v1=_mixed_controls(),
        simulation_mode="prepare",
    )
    runner = PipelineRunner(config)

    cases = runner.generate()

    assert [case["case_role"] for case in cases] == ["positive", "true_negative"]
    assert cases[0]["split_role"] == "dataset_member"
    assert cases[1]["split"] == "test"
    assert cases[1]["split_role"] == "independent_test_control"
    positive_meta = json.loads(open(cases[0]["phantom"]["meta"], encoding="utf-8").read())
    negative_meta = json.loads(open(cases[1]["phantom"]["meta"], encoding="utf-8").read())
    assert positive_meta["generation_profile"] == GENERATION_PROFILE
    assert positive_meta["case_role"] == "positive"
    assert positive_meta["n_tumors"] == 1
    assert negative_meta["case_role"] == "true_negative"
    assert negative_meta["split_role"] == "independent_test_control"
    assert negative_meta["n_tumors"] == 0


def test_windows_v1_simind_rr_uses_the_domain_isolated_case_seed(tmp_path):
    controls = _mixed_controls(seed=20260822)
    config = PipelineConfig.for_windows_v1(
        run_id="rr-domain",
        runs_root=str(tmp_path),
        windows_v1=controls,
        simulation_mode="prepare",
    )
    runner = PipelineRunner(config)

    jobs = runner.prepare_simind()

    assert jobs[0].rr_seed == SeedBundle.from_case(controls.seed, "case_0001").simind
    assert jobs[1].rr_seed == SeedBundle.from_case(controls.seed, "case_0002").simind
    assert jobs[0].rr_seed != jobs[1].rr_seed


def test_windows_v1_package_manifest_contains_full_runtime_and_config_contract(tmp_path):
    controls = WindowsV1Config.from_dict(
        {
            "cohort": {"mode": "positive_only", "positive_cases": 1, "negative_cases": 0},
            "lesions": {"tumor_count_min": 1, "tumor_count_max": 1},
            "seed": 20260822,
        }
    )
    config = PipelineConfig.for_windows_v1(
        run_id="manifest-contract",
        runs_root=str(tmp_path),
        windows_v1=controls,
        simulation_mode="mock",
        nn_multiplier=10,
    )
    runner = PipelineRunner(config)

    manifest_path = runner.package()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "windows_v1"
    assert manifest["generation_profile"] == GENERATION_PROFILE
    assert manifest["runtime_backend"] == "windows_native"
    assert len(manifest["effective_config_sha256"]) == 64
    assert manifest["windows_v1"] == controls.to_dict()
    assert manifest["windows_runtime"]["status"] == "validated_windows_v1"
    assert manifest["windows_platform"]["system"] == "Windows"
    assert manifest["case_roles"] == {"case_0001": "positive"}
    assert manifest["simind_jobs"][0]["command"][0].lower().endswith("simind.exe")
    assert "/NN:10" in manifest["simind_jobs"][0]["command"]
    assert any(token.startswith("/RR:") for token in manifest["simind_jobs"][0]["command"])
    assert "observation" not in runner.ledger.load()["stages"]
