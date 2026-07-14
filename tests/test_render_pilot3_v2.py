from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from core.case_writer_v2 import (  # noqa: E402
    DatasetContractV2,
    build_split_plan,
    freeze_dataset,
    write_case_v2,
    write_split_plan,
)
from core.provenance import sha256_bytes  # noqa: E402
from core.simind_postprocess import audit_simind_completion  # noqa: E402
from render_pilot3_v2 import PilotRenderError, render_pilot3  # noqa: E402
from test_case_writer_v2 import make_payload  # noqa: E402


REQUIRED_ARTIFACTS = (
    "phantom_npz",
    "metadata_json",
    "projection_a00",
    "projection_mhd",
    "projection_res",
    "projection_spe",
    "simind_run_provenance",
)


def _write_small_simind_evidence(
    staging: Path,
    metadata: dict[str, object],
    *,
    case_id: str,
) -> dict[str, str]:
    output_stem = staging / case_id
    shape = (60, 8, 8)
    views = np.arange(shape[0], dtype=np.float32)[:, None, None]
    detector_u = np.arange(shape[2], dtype=np.float32)[None, None, :]
    values = np.broadcast_to(1.0 + views + detector_u, shape).astype("<f4")
    values.tofile(output_stem.with_suffix(".a00"))
    output_stem.with_suffix(".mhd").write_text(
        "\n".join(
            (
                "ObjectType = Image",
                "BinaryData = True",
                "BinaryDataByteOrderMSB = False",
                "CompressedData = False",
                "NDims = 3",
                "DimSize = 8 8 60",
                "ElementType = MET_FLOAT",
                f"ElementDataFile = {case_id}.a00",
                "",
            )
        ),
        encoding="ascii",
    )
    output_stem.with_suffix(".res").write_text("fixture result\n", encoding="ascii")
    output_stem.with_suffix(".spe").write_bytes(b"fixture spectrum")
    audit = audit_simind_completion(output_stem, expected_shape=shape, exit_code=0)

    simulation = metadata["simulation"]
    physics = metadata["physics"]
    config_hashes = metadata["config_hashes"]
    assert isinstance(simulation, dict)
    assert isinstance(physics, dict)
    assert isinstance(config_hashes, dict)
    smc_snapshot = "SMCV2 render fixture\n"
    ini_snapshot = "[render fixture]\n"
    smc_digest = sha256_bytes(smc_snapshot.encode("utf-8"))
    ini_digest = sha256_bytes(ini_snapshot.encode("utf-8"))
    command = [
        "simind",
        "ge870_czt",
        case_id,
        f"/NN:{physics['nn_multiplier']}",
        f"/RR:{physics['rr_seed']}",
    ]
    simulation.update(
        {
            "command": command,
            "binary_sha256": "b" * 64,
            "smc_snapshot_sha256": smc_digest,
            "simind_ini_snapshot_sha256": ini_digest,
            "input_sha256": {"source": "d" * 64, "density": "e" * 64},
            "output_sha256": dict(audit.sha256),
            "projection_stats": {
                "view_count": 60,
                "projection_weight_sum": float(values.sum(dtype=np.float64)),
                "projection_per_view_weight_sum": [
                    float(value)
                    for value in values.sum(axis=(1, 2), dtype=np.float64)
                ],
                "finite": True,
            },
        }
    )
    config_hashes["simind_ini_sha256"] = ini_digest
    provenance = {
        "schema_version": "pars_simind_run_v2",
        "status": "complete",
        "case_id": case_id,
        "protocol_name": "SPECT_60MBq_28p4s_v2",
        "expected_shape": list(shape),
        "timeout_seconds": 7200.0,
        "command": command,
        "environment_overrides": {},
        "rr_seed": physics["rr_seed"],
        "nn_multiplier": physics["nn_multiplier"],
        "exit_code": 0,
        "started_utc": "2026-07-14T00:00:00Z",
        "finished_utc": "2026-07-14T00:01:00Z",
        "binary_sha256": simulation["binary_sha256"],
        "executable_argument_file_sha256": {},
        "smc": {
            "source_name": "ge870_czt.smc",
            "sha256": smc_digest,
            "snapshot": smc_snapshot,
        },
        "simind_ini": {
            "source_name": "simind.ini",
            "sha256": ini_digest,
            "snapshot": ini_snapshot,
        },
        "inputs": {"source_sha256": "d" * 64, "density_sha256": "e" * 64},
        "completion_audit": audit.to_dict(),
        "stdout_tail": "",
        "stderr_tail": "",
    }
    (staging / "run_provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    return {
        "projection_a00": f"{case_id}.a00",
        "projection_mhd": f"{case_id}.mhd",
        "projection_res": f"{case_id}.res",
        "projection_spe": f"{case_id}.spe",
        "simind_run_provenance": "run_provenance.json",
    }


