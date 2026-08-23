"""Compare a finalized Windows v1 run with a frozen behavioral baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .freeze_windows_v1_run import freeze_run, read_json
except ImportError:
    from freeze_windows_v1_run import freeze_run, read_json


VOLATILE_RES_PREFIXES = (
    "Simulation started.",
    "Simulation stopped.",
    "Elapsed time.......",
    "DetectorHits/CPUsec",
)
ALLOWED_SOFTWARE_CHANGES = {
    "pipeline/provenance.py",
    "pipeline/runner.py",
}


def stable_res_lines(path: Path) -> list[str]:
    return [
        line.rstrip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not line.lstrip().startswith(VOLATILE_RES_PREFIXES)
    ]


def stable_meta(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    payload.pop("generation_time_s", None)
    return payload


def compare_runs(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_root = Path(baseline["run_root"])
    candidate_root = Path(candidate["run_root"])
    checks: list[dict[str, Any]] = []

    def equal(name: str, left: Any, right: Any) -> None:
        passed = left == right
        checks.append({"name": name, "status": "passed" if passed else "failed"})
        if not passed:
            raise RuntimeError(f"Windows v1 equivalence failed: {name}")

    for key in (
        "schema_version",
        "generation_profile",
        "runtime_backend",
        "windows_v1",
        "scientific_authority",
        "windows_runtime",
        "stages",
        "inventory_file_count",
    ):
        equal(key, baseline[key], candidate[key])

    baseline_software = baseline["software_sha256"]
    candidate_software = candidate["software_sha256"]
    changed_software = {
        key
        for key in set(baseline_software) | set(candidate_software)
        if baseline_software.get(key) != candidate_software.get(key)
    }
    equal("software_change_scope", changed_software, ALLOWED_SOFTWARE_CHANGES)

    baseline_cases = {item["case_id"]: item for item in baseline["cases"]}
    candidate_cases = {item["case_id"]: item for item in candidate["cases"]}
    equal("case_ids", set(baseline_cases), set(candidate_cases))
    res_comparisons: dict[str, Any] = {}
    for case_id in sorted(baseline_cases):
        before = baseline_cases[case_id]
        after = candidate_cases[case_id]
        for key in ("case_role", "split_role", "seed", "rr_seed", "command"):
            equal(f"{case_id}.{key}", before[key], after[key])
        for key in ("phantom_npz", "act", "atn", "expectation_a00"):
            equal(
                f"{case_id}.{key}.bytes",
                before[key]["bytes"],
                after[key]["bytes"],
            )
            equal(
                f"{case_id}.{key}.sha256",
                before[key]["sha256"],
                after[key]["sha256"],
            )

        equal(
            f"{case_id}.phantom_meta.scientific_fields",
            stable_meta(baseline_root / before["phantom_meta"]["path"]),
            stable_meta(candidate_root / after["phantom_meta"]["path"]),
        )
        equal(
            f"{case_id}.projection_qc",
            before["projection_qc"],
            after["projection_qc"],
        )

        before_res = baseline_root / before["expectation_res"]["path"]
        after_res = candidate_root / after["expectation_res"]["path"]
        raw_equal = before["expectation_res"]["sha256"] == after["expectation_res"]["sha256"]
        stable_equal = stable_res_lines(before_res) == stable_res_lines(after_res)
        if not raw_equal and not stable_equal:
            raise RuntimeError(f"Windows v1 equivalence failed: {case_id}.expectation_res")
        checks.append(
            {
                "name": f"{case_id}.expectation_res",
                "status": "passed",
                "raw_byte_equal": raw_equal,
                "stable_lines_equal": stable_equal,
            }
        )
        res_comparisons[case_id] = {
            "baseline_sha256": before["expectation_res"]["sha256"],
            "candidate_sha256": after["expectation_res"]["sha256"],
            "raw_byte_equal": raw_equal,
            "stable_lines_equal": stable_equal,
            "allowed_volatile_prefixes": list(VOLATILE_RES_PREFIXES),
        }

    return {
        "evidence_schema": "windows_v1_equivalence_report_v1",
        "status": "passed",
        "baseline_run_id": baseline["run_id"],
        "baseline_source_commit": baseline["source_commit"],
        "candidate_run_id": candidate["run_id"],
        "candidate_source_commit": candidate["source_commit"],
        "checks": checks,
        "res_comparisons": res_comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-evidence", type=Path, required=True)
    parser.add_argument("--candidate-run-root", type=Path, required=True)
    parser.add_argument("--candidate-source-commit", required=True)
    parser.add_argument("--candidate-config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline = read_json(args.baseline_evidence)
    candidate = freeze_run(
        args.candidate_run_root,
        source_commit=args.candidate_source_commit,
        config_path=args.candidate_config,
    )
    report = compare_runs(baseline, candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
