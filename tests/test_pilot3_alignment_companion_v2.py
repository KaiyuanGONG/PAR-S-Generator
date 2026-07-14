from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_pilot3_alignment_companion_v2 import (  # noqa: E402
    ALIGNMENT_NN_MULTIPLIER,
    BASE_HISTORIES_PER_PROJECTION,
    EXPECTED_CASE_IDS,
    FrozenAlignmentCase,
    load_frozen_alignment_cases,
    snapshot_case_inputs,
)


def test_companion_matches_actual_task11_history_baseline() -> None:
    assert BASE_HISTORIES_PER_PROJECTION == 80_000
    assert ALIGNMENT_NN_MULTIPLIER == 5
    assert BASE_HISTORIES_PER_PROJECTION * ALIGNMENT_NN_MULTIPLIER == 400_000
    assert EXPECTED_CASE_IDS == ("case_00000", "case_00001", "case_00002")


def test_companion_requires_a_frozen_dataset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read dataset completion marker"):
        load_frozen_alignment_cases(tmp_path)


def test_companion_stages_byte_identical_simind_filenames(tmp_path: Path) -> None:
    source = tmp_path / "simind_source_bin.bin"
    density = tmp_path / "simind_density_bin.bin"
    source.write_bytes(b"source-bytes")
    density.write_bytes(b"density-bytes")
    case = FrozenAlignmentCase(
        case_id="case_00000",
        phantom_npz=tmp_path / "phantom.npz",
        source_bin=source,
        density_bin=density,
        metadata_json=tmp_path / "metadata.json",
        rr_seed=7765,
        binary_sha256="binary",
        smc_sha256="smc",
        simind_ini_sha256="ini",
    )

    staged_source, staged_density = snapshot_case_inputs(case, tmp_path / "run")

    assert staged_source.name == "case_00000_act_av.bin"
    assert staged_density.name == "case_00000_atn_av.bin"
    assert staged_source.read_bytes() == source.read_bytes()
    assert staged_density.read_bytes() == density.read_bytes()
