from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    ARRAY_CONTRACT_V2,
    CasePayloadV2,
    CaseWriteError,
    build_split_plan,
    load_case_record_v2,
    write_case_v2,
    write_split_plan,
)
from core.provenance import sha256_file  # noqa: E402
from core.schemas_v2 import FROZEN_PROJECTION_COORDINATES_V1  # noqa: E402
from core.seeds import SeedBundle  # noqa: E402


def _arrays(shape: tuple[int, int, int] = (8, 9, 10)) -> dict[str, np.ndarray]:
    body = np.ones(shape, dtype=np.uint8)
    liver = np.zeros(shape, dtype=np.uint8)
    liver[1:-1, 1:-1, 1:-1] = 1
    regions = np.zeros(shape, dtype=np.uint8)
    regions[liver > 0] = 4
    regions[1:-1, 1:-1, 1:5] = 2
    tumors = np.zeros(shape, dtype=np.uint16)
    tumors[3:5, 3:5, 3:5] = 1
    tumor_union = (tumors > 0).astype(np.uint8)
    perfusion = liver.copy()
    relative = liver.astype(np.float32)
    relative[tumors > 0] = 2.0
    probability = relative / relative.sum(dtype=np.float64)
    base_histories = 80_000
    return {
        "activity_relative": relative.astype(np.float32),
        "activity_probability": probability.astype(np.float32),
        "simind_source_weights": (probability * base_histories).astype(np.float32),
        "mu_true_140kev": (body * 0.15 + liver * 0.01).astype(np.float32),
        "mu_input_140kev": (body * 0.149 + liver * 0.011).astype(np.float32),
        "body_mask": body,
        "liver_mask": liver,
        "liver_region_proxy": regions,
        "tumor_instance_mask": tumors,
        "tumor_union_mask": tumor_union,
        "perfusion_mask": perfusion,
    }


