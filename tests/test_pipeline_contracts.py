from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cli import main as cli_main
from core.interfile_writer import convert_npz_to_interfile
from core.phantom_generator import PhantomConfig
from pipeline.contracts import assign_fixed_splits, read_jsonl, sha256_file
from pipeline.observation import assign_empirical_count_targets, sample_poisson_observation
from pipeline.pilot import select_representative_cases
from pipeline.qc import load_projection, validate_projection_artifacts
from pipeline.runner import PipelineConfig, PipelinePaused, PipelineRunner
from pipeline.simind import (
    SimindJob,
    assert_simind_artifact_paths_clear,
    build_simind_args,
    build_simind_tokens,
    job_record,
    relocate_simind_artifacts,
    render_batch_script,
    simind_output_argument,
)
from core.simind_runner import build_simind_command
from core.windows_v1 import WindowsV1Config


def test_fixed_split_is_deterministic_and_phantom_level():
    case_ids = [f"case_{i:04d}" for i in range(1, 501)]
    first = assign_fixed_splits(case_ids, seed=42)
    second = assign_fixed_splits(reversed(case_ids), seed=42)
    assert first == second
    assert list(first.values()).count("train") == 400
    assert list(first.values()).count("val") == 50
    assert list(first.values()).count("test") == 50


def test_activity_time_contract_is_coherent_and_controls_simind_index25(tmp_path: Path):
    config = _smoke_config(tmp_path, "activity-time")
    config.simulation_mode = "prepare"
    runner = PipelineRunner(config)
    jobs = runner.prepare_simind()
    assert jobs[0].rr_seed == config.simind_seed_base + 1
    assert any(index == 25 and float(value) == 1704.0 for index, value in jobs[0].overrides)
    command = build_simind_args(jobs[0])
    assert any("/25:1704" in token for token in command)
    assert any("/100:160/101:208" in token for token in command)
    assert any("/IN:x21,100x" in token for token in command)
    assert command[-1] == f"/RR:{config.simind_seed_base + 1}"

    with pytest.raises(ValueError, match="Index-25"):
        PipelineConfig(
            run_id="bad-product",
            source_activity_mbq=60.0,
            exposure_time_s_per_projection=20.0,
            smc_index25_activity_time=1704.0,
        )


def test_gui_worker_and_pipeline_share_exact_simind_tokens():
    expected = build_simind_tokens(
        smc_stem="ge870_czt",
        output_stem="out/case_0001",
        source_stem="case_0001",
        density_stem="case_0001",
        nn_multiplier=5,
        overrides=[(100, "128")],
    )
    actual = build_simind_command(
        "ge870_czt", "out/case_0001", "case_0001", "case_0001", 5, [(100, "128")]
    )
    assert actual == expected


def test_simind_runtime_switches_are_explicit_and_validated():
    args = build_simind_tokens(
        smc_stem="attenuation",
        output_stem="mu_control",
        source_stem="mu_control",
        density_stem="mu_control",
        runtime_switches=["/SC:0"],
    )
    assert args[-1] == "/SC:0"
    seeded_args = build_simind_tokens(
        smc_stem="attenuation",
        output_stem="mu_control",
        source_stem="mu_control",
        density_stem="mu_control",
        rr_seed=9200,
        overrides=[(85, "4")],
        runtime_switches=["/SC:1"],
    )
    assert seeded_args[-1] == "/RR:9200"
    assert seeded_args[-2] == "/SC:1/85:4"
    with pytest.raises(ValueError, match="Unsafe"):
        build_simind_tokens(
            smc_stem="attenuation",
            output_stem="mu_control",
            source_stem="mu_control",
            density_stem="mu_control",
            runtime_switches=["/SC:0 & bad"],
        )


def test_simind_execution_uses_safe_relative_output_argument(tmp_path: Path):
    root = tmp_path / "parent-with-hyphen"
    working = root / "input"
    output = root / "output" / "case_0001"
    working.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    job = SimindJob(
        case_id="case_0001",
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        working_dir=working,
        output_stem=output,
        source_stem="case_0001",
        density_stem="case_0001",
        nn_multiplier=1,
    )

    assert simind_output_argument(output, working) == "case_0001"
    assert build_simind_args(job)[1] == "case_0001"
    assert job_record(job)["output_stem"] == str(output.resolve())
    assert job_record(job)["output_argument"] == "case_0001"
    script = render_batch_script([job])
    simind_line = next(line for line in script.splitlines() if "simind.exe" in line)
    assert str(output.resolve()) not in simind_line
    assert " case_0001 " in simind_line
    assert "move /Y" in script


