from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_setup_script_locks_python_and_builds_the_frontend() -> None:
    script = (ROOT / "setup_windows.ps1").read_text(encoding="utf-8")
    assert "3.11" in script
    assert "requirements-windows-v1.lock.txt" in script
    assert "npm.cmd ci" in script
    assert "npm.cmd run build" in script
    assert "[string]$Python" in script
    assert "conda info --envs --json" in script
    assert "22.22.2+" in script
    assert "24.15+" in script
    assert "import fastapi, numpy, scipy, PyQt6, uvicorn, websockets" in script
    lock = (ROOT / "requirements-windows-v1.lock.txt").read_text(encoding="utf-8")
    assert "websockets==17.0.1" in lock


def test_windows_ci_fails_fast_and_preserves_release_bytes_and_failure_evidence() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows-v1.yml").read_text(encoding="utf-8")
    assert workflow.count('$PSNativeCommandUseErrorActionPreference = $true') == 6
    assert 'node-version: "24.15.0"' in workflow
    assert "include-hidden-files: true" in workflow
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "simind/ge870_czt.smc -text -eol -whitespace" in attributes


def test_start_script_uses_the_managed_environment_and_web_entrypoint() -> None:
    script = (ROOT / "start_windows.ps1").read_text(encoding="utf-8")
    assert ".venv-windows-v1" in script
    assert "main.py" in script
    assert "legacy_pyqt.py" not in script


def test_verifier_covers_hash_tests_launch_prepare_mock_and_real_confirmation() -> None:
    script = (ROOT / "scripts" / "verify_windows_v1.ps1").read_text(encoding="utf-8")
    assert "F984B8753F54B9F671F9FC1BCB2B45461E7CAE8D027376B446DD1ED55A9A8319" in script
    assert "4D10EAB246A7A6690663230D2F33AEB3C32F67C598AF36B56D1575F0E3551D10" in script
    assert "python -m pytest" in script
    assert "npm.cmd run test:e2e" in script
    assert '"prepare"' in script
    assert '"mock"' in script
    assert "Read-Host" in script
    assert "--allow-simind-execution" in script
