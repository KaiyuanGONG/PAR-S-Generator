from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from task12e_linux_common import (  # noqa: E402
    BUNDLE_SCHEMA,
    ENVIRONMENT_SCHEMA,
    NODE_COMPLETE_SCHEMA,
    PLAN_SCHEMA,
    atomic_write_json,
    node_case_specs,
    normalized_res_sha256,
    safe_extract_tar,
    sha256_file,
    validate_bundle,
    validate_node_id,
)


def _load_script(name: str):
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task12d_manual_acceptance_releases_linux_only() -> None:
    document = json.loads(
        (REPO_ROOT / "docs" / "reports" / "task12d_manual_acceptance.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["decision"] == "approved_with_linux_platform_homologation_required"
    assert document["release"]["go_for_task12e_linux_homologation"] is True
    assert document["release"]["go_for_50_case_generation"] is False
    assert document["metadata_override"]["replacement_value"] == 50


def test_task12e_v1_failure_is_non_scientific_and_v2_only() -> None:
    document = json.loads(
        (
            REPO_ROOT
            / "docs"
            / "reports"
            / "task12e_v1_environment_preflight_failure.json"
        ).read_text(encoding="utf-8")
    )
    assert document["root_cause"]["scientific_environment_difference"] is False
    assert document["release"]["accept_v1_worker_outputs"] is False
    assert document["release"]["go_for_bundle_v2_environment_recapture"] is True


def test_task12e_static_plan_freezes_three_homogeneous_nodes() -> None:
    plan = json.loads(
        (REPO_ROOT / "configs" / "task12e_linux_homologation_v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["expected_nodes"] == ["cnc5", "cnc7", "cnc8"]
    assert plan["canonical_projection_node"] == "cnc5"
    assert plan["expected_linux_simind_sha256"] == (
        "e143e2e0b0315c9cd8b6bb187d6bd28448e096c255f8d16ee0c14787d1537f9d"
    )
    assert plan["observed_resources_per_node"]["cpu_quota_equivalent"] == 55.0
    assert plan["fixture_execution"]["initial_max_parallel_per_node"] == 6
    assert plan["fixture_execution"]["requested_parallel_by_node"] == {
        "cnc5": 6,
        "cnc7": 3,
        "cnc8": 3,
    }
    assert plan["environment"]["shared_prefix_comparison"] == "resolved_realpath"


def test_environment_prefix_uses_realpath_alias_equivalence(monkeypatch) -> None:
    module = _load_script("capture_task12e_linux_environment.py")
    canonical = "/export/work/ummisco/home/kgong/conda-envs/pars-v2-linux-py311"
    aliases = {
        "/home/kgong/conda-envs/pars-v2-linux-py311": canonical,
        canonical: canonical,
    }
    monkeypatch.setattr(module.os.path, "realpath", lambda value: aliases[str(value)])
    logical = module._prefix_realpath(
        "/home/kgong/conda-envs/pars-v2-linux-py311"
    )
    physical = module._prefix_realpath(canonical)
    assert logical == physical


def test_normalized_res_ignores_only_runtime_lines(tmp_path: Path) -> None:
    first = tmp_path / "first.res"
    second = tmp_path / "second.res"
    first.write_text(
        "header\n Simulation started.: one\nvalue=5\nElapsed time.......: 1\n",
        encoding="utf-8",
    )
    second.write_text(
        "header\n Simulation started.: two\nvalue=5\nElapsed time.......: 99\n",
        encoding="utf-8",
    )
    assert normalized_res_sha256(first) == normalized_res_sha256(second)
    second.write_text(second.read_text(encoding="utf-8").replace("value=5", "value=6"))
    assert normalized_res_sha256(first) != normalized_res_sha256(second)


def test_bundle_validation_and_node_assignment(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"frozen")
    plan = {
        "schema_version": PLAN_SCHEMA,
        "expected_nodes": ["cnc5", "cnc7", "cnc8"],
        "hostname_prefix_by_node": {
            "cnc5": "cnc5-",
            "cnc7": "cnc7-",
            "cnc8": "cnc8-",
        },
        "cases": [
            {"case_id": "clinical", "nodes": ["cnc5", "cnc7", "cnc8"]},
            {"case_id": "coordinate", "nodes": ["cnc5"]},
        ],
    }
    atomic_write_json(root / "TASK12E_PLAN.json", plan)
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "status": "complete",
        "plan_relative_path": "TASK12E_PLAN.json",
        "plan_sha256": sha256_file(root / "TASK12E_PLAN.json"),
        "files": [
            {
                "relative_path": "TASK12E_PLAN.json",
                "size_bytes": (root / "TASK12E_PLAN.json").stat().st_size,
                "sha256": sha256_file(root / "TASK12E_PLAN.json"),
            },
            {
                "relative_path": "payload.bin",
                "size_bytes": payload.stat().st_size,
                "sha256": sha256_file(payload),
            },
        ],
    }
    atomic_write_json(root / "BUNDLE_MANIFEST.json", manifest)
    assert validate_bundle(root)["status"] == "complete"
    assert [item["case_id"] for item in node_case_specs(plan, "cnc5")] == [
        "clinical",
        "coordinate",
    ]
    assert [item["case_id"] for item in node_case_specs(plan, "cnc7")] == [
        "clinical"
    ]
    validate_node_id(plan, "cnc5", "cnc5-pod")
    with pytest.raises(ValueError, match="cannot be used"):
        validate_node_id(plan, "cnc5", "cnc7-pod")
    payload.write_bytes(b"drifted")
    with pytest.raises(ValueError, match="size mismatch"):
        validate_bundle(root)


def test_safe_extract_rejects_parent_escape(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("x", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(payload, arcname="../escape")
    with pytest.raises(ValueError, match="unsafe archive member"):
        safe_extract_tar(archive, tmp_path / "extract")


def test_raw_projection_audit_passes_centered_fixture(tmp_path: Path) -> None:
    module = _load_script("finalize_task12e_linux_local.py")
    values = np.zeros((60, 128, 128), dtype="<f4")
    values[:, 32:96, 32:96] = 1.0
    path = tmp_path / "case.a00"
    values.tofile(path)
    report = module._raw_projection_audit(path)
    assert report["status"] == "pass"
    assert report["outer_8px_count_fraction"] == 0.0


def test_remote_scripts_are_headless_and_resume_aware() -> None:
    worker = (SCRIPTS / "run_task12e_linux_worker.py").read_text(encoding="utf-8")
    master = (SCRIPTS / "finalize_task12e_linux_master.py").read_text(
        encoding="utf-8"
    )
    launcher = (SCRIPTS / "launch_task12e_linux_screen.sh").read_text(
        encoding="utf-8"
    )
    assert "PyQt" not in worker + master
    assert "--resume" in worker
    assert "/NN:" in worker and "/RR:" in worker
    assert "ThreadPoolExecutor" in worker and "--max-parallel" in worker
    assert "normalized_res_sha256" in master
    assert "screen -dmS" in launcher
    assert "MAX_PARALLEL" in launcher and "tee -a" in launcher


def test_worker_resource_gate_understands_cgroup_v1_quota() -> None:
    worker = _load_script("run_task12e_linux_worker.py")
    plan = {
        "minimum_resources_per_node": {
            "cpu_quota_equivalent": 4.0,
            "memory_bytes": 8 * 1024**3,
        }
    }
    worker._validate_minimum_resources(
        plan,
        {
            "resources": {
                "cpu_count": 56,
                "cpu_quota_v1": "5500000",
                "cpu_period_v1": "100000",
                "memory_limit_v1": "262144000000",
            }
        },
    )
    with pytest.raises(ValueError, match="CPU quota"):
        worker._validate_minimum_resources(
            plan,
            {
                "resources": {
                    "cpu_count": 56,
                    "cpu_quota_v1": "100000",
                    "cpu_period_v1": "100000",
                    "memory_limit_v1": "262144000000",
                }
            },
        )


def test_worker_resource_gate_understands_cgroup_v2_quota() -> None:
    worker = _load_script("run_task12e_linux_worker.py")
    plan = {
        "minimum_resources_per_node": {
            "cpu_quota_equivalent": 4.0,
            "memory_bytes": 8 * 1024**3,
        }
    }
    worker._validate_minimum_resources(
        plan,
        {
            "resources": {
                "cpu_count": 56,
                "cpu_max_v2": "5500000 100000",
                "memory_max_v2": "262144000000",
            }
        },
    )
    with pytest.raises(ValueError, match="CPU quota"):
        worker._validate_minimum_resources(
            plan,
            {
                "resources": {
                    "cpu_count": 56,
                    "cpu_max_v2": "100000 100000",
                    "memory_max_v2": "262144000000",
                }
            },
        )


def test_worker_parallelism_is_bounded_by_frozen_plan() -> None:
    worker = _load_script("run_task12e_linux_worker.py")
    plan = {"execution": {"maximum_parallel_per_node": 6}}
    assert worker._bounded_parallelism(plan, 6, 6) == 6
    assert worker._bounded_parallelism(plan, 6, 3) == 3
    with pytest.raises(ValueError, match="within 1..6"):
        worker._bounded_parallelism(plan, 7, 6)


def test_worker_actually_executes_three_cases_concurrently() -> None:
    worker = _load_script("run_task12e_linux_worker.py")
    barrier = threading.Barrier(3, timeout=3)
    cases = tuple({"case_id": f"case_{index}"} for index in range(3))

    def execute(case):
        barrier.wait()
        return case

    completed = worker._execute_cases_concurrently(cases, 3, execute)
    assert {item["case_id"] for item in completed} == {
        "case_0",
        "case_1",
        "case_2",
    }


def test_linux_master_accepts_three_identical_node_shards(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    shared = tmp_path / "shared"
    bundle.mkdir()
    clinical = ["case_0", "case_1", "case_2"]
    coordinate = ["coord_0", "coord_1", "coord_2"]
    nodes = ["cnc5", "cnc7", "cnc8"]
    cases = [
        {"case_id": case_id, "nodes": nodes, "fixture_group": "clinical"}
        for case_id in clinical
    ] + [
        {"case_id": case_id, "nodes": ["cnc5"], "fixture_group": "coordinate"}
        for case_id in coordinate
    ]
    plan = {
        "schema_version": PLAN_SCHEMA,
        "expected_nodes": nodes,
        "canonical_projection_node": "cnc5",
        "expected_linux_simind_sha256": "simhash",
        "hostname_prefix_by_node": {node: f"{node}-" for node in nodes},
        "clinical_case_ids": clinical,
        "coordinate_case_ids": coordinate,
        "execution": {
            "maximum_parallel_per_node": 6,
            "requested_parallel_by_node": {
                "cnc5": 6,
                "cnc7": 3,
                "cnc8": 3,
            },
        },
        "cases": cases,
    }
    atomic_write_json(bundle / "TASK12E_PLAN.json", plan)
    atomic_write_json(
        bundle / "BUNDLE_MANIFEST.json",
        {
            "schema_version": BUNDLE_SCHEMA,
            "status": "complete",
            "plan_relative_path": "TASK12E_PLAN.json",
            "plan_sha256": sha256_file(bundle / "TASK12E_PLAN.json"),
            "files": [
                {
                    "relative_path": "TASK12E_PLAN.json",
                    "size_bytes": (bundle / "TASK12E_PLAN.json").stat().st_size,
                    "sha256": sha256_file(bundle / "TASK12E_PLAN.json"),
                }
            ],
        },
    )
    shared.mkdir()
    atomic_write_json(
        shared / "LINUX_ENVIRONMENT.json",
        {"schema_version": ENVIRONMENT_SCHEMA, "status": "pass"},
    )
    bundle_sha = sha256_file(bundle / "BUNDLE_MANIFEST.json")
    environment_sha = sha256_file(shared / "LINUX_ENVIRONMENT.json")
    for node in nodes:
        assigned = clinical + (coordinate if node == "cnc5" else [])
        node_root = shared / "nodes" / node
        for case_id in assigned:
            case_root = node_root / case_id
            case_root.mkdir(parents=True)
            artifacts = {}
            for extension, payload in {
                "a00": f"numeric-{case_id}".encode(),
                "mhd": f"header-{case_id}".encode(),
                "spe": f"spectrum-{case_id}".encode(),
                "res": (
                    f"physics-{case_id}\nSimulation started.: {node}\n"
                    f"Elapsed time.......: {node}\n"
                ).encode(),
            }.items():
                path = case_root / f"{case_id}.{extension}"
                path.write_bytes(payload)
                artifacts[extension] = {
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                if extension == "res":
                    artifacts[extension]["normalized_sha256"] = normalized_res_sha256(
                        path
                    )
            atomic_write_json(
                case_root / "run_provenance.json",
                {
                    "status": "complete",
                    "case_id": case_id,
                    "bundle_manifest_sha256": bundle_sha,
                    "output_artifacts": artifacts,
                },
            )
        atomic_write_json(
            node_root / "NODE_COMPLETE.json",
            {
                "schema_version": NODE_COMPLETE_SCHEMA,
                "status": "complete",
                "bundle_manifest_sha256": bundle_sha,
                "runtime_fingerprint": {
                    "simind_sha256": "simhash",
                    "environment_capture_sha256": environment_sha,
                    "dependency_hashes": {"libc.so.6": "same"},
                },
                "max_parallel": len(assigned),
                "cases": [{"case_id": case_id} for case_id in assigned],
            },
        )
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "finalize_task12e_linux_master.py"),
            "--bundle-root",
            str(bundle),
            "--shared-root",
            str(shared),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    master = json.loads(
        (shared / "master" / "TASK12E_LINUX_MASTER.json").read_text(
            encoding="utf-8"
        )
    )
    assert master["status"] == "pass"
    assert master["cross_node_byte_gate"]["status"] == "pass"
    assert (shared / "master" / "task12e_linux_results.tar.gz").is_file()


def test_environment_yaml_pins_critical_versions() -> None:
    text = (REPO_ROOT / "configs" / "task12e_linux_environment.yml").read_text(
        encoding="utf-8"
    )
    for value in ("python=3.11.14", "numpy=2.4.3", "scipy=1.17.1", "scikit-image=0.26.0"):
        assert value in text
