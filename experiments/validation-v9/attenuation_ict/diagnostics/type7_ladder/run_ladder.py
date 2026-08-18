"""Isolated SIMIND type-7 stored-value response diagnostic.

This diagnostic intentionally makes no absolute count-rate claim.  For each
stored XCAT attenuation value it compares Scattwin's primary and air images
from the same simulation and records the mode-3 aligned-mu readback.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
SOURCE_INPUT = ROOT / "input" / "water_column_mu_0p15_act_av.bin"
ATTENUATION_INPUT = ROOT / "input" / "water_column_mu_0p15_atn_av.bin"
SMC_INPUT = ROOT / "input" / "attenuation_ict.smc"
SIMIND_EXE = ROOT.parents[2] / "simind" / "simind.exe"
SHAPE = (128, 128, 128)
PATH_LENGTH_VOX = 20
VALUES = (0.0, 0.04, 0.05, 0.06, 0.0663, 0.068, 0.075, 0.0795, 0.08, 0.10, 0.12, 0.15)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_label(value: float) -> str:
    return f"{value:.4f}".replace(".", "p")


def projection_summary(path: Path) -> dict:
    values = np.fromfile(path, dtype=np.float32)
    expected = 60 * 128 * 128
    if values.size != expected or not np.isfinite(values).all() or np.any(values < 0):
        raise RuntimeError(f"Invalid projection component: {path}")
    return {
        "path": str(path.resolve()),
        "sum": float(values.sum(dtype=np.float64)),
        "nonzero": int(np.count_nonzero(values)),
        "sha256": sha256(path),
    }


def main() -> None:
    if not SIMIND_EXE.exists():
        raise FileNotFoundError(SIMIND_EXE)
    WORK.mkdir(parents=True, exist_ok=True)
    smc = WORK / "attenuation_ict.smc"
    if not smc.exists():
        shutil.copy2(SMC_INPUT, smc)
    win = WORK / "attenuation_ict.win"
    if not win.exists():
        win.write_text("126.0,154.0,0\n", encoding="ascii")

    base_attenuation = np.fromfile(ATTENUATION_INPUT, dtype=np.float32).reshape(SHAPE)
    support = base_attenuation > 0
    source_bytes = SOURCE_INPUT.read_bytes()
    results = []
    for index, stored_value in enumerate(VALUES):
        label = safe_label(stored_value)
        input_stem = f"input_{label}"
        output_stem = f"ladder_{label}"
        source_path = WORK / f"{input_stem}_act_av.bin"
        attenuation_path = WORK / f"{input_stem}_atn_av.bin"
        if not source_path.exists():
            source_path.write_bytes(source_bytes)
        if not attenuation_path.exists():
            attenuation = np.zeros(SHAPE, dtype=np.float32)
            attenuation[support] = np.float32(stored_value)
            attenuation.tofile(attenuation_path)

        required = {
            component: WORK / f"{output_stem}_{component}_w1.a00"
            for component in ("air", "pri", "sca", "tot")
        }
        result_path = WORK / f"{output_stem}.res"
        if not all(path.exists() for path in (*required.values(), result_path)):
            occupied = sorted(WORK.glob(f"{output_stem}*"))
            if occupied:
                raise FileExistsError(
                    f"Refusing to mix partial artifacts for {output_stem}: {occupied}"
                )
            command = [
                str(SIMIND_EXE),
                "attenuation_ict",
                output_stem,
                f"/FS:{input_stem}",
                f"/FD:{input_stem}",
                "/NN:3000",
                "/IN:x22,3x/84:1/CA:2",
                "/RR:9630",
            ]
            completed = subprocess.run(
                command,
                cwd=WORK,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )
            (WORK / f"{output_stem}.log").write_text(
                completed.stdout + completed.stderr,
                encoding="utf-8",
            )
            if completed.returncode != 0:
                raise RuntimeError(f"SIMIND failed for {stored_value}: {completed.returncode}")
        summaries = {name: projection_summary(path) for name, path in required.items()}
        ict_path = WORK / f"{output_stem}.ict"
        ict = np.fromfile(ict_path, dtype=np.float32)
        positive = ict[np.isfinite(ict) & (ict > 0)]
        readback_mu = float(np.median(positive)) if positive.size else 0.0
        primary_air_ratio = summaries["pri"]["sum"] / summaries["air"]["sum"]
        results.append(
            {
                "stored_value": stored_value,
                "expected_if_mu_times_voxel": math.exp(-stored_value * PATH_LENGTH_VOX),
                "mode3_readback_mu_cm_inverse": readback_mu,
                "primary_air_ratio": primary_air_ratio,
                "components": summaries,
                "res": str(result_path.resolve()),
                "res_sha256": sha256(result_path),
                "input_source_sha256": sha256(source_path),
                "input_attenuation_sha256": sha256(attenuation_path),
            }
        )

    payload = {
        "purpose": "Diagnose the type-7 stored-value response without absolute cps/MBq claims.",
        "shape": list(SHAPE),
        "path_length_vox": PATH_LENGTH_VOX,
        "nn_multiplier": 3000,
        "rr_seed": 9630,
        "observable": "same-run Scattwin primary_w1 sum divided by air_w1 sum",
        "results": results,
    }
    (WORK / "analysis.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for row in results:
        print(
            f"stored={row['stored_value']:.4f} "
            f"readback_mu={row['mode3_readback_mu_cm_inverse']:.6f} "
            f"primary/air={row['primary_air_ratio']:.6f} "
            f"expected={row['expected_if_mu_times_voxel']:.6f}"
        )


if __name__ == "__main__":
    main()
