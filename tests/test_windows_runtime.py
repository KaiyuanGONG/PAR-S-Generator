from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from core.windows_runtime import (
    VERIFIED_SIMIND_SHA256,
    VERIFIED_SMC_SHA256,
    WindowsPathError,
    assess_windows_runtime,
    validate_windows_path,
)
from pipeline import simind
from pipeline.simind import SimindJob, run_job


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_bundled_windows_runtime_matches_the_validated_pair() -> None:
    exe = REPO_ROOT / "simind" / "simind.exe"
    smc = REPO_ROOT / "simind" / "ge870_czt.smc"

    assessment = assess_windows_runtime(exe, smc)

    assert assessment.smc_sha256 == VERIFIED_SMC_SHA256
    if assessment.simind_sha256 != VERIFIED_SIMIND_SHA256:
        pytest.skip("validated licensed SIMIND executable is not installed")
    assert assessment.status == "validated_windows_v1"
    assert assessment.simind_sha256 == VERIFIED_SIMIND_SHA256


def test_runtime_hash_mismatch_is_explicitly_unverified(tmp_path: Path) -> None:
    exe = tmp_path / "simind.exe"
    smc = tmp_path / "custom.smc"
    exe.write_bytes(b"different executable")
    smc.write_bytes(b"different change file")

    assessment = assess_windows_runtime(exe, smc)

    assert assessment.status == "unverified_runtime"
    assert assessment.simind_sha256 == _sha256(exe)
    assert assessment.smc_sha256 == _sha256(smc)
    assert assessment.mismatches == ("simind_executable", "smc")


@pytest.mark.parametrize(
    ("raw_path", "message"),
    [
        (r"\\server\share\simind.exe", "local Windows drive"),
        (r"//server/share/ge870_czt.smc", "local Windows drive"),
        (r"C:\data\CON\ge870_czt.smc", "reserved Windows name"),
        (r"C:\data\trailing.\ge870_czt.smc", "trailing dot or space"),
        (r"C:\data\trailing \ge870_czt.smc", "trailing dot or space"),
    ],
)
def test_windows_path_policy_rejects_unc_reserved_and_ambiguous_names(
    raw_path: str,
    message: str,
) -> None:
    with pytest.raises(WindowsPathError, match=message):
        validate_windows_path(raw_path, "smc", require_exists=False)


def test_windows_path_policy_rejects_resolved_paths_over_240_characters(tmp_path: Path) -> None:
    too_long = tmp_path.joinpath(*(["segment0123456789"] * 20), "simind.exe")

    with pytest.raises(WindowsPathError, match="240 characters"):
        validate_windows_path(too_long, "simind_exe", require_exists=False)


def test_windows_path_policy_accepts_spaces_chinese_and_accents(tmp_path: Path) -> None:
    output = tmp_path / "含 空格" / "résultats"
    output.mkdir(parents=True)

    result = validate_windows_path(output, "runs_root")

    assert result == output.resolve()


def test_windows_path_policy_requires_correct_file_extensions(tmp_path: Path) -> None:
    wrong = tmp_path / "simind.txt"
    wrong.write_bytes(b"stub")

    with pytest.raises(WindowsPathError, match=r"\.exe"):
        validate_windows_path(wrong, "simind_exe")


def _job(tmp_path: Path, *, expected_exe: str, expected_smc: str) -> SimindJob:
    working = tmp_path / "working"
    working.mkdir()
    exe = tmp_path / "simind.exe"
    smc = tmp_path / "ge870_czt.smc"
    exe.write_bytes(b"runtime exe")
    smc.write_bytes(b"runtime smc")
    (working / smc.name).write_bytes(smc.read_bytes())
    return SimindJob(
        case_id="case_00001",
        simind_exe=exe,
        smc_file=smc,
        working_dir=working,
        output_stem=tmp_path / "expectation" / "case_00001",
        source_stem="case_00001",
        density_stem="case_00001",
        expected_executable_sha256=expected_exe,
        expected_smc_sha256=expected_smc,
    )


def test_run_job_refuses_runtime_changed_since_plan(tmp_path: Path, monkeypatch) -> None:
    job = _job(tmp_path, expected_exe="0" * 64, expected_smc="1" * 64)
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(simind.subprocess, "run", fake_run)
    monkeypatch.setattr(simind, "assert_simind_artifact_paths_clear", lambda *args: None)

    with pytest.raises(RuntimeError, match="changed since planning"):
        run_job(job, tmp_path / "runtime.log", shape=(1, 1, 1))

    assert called is False


def test_run_job_marks_post_execution_runtime_hash_drift_failed(tmp_path: Path, monkeypatch) -> None:
    exe_bytes = b"runtime exe"
    smc_bytes = b"runtime smc"
    expected_exe = hashlib.sha256(exe_bytes).hexdigest()
    expected_smc = hashlib.sha256(smc_bytes).hexdigest()
    job = _job(tmp_path, expected_exe=expected_exe, expected_smc=expected_smc)

    def fake_run(*args, **kwargs):
        job.simind_exe.write_bytes(b"mutated while running")
        return subprocess.CompletedProcess(args[0], 0, "ok", "")

    monkeypatch.setattr(simind.subprocess, "run", fake_run)
    monkeypatch.setattr(simind, "assert_simind_artifact_paths_clear", lambda *args: None)
    monkeypatch.setattr(simind, "relocate_simind_artifacts", lambda *args, **kwargs: [])
    monkeypatch.setattr(simind, "completion_qc", lambda *args, **kwargs: {"status": "passed"})

    qc = run_job(job, tmp_path / "runtime.log", shape=(1, 1, 1))

    assert qc["status"] == "failed"
    assert "runtime_hash_drift:simind_executable" in qc["failures"]
    assert qc["runtime_hashes"]["before"]["simind_executable"] == expected_exe
    assert qc["runtime_hashes"]["after"]["simind_executable"] != expected_exe
