from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cli import main as cli_main
from core.interfile_writer import convert_npz_to_interfile
from core.phantom_generator import PhantomConfig
from pipeline.contracts import assign_fixed_splits, read_jsonl, sha256_file
from pipeline.observation import sample_poisson_observation
from pipeline.qc import load_projection, validate_projection_artifacts
from pipeline.runner import PipelineConfig, PipelinePaused, PipelineRunner
from pipeline.simind import build_simind_tokens
from core.simind_runner import build_simind_command


def test_fixed_split_is_deterministic_and_phantom_level():
    case_ids = [f"case_{i:04d}" for i in range(1, 501)]
    first = assign_fixed_splits(case_ids, seed=42)
    second = assign_fixed_splits(reversed(case_ids), seed=42)
    assert first == second
    assert list(first.values()).count("train") == 400
    assert list(first.values()).count("val") == 50
    assert list(first.values()).count("test") == 50


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


def test_interfile_export_is_exact_float32_c_order(tmp_path: Path):
    shape = (3, 4, 5)
    activity = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    mu_map = activity / 100
    source = tmp_path / "case_0001.npz"
    np.savez(source, activity=activity, mu_map=mu_map)
    result = convert_npz_to_interfile(source, tmp_path / "bin")
    assert result["readback_verified"] is True
    assert np.array_equal(np.fromfile(result["act_bin"], np.float32).reshape(shape), activity)
    assert np.array_equal(np.fromfile(result["atn_bin"], np.float32).reshape(shape), mu_map)
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
    config = _smoke_config(tmp_path, "cli-prepare")
    config.phantom.n_cases = 1
    config.simulation_mode = "prepare"
    config.create_poisson_observation = False
    config_path = tmp_path / "prepare.json"
    config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")

    assert cli_main(["run", "--config", str(config_path)]) == 0
    state = json.loads((tmp_path / "runs" / "cli-prepare" / "run.json").read_text(encoding="utf-8"))
    assert state["finalized"] is False
    assert state["stages"]["simind_plan"]["status"] == "prepared"
    assert state["stages"]["expectation"]["status"] == "skipped"
    with pytest.raises(RuntimeError, match="prepared-only"):
        PipelineRunner.open(tmp_path / "runs" / "cli-prepare").finalize()