@pytest.fixture
def frozen_pilot(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    families = tuple(f"family_{index:05d}" for index in range(3))
    case_ids = tuple(f"case_{index:05d}" for index in range(3))
    plan = build_split_plan(
        families,
        dataset_id="PAR-S-TARE-HCC-NoPVI-SYN-v2-test",
        profile_id="population_tare_hcc_nopvi_v2",
        global_seed=20260714,
        ratios={"train": 1 / 3, "val": 1 / 3, "test": 1 / 3},
    )
    write_split_plan(plan, root)

    records = []
    morphologies = ("normal", "cirrhotic", "cirrhotic")
    territories = ("whole_liver", "right_lobar", "left_lobar")
    for index, (case_id, family_id) in enumerate(zip(case_ids, families)):
        staging = tmp_path / "simind" / case_id
        staging.mkdir(parents=True)
        payload = make_payload(
            case_id,
            family_id=family_id,
            split=plan.family_to_split[family_id],
        )
        metadata = json.loads(json.dumps(payload.metadata))
        metadata["patient"]["liver_morphology"] = morphologies[index]
        metadata["activity"]["injection_territory"] = territories[index]
        evidence = _write_small_simind_evidence(
            staging,
            metadata,
            case_id=case_id,
        )
        records.append(
            write_case_v2(
                replace(
                    payload,
                    metadata=metadata,
                    extra_artifacts={
                        name: staging / relative_path
                        for name, relative_path in evidence.items()
                    },
                ),
                root,
            )
        )

    contract = DatasetContractV2(
        output_root=root,
        dataset_id="PAR-S-TARE-HCC-NoPVI-SYN-v2-test",
        dataset_version="2.0.0-test",
        dataset_role="main",
        expected_case_ids=case_ids,
        allowed_profile_ids=("population_tare_hcc_nopvi_v2",),
        split_plan_sha256=plan.sha256,
        required_artifact_names=REQUIRED_ARTIFACTS,
    )
    freeze_dataset(tuple(records), contract)
    return root


def test_render_pilot3_writes_png_and_machine_summary(
    frozen_pilot: Path,
    tmp_path: Path,
) -> None:
    output_png = tmp_path / "pilot3.png"
    output_json = tmp_path / "pilot3.json"

    result = render_pilot3(frozen_pilot, output_png, output_json)

    assert output_png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert output_png.stat().st_size > 10_000
    stored = json.loads(output_json.read_text(encoding="utf-8"))
    assert stored == result
    assert stored["status"] == "pass"
    assert stored["case_count"] == 3
    assert {item["split"] for item in stored["cases"]} == {"train", "val", "test"}
    assert {item["liver_morphology"] for item in stored["cases"]} == {
        "normal",
        "cirrhotic",
    }
    assert all(item["projection_view_count"] == 60 for item in stored["cases"])


def test_render_pilot3_fails_before_writing_when_manifest_drifted(
    frozen_pilot: Path,
    tmp_path: Path,
) -> None:
    manifest = frozen_pilot / "case_manifest.jsonl"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    output_png = tmp_path / "must-not-exist.png"
    output_json = tmp_path / "must-not-exist.json"

    with pytest.raises(PilotRenderError, match="manifest"):
        render_pilot3(frozen_pilot, output_png, output_json)

    assert not output_png.exists()
    assert not output_json.exists()
