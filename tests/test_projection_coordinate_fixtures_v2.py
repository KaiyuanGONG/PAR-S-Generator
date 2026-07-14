from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "src"))

import run_projection_coordinate_fixtures_v2 as fixtures  # noqa: E402
from core.provenance import sha256_file  # noqa: E402
from core.simind_exec import SimindRunResult  # noqa: E402
from core.simind_postprocess import audit_simind_completion  # noqa: E402


CONFIG = REPO_ROOT / "configs" / "projection_coordinate_fixtures_v2.json"


def test_fixture_plan_freezes_three_wide_asymmetric_cases_and_nn5(tmp_path: Path) -> None:
    plan = fixtures.load_fixture_plan(CONFIG)

    assert tuple(plan["shape_zyx"]) == (128, 128, 128)
    assert plan["base_histories_per_projection"] == 80_000
    assert plan["nn_multiplier"] == 5
    cases = plan["cases"]
    assert len(cases) == 3
    rr_values = [case["rr_seed"] for case in cases]
    assert len(set(rr_values)) == 3
    assert all(1 <= value <= 10_007 for value in rr_values)
    for case in cases:
        assert 3 <= len(case["spots"]) <= 8
        assert len({spot["relative_weight"] for spot in case["spots"]}) == len(
            case["spots"]
        )
        assert all(3 <= spot["radius_vox"] <= 5 for spot in case["spots"])

    drifted = json.loads(CONFIG.read_text(encoding="utf-8"))
    drifted["nn_multiplier"] = 1
    path = tmp_path / "nn1.json"
    path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(ValueError, match="require /NN=5"):
        fixtures.load_fixture_plan(path)


def test_prepared_fixture_is_deterministic_c_order_sparse_and_zero_mu(
    tmp_path: Path,
) -> None:
    case = fixtures.load_fixture_plan(CONFIG)["cases"][0]
    first = fixtures.prepare_fixture_case(case, tmp_path / "first")
    second = fixtures.prepare_fixture_case(case, tmp_path / "second")

    with np.load(first["phantom_npz"], allow_pickle=False) as archive:
        assert set(archive.files) == {"simind_source_weights", "mu_true_140kev"}
        weights = np.asarray(archive["simind_source_weights"])
        mu_true = np.asarray(archive["mu_true_140kev"])
    assert weights.shape == mu_true.shape == (128, 128, 128)
    assert weights.dtype == mu_true.dtype == np.float32
    assert float(weights.sum(dtype=np.float64)) == pytest.approx(80_000, abs=0.05)
    assert 300 < np.count_nonzero(weights) < 2_000
    assert not np.any(mu_true)
    assert Path(first["source_bin"]).read_bytes() == np.asarray(
        weights, dtype="<f4", order="C"
    ).tobytes(order="C")
    assert not np.any(
        np.fromfile(first["density_bin"], dtype="<f4").reshape(128, 128, 128)
    )
    assert sha256_file(first["phantom_npz"]) == sha256_file(second["phantom_npz"])


def _runtime_inputs(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "simind.exe"
    executable.write_bytes(b"fixture executable")
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw["expected_simind_binary_sha256"] = sha256_file(executable)
    config = tmp_path / "fixture_plan.json"
    config.write_text(json.dumps(raw), encoding="utf-8")
    smc_dir = tmp_path / "smc_dir"
    smc_dir.mkdir()
    return config, executable


def _write_success(spec) -> SimindRunResult:
    final_dir = spec.output_root / spec.protocol_name / spec.case_id
    final_dir.mkdir(parents=True)
    stem = final_dir / spec.case_id
    np.zeros(spec.expected_shape, dtype="<f4").tofile(stem.with_suffix(".a00"))
    stem.with_suffix(".mhd").write_text(
        "\n".join(
            [
                "ObjectType = Image",
                "BinaryData = True",
                "BinaryDataByteOrderMSB = False",
                "CompressedData = False",
                "NDims = 3",
                "DimSize = 128 128 60",
                "ElementType = MET_FLOAT",
                f"ElementDataFile = {spec.case_id}.a00",
                "",
            ]
        ),
        encoding="ascii",
    )
    stem.with_suffix(".res").write_text("fixture\n", encoding="ascii")
    stem.with_suffix(".spe").write_bytes(b"fixture")
    audit = audit_simind_completion(stem, expected_shape=spec.expected_shape, exit_code=0)
    (final_dir / "run_provenance.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "case_id": spec.case_id,
                "rr_seed": spec.rr_seed,
                "nn_multiplier": spec.nn_multiplier,
                "completion_audit": audit.to_dict(),
            }
        ),
        encoding="utf-8",
    )
    return SimindRunResult(
        case_id=spec.case_id,
        success=True,
        exit_code=0,
        command=("simind", f"/NN:{spec.nn_multiplier}", f"/RR:{spec.rr_seed}"),
        expected_shape=spec.expected_shape,
        started_utc="2026-07-14T00:00:00Z",
        finished_utc="2026-07-14T00:01:00Z",
        final_dir=final_dir,
        output_hashes=audit.sha256,
    )