def _metadata(
    shape: tuple[int, int, int] = (8, 9, 10),
    *,
    case_id: str = "case_00001",
    global_seed: int = 20260714,
) -> dict[str, object]:
    digest = "a" * 64
    seeds = SeedBundle.from_case(global_seed, case_id)
    affine = np.diag((4.42, 4.42, 4.42, 1.0))
    affine[:3, 3] = (-15.47, -17.68, -19.89)
    return {
        "seeds": {
            "global_seed": global_seed,
            **seeds.child_seeds,
        },
        "config_hashes": {
            "evidence_registry_sha256": digest,
            "population_config_sha256": digest,
            "scanner_config_sha256": digest,
            "simind_ini_sha256": digest,
        },
        "patient": {
            "sex": "male",
            "age_years": 66.0,
            "height_cm": 174.0,
            "weight_kg": 80.0,
            "bmi": 26.4,
            "liver_morphology": "cirrhotic",
            "evidence_types": {"morphology": "engineering_prior"},
        },
        "target_metrics": {
            "liver": {
                "volume_ml": 1450.0,
                "extent_mm_zyx": [170.0, 130.0, 210.0],
                "centroid_world_mm": [0.0, 0.0, 0.0],
                "left_fraction": 0.31,
                "s1_3_to_s4_8_ratio": 0.25,
                "caudate_fraction": 0.02,
                "surface_roughness": 0.1,
            },
            "tumors": {
                "count_bin": "1",
                "dmax_bin": "10-<80_mm",
                "lobe_extent": "unilobar",
            },
        },
        "actual_metrics": {
            "liver": {
                "volume_ml": 1400.0,
                "extent_mm_zyx": [168.0, 128.0, 208.0],
                "centroid_world_mm": [0.0, 0.0, 0.0],
                "left_fraction": 0.30,
                "s1_3_to_s4_8_ratio": 0.24,
                "caudate_fraction": 0.02,
                "surface_area_mm2": 42000.0,
                "sphericity": 0.72,
                "surface_roughness": 0.11,
            },
            "path_lengths": {
                "angles_deg": [float((90 + index * 6) % 360) for index in range(60)],
                "body": [
                    {"mean_mm": 250.0, "p05_mm": 120.0, "p50_mm": 250.0, "p95_mm": 350.0}
                    for _ in range(60)
                ],
                "liver": [
                    {"mean_mm": 120.0, "p05_mm": 40.0, "p50_mm": 120.0, "p95_mm": 190.0}
                    for _ in range(60)
                ],
                "support_definition": "positive_rays_per_mask",
            },
            "tumors": {
                "count_bin": "1",
                "realized_count": 1,
                "lobe_extent": "unilobar",
                "tumor_union_fraction_liver": 0.01,
                "tumor_union_fraction_perfused": 0.014,
                "lesions": [
                    {
                        "instance_id": 1,
                        "center_world_mm": [0.0, 0.0, 0.0],
                        "normalized_liver_coordinate_zyx": [0.5, 0.5, 0.5],
                        "liver_region_proxy": 4,
                        "capsule_clearance_mm": 10.0,
                        "recist_3d_mm": 20.0,
                        "principal_axes_mm": [20.0, 18.0, 17.0],
                        "equivalent_diameter_mm": 18.0,
                        "volume_ml": 3.0,
                        "sphericity": 0.9,
                        "morphology": "smooth_nodular",
                        "necrotic_fraction": 0.0,
                        "tnr_mean": 2.1,
                        "tnr_max": 2.8,
                    }
                ],
            },
        },
        "activity": {
            "injection_territory": "whole_liver",
            "activity_pattern": "physiologic_heterogeneous",
            "perfused_volume_ml": 1000.0,
            "injection_tumor_coverage_fraction": 1.0,
            "tumor_volume_fraction_perfused": 0.014,
            "mismatch_challenge": False,
        },
        "spatial": {
            "affine_4x4": affine.tolist(),
            "world_origin_mm": [-15.47, -17.68, -19.89],
            "orientation_code": "SAR",
            "axis_order": "ZYX",
            "reference_phase": "end_expiration",
            "dvf_convention": "ref_to_phase",
            "dvf_units": "mm",
        },
        "acquisition": {
            "matrix": list(shape),
            "voxel_size_mm": 4.42,
            "views": 60,
            "starting_angle_deg": 180.0,
            "rotation_direction": "clockwise",
            "orbit_cm": 30.0,
            "energy_window_kev": [126.0, 154.0],
            "projection_coordinates": FROZEN_PROJECTION_COORDINATES_V1.to_dict(),
        },
        "physics": {
            "base_histories_per_projection": 80_000,
            "activity_mbq": 60.0,
            "time_per_projection_s": 28.4,
            "smc_index25": 1704.0,
            "nn_multiplier": 1,
            "rr_seed": seeds.simind,
            "hepatic_only": True,
            "lung_shunt_fraction": 0.0,
            "extrahepatic_uptake": False,
        },
        "simulation": {
            "status": "complete",
            "exit_code": 0,
            "command": ["simind", "ge870_czt", f"/RR:{seeds.simind}"],
            "simind_version": "test-fixture",
            "binary_sha256": "b" * 64,
            "smc_snapshot_sha256": "c" * 64,
            "simind_ini_snapshot_sha256": digest,
            "input_sha256": {"source": "d" * 64, "density": "e" * 64},
            "output_sha256": {
                "a00": "f" * 64,
                "mhd": "1" * 64,
                "res": "2" * 64,
                "spe": "3" * 64,
            },
            "projection_stats": {
                "view_count": 60,
                "projection_weight_sum": 1000.0,
                "projection_per_view_weight_sum": [1000.0 / 60.0] * 60,
                "finite": True,
            },
            "completion_status": "complete",
        },
        "quality_control": {"status": "pass", "failed_gates": []},
    }


