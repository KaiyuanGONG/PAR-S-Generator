from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import GridSpecV2
from core.production_v2 import (
    prepare_negative_case,
    summarize_prepared_negative_case,
)
from core.schemas_v2 import load_evidence_registry, load_profile


def test_negative_control_has_physiological_source_and_exactly_zero_tumor(
    tmp_path: Path,
) -> None:
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    profile = load_profile(
        REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json",
        registry,
    )
    prepared = prepare_negative_case(
        "negative_00000",
        profile,
        GridSpecV2(shape=(128, 128, 128), voxel_size_mm=4.42),
        global_seed=20260718,
        base_histories=80_000,
        work_dir=tmp_path / "negative_00000",
    )
    summary = summarize_prepared_negative_case(prepared)

    assert summary["status"] == "pass"
    assert summary["dataset_role"] == "negative"
    assert summary["population_weight"] == 0.0
    assert summary["realized_tumor_count"] == 0
    assert not np.asarray(prepared.arrays["tumor_union_mask"]).any()
    assert not np.asarray(prepared.arrays["tumor_instance_mask"]).any()
    assert float(np.asarray(prepared.arrays["activity_probability"]).sum()) == pytest.approx(1.0)
    assert summary["source_weight_sum"] == pytest.approx(80_000, abs=0.1)
