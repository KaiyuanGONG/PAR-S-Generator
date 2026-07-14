from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.interfile_writer import (  # noqa: E402
    write_bin,
    write_attenuation_map_v2,
    write_voxel_source,
)
from core.simind_exec import (  # noqa: E402
    SIMIND_PROTOCOL_NAME_V2,
    build_simind_command,
)
from core.smc_parser import (  # noqa: E402
    SMC_FLAG_LABELS,
    parse_smc,
    validate_voxel_source_smc,
)


def _scanner_profile() -> dict:
    return json.loads(
        (REPO_ROOT / "configs" / "scanner_ge870_tcmma_v1.json").read_text(
            encoding="utf-8"
        )
    )


def test_voxel_source_sum_is_base_histories_not_nn_or_activity_time(tmp_path: Path) -> None:
    probability = np.zeros((8, 8, 8), dtype=np.float32)
    probability[2:6, 2:6, 2:6] = np.linspace(0.1, 1.0, 64).reshape(4, 4, 4)
    result = write_voxel_source(
        probability,
        tmp_path / "case_00001",
        base_histories=80_000,
    )
    written = np.fromfile(result.path, dtype="<f4").reshape(probability.shape)

    assert result.path.name == "case_00001_act_av.bin"
    assert result.base_histories == 80_000
    assert float(written.sum(dtype=np.float64)) == pytest.approx(80_000, abs=0.02)
    assert result.source_sum == pytest.approx(float(written.sum(dtype=np.float64)))
    assert result.nn_multiplier is None
    assert result.activity_time_product_mbq_s is None


def test_raw_writer_always_emits_little_endian_c_order(tmp_path: Path) -> None:
    native_values = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    big_endian_noncontiguous = native_values[:, :, ::-1].astype(">f4")
    path = write_bin(big_endian_noncontiguous, tmp_path / "endian", "_act_av")

    expected = np.asarray(big_endian_noncontiguous, dtype=np.float32)
    written = np.fromfile(path, dtype="<f4").reshape(expected.shape)
    assert np.array_equal(written, expected)
    assert path.read_bytes() == np.asarray(expected, dtype="<f4", order="C").tobytes(
        order="C"
    )


def test_writer_rejects_invalid_probability_and_mu_input_semantics(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        write_voxel_source(
            np.asarray([[[-1.0, 1.0]]], dtype=np.float32),
            tmp_path / "bad",
            base_histories=100,
        )
    with pytest.raises(ValueError, match="positive sum"):
        write_voxel_source(
            np.zeros((2, 2, 2), dtype=np.float32),
            tmp_path / "empty",
            base_histories=100,
        )
    mu = np.full((2, 2, 2), 0.15, dtype=np.float32)
    with pytest.raises(ValueError, match="only mu_true_140kev"):
        write_attenuation_map_v2(
            mu,
            tmp_path / "case_00001",
            semantic_key="mu_input_140kev",
        )


def test_smc_and_scanner_contract_keep_four_count_concepts_separate() -> None:
    scanner = _scanner_profile()["parameters"]
    smc = parse_smc(REPO_ROOT / "simind" / "ge870_czt.smc")
    contract = validate_voxel_source_smc(smc)

    assert scanner["activity_mbq"]["value"] == 60.0
    assert scanner["time_per_projection_s"]["value"] == 28.4
    assert 60.0 * 28.4 == pytest.approx(1704.0)
    assert scanner["base_histories_per_projection"]["value"] == 80_000
    assert scanner["voxel_source_index26_semantics"]["value"] == "ignored_for_voxel_source"
    assert smc.get_value(25) == pytest.approx(1704.0)
    assert contract.index26_semantics == "ignored_for_voxel_source"
    assert smc.get_flag(8) is True
    assert SMC_FLAG_LABELS[8] == "Random-number sequence control"
    assert SMC_FLAG_LABELS[7].startswith("Crystal rear volume")
    assert contract.projection_views == 60
    assert contract.rotation_code == 2
    assert contract.starting_angle_deg == pytest.approx(180.0)
    assert contract.projection_pixel_size_cm == pytest.approx(0.442)
    assert contract.density_pixel_size_cm == pytest.approx(0.442)
    assert contract.image_matrix_xy == (128, 128)
    assert contract.density_matrix_ij == (128, 128)
    assert contract.source_matrix_ij == (128, 128)


@pytest.mark.parametrize("index", [28, 29, 30, 31, 32, 34, 41, 42, 76, 77, 78, 79, 81, 82])
def test_smc_geometry_contract_rejects_every_coordinate_index_drift(index: int) -> None:
    smc = parse_smc(REPO_ROOT / "simind" / "ge870_czt.smc")
    values = list(smc.values)
    values[index - 1] += 1.0
    with pytest.raises(ValueError, match=rf"Index {index}"):
        validate_voxel_source_smc(replace(smc, values=tuple(values)))


def test_command_requires_case_specific_rr_and_protocol_name_has_28p4s() -> None:
    command = build_simind_command(
        executable=Path("simind.exe"),
        smc_stem="ge870_czt",
        output_stem="case_00001",
        source_stem="case_00001",
        density_stem="case_00001",
        nn_multiplier=10,
        rr_seed=1234567,
    )
    assert "/NN:10" in command
    assert "/RR:1234567" in command
    assert SIMIND_PROTOCOL_NAME_V2 == "SPECT_60MBq_28p4s_v2"
    assert "20s" not in SIMIND_PROTOCOL_NAME_V2


def test_core_simind_modules_do_not_import_qt() -> None:
    for filename in ("simind_exec.py", "simind_postprocess.py", "smc_parser.py"):
        source = (REPO_ROOT / "src" / "core" / filename).read_text(encoding="utf-8")
        assert "PyQt" not in source
