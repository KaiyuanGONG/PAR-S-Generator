from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_task12f_linux50_bundle import _apply_mismatch_challenge_design
from build_task13_formal550_bundle import _role_config
from core.seeds import SeedBundle
from generate_dataset_v2 import build_generation_plan
from task13_formal550_runtime import patch_runtime_contract, restore_runtime_contract


def _config() -> dict:
    return json.loads(
        (REPO_ROOT / "configs" / "task13_formal550_v1.json").read_text(
            encoding="utf-8"
        )
    )


def test_formal_config_freezes_500_main_plus_50_test_only_negative() -> None:
    config = _config()
    assert config["campaign"]["case_count"] == 550
    assert config["datasets"]["main"]["case_count"] == 500
    assert config["datasets"]["negative"]["case_count"] == 50
    assert config["datasets"]["main"]["split_ratios"] == {
        "train": 0.8,
        "val": 0.1,
        "test": 0.1,
    }
    assert config["datasets"]["negative"]["split_ratios"] == {
        "train": 0.0,
        "val": 0.0,
        "test": 1.0,
    }
    assert config["execution"]["max_tumor_target_attempts"] == 64
    assert config["frozen_evidence"]["release_flag"] == (
        "go_for_formal_500_plus_50_generation"
    )


def test_formal_split_plans_and_rr_seeds_are_exact_and_disjoint() -> None:
    config = _config()
    all_entries = []
    rr_values = []
    for role in ("main", "negative"):
        role_cfg = _role_config(config, role)
        dataset = role_cfg["dataset"]
        profile_id = (
            "population_tare_hcc_nopvi_v2"
            if role == "main"
            else "negative_control_v2"
        )
        _, plan = build_generation_plan(
            dataset_id=dataset["dataset_id"],
            dataset_version=dataset["dataset_version"],
            dataset_role=dataset["dataset_role"],
            profile_id=profile_id,
            case_count=dataset["case_count"],
            family_size=dataset["family_size"],
            global_seed=dataset["global_seed"],
            ratios=dataset["split_ratios"],
        )
        if role == "main":
            plan = _apply_mismatch_challenge_design(
                plan, config["challenge_design"]
            )
        splits = Counter(str(entry["split"]) for entry in plan["entries"])
        assert splits == (
            Counter({"train": 400, "val": 50, "test": 50})
            if role == "main"
            else Counter({"test": 50})
        )
        all_entries.extend(plan["entries"])
        rr_values.extend(
            SeedBundle.from_case(dataset["global_seed"], entry["case_id"]).simind
            for entry in plan["entries"]
        )
    assert len(all_entries) == len({entry["case_id"] for entry in all_entries}) == 550
    assert len(rr_values) == len(set(rr_values)) == 550
    assert Counter(index % 3 for index in range(550)) == Counter({0: 184, 1: 183, 2: 183})


def test_task13_runtime_patch_uses_formal_marker_and_archive_names() -> None:
    import task12f_linux50_common as common

    previous = patch_runtime_contract()
    try:
        assert common.BUNDLE_SCHEMA == "pars_v2_task13_formal550_bundle_v1"
        assert common.PLAN_SCHEMA == "pars_v2_task13_formal550_plan_v1"
        assert common.CASE_MARKER_FILENAME == "TASK13_CASE.json"
        assert common.MASTER_FILENAME == "TASK13_FORMAL550_MASTER.json"
        assert common.RESULT_ARCHIVE_NAME == "task13_formal550_results.tar.gz"
    finally:
        restore_runtime_contract(previous)
