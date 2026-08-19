from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from gate_b import bridge  # noqa: E402
from gate_b.selection import freeze_selection  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_selection_replays_byte_for_byte(tmp_path: Path) -> None:
    frozen = REPO_ROOT / "gate_b" / "freeze"
    selection = json.loads((frozen / "selection.json").read_text(encoding="utf-8"))
    assert selection["selected_case_ids_in_order"] == [
        "case_0035",
        "case_0028",
        "case_0040",
        "case_0008",
        "case_0038",
        "case_0043",
        "case_0099",
        "case_0009",
        "case_0098",
        "case_0036",
    ]
    assert selection["sentinel"]["case_id"] == "case_0035"
    assert selection["selection_algorithm"]["code_sha256"] == _sha(
        REPO_ROOT / "src" / "gate_b" / "selection.py"
    )
    assert selection["selection_algorithm"]["config_sha256"] == _sha(
        REPO_ROOT / "configs" / "gate_b_hybrid_921e2e7.json"
    )

    replay = tmp_path / "replay"
    freeze_selection(
        parent_root=Path(selection["parent"]["root_read_only"]),
        config_path=REPO_ROOT / "configs" / "gate_b_hybrid_921e2e7.json",
        output_dir=replay,
    )
    for name in (
        "selection.json",
        "selection.csv",
        "selection.md",
        "candidate_features.json",
        "candidate_features.csv",
    ):
        assert (replay / name).read_bytes() == (frozen / name).read_bytes()


def test_frozen_selection_coverage_and_parent_hashes() -> None:
    frozen = REPO_ROOT / "gate_b" / "freeze"
    selection = json.loads((frozen / "selection.json").read_text(encoding="utf-8"))
    candidates = json.loads(
        (frozen / "candidate_features.json").read_text(encoding="utf-8")
    )
    assert len(candidates["candidates"]) == 100
    assert selection["selected_count"] == 10
    coverage = selection["selection_algorithm"]["coverage"]
    for label, minimum in selection["selection_algorithm"]["quotas"].items():
        assert coverage[label] >= minimum
    for record in selection["selected"]:
        assert record["pilot_only"] is True
        parent = record["candidate"]["parent"]
        for role in ("npz", "metadata", "qc"):
            assert len(parent[role]["sha256"]) == 64
            assert parent[role]["bytes"] > 0


def test_exact_three_argument_packed_command_and_relative_smc_path() -> None:
    config = bridge.load_contract(REPO_ROOT)
    argv = bridge.build_packed_argv(
        "/workspace/par_s_runtime_staging/simind_v8_20260817T182142Z/official_v8/simind/simind",
        "case_0035",
        930035,
        config,
    )
    assert argv == (
        "/workspace/par_s_runtime_staging/simind_v8_20260817T182142Z/official_v8/simind/simind",
        "ge870_czt",
        "mc/FS:case_0035/FD:case_0035/NN:10/RR:930035/FW:n2_photopeak"
        "/IN:x21,100x/IN:x22,3x/25:1704/100:160/101:208/CA:2/84:1/IN:x50,Nx",
    )
    assert len(argv) == 3
    environment = bridge.child_environment(Path("C:/job/smc_dir"))
    assert environment["SMC_DIR"] == "smc_dir/"
    assert len(environment["SMC_DIR"].encode("ascii")) < 100


