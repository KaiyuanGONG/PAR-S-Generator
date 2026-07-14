from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_pilot3_alignment_companion_v2 import (  # noqa: E402
    ALIGNMENT_NN_MULTIPLIER,
    BASE_HISTORIES_PER_PROJECTION,
    EXPECTED_CASE_IDS,
    load_frozen_alignment_cases,
)


def test_companion_matches_actual_task11_history_baseline() -> None:
    assert BASE_HISTORIES_PER_PROJECTION == 80_000
    assert ALIGNMENT_NN_MULTIPLIER == 5
    assert BASE_HISTORIES_PER_PROJECTION * ALIGNMENT_NN_MULTIPLIER == 400_000
    assert EXPECTED_CASE_IDS == ("case_00000", "case_00001", "case_00002")


def test_companion_requires_a_frozen_dataset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read dataset completion marker"):
        load_frozen_alignment_cases(tmp_path)
