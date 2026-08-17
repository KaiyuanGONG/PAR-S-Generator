"""
Tests for smc_parser module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.smc_parser import (
    SMC_CATEGORIES,
    SMC_FLAG_LABELS,
    SMC_PARAM_LABELS,
    SmcData,
    parse_smc,
)

SMC_FILE = Path(__file__).parent.parent / "simind" / "ge870_czt.smc"


@pytest.fixture
def smc_data() -> SmcData:
    return parse_smc(SMC_FILE)


class TestSmcParsing:
    def test_file_exists(self):
        assert SMC_FILE.exists(), f"Bundled SMC not found: {SMC_FILE}"

    def test_parse_returns_smc_data(self, smc_data: SmcData):
        assert isinstance(smc_data, SmcData)

    def test_description(self, smc_data: SmcData):
        assert len(smc_data.description) > 0

    def test_values_count(self, smc_data: SmcData):
        assert len(smc_data.values) == 120

    def test_flags_count(self, smc_data: SmcData):
        assert len(smc_data.flags) == 30

    def test_text_variables_count(self, smc_data: SmcData):
        assert len(smc_data.text_variables) == 8

    def test_data_files_count(self, smc_data: SmcData):
        assert len(smc_data.data_files) == 12

    def test_known_value_index1(self, smc_data: SmcData):
        """Index 1 = photon energy, should be 140.0 keV."""
        assert smc_data.get_value(1) == pytest.approx(140.0, rel=1e-3)

    def test_known_value_index9(self, smc_data: SmcData):
        """Index 9 = crystal thickness."""
        assert smc_data.get_value(9) == pytest.approx(0.725, rel=1e-3)

    def test_known_value_index29(self, smc_data: SmcData):
        """Index 29 = SPECT projections."""
        assert smc_data.get_value(29) == pytest.approx(60, rel=1e-3)

    def test_known_value_index76(self, smc_data: SmcData):
        """Index 76 = image matrix X, should be 128."""
        assert smc_data.get_value(76) == pytest.approx(128.0, rel=1e-3)

    def test_flag_forced_detection(self, smc_data: SmcData):
        """Flag 1 (forced detection) should be True for standard configs."""
        assert smc_data.get_flag(1) is True

    def test_flag_simulate_spect(self, smc_data: SmcData):
        """Flag 5 (simulate SPECT) should be True."""
        assert smc_data.get_flag(5) is True

    def test_get_value_out_of_range(self, smc_data: SmcData):
        with pytest.raises(IndexError):
            smc_data.get_value(0)
        with pytest.raises(IndexError):
            smc_data.get_value(121)

    def test_get_flag_out_of_range(self, smc_data: SmcData):
        with pytest.raises(IndexError):
            smc_data.get_flag(0)
        with pytest.raises(IndexError):
            smc_data.get_flag(31)


class TestSmcMetadata:
    def test_param_labels_keys_in_range(self):
        for idx in SMC_PARAM_LABELS:
            assert 1 <= idx <= 120

    def test_categories_indices_in_range(self):
        for _name, indices in SMC_CATEGORIES:
            for idx in indices:
                assert 1 <= idx <= 120

    def test_flag_labels_keys_in_range(self):
        for idx in SMC_FLAG_LABELS:
            assert 1 <= idx <= 30


class TestSmcParseErrors:
    def test_nonexistent_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            parse_smc(tmp_path / "nonexistent.smc")

    def test_invalid_format(self, tmp_path: Path):
        bad = tmp_path / "bad.smc"
        bad.write_text("NOT_SMCV2\n")
        with pytest.raises(ValueError, match="Not a valid SMCV2"):
            parse_smc(bad)

    def test_auto_extension(self):
        """parse_smc should add .smc extension if missing."""
        stem_path = SMC_FILE.with_suffix("")
        data = parse_smc(stem_path)
        assert len(data.values) == 120