def test_simind_output_argument_rejects_switch_like_path(tmp_path: Path):
    working = tmp_path / "input"
    output = tmp_path / "output" / "case-0001"
    working.mkdir()
    output.parent.mkdir()
    with pytest.raises(ValueError, match="parsed as switches"):
        simind_output_argument(output, working)


def test_simind_artifacts_are_relocated_without_overwrite(tmp_path: Path):
    staging = tmp_path / "input" / "case_0001"
    destination = tmp_path / "output" / "case_0001"
    staging.parent.mkdir()
    staging.with_suffix(".a00").write_bytes(b"projection")
    staging.with_suffix(".res").write_text("result", encoding="utf-8")

    moved = relocate_simind_artifacts(staging, destination)

    assert {path.suffix for path in moved} == {".a00", ".res"}
    assert destination.with_suffix(".a00").read_bytes() == b"projection"
    assert not staging.with_suffix(".a00").exists()
    staging.with_suffix(".a00").write_bytes(b"replacement")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        relocate_simind_artifacts(staging, destination)


def test_scattwin_component_artifacts_are_relocated_as_primary(tmp_path: Path):
    staging = tmp_path / "input" / "mu_control"
    destination = tmp_path / "output" / "mu_control"
    staging.parent.mkdir()
    for suffix in ("_air_w1.a00", "_pri_w1.a00", "_pri_w1.mhd", ".res"):
        (staging.parent / f"{staging.name}{suffix}").write_bytes(suffix.encode("ascii"))

    moved = relocate_simind_artifacts(
        staging,
        destination,
        primary_artifact_suffix="_pri_w1.a00",
    )

    moved_names = {path.name for path in moved}
    assert "mu_control_air_w1.a00" in moved_names
    assert "mu_control_pri_w1.a00" in moved_names
    assert (destination.parent / "mu_control_pri_w1.a00").exists()
    assert not (staging.parent / "mu_control_pri_w1.a00").exists()


def test_simind_launch_refuses_preexisting_staging_or_destination(tmp_path: Path):
    staging = tmp_path / "input" / "case_0001"
    destination = tmp_path / "output" / "case_0001"
    staging.parent.mkdir()
    destination.parent.mkdir()
    staging.with_suffix(".res").write_text("stale", encoding="utf-8")

    with pytest.raises(FileExistsError, match="mix or overwrite"):
        assert_simind_artifact_paths_clear(staging, destination)

    staging.with_suffix(".res").unlink()
    destination.with_suffix(".a00").write_bytes(b"partial")
    with pytest.raises(FileExistsError, match="mix or overwrite"):
        assert_simind_artifact_paths_clear(staging, destination)


def test_interfile_export_is_exact_float32_c_order(tmp_path: Path):
    shape = (3, 4, 5)
    activity = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    mu_map = activity / 100
    source = tmp_path / "case_0001.npz"
    np.savez(source, activity=activity, mu_map=mu_map)
    result = convert_npz_to_interfile(source, tmp_path / "bin")
    assert result["readback_verified"] is True
    assert np.array_equal(np.fromfile(result["act_bin"], np.float32).reshape(shape), activity)
    stored = np.fromfile(result["atn_bin"], np.float32).reshape(shape)
    assert np.array_equal(stored, np.asarray(mu_map * 0.442, dtype=np.float32))
    assert result["type7_conversion_formula"] == "stored_value = mu_cm_inverse * voxel_size_cm"
    assert result["type7_stored_unit"] == "dimensionless_per_voxel_optical_thickness"
    assert result["type7_roundtrip_max_abs_error_cm_inverse"] <= 1e-6
    assert result["act_sha256"] == sha256_file(result["act_bin"])