def test_runner_writes_standard_descriptor_and_complete_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, executable = _runtime_inputs(tmp_path)
    output = tmp_path / "output"
    seen_specs = []

    monkeypatch.setattr(
        fixtures,
        "_git_identity",
        lambda: {"generator_git_commit": "a" * 40, "generator_git_tree": "b" * 40},
    )

    def fake_run(spec):
        seen_specs.append(spec)
        return _write_success(spec)

    monkeypatch.setattr(fixtures, "run_simind_case", fake_run)
    complete = fixtures.run_fixture_suite(
        config_path=config,
        output_root=output,
        simind_exe=executable,
        smc_dir=tmp_path / "smc_dir",
    )

    assert complete["status"] == "complete"
    assert (output / "COMPLETE.json").is_file()
    assert len(seen_specs) == 3
    assert {spec.rr_seed for spec in seen_specs} == {101, 5003, 10007}
    assert all(spec.nn_multiplier == 5 for spec in seen_specs)
    assert all(spec.expected_shape == (60, 128, 128) for spec in seen_specs)
    descriptor = json.loads(
        (output / "projection_alignment_cases_v1.json").read_text(encoding="utf-8")
    )
    assert set(descriptor) == {"schema_version", "projection_coordinates", "cases"}
    assert descriptor["schema_version"] == "pars_projection_alignment_cases_v1"
    assert len(descriptor["cases"]) == 3
    assert all(
        set(case) == {"case_id", "phantom_npz", "projection_a00", "projection_mhd"}
        for case in descriptor["cases"]
    )
    context = json.loads((output / "RUN_CONTEXT.json").read_text(encoding="utf-8"))
    assert context["generator_git_commit"] == "a" * 40
    assert context["generator_git_tree"] == "b" * 40
    assert context["nn_multiplier"] == 5


def test_failure_retains_diagnostics_and_never_writes_descriptor_or_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, executable = _runtime_inputs(tmp_path)
    output = tmp_path / "failed_output"
    failure_dir = output / "simind" / "_failures" / "coord_spots_001" / "attempt"
    monkeypatch.setattr(
        fixtures,
        "_git_identity",
        lambda: {"generator_git_commit": "a" * 40, "generator_git_tree": "b" * 40},
    )

    def fail(spec):
        failure_dir.mkdir(parents=True)
        (failure_dir / "run_provenance.json").write_text(
            json.dumps({"status": "failed", "rr_seed": spec.rr_seed}),
            encoding="utf-8",
        )
        return SimindRunResult(
            case_id=spec.case_id,
            success=False,
            exit_code=None,
            command=("simind",),
            expected_shape=spec.expected_shape,
            started_utc="2026-07-14T00:00:00Z",
            finished_utc="2026-07-14T00:00:01Z",
            failure_dir=failure_dir,
            error="fixture failure",
        )

    monkeypatch.setattr(fixtures, "run_simind_case", fail)
    with pytest.raises(RuntimeError, match="diagnostics retained"):
        fixtures.run_fixture_suite(
            config_path=config,
            output_root=output,
            simind_exe=executable,
            smc_dir=tmp_path / "smc_dir",
        )
    assert failure_dir.is_dir()
    assert (failure_dir / "run_provenance.json").is_file()
    assert not (output / "projection_alignment_cases_v1.json").exists()
    assert not (output / "COMPLETE.json").exists()
