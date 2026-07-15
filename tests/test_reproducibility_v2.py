from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import scripts.run_pilot15_v2 as runner  # noqa: E402
from core.provenance import sha256_file  # noqa: E402
from core.reproducibility_v2 import (  # noqa: E402
    array_manifest,
    canonical_json_sha256,
    capture_generator_source_binding,
    capture_python_runtime,
    load_and_validate_preflight_input_bundle,
    prove_preflight_byte_identity,
    write_preflight_input_bundle,
)


def _make_bundle(tmp_path: Path):
    root = tmp_path / "preflight"
    case_id = "case_00000"
    case_root = root / "cases" / case_id
    case_root.mkdir(parents=True)
    source = case_root / f"{case_id}_act_av.bin"
    density = case_root / f"{case_id}_atn_av.bin"
    source.write_bytes(b"frozen-source")
    density.write_bytes(b"frozen-density")
    summaries = [
        {
            "case_id": case_id,
            "source_sha256": sha256_file(source),
            "density_sha256": sha256_file(density),
            "array_manifest": array_manifest(
                {"liver_mask": np.asarray([[1, 0]], dtype=np.uint8)}
            ),
        }
    ]
    reference = write_preflight_input_bundle(root, summaries)
    report = root / "PREFLIGHT.json"
    report.write_text("{}", encoding="utf-8")
    bound = load_and_validate_preflight_input_bundle(
        report,
        reference,
        expected_case_ids=[case_id],
        case_summaries=summaries,
    )
    return root, reference, summaries, bound[case_id]


def test_python_conda_runtime_fingerprint_is_self_verifying() -> None:
    runtime = capture_python_runtime()
    digest = runtime.pop("binding_sha256")

    assert digest == canonical_json_sha256(runtime)
    assert runtime["python"]["executable_sha256"]
    assert runtime["python"]["prefix"]
    assert runtime["conda"]["prefix_matches_python_prefix"] is True
    assert runtime["python_distributions_sha256"] == canonical_json_sha256(
        runtime["python_distributions"]
    )
    assert {item["name"] for item in runtime["critical_modules"]} == {
        "numpy",
        "scipy",
        "skimage",
    }


def test_generator_source_binding_hashes_the_generation_pipeline() -> None:
    binding = capture_generator_source_binding(REPO_ROOT)
    digest = binding.pop("binding_sha256")

    assert digest == canonical_json_sha256(binding)
    assert len(binding["git_commit"]) == 40
    assert len(binding["git_tree"]) == 40
    paths = {item["path"] for item in binding["source_files"]}
    assert "scripts/run_pilot15_v2.py" in paths
    assert "scripts/preflight_pilot15_v2.py" in paths
    assert "src/core/pilot_v2.py" in paths


def test_preflight_bundle_verifies_manifest_order_and_every_input_byte(
    tmp_path: Path,
) -> None:
    root, reference, summaries, frozen = _make_bundle(tmp_path)

    assert frozen.source_path.read_bytes() == b"frozen-source"
    assert frozen.density_path.read_bytes() == b"frozen-density"
    frozen.source_path.write_bytes(b"tampered-source")
    with pytest.raises(RuntimeError, match="source (size|hash) mismatch"):
        load_and_validate_preflight_input_bundle(
            root / "PREFLIGHT.json",
            reference,
            expected_case_ids=["case_00000"],
            case_summaries=summaries,
        )


def test_preflight_byte_identity_passes_and_runner_consumes_frozen_paths(
    tmp_path: Path,
) -> None:
    root, _, _, frozen = _make_bundle(tmp_path)
    generated = tmp_path / "generated"
    generated.mkdir()
    generated_source = generated / "case_00000_act_av.bin"
    generated_density = generated / "case_00000_atn_av.bin"
    generated_source.write_bytes(frozen.source_path.read_bytes())
    generated_density.write_bytes(frozen.density_path.read_bytes())
    evidence = generated / "PREFLIGHT_BYTE_IDENTITY.json"

    @dataclass(frozen=True)
    class Prepared:
        source_bin: Path
        density_bin: Path
        arrays: dict[str, np.ndarray]

    prepared, evidence_path = runner._bind_preflight_inputs(
        Prepared(
            generated_source,
            generated_density,
            {"liver_mask": np.asarray([[1, 0]], dtype=np.uint8)},
        ),
        frozen,
        evidence_path=evidence,
    )

    assert prepared.source_bin == frozen.source_path
    assert prepared.density_bin == frozen.density_path
    assert evidence_path == evidence
    assert json.loads(evidence.read_text(encoding="utf-8"))["status"] == "pass"


def test_preflight_byte_identity_fails_before_binding_on_regeneration_drift(
    tmp_path: Path,
) -> None:
    _, _, _, frozen = _make_bundle(tmp_path)
    generated_source = tmp_path / "generated_source.bin"
    generated_density = tmp_path / "generated_density.bin"
    generated_source.write_bytes(b"different-source")
    generated_density.write_bytes(frozen.density_path.read_bytes())
    evidence = tmp_path / "failed_identity.json"

    with pytest.raises(RuntimeError, match="byte identity failed"):
        prove_preflight_byte_identity(
            generated_source=generated_source,
            generated_density=generated_density,
            frozen=frozen,
            generated_arrays={
                "liver_mask": np.asarray([[1, 0]], dtype=np.uint8)
            },
            evidence_path=evidence,
        )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["status"] == "fail"
    assert "source_sha256" in document["drifted"]


def test_preflight_byte_identity_rejects_gt_array_drift(tmp_path: Path) -> None:
    _, _, _, frozen = _make_bundle(tmp_path)
    evidence = tmp_path / "failed_array_identity.json"

    with pytest.raises(RuntimeError, match="byte identity failed"):
        prove_preflight_byte_identity(
            generated_source=frozen.source_path,
            generated_density=frozen.density_path,
            frozen=frozen,
            generated_arrays={
                "liver_mask": np.asarray([[0, 1]], dtype=np.uint8)
            },
            evidence_path=evidence,
        )
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["all_arrays_byte_identical"] is False
    assert document["drifted"] == ["array:liver_mask"]


def test_task12c_fixture_cannot_launch_simind() -> None:
    source = (
        REPO_ROOT / "scripts" / "validate_task12c_reproducibility_v2.py"
    ).read_text(encoding="utf-8")
    assert "run_simind_case" not in source
    assert "SimindRunSpec" not in source
    array_manifest,
