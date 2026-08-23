from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.seeds import SEED_NAMESPACES, SeedBundle  # noqa: E402


def test_seed_bundle_is_exactly_reproducible() -> None:
    first = SeedBundle.from_case(global_seed=20260713, case_id="case_0042")
    second = SeedBundle.from_case(global_seed=20260713, case_id="case_0042")

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert set(first.child_seeds) == set(SEED_NAMESPACES)


def test_seed_tree_has_no_collision_for_formal_500_plus_negative_50() -> None:
    bundles = [
        SeedBundle.from_case(20260713, f"case_{index:05d}")
        for index in range(500)
    ]
    bundles += [
        SeedBundle.from_case(20260713, f"negative_{index:04d}")
        for index in range(50)
    ]

    case_seeds = [bundle.case_seed for bundle in bundles]
    all_children = [seed for bundle in bundles for seed in bundle.child_seeds.values()]
    assert len(case_seeds) == len(set(case_seeds))
    assert len(all_children) == len(set(all_children))
    assert set(case_seeds).isdisjoint(all_children)


def test_legacy_main_alias_and_frozen_pilot_rr_values_are_stable() -> None:
    canonical = [
        SeedBundle.from_case(20260713, f"case_{index:05d}").simind
        for index in range(500)
    ]
    legacy = [
        SeedBundle.from_case(20260713, f"main_{index:04d}").simind
        for index in range(500)
    ]
    negative = [
        SeedBundle.from_case(20260713, f"negative_{index:04d}").simind
        for index in range(50)
    ]

    assert canonical == legacy
    assert set(canonical).isdisjoint(negative)
    assert [
        SeedBundle.from_case(20260714, f"case_{index:05d}").simind
        for index in range(3)
    ] == [7765, 5706, 3647]


def test_namespaces_and_global_seed_change_every_child_seed() -> None:
    base = SeedBundle.from_case(1, "case_0001")
    other_case = SeedBundle.from_case(1, "case_0002")
    other_global = SeedBundle.from_case(2, "case_0001")

    assert base.case_seed != other_case.case_seed
    assert base.case_seed != other_global.case_seed
    assert all(base.child_seeds[name] != other_case.child_seeds[name] for name in SEED_NAMESPACES)
    assert all(base.child_seeds[name] != other_global.child_seeds[name] for name in SEED_NAMESPACES)
    assert 1 <= base.simind <= 2_147_483_646


@pytest.mark.parametrize("global_seed,case_id", [(-1, "case_0001"), (1, ""), (1, "   ")])
def test_seed_bundle_rejects_invalid_identity(global_seed: int, case_id: str) -> None:
    with pytest.raises(ValueError):
        SeedBundle.from_case(global_seed, case_id)
