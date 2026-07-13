from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_file(path: Path) -> dict:
    path = path.resolve()
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
    ).strip()


def capture_worktree_state(repo: Path) -> dict:
    entries = []
    for line in git_output(repo, "status", "--short", "--untracked-files=all").splitlines():
        relative_path = line[3:]
        path = repo / relative_path
        entry = {"status": line[:2], "relative_path": relative_path.replace("\\", "/")}
        if path.is_file():
            entry.update(size_bytes=path.stat().st_size, sha256=sha256_file(path))
        else:
            entry["exists"] = path.exists()
        entries.append(entry)
    return {
        "repo_root": str(repo.resolve()),
        "head": git_output(repo, "rev-parse", "HEAD"),
        "branch": git_output(repo, "branch", "--show-current"),
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture the immutable PAR-S Generator V1 baseline.")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_repo = args.source_repo.resolve()
    runtime_paths = {
        "installed_simind_exe": Path(r"C:\simind\simind.exe"),
        "bundled_simind_exe": source_repo / "simind" / "simind.exe",
        "simind_ini": Path(r"C:\simind\smc_dir\simind.ini"),
        "ge870_czt_smc": source_repo / "simind" / "ge870_czt.smc",
        "simind_manual": source_repo / "docs" / "simind_manual.pdf",
        "ge870_czt_pds": source_repo / "docs" / "DOC2109131-NMCT-870-CZT-PDS.pdf",
    }
    missing = [str(path) for path in runtime_paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing runtime artifacts: {missing}")

    snapshot = {
        "schema_version": "pars_generator_v1_baseline_snapshot_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": "b19feb9c0dbd206961986da6bb38f212aedc3143",
        "read_only": True,
        "source_worktree_state": capture_worktree_state(source_repo),
        "runtime_artifacts": {name: describe_file(path) for name, path in runtime_paths.items()},
        "known_baseline_tests": {
            "command": "conda run -n SPECT python -m pytest -q tests",
            "collected": 24,
            "passed": 23,
            "failed": 1,
            "status": "known_baseline_failure",
            "failure_id": "tests/test_ui_smoke.py::test_tumor_single_value_maps_to_min_max",
            "root_cause": (
                "The test expects preview tumor controls to overwrite batch min/max, while the same "
                "historical implementation explicitly keeps those controls preview-only."
            ),
            "scope_decision": "record_and_continue_without_ui_changes",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
