"""
Interfile Writer
================
Converts .npz phantom files to SIMIND-compatible Interfile format (.h33 + .i33).
Based on PAR-S/notebooks/DataPreparation.ipynb
"""

from __future__ import annotations
import struct
from pathlib import Path
from typing import Optional
import numpy as np


def write_interfile(
    volume: np.ndarray,
    output_stem: Path,
    voxel_size_mm: float = 4.20,
    data_type: str = "float",
    description: str = "",
) -> tuple[Path, Path]:
    """
    Write a 3D numpy array as Interfile (.h33 header + .i33 binary data).

    Parameters
    ----------
    volume : np.ndarray  shape (Z, Y, X)
    output_stem : Path   e.g. Path("output/case_0001_act")
    voxel_size_mm : float
    data_type : "float" | "short"
    description : str

    Returns
    -------
    (header_path, data_path)
    """
    output_stem = Path(output_stem)
    header_path = output_stem.with_suffix(".h33")
    data_path = output_stem.with_suffix(".i33")

    nz, ny, nx = volume.shape
    vox_cm = voxel_size_mm / 10.0

    if data_type == "float":
        arr = volume.astype(np.float32)
        number_format = "short float"
        bytes_per_pixel = 4
    else:
        arr = volume.astype(np.int16)
        number_format = "signed integer"
        bytes_per_pixel = 2

    # Write binary data (SIMIND expects Z-major, Fortran-style ordering)
    arr.tofile(str(data_path))

    # Write Interfile header
    header = f"""\
!INTERFILE :=
!imaging modality := nucmed
!version of keys := 3.3
;{description}

!GENERAL DATA :=
!data offset in bytes := 0
!name of data file := {data_path.name}
!total number of images := {nz}

!GENERAL IMAGE DATA :=
!type of data := Tomographic
!number format := {number_format}
!number of bytes per pixel := {bytes_per_pixel}
imagedata byte order := LITTLEENDIAN

!SPECT STUDY (General) :=
!number of images/energy window := {nz}
!process status := Reconstructed
!number of projections := {nz}
!extent of rotation := 360
!time per projection (sec) := 30
!study duration (sec) := {nz * 30}

!SPECT STUDY (reconstructed data) :=
!number of slices := {nz}
!matrix size [1] := {nx}
!matrix size [2] := {ny}
!scaling factor (mm/pixel) [1] := {voxel_size_mm:.4f}
!scaling factor (mm/pixel) [2] := {voxel_size_mm:.4f}
!slice thickness (pixels) := 1
slice-to-slice separation (pixels) := 1
centre-centre slice separation (pixels) := 1
!END OF INTERFILE :=
"""
    with open(header_path, "w", encoding="ascii") as f:
        f.write(header)

    return header_path, data_path


def convert_npz_to_interfile(
    npz_path: Path,
    output_dir: Path,
    voxel_size_mm: float = 4.20,
) -> dict:
    """
    Convert a single .npz phantom file to Interfile pairs.

    Returns dict with paths: {act_h33, act_i33, atn_h33, atn_i33}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = np.load(npz_path)
    stem = npz_path.stem  # e.g. "case_0001"

    result = {}

    if "activity" in data:
        act_stem = output_dir / f"{stem}_act_1"
        h, d = write_interfile(
            data["activity"], act_stem, voxel_size_mm,
            data_type="float", description=f"Activity map: {stem}"
        )
        result["act_h33"] = h
        result["act_i33"] = d

    if "mu_map" in data:
        atn_stem = output_dir / f"{stem}_atn_1"
        h, d = write_interfile(
            data["mu_map"], atn_stem, voxel_size_mm,
            data_type="float", description=f"Attenuation map: {stem}"
        )
        result["atn_h33"] = h
        result["atn_i33"] = d

    return result


def batch_convert_npz_to_interfile(
    npz_dir: Path,
    output_dir: Path,
    voxel_size_mm: float = 4.20,
    progress_callback=None,
) -> list[dict]:
    """
    Convert all .npz files in a directory to Interfile format.

    progress_callback(current, total, filename) -> None
    """
    npz_files = sorted(Path(npz_dir).glob("case_*.npz"))
    results = []
    for i, npz_path in enumerate(npz_files):
        if progress_callback:
            progress_callback(i, len(npz_files), npz_path.name)
        r = convert_npz_to_interfile(npz_path, output_dir, voxel_size_mm)
        results.append(r)
    return results


def generate_simind_bat(
    interfile_dir: Path,
    simind_exe: Path,
    smc_file: Path,
    output_dir: Path,
    bat_path: Path,
    photons_per_proj: int = 5_000_000,
) -> Path:
    """
    Generate a Windows .bat script to run SIMIND on all Interfile pairs.

    Parameters
    ----------
    interfile_dir : directory containing *_act_1.h33 files
    simind_exe    : path to simind.exe
    smc_file      : path to .smc configuration file
    output_dir    : where SIMIND writes its output
    bat_path      : where to write the .bat file
    photons_per_proj : number of photon histories per projection
    """
    interfile_dir = Path(interfile_dir)
    output_dir = Path(output_dir)
    bat_path = Path(bat_path)

    act_headers = sorted(interfile_dir.glob("case_*_act_1.h33"))

    lines = [
        "@echo off",
        "echo PAR-S Generator - SIMIND Batch Runner",
        "echo ========================================",
        f"echo Total cases: {len(act_headers)}",
        "echo.",
        "",
        f'set SIMIND="{simind_exe}"',
        f'set SMC="{smc_file}"',
        f'set OUTDIR="{output_dir}"',
        "",
        "if not exist %OUTDIR% mkdir %OUTDIR%",
        "",
    ]

    for i, act_h33 in enumerate(act_headers):
        stem = act_h33.stem.replace("_act_1", "")
        atn_h33 = interfile_dir / f"{stem}_atn_1.h33"
        out_stem = output_dir / stem

        lines += [
            f"echo [{i + 1}/{len(act_headers)}] Processing {stem}...",
            f'%SIMIND% %SMC% /FI:"{act_h33}" /FA:"{atn_h33}" /FO:"{out_stem}" /NN:{photons_per_proj}',
            "if errorlevel 1 (",
            f"    echo ERROR: Failed on {stem}",
            "    pause",
            "    exit /b 1",
            ")",
            "",
        ]

    lines += [
        "echo.",
        "echo All cases completed successfully.",
        "pause",
    ]

    bat_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bat_path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(lines))

    return bat_path