def test_run_job_uses_shell_false_and_one_three_argument_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "package"
    job_dir = package / "jobs" / "j0035"
    job_dir.mkdir(parents=True)
    exact = ("C:\\fake\\simind.exe", "ge870_czt", "packed")
    job = {
        "case_id": "case_0035",
        "job_dir": "jobs/j0035",
        "rr_seed": 930035,
        "exact_argv": list(exact),
    }
    runtime = bridge.VerifiedRuntime(
        root=Path("C:/fake"),
        binary=Path(exact[0]),
        smc_source=tmp_path,
        binary_sha256="e" * 64,
        records=(),
    )
    config = {"runtime": {"simind_elf_sha256": "e" * 64}}
    calls: list[tuple[tuple[str, ...], dict]] = []

    monkeypatch.setattr(bridge, "verify_package_root", lambda _path: {"status": "passed"})
    monkeypatch.setattr(bridge, "_plan_job", lambda _root, _case: ({}, job_dir, job))
    monkeypatch.setattr(bridge, "load_runtime_lock", lambda _root: {})
    monkeypatch.setattr(bridge, "verify_runtime", lambda _root, _lock: runtime)
    monkeypatch.setattr(bridge, "load_contract", lambda _root: config)
    monkeypatch.setattr(bridge, "build_packed_argv", lambda *_args: exact)
    monkeypatch.setattr(
        bridge,
        "materialize_private_smc",
        lambda _job, _runtime: (job_dir / "smc_dir", []),
    )
    monkeypatch.setattr(bridge, "verify_private_smc_post", lambda _path, _pre: [])
    monkeypatch.setattr(bridge, "_current_output_inventory", lambda _job: [])

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    result = bridge.run_job(
        package_root=package,
        runtime_root=Path("C:/fake"),
        case_id="case_0035",
    )
    assert result["scientific_invocation_count"] == 1
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == exact and len(argv) == 3
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == job_dir
    assert kwargs["env"]["SMC_DIR"] == "smc_dir/"


