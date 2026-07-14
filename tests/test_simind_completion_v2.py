from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.simind_postprocess import (  # noqa: E402
    SimindCompletionError,
    audit_simind_completion,
)


SHAPE = (60, 16, 16)


def _write_quartet(
    root: Path,
    case_id: str = "case_00001",
    *,
    shape: tuple[int, int, int] = SHAPE,
    element_name: str | None = None,
    values: np.ndarray | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stem = root / case_id
    array = (
        np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
        if values is None
        else np.asarray(values, dtype=np.float32)
    )
    array.tofile(stem.with_suffix(".a00"))
    views, rows, columns = shape
    stem.with_suffix(".mhd").write_text(
        "\n".join(
            [
                "ObjectType = Image",
                "BinaryData = True",
                "BinaryDataByteOrderMSB = False",
                "CompressedData = False",
                "NDims = 3",
                f"DimSize = {columns} {rows} {views}",
                "ElementType = MET_FLOAT",
                f"ElementDataFile = {element_name or case_id + '.a00'}",
                "",
            ]
        ),
        encoding="ascii",
    )
    stem.with_suffix(".res").write_text("SIMIND result\n", encoding="ascii")
    stem.with_suffix(".spe").write_bytes(b"SIMIND spectrum\x00")
    return stem


def test_completion_requires_all_four_files_and_exit_zero(tmp_path: Path) -> None:
    stem = tmp_path / "case_00001"
    np.zeros(SHAPE, dtype=np.float32).tofile(stem.with_suffix(".a00"))
    with pytest.raises(SimindCompletionError, match="missing required outputs"):
        audit_simind_completion(stem, expected_shape=SHAPE, exit_code=0)

    _write_quartet(tmp_path)
    with pytest.raises(SimindCompletionError, match="exit code"):
        audit_simind_completion(stem, expected_shape=SHAPE, exit_code=7)


def test_completion_checks_bytes_dims_finite_views_and_pairing(tmp_path: Path) -> None:
    stem = _write_quartet(tmp_path / "valid")
    audit = audit_simind_completion(stem, expected_shape=SHAPE, exit_code=0)
    assert audit.complete
    assert audit.view_count == 60
    assert audit.expected_bytes == np.prod(SHAPE) * 4
    assert set(audit.sha256) == {"a00", "mhd", "res", "spe"}

    wrong_size = _write_quartet(tmp_path / "wrong_size")
    wrong_size.with_suffix(".a00").write_bytes(b"too short")
    with pytest.raises(SimindCompletionError, match="byte size"):
        audit_simind_completion(wrong_size, expected_shape=SHAPE, exit_code=0)

    wrong_dims = _write_quartet(tmp_path / "wrong_dims")
    wrong_dims.with_suffix(".mhd").write_text(
        wrong_dims.with_suffix(".mhd").read_text(encoding="ascii").replace(
            "DimSize = 16 16 60", "DimSize = 16 16 59"
        ),
        encoding="ascii",
    )
    with pytest.raises(SimindCompletionError, match="DimSize"):
        audit_simind_completion(wrong_dims, expected_shape=SHAPE, exit_code=0)

    nan_values = np.zeros(SHAPE, dtype=np.float32)
    nan_values[0, 0, 0] = np.nan
    nonfinite = _write_quartet(tmp_path / "nonfinite", values=nan_values)
    with pytest.raises(SimindCompletionError, match="non-finite"):
        audit_simind_completion(nonfinite, expected_shape=SHAPE, exit_code=0)

    mismatched = _write_quartet(
        tmp_path / "mismatched", element_name="case_99999.a00"
    )
    with pytest.raises(SimindCompletionError, match="does not pair"):
        audit_simind_completion(mismatched, expected_shape=SHAPE, exit_code=0)


def test_completion_rejects_non_60_view_contract(tmp_path: Path) -> None:
    stem = _write_quartet(tmp_path, shape=(59, 16, 16))
    with pytest.raises(ValueError, match="60 views"):
        audit_simind_completion(stem, expected_shape=(59, 16, 16), exit_code=0)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("BinaryData = True", "BinaryData = False", "BinaryData"),
        (
            "BinaryDataByteOrderMSB = False",
            "BinaryDataByteOrderMSB = True",
            "BinaryDataByteOrderMSB",
        ),
        ("CompressedData = False", "CompressedData = True", "CompressedData"),
        ("NDims = 3", "NDims = 2", "NDims"),
        ("ElementType = MET_FLOAT", "ElementType = MET_DOUBLE", "ElementType"),
    ],
)
def test_completion_rejects_noncanonical_mhd_contract(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    stem = _write_quartet(tmp_path / message)
    header = stem.with_suffix(".mhd")
    header.write_text(
        header.read_text(encoding="ascii").replace(field, replacement),
        encoding="ascii",
    )
    with pytest.raises(SimindCompletionError, match=message):
        audit_simind_completion(stem, expected_shape=SHAPE, exit_code=0)