def make_payload(
    case_id: str = "case_00001",
    *,
    family_id: str = "family_00001",
    split: str = "train",
    role: str = "main",
) -> CasePayloadV2:
    metadata = _metadata(case_id=case_id)
    if role == "negative":
        metadata["target_metrics"]["tumors"] = {
            "count_bin": "0",
            "dmax_bin": "none",
            "lobe_extent": "none",
        }
        metadata["actual_metrics"]["tumors"] = {
            "count_bin": "0",
            "realized_count": 0,
            "lobe_extent": "none",
            "tumor_union_fraction_liver": 0.0,
            "tumor_union_fraction_perfused": 0.0,
            "lesions": [],
        }
        metadata["activity"]["tumor_volume_fraction_perfused"] = 0.0
    return CasePayloadV2(
        case_id=case_id,
        case_family_id=family_id,
        profile_id="population_tare_hcc_nopvi_v2",
        dataset_id="PAR-S-TARE-HCC-NoPVI-SYN-v2-test",
        dataset_version="2.0.0-test",
        dataset_role=role,
        split=split,
        population_weight=1.0 if role == "main" else 0.0,
        sampling_probability=0.01,
        arrays=_arrays(),
        metadata=metadata,
    )


def write_payload(payload: CasePayloadV2, root: Path, *, resume: bool = False):
    split_ratios = {name: float(name == payload.split) for name in ("train", "val", "test")}
    plan = build_split_plan(
        [payload.case_family_id],
        dataset_id=payload.dataset_id,
        profile_id=payload.profile_id,
        global_seed=int(payload.metadata["seeds"]["global_seed"]),
        ratios=split_ratios,
    )
    write_split_plan(plan, root)
    return write_case_v2(payload, root, resume=resume)


def test_writer_emits_strict_npz_json_record_and_hashes(tmp_path: Path) -> None:
    record = write_payload(make_payload(), tmp_path)
    npz_path = tmp_path / record.artifacts["phantom_npz"].relative_path
    metadata_path = tmp_path / record.artifacts["metadata_json"].relative_path
    record_path = tmp_path / "cases" / record.case_id / "case_record.json"

    assert npz_path.is_file() and metadata_path.is_file() and record_path.is_file()
    with np.load(npz_path, allow_pickle=False) as arrays:
        assert set(arrays.files) == set(ARRAY_CONTRACT_V2)
        for key, spec in ARRAY_CONTRACT_V2.items():
            assert arrays[key].dtype == np.dtype(spec.dtype)
        assert float(arrays["activity_probability"].sum(dtype=np.float64)) == pytest.approx(1.0)
        assert float(arrays["simind_source_weights"].sum(dtype=np.float64)) == pytest.approx(80_000, rel=1e-6)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "pars_syn_v2"
    assert metadata["array_contract"]["mu_true_140kev"]["unit"] == "cm^-1"
    assert metadata["array_contract"]["activity_probability"]["unit"] == "dimensionless_sum_1"
    assert metadata["case_id"] == record.case_id
    assert metadata["case_family_id"] == record.case_family_id
    assert metadata["split"] == "train"
    assert (
        record.projection_coordinate_contract_id
        == "pars_simind_v8_xcat_zyx_sar_v1"
    )
    assert (
        record.loader_transform_id
        == "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
    )
    assert metadata["spatial"]["orientation_code"] == "SAR"
    assert (
        metadata["acquisition"]["projection_coordinates"]
        == FROZEN_PROJECTION_COORDINATES_V1.to_dict()
    )
    assert sha256_file(npz_path) == record.artifacts["phantom_npz"].sha256
    assert sha256_file(metadata_path) == record.artifacts["metadata_json"].sha256
    assert load_case_record_v2(record_path, dataset_root=tmp_path, verify_hashes=True) == record


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda arrays: arrays.pop("mu_input_140kev"), "required array keys"),
        (lambda arrays: arrays.__setitem__("body_mask", arrays["body_mask"].astype(bool)), "body_mask.*uint8"),
        (lambda arrays: arrays["tumor_instance_mask"].__setitem__((0, 0, 0), 3), "contained in liver"),
        (lambda arrays: arrays["activity_probability"].__setitem__((3, 3, 3), 0.2), "sum to 1"),
    ],
)
def test_writer_rejects_array_contract_violations(tmp_path: Path, mutation, message: str) -> None:
    payload = make_payload()
    arrays = dict(payload.arrays)
    mutation(arrays)
    with pytest.raises(CaseWriteError, match=message):
        write_payload(replace(payload, arrays=arrays), tmp_path)
    assert not (tmp_path / "cases" / payload.case_id).exists()


