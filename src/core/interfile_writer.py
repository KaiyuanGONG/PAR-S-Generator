"""
Binary Exporter
===============
Converts .npz phantom files to SIMIND-compatible raw binary format.

SIMIND XcatBinMap convention (Index-14 = -7, Index-15 = -7):
  - Source file:  <stem>_act_av.bin   (read via /FS:<stem>)
  - Density file: <stem>_atn_av.bin   (read via /FD:<stem>)
  - Format: float32, C-order (Z, Y, X), no header
"""

from __future__ import annotations

from pathlib import Path
import os

import numpy as np

from pipeline.contracts import sha256_file
from pipeline.simind import build_simind_tokens


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
    """
    output_stem = Path(output_stem)
    bin_path = output_stem.parent / (output_stem.name + suffix + ".bin")
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    encoded = np.asarray(volume, dtype=np.float32, order="C")
    if encoded.ndim != 3 or not np.isfinite(encoded).all():
        raise ValueError("SIMIND binary volumes must be finite 3D arrays")
    temp = bin_path.with_name(f".{bin_path.name}.{os.getpid()}.tmp")
    encoded.tofile(str(temp))
    # Read back before publishing the file.  This makes dtype/order/length an
    # executable contract instead of a comment beside an unchecked write.
    decoded = np.fromfile(temp, dtype=np.float32)
    if decoded.size != encoded.size or not np.array_equal(decoded.reshape(encoded.shape), encoded):
        temp.unlink(missing_ok=True)
        raise IOError(f"Binary read-back failed for {bin_path}")
    temp.replace(bin_path)
    return bin_path


def _validate_npz_arrays(npz_path: Path, activity: np.ndarray, mu_map: np.ndarray) -> None:
    if activity.ndim != 3 or mu_map.ndim != 3:
        raise ValueError(f"{npz_path.name}: activity and mu_map must both be 3D arrays.")
    if activity.shape != mu_map.shape:
        raise ValueError(
            f"{npz_path.name}: activity shape {activity.shape} and mu_map shape {mu_map.shape} must match."
        )
    if not np.isfinite(activity).all():
        raise ValueError(f"{npz_path.name}: activity contains NaN/Inf.")
    if not np.isfinite(mu_map).all():
        raise ValueError(f"{npz_path.name}: mu_map contains NaN/Inf.")


def convert_npz_to_interfile(
    npz_path: Path,
    output_dir: Path,
    voxel_size_mm: float = 4.42,
) -> dict:
    """
    Convert a single .npz phantom file to SIMIND binary pairs.

    `voxel_size_mm` is retained for compatibility with older call sites.
    The exporter writes raw arrays only and does not embed voxel metadata.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    npz_path = Path(npz_path)
    stem = npz_path.stem
    base = output_dir / stem

    with np.load(npz_path) as data:
        if "activity" not in data or "mu_map" not in data:
            raise ValueError(f"{npz_path.name}: missing required arrays 'activity' and/or 'mu_map'.")

        activity = np.asarray(data["activity"], dtype=np.float32)
        mu_map = np.asarray(data["mu_map"], dtype=np.float32)
        _validate_npz_arrays(npz_path, activity, mu_map)

        result = {
            "act_bin": write_bin(activity, base, "_act_av"),
            "atn_bin": write_bin(mu_map, base, "_atn_av"),
        }

    expected_bytes = int(activity.size) * np.dtype(np.float32).itemsize
    for label in ("act_bin", "atn_bin"):
        path = result[label]
        if path.stat().st_size != expected_bytes:
            raise IOError(f"Unexpected binary size after export: {path}")
    result.update(
        {
            "shape": list(activity.shape),
            "dtype": "float32",
            "order": "C (Z,Y,X)",
            "voxel_size_mm": float(voxel_size_mm),
            "act_sha256": sha256_file(result["act_bin"]),
            "atn_sha256": sha256_file(result["atn_bin"]),
            "readback_verified": True,
        }
    )

    return result


def batch_convert_npz_to_interfile(
    npz_dir: Path,
    output_dir: Path,
    voxel_size_mm: float = 4.42,
    progress_callback=None,
) -> list[dict]:
    """
    Convert all .npz files in a directory to SIMIND binary format.
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
    nn_multiplier: int = 0,
    custom_overrides: list[tuple[int, str]] | None = None,
) -> Path:
    """
    Generate a Windows .bat script to run SIMIND on all binary pairs.

    `photons_per_proj` is retained for backward compatibility but is informational only.
    `nn_multiplier` adds /NN:<value> to each SIMIND invocation (0 = omit).
    `custom_overrides` adds /<index>:<value> CLI overrides.
    """
    interfile_dir = Path(interfile_dir).resolve()
    output_dir = Path(output_dir).resolve()
    simind_exe = Path(simind_exe).resolve()
    smc_file = Path(smc_file).resolve()
    bat_path = Path(bat_path)

    smc_stem = smc_file.stem
    act_bins = sorted(interfile_dir.glob("case_*_act_av.bin"))
    if not act_bins:
        raise FileNotFoundError(f"No case_*_act_av.bin found in {interfile_dir}")

    missing_atn: list[str] = []
    for act_bin in act_bins:
        stem = act_bin.name.replace("_act_av.bin", "")
        atn_bin = interfile_dir / f"{stem}_atn_av.bin"
        if not atn_bin.exists():
            missing_atn.append(stem)
    if missing_atn:
        preview = ", ".join(missing_atn[:8])
        if len(missing_atn) > 8:
            preview += f", ... (+{len(missing_atn) - 8})"
        raise FileNotFoundError(f"Missing case_*_atn_av.bin for: {preview}")

    lines = [
        "@echo off",
        "echo PAR-S Generator - SIMIND Batch Runner",
        "echo ========================================",
        f"echo Total cases: {len(act_bins)}",
        f"echo SMC: {smc_stem}.smc",
        "echo Photon histories are controlled by Index-26 in the selected .smc file.",
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
        "if errorlevel 1 (",
        "    echo ERROR: Failed to copy .smc into binary directory.",
        "    pause",
        "    exit /b 1",
        ")",
        f'pushd "{interfile_dir}"',
        "",
    ]

    for i, act_bin in enumerate(act_bins):
        stem = act_bin.name.replace("_act_av.bin", "")
        out_stem = output_dir / stem
        lines += [
            f'echo [{i + 1}/{len(act_bins)}] Processing {stem}...',
            '"%SIMIND%" '
            + " ".join(
                f'"{token}"' if " " in token else token
                for token in build_simind_tokens(
                    smc_stem=smc_stem,
                    output_stem=str(out_stem),
                    source_stem=stem,
                    density_stem=stem,
                    nn_multiplier=nn_multiplier,
                    overrides=custom_overrides or [],
                )
            ),
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
    # Kept for API compatibility.  Pipeline-generated runs use the shared
    # ``pipeline.simind`` contract; this legacy helper now at least writes
    # atomically and is covered by the same binary read-back checks.
    bat_path.parent.mkdir(parents=True, exist_ok=True)
    temp = bat_path.with_name(f".{bat_path.name}.{os.getpid()}.tmp")
    with open(temp, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(lines))
    temp.replace(bat_path)

    return bat_path
