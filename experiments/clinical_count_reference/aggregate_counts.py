"""Aggregate anonymized raw liver-SPECT count distributions.

The output deliberately excludes patient names, directory names, UIDs and
absolute paths.  It supports empirical observation matching only and must not
be interpreted as an activity, dose, sensitivity or cps/MBq calibration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pydicom


RAW_DIRECTORY_NAMES = {"tomo", "tomo anon"}


def sha256_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def candidate_files(case_root: Path) -> list[Path]:
    top_level = sorted(case_root.glob("dicom-series-001-*"))
    if top_level:
        return [top_level[0]]
    for directory in sorted(path for path in case_root.iterdir() if path.is_dir()):
        if directory.name.lower() not in RAW_DIRECTORY_NAMES:
            continue
        files = sorted(
            path for path in directory.iterdir() if path.is_file() and path.name != "VERSION"
        )
        if files:
            return files
    return []


def discover_series(root: Path) -> list[list[Path]]:
    """Discover raw series without retaining patient-identifying paths."""
    series: list[list[Path]] = []
    non_listmode_root = root / "patient_nolm" if (root / "patient_nolm").exists() else root
    for case_root in sorted(path for path in non_listmode_root.iterdir() if path.is_dir()):
        paths = candidate_files(case_root)
        if paths:
            series.append(paths)

    listmode_root = root / "patient_lm"
    if listmode_root.exists():
        seen_sop_instances: set[str] = set()
        for path in sorted(candidate for candidate in listmode_root.rglob("*") if candidate.is_file()):
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            try:
                dataset = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
            except Exception:
                continue
            if (
                str(getattr(dataset, "Modality", "")).upper() != "NM"
                or int(getattr(dataset, "NumberOfFrames", 0)) != 60
                or int(getattr(dataset, "Rows", 0)) != 128
                or int(getattr(dataset, "Columns", 0)) != 128
                or int(getattr(dataset, "SamplesPerPixel", 1)) != 1
                or not str(getattr(dataset, "PhotometricInterpretation", "")).startswith(
                    "MONOCHROME"
                )
                or "TOMO" not in [str(value).upper() for value in getattr(dataset, "ImageType", [])]
            ):
                continue
            sop_instance = str(getattr(dataset, "SOPInstanceUID", path.resolve()))
            if sop_instance in seen_sop_instances:
                continue
            seen_sop_instances.add(sop_instance)
            series.append([path])
    return series


def load_series(paths: list[Path]) -> tuple[np.ndarray, list[float], dict]:
    frames: list[np.ndarray] = []
    durations_ms: list[float] = []
    metadata: dict = {}
    for path in paths:
        dataset = pydicom.dcmread(str(path), force=True)
        array = np.asarray(dataset.pixel_array)
        if array.ndim == 3:
            frames.extend(array)
        elif array.ndim == 2:
            frames.append(array)
        else:
            raise ValueError(f"Unsupported DICOM pixel rank {array.ndim}")
        duration = getattr(dataset, "ActualFrameDuration", None)
        if duration is not None:
            durations_ms.append(float(duration))
        if not metadata:
            metadata = {
                "rows": int(getattr(dataset, "Rows", array.shape[-2])),
                "columns": int(getattr(dataset, "Columns", array.shape[-1])),
                "modality": str(getattr(dataset, "Modality", "")),
            }
    return np.stack(frames), durations_ms, metadata


def aggregate(root: Path) -> dict:
    accepted: list[dict] = []
    excluded: list[dict] = []
    discovered = discover_series(root)
    for ordinal, paths in enumerate(discovered, start=1):
        anonymous_id = f"clinical_{ordinal:02d}"
        try:
            array, durations_ms, metadata = load_series(paths)
        except Exception as exc:  # evidence keeps the failure, not patient-identifying paths
            excluded.append(
                {"case_id": anonymous_id, "reason": f"dicom_read_failed:{type(exc).__name__}"}
            )
            continue
        if array.shape != (60, 128, 128):
            excluded.append(
                {
                    "case_id": anonymous_id,
                    "reason": "protocol_shape_mismatch",
                    "shape": list(array.shape),
                }
            )
            continue
        if not np.issubdtype(array.dtype, np.integer) or np.any(array < 0):
            excluded.append({"case_id": anonymous_id, "reason": "invalid_count_dtype_or_range"})
            continue
        view_sums = array.sum(axis=(1, 2), dtype=np.float64)
        accepted.append(
            {
                "case_id": anonymous_id,
                "shape": list(array.shape),
                "source_file_count": len(paths),
                "source_series_sha256": sha256_files(paths),
                "pixel_dtype": str(array.dtype),
                "total_counts": int(array.sum(dtype=np.int64)),
                "view_sum_mean": float(view_sums.mean()),
                "view_sum_std": float(view_sums.std(ddof=0)),
                "angular_cv": float(view_sums.std(ddof=0) / view_sums.mean()),
                "actual_frame_duration_ms_median": (
                    float(np.median(durations_ms)) if durations_ms else None
                ),
                "metadata": metadata,
            }
        )

    totals = np.asarray([row["total_counts"] for row in accepted], dtype=np.float64)
    cvs = np.asarray([row["angular_cv"] for row in accepted], dtype=np.float64)
    return {
        "purpose": "Empirical raw-count and angular-profile matching for the current liver SPECT protocol.",
        "claim_boundary": (
            "Not an activity, administered-dose, scanner-sensitivity or absolute cps/MBq calibration."
        ),
        "source_scope": "De-identified local raw TOMO series; IRAC/reconstructed and CT series excluded.",
        "acceptance_contract": {
            "shape": [60, 128, 128],
            "integer_nonnegative_pixels": True,
        },
        "accepted_case_count": len(accepted),
        "excluded_case_count": len(excluded),
        "accepted_cases": accepted,
        "excluded_cases": excluded,
        "distribution": {
            "total_counts_min": int(totals.min()) if totals.size else None,
            "total_counts_median": float(np.median(totals)) if totals.size else None,
            "total_counts_max": int(totals.max()) if totals.size else None,
            "angular_cv_min": float(cvs.min()) if cvs.size else None,
            "angular_cv_median": float(np.median(cvs)) if cvs.size else None,
            "angular_cv_max": float(cvs.max()) if cvs.size else None,
        },
        "selected_policy": (
            "Match synthetic observation totals and angular-profile variability to this empirical "
            "distribution; retain SIMIND transport as an expectation and record every scaling/noise seed."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = aggregate(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["distribution"], indent=2))
    print(
        f"accepted={payload['accepted_case_count']} excluded={payload['excluded_case_count']}"
    )


if __name__ == "__main__":
    main()