def test_writer_rejects_metadata_schema_and_dataset_role_mismatch(tmp_path: Path) -> None:
    payload = make_payload()
    bad_metadata = dict(payload.metadata)
    bad_metadata["invented_field"] = "not allowed"
    with pytest.raises(CaseWriteError, match="unknown metadata fields"):
        write_payload(replace(payload, metadata=bad_metadata), tmp_path / "unknown")

    with pytest.raises(CaseWriteError, match="negative.*population_weight=0"):
        write_payload(
            replace(payload, dataset_role="negative", population_weight=1.0),
            tmp_path / "negative",
        )


def test_writer_rejects_incomplete_scientific_metadata(tmp_path: Path) -> None:
    payload = make_payload()
    metadata = json.loads(json.dumps(payload.metadata))
    del metadata["actual_metrics"]["path_lengths"]
    with pytest.raises(CaseWriteError, match="actual_metrics.*path_lengths"):
        write_payload(replace(payload, metadata=metadata), tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda metadata: metadata["spatial"].__setitem__(
                "orientation_code", "RAS"
            ),
            "SAR",
        ),
        (
            lambda metadata: metadata["acquisition"][
                "projection_coordinates"
            ].__setitem__("loader_transform_id", "implicit_flip"),
            "frozen PAR-S coordinate contract",
        ),
        (
            lambda metadata: metadata["actual_metrics"]["path_lengths"].__setitem__(
                "angles_deg", [float(index * 6) for index in range(60)]
            ),
            "common-projector angles",
        ),
    ],
)
def test_writer_rejects_unfrozen_coordinate_metadata(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    payload = make_payload()
    metadata = json.loads(json.dumps(payload.metadata))
    mutation(metadata)
    with pytest.raises(CaseWriteError, match=message):
        write_payload(replace(payload, metadata=metadata), tmp_path)


def test_writer_rejects_child_seed_not_derived_from_case_identity(tmp_path: Path) -> None:
    payload = make_payload()
    metadata = json.loads(json.dumps(payload.metadata))
    metadata["seeds"]["activity"] += 1
    with pytest.raises(CaseWriteError, match="child seeds.*global_seed.*case_id"):
        write_payload(replace(payload, metadata=metadata), tmp_path)


def test_writer_is_atomic_and_cleans_failed_staging(tmp_path: Path, monkeypatch) -> None:
    import core.case_writer_v2 as writer

    def fail_npz(*_args, **_kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(writer, "_write_deterministic_npz", fail_npz)
    with pytest.raises(CaseWriteError, match="injected disk failure"):
        write_payload(make_payload(), tmp_path)
    assert not (tmp_path / "cases" / "case_00001").exists()
    staging = tmp_path / ".staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_resume_verifies_existing_content_and_duplicate_is_rejected(tmp_path: Path) -> None:
    payload = make_payload()
    first = write_payload(payload, tmp_path)
    with pytest.raises(CaseWriteError, match="duplicate case_id"):
        write_payload(payload, tmp_path)
    assert write_payload(payload, tmp_path, resume=True) == first

    changed = dict(payload.arrays)
    changed["mu_input_140kev"] = changed["mu_input_140kev"].copy()
    changed["mu_input_140kev"][0, 0, 0] = 0.01
    with pytest.raises(CaseWriteError, match="resume content mismatch"):
        write_payload(replace(payload, arrays=changed), tmp_path, resume=True)


def test_frozen_dataset_refuses_late_case_writes(tmp_path: Path) -> None:
    (tmp_path / "DATASET_COMPLETE.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(CaseWriteError, match="already frozen"):
        write_case_v2(make_payload(), tmp_path)