def test_projection_completion_requires_shape_values_and_res(tmp_path: Path):
    path = tmp_path / "case_0001.a00"
    np.ones((2, 3, 4), np.float32).tofile(path)
    failed = validate_projection_artifacts(path, shape=(2, 3, 4))
    assert failed["status"] == "failed"
    path.with_suffix(".res").write_text("Simulation stopped.:\n", encoding="utf-8")
    passed = validate_projection_artifacts(path, shape=(2, 3, 4))
    assert passed["status"] == "passed"
    assert np.array_equal(load_projection(path, shape=(2, 3, 4)), np.ones((2, 3, 4), np.float32))
    mismatch = validate_projection_artifacts(
        path, shape=(2, 3, 4), expected_command_tokens=("/FS:case_0001", "/NN:5")
    )
    assert mismatch["status"] == "failed"
    path.with_suffix(".res").write_text(
        "Simulation stopped.:\nCommand: ge870 out /FS:case_0001 /NN:5\n", encoding="utf-8"
    )
    matched = validate_projection_artifacts(
        path, shape=(2, 3, 4), expected_command_tokens=("/FS:case_0001", "/NN:5")
    )
    assert matched["status"] == "passed"


def test_canonical_projection_keeps_view_order_and_flips_detector_row(tmp_path: Path):
    raw = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
    path = tmp_path / "orientation.a00"
    raw.tofile(path)
    loaded = load_projection(path, shape=raw.shape)
    assert np.array_equal(loaded, raw[:, ::-1, :])
    assert not np.array_equal(loaded, raw[::-1, ::-1, :])
    path.with_suffix(".res").write_text("Simulation stopped.:\n", encoding="utf-8")
    qc = validate_projection_artifacts(path, shape=raw.shape)
    view_sums = raw.sum(axis=(1, 2), dtype=np.float64)
    assert qc["metrics"]["view_sum_min"] == pytest.approx(float(view_sums.min()))
    assert qc["metrics"]["view_sum_median"] == pytest.approx(float(np.median(view_sums)))
    assert qc["metrics"]["view_sum_max"] == pytest.approx(float(view_sums.max()))
    assert qc["metrics"]["angular_cv"] == pytest.approx(
        float(view_sums.std() / view_sums.mean())
    )


def test_existing_readonly_projection_sample_passes_strong_qc():
    sample = Path("output/SPECT_60Mbq20s/case_0001.a00")
    if not sample.exists():
        pytest.skip("legacy projection sample is not present")
    result = validate_projection_artifacts(sample, require_mhd=True)
    assert result["status"] == "passed"
    assert result["metrics"]["shape"] == [60, 128, 128]
    assert result["mhd"]["dim_size"] == [128, 128, 60]
    assert result["metrics"]["noninteger_positive_fraction"] > 0.99
    assert result["res_effective"]["source"] == "SIMIND .res"
    assert result["res_effective"]["projection_count"] == 60
    assert result["res_effective"]["detector_matrix_i"] == 128
    assert result["res_effective"]["photon_energy_kev"] == pytest.approx(140.0)
    assert result["res_effective"]["activity_time_value"] == pytest.approx(1704.0)


def test_offline_poisson_is_reproducible_and_separate(tmp_path: Path):
    shape = (2, 3, 4)
    expectation = tmp_path / "expectation.a00"
    np.full(shape, 4.5, np.float32).tofile(expectation)
    one = tmp_path / "one.a00"
    two = tmp_path / "two.a00"
    r1 = sample_poisson_observation(expectation, one, seed=123, shape=shape)
    r2 = sample_poisson_observation(expectation, two, seed=123, shape=shape)
    assert r1["sha256"] == r2["sha256"]
    observed = np.fromfile(one, np.float32)
    assert np.all(observed == np.floor(observed))
    assert np.all(np.fromfile(expectation, np.float32) == 4.5)