def test_private_smc_copy_allows_only_ranlux_mutation(tmp_path: Path) -> None:
    source = tmp_path / "official" / "smc_dir"
    source.mkdir(parents=True)
    names = (*bridge.IMMUTABLE_SMC_NAMES, "ranlux2.num")
    records = []
    for index, name in enumerate(names):
        raw = f"locked-{index}-{name}\n".encode("ascii")
        (source / name).write_bytes(raw)
        records.append({"name": name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    runtime = bridge.VerifiedRuntime(
        root=source.parent,
        binary=source.parent / "simind",
        smc_source=source,
        binary_sha256="a" * 64,
        records=tuple(records),
    )
    job = tmp_path / "job"
    job.mkdir()
    private, before = bridge.materialize_private_smc(job, runtime)
    assert sorted(path.name for path in private.iterdir()) == sorted(names)
    (private / "ranlux2.num").write_bytes(b"new-private-state\n")
    bridge.verify_private_smc_post(private, before)
    (private / "simind.ini").write_bytes(b"tampered\n")
    with pytest.raises(bridge.GateBError, match="immutable private SMC changed"):
        bridge.verify_private_smc_post(private, before)


def test_type7_reference_and_float32_roundtrip(tmp_path: Path) -> None:
    contract = bridge.validate_reference_smc(
        REPO_ROOT / "reference" / "simind" / "gate_b" / "ge870_czt_type7.smc"
    )
    assert contract["indices_14_15"] == [-7.0, -7.0]
    assert contract["flag_11"] is True and contract["flag_15"] is True
    mu = np.linspace(0.0, 0.25, 4096, dtype=np.float32).reshape(16, 16, 16)
    stored = np.asarray(mu * np.float32(0.442), dtype=np.dtype("<f4"), order="C")
    path = tmp_path / "atn.bin"
    bridge._write_raw_float32(path, stored)
    recovered = np.fromfile(path, dtype=np.dtype("<f4")).reshape(mu.shape) / np.float32(0.442)
    assert float(np.max(np.abs(recovered.astype(np.float64) - mu.astype(np.float64)))) <= 1e-6


def test_gate_b_export_delegates_to_master_write_bin(tmp_path: Path) -> None:
    array = np.arange(64, dtype=np.float32).reshape(4, 4, 4)
    record = bridge._export_with_master_write_bin(
        output_stem=tmp_path / "case_0035",
        suffix="_act_av",
        array=array,
    )
    path = tmp_path / "case_0035_act_av.bin"
    assert record["dtype"] == "<f4" and record["order"] == "C_ZYX"
    assert np.array_equal(np.fromfile(path, dtype=np.dtype("<f4")).reshape(array.shape), array)


def test_headerless_component_parser_has_fixed_shape_dtype_and_orientation(tmp_path: Path) -> None:
    raw = np.zeros((60, 128, 128), dtype=np.dtype("<f4"))
    raw[0, 3, 7] = np.float32(2.5)
    raw.tofile(tmp_path / bridge.COMPONENT_FILES["total"])
    canonical, record = bridge._load_component(tmp_path, "total")
    assert canonical.shape == (60, 128, 128)
    assert canonical.dtype == np.dtype("<f4")
    assert canonical[0, 124, 7] == np.float32(2.5)
    assert record["bytes"] == 60 * 128 * 128 * 4
    (tmp_path / bridge.COMPONENT_FILES["total"]).write_bytes(b"short")
    with pytest.raises(bridge.GateBError, match="byte size differs"):
        bridge._load_component(tmp_path, "total")


def test_report_parser_binds_native_detector_identity_and_packed_command(tmp_path: Path) -> None:
    config = bridge.load_contract(REPO_ROOT)
    argv = bridge.build_packed_argv(
        "/workspace/runtime/simind", "case_0035", 930035, config
    )
    report = f"""
InputFile.: ge870_czt
OutputFile: mc
ScoreRout.: scattwin
SourceType.........: XcatBinMap
PhantomType........: XcatBinMap
MatrixSize I.......: 128
MatrixSize J.......: 128
Number detectors  I: 160
Number Detectors J: 208
Nr of Projections.: 60
StartingAngle.....: 180
RotationAngle......: 6
Projection.[start]: 1
Projection...[end]: 60
PhotonsPerProj....: 1234
Scattwin results: Window file: n2_photopeak.win
Scatter/Primary....: 0.4
Scatter/Total......: 0.2857
Command: {argv[1]} {argv[2]}
"""
    path = tmp_path / "mc.res"
    path.write_text(report, encoding="ascii")
    parsed = bridge.parse_report(path, expected_argv=argv, expected_histories=1234.0, config=config)
    assert parsed["detector_i"] == 160 and parsed["detector_j"] == 208
    assert parsed["matrix_i"] == 128 and parsed["matrix_j"] == 128
    path.write_text(report.replace("Number detectors  I: 160", "Number detectors  I: 128"), encoding="ascii")
    with pytest.raises(bridge.GateBError, match="detector_i"):
        bridge.parse_report(path, expected_argv=argv, expected_histories=1234.0, config=config)


def test_deterministic_tar_and_traversal_rejection(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"alpha\n")
    nested = source / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(bytes(range(32)))
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    bridge._deterministic_tar_gz(source, first, arc_root="frozen")
    bridge._deterministic_tar_gz(source, second, arc_root="frozen")
    assert first.read_bytes() == second.read_bytes()

    malicious = tmp_path / "malicious.tar.gz"
    with tarfile.open(malicious, "w:gz") as archive:
        item = tarfile.TarInfo("frozen/../escape.txt")
        payload = b"escape"
        item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))
    with pytest.raises(bridge.GateBError, match="unsafe archive path"):
        bridge.safe_extract(malicious, tmp_path / "extract", expected_root="frozen")


def test_source_package_file_set_includes_all_remote_imports() -> None:
    paths = bridge._package_source_paths(REPO_ROOT)
    assert {"gate_b/__init__.py", "gate_b/bridge.py", "gate_b/selection.py"} <= set(paths)
    assert all(path.is_file() for path in paths.values())


def test_analysis_numeric_evidence_is_canonicalized_after_gate_decisions() -> None:
    value = {
        "plain": 0.123456789,
        "numpy": np.float64(-0.00000001),
        "nested": [np.float32(1.23456789)],
        "integer": 7,
    }
    assert bridge.canonicalize_analysis_numeric(value) == {
        "plain": 0.1234568,
        "numpy": 0.0,
        "nested": [1.2345679],
        "integer": 7,
    }
