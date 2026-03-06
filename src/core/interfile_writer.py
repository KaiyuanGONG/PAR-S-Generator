"""
Interfile Writer
================
Converts .npz phantom files to SIMIND-compatible binary format.

SIMIND XcatBinMap convention (Index-14 = -7, Index-15 = -7):
  - Source file:  <stem>_act_av.bin   (read via /FS:<stem>)
  - Density file: <stem>_atn_av.bin   (read via /FD:<stem>)
  - Format: float32, C-order (Z, Y, X), no header
"""

from __future__ import annotations
from pathlib import Path
import numpy as np


def write_bin(
    volume: np.ndarray,
    output_stem: Path,
    suffix: str,
) -> Path:
    """
    Write a 3D numpy array as raw float32 binary.

    Parameters
    ----------
    volume      : np.ndarray  shape (Z, Y, X)
    output_stem : base path, e.g. Path("output/case_0001")
    suffix      : "_act_av" or "_atn_av"

    Returns
    -------
    bin_path : Path  (e.g. output/case_0001_act_av.bin)
    """
    output_stem = Path(output_stem)
    bin_path = output_stem.parent / (output_stem.name + suffix + ".bin")
    volume.astype(np.float32).tofile(str(bin_path))
    return bin_path


def convert_npz_to_interfile(
    npz_path: Path,
    output_dir: Path,
    voxel_size_mm: float = 4.42,  # kept for API compatibility
) -> dict:
    """
    Convert a single .npz phantom file to SIMIND binary pairs.

    Output files:
      case_XXXX_act_av.bin  — activity map  (read by SIMIND via /FS:case_XXXX)
      case_XXXX_atn_av.bin  — attenuation map (read by SIMIND via /FD:case_XXXX)

    Returns dict with paths: {act_bin, atn_bin}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = np.load(npz_path)
    stem = npz_path.stem  # e.g. "case_0001"
    base = output_dir / stem

    result = {}

    if "activity" in data:
        result["act_bin"] = write_bin(data["activity"], base, "_act_av")

    if "mu_map" in data:
        result["atn_bin"] = write_bin(data["mu_map"], base, "_atn_av")

    return result


def batch_convert_npz_to_interfile(
    npz_dir: Path,
    output_dir: Path,
    voxel_size_mm: float = 4.42,
    progress_callback=None,
) -> list[dict]:
    """
    Convert all .npz files in a directory to SIMIND binary format.

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
    photons_per_proj: int = 5_000_000,  # informational; actual count set by Index-26 in .smc
) -> Path:
    """
    Generate a Windows .bat script to run SIMIND on all binary pairs.

    SIMIND XcatBinMap call format:
        cd /d <binary_dir>
        copy <smc_file> .
        simind.exe <smc_stem> <output_abs_path> /FS:<case_stem> /FD:<case_stem>

    Key constraints:
    - /FS: and /FD: must be SHORT RELATIVE paths (SIMIND parses backslashes as switches)
    - SIMIND must run from the directory containing the _act_av.bin / _atn_av.bin files
    - The smc file is copied to the binary dir so it can be referenced by stem only
    - Output path (2nd positional arg) can be absolute (handled as positional, not switch)

    Parameters
    ----------
    interfile_dir : directory containing case_XXXX_act_av.bin files
    simind_exe    : path to simind.exe
    smc_file      : path to .smc configuration file (e.g. simind/ge870_czt.smc)
    output_dir    : where SIMIND writes its output (.a00, .h00, etc.)
    bat_path      : where to write the .bat file
    """
    interfile_dir = Path(interfile_dir).resolve()
    output_dir = Path(output_dir).resolve()
    simind_exe = Path(simind_exe).resolve()
    smc_file = Path(smc_file).resolve()
    bat_path = Path(bat_path)

    smc_stem = smc_file.stem          # e.g. "ge870_czt"

    act_bins = sorted(interfile_dir.glob("case_*_act_av.bin"))

    lines = [
        "@echo off",
        "echo PAR-S Generator - SIMIND Batch Runner",
        "echo ========================================",
        f"echo Total cases: {len(act_bins)}",
        f"echo SMC: {smc_stem}.smc",
        f"echo Photons/proj (Index-26 in smc): see ge870_czt.smc",
        "echo.",
        "",
        f'set "SIMIND={simind_exe}"',
        f'set "OUTDIR={output_dir}"',
        "",
        'if not exist "%OUTDIR%" mkdir "%OUTDIR%"',
        "",
        "REM SIMIND switch parser treats backslashes as delimiters.",
        "REM Solution: cd to binary dir so /FS: /FD: use short relative stems.",
        "REM The smc is copied locally so it can be referenced without a path.",
        f'copy "{smc_file}" "{interfile_dir}\\" /Y > nul',
        f'pushd "{interfile_dir}"',
        "",
    ]

    for i, act_bin in enumerate(act_bins):
        # stem = "case_0001"  (SIMIND auto-appends _act_av.bin / _atn_av.bin)
        stem = act_bin.name.replace("_act_av.bin", "")
        out_stem = output_dir / stem   # absolute — OK as 2nd positional arg

        lines += [
            f'echo [{i + 1}/{len(act_bins)}] Processing {stem}...',
            f'"%SIMIND%" {smc_stem} "{out_stem}" /FS:{stem} /FD:{stem}',
            "if errorlevel 1 (",
            f"    echo ERROR: Failed on {stem}",
            "    popd",
            "    pause",
            "    exit /b 1",
            ")",
            "",
        ]

    lines += [
        "popd",
        "echo.",
        "echo All cases completed successfully.",
        "pause",
    ]

    bat_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bat_path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(lines))

    return bat_path