def test_empirical_observation_targets_are_stratified_reproducible_and_nonabsolute(tmp_path: Path):
    ids = [f"case_{index:04d}" for index in range(1, 11)]
    reference = (2_000_000, 2_500_000, 3_000_000, 4_000_000)
    first = assign_empirical_count_targets(ids, reference, seed=42)
    second = assign_empirical_count_targets(list(reversed(ids)), reference, seed=42)
    assert first == second
    assert min(first.values()) >= min(reference)
    assert max(first.values()) <= max(reference)
    assert len(set(first.values())) > 4

    shape = (2, 3, 4)
    expectation = tmp_path / "expectation.a00"
    np.full(shape, 4.5, np.float32).tofile(expectation)
    output = tmp_path / "empirical.a00"
    result = sample_poisson_observation(
        expectation,
        output,
        seed=123,
        target_total_counts=2_500_000,
        shape=shape,
        protocol_status="empirical_protocol_matching",
    )
    assert result["scale_policy"] == "target_total_counts_divided_by_expectation_sum"
    assert result["target_relative_error"] < 0.01
    assert "not activity" in result["claim_boundary"]


def _smoke_config(tmp_path: Path, run_id: str) -> PipelineConfig:
    phantom = PhantomConfig(n_cases=2, global_seed=42, use_global_seed=True)
    return PipelineConfig(
        run_id=run_id,
        runs_root=str(tmp_path / "runs"),
        phantom=phantom,
        simind_exe=str(Path("simind/simind.exe").resolve()),
        smc_file=str(Path("simind/ge870_czt.smc").resolve()),
        simulation_mode="mock",
        create_poisson_observation=True,
        observation_protocol_status="toy",
    )


def test_two_case_pipeline_smoke_and_strong_resume(tmp_path: Path):
    config = _smoke_config(tmp_path, "smoke-two")
    runner = PipelineRunner(config)
    final = runner.run_all()
    assert final["finalized"] is True
    root = tmp_path / "runs" / "smoke-two"
    assert {p.name for p in root.iterdir() if p.is_dir()} >= {
        "phantom", "simind_input", "expectation", "observation", "qc", "logs", "figures"
    }
    cases = read_jsonl(root / "cases.jsonl")
    assert len(cases) == 2
    assert {case["split"] for case in cases} == {"train", "test"}
    assert all(case["expectation"]["backend"] == "deterministic_mock_not_simind" for case in cases)
    assert all(case["phantom"]["npz_relpath"].startswith("phantom/") for case in cases)
    assert all(case["observation"]["parent_phantom_id"] == case["phantom_id"] for case in cases)
    assert all(case["observation"]["split"] == case["split"] for case in cases)
    assert all(case["observation"]["observation_relpath"].startswith("observation/") for case in cases)
    manifest = json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["simulation_mode"] == "mock"
    assert manifest["scope"] == "synthetic_liver_spect_data_preparation_only"
    assert (root / "figures" / "data_flow.svg").exists()
    assert (root / "figures" / "phantom_distribution_data.csv").exists()
    run_state = json.loads((root / "run.json").read_text(encoding="utf-8"))
    assert set(run_state["provenance"]["software_sha256"]) >= {
        "core/phantom_generator.py", "pipeline/runner.py", "pipeline/qc.py", "pipeline/simind.py"
    }
    assert run_state["provenance"]["smc"]["sha256"]
    assert run_state["provenance"]["type7_attenuation"]["density_threshold_times_1000"] == 100

    resumed = PipelineRunner(config, resume=True)
    resumed.run_all()
    corrupt = Path(cases[0]["phantom"]["npz"])
    original = corrupt.read_bytes()
    corrupt.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
    with pytest.raises(RuntimeError, match="Resume rejected"):
        PipelineRunner(config, resume=True).generate()
    corrupt.write_bytes(original)

    projection = Path(cases[0]["expectation"]["a00"])
    projection_bytes = projection.read_bytes()
    projection.write_bytes(bytes([projection_bytes[0] ^ 0x01]) + projection_bytes[1:])
    with pytest.raises(RuntimeError, match="Resume rejected"):
        PipelineRunner(config, resume=True).simulate_or_mock()

    manifest_path = root / "dataset_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_path.write_bytes(manifest_bytes[:-1] + bytes([manifest_bytes[-1] ^ 0x01]))
    with pytest.raises(RuntimeError, match="Resume rejected"):
        PipelineRunner(config, resume=True).run_all()


