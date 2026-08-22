import struct

import numpy as np

from core.interfile_writer import convert_npz_to_interfile


def test_type7_export_is_explicit_little_endian_float32_with_readback_hashes(tmp_path):
    activity = np.arange(8, dtype=np.float32).reshape(2, 2, 2)
    mu_map = np.full((2, 2, 2), 0.25, dtype=np.float32)
    source = tmp_path / "case_0001.npz"
    np.savez(source, activity=activity, mu_map=mu_map)

    result = convert_npz_to_interfile(source, tmp_path / "export", voxel_size_mm=4.42)

    assert result["dtype"] == "<f4"
    assert result["byte_order"] == "little"
    assert result["order"] == "C (Z,Y,X)"
    assert result["readback_verified"] is True
    assert len(result["act_sha256"]) == 64
    assert len(result["atn_sha256"]) == 64
    assert result["act_bin"].read_bytes()[:4] == struct.pack("<f", 0.0)
    assert result["act_bin"].read_bytes()[4:8] == struct.pack("<f", 1.0)
    assert np.array_equal(np.fromfile(result["act_bin"], dtype="<f4").reshape(activity.shape), activity)
    assert np.allclose(
        np.fromfile(result["atn_bin"], dtype="<f4").reshape(mu_map.shape),
        mu_map * np.float32(0.442),
        rtol=0,
        atol=0,
    )


def test_formal_shape_type7_exports_have_exact_expected_byte_length(tmp_path):
    shape = (128, 128, 128)
    source = tmp_path / "case_0001.npz"
    np.savez(
        source,
        activity=np.zeros(shape, dtype=np.float32),
        mu_map=np.zeros(shape, dtype=np.float32),
    )

    result = convert_npz_to_interfile(source, tmp_path / "export", voxel_size_mm=4.42)

    assert result["expected_bytes"] == 8_388_608
    assert result["act_bin"].stat().st_size == 8_388_608
    assert result["atn_bin"].stat().st_size == 8_388_608