def test_representative_pilot_selection_is_deterministic(tmp_path: Path):
    config = _smoke_config(tmp_path, "pilot-selection")
    config.phantom.n_cases = 4
    config.create_poisson_observation = False
    runner = PipelineRunner(config)
    cases = runner.run_phantom_qc()
    first = select_representative_cases(cases, 2)
    second = select_representative_cases(list(reversed(cases)), 2)
    assert first["selected"] == second["selected"]
    assert len(first["selected"]) == 2
    assert all(row["case_number"] > 0 for row in first["selected"])


def test_explicit_case_numbers_reproduce_nonconsecutive_cases(tmp_path: Path):
    phantom = PhantomConfig(n_cases=2, global_seed=42, use_global_seed=True)
    config = PipelineConfig(
        run_id="selected-cases",
        runs_root=str(tmp_path / "runs"),
        phantom=phantom,
        case_numbers=[3, 17],
        simind_exe=str(Path("simind/simind.exe").resolve()),
        smc_file=str(Path("simind/ge870_czt.smc").resolve()),
        simulation_mode="prepare",
    )
    cases = PipelineRunner(config).generate()
    assert [row["case_id"] for row in cases] == ["case_0003", "case_0017"]
    assert [row["seed"] for row in cases] == [45, 59]


def test_noncanonical_case_order_maps_jobs_by_case_id(tmp_path: Path):
    """Regression for selection-order versus ledger-order projection swaps."""
    phantom = PhantomConfig(n_cases=3, global_seed=42, use_global_seed=True)
    config = PipelineConfig(
        run_id="selected-order-mapping",
        runs_root=str(tmp_path / "runs"),
        phantom=phantom,
        case_numbers=[17, 3, 11],
        simind_exe=str(Path("simind/simind.exe").resolve()),
        smc_file=str(Path("simind/ge870_czt.smc").resolve()),
        simulation_mode="mock",
        create_poisson_observation=False,
    )
    PipelineRunner(config).simulate_or_mock()
    cases = read_jsonl(tmp_path / "runs" / "selected-order-mapping" / "cases.jsonl")

    for record in cases:
        assert Path(record["expectation"]["a00"]).stem == record["case_id"]
        assert Path(record["qc"]["projection"]["path"]).stem == (
            f"{record['case_id']}_projection_qc"
        )


def test_pause_checkpoints_and_resume_reuses_completed_case(tmp_path: Path):
    config = _smoke_config(tmp_path, "pause-resume")
    config.simulation_mode = "prepare"
    config.create_poisson_observation = False
    runner = PipelineRunner(config)

    def pause_after_first(_message: str):
        runner.request_pause()

    with pytest.raises(PipelinePaused):
        runner.generate(progress=pause_after_first)
    root = tmp_path / "runs" / "pause-resume"
    partial = read_jsonl(root / "cases.jsonl")
    assert len(partial) == 1
    first_hash = partial[0]["phantom"]["npz_sha256"]
    resumed = PipelineRunner(config, resume=True)
    cases = resumed.generate()
    assert len(cases) == 2
    assert cases[0]["phantom"]["npz_sha256"] == first_hash


def test_cli_prepare_mode_completes_without_attempting_finalize(tmp_path: Path):
    config = PipelineConfig.for_windows_v1(
        run_id="cli-prepare",
        runs_root=str(tmp_path / "runs"),
        windows_v1=WindowsV1Config.from_dict(
            {
                "cohort": {"mode": "positive_only", "positive_cases": 1, "negative_cases": 0},
                "lesions": {"tumor_count_min": 1, "tumor_count_max": 1},
            }
        ),
        simulation_mode="prepare",
        create_poisson_observation=False,
    )
    config_path = tmp_path / "prepare.json"
    config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")

    assert cli_main(["run", "--config", str(config_path)]) == 0
    state = json.loads((tmp_path / "runs" / "cli-prepare" / "run.json").read_text(encoding="utf-8"))
    assert state["finalized"] is False
    assert state["stages"]["simind_plan"]["status"] == "prepared"
    assert state["stages"]["expectation"]["status"] == "skipped"
    with pytest.raises(RuntimeError, match="prepared-only"):
        PipelineRunner.open(tmp_path / "runs" / "cli-prepare").finalize()
