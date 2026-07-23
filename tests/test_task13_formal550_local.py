from __future__ import annotations

from pathlib import Path
import sys

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from core import pilot_v2


def test_completed_metadata_supports_tumor_negative_case() -> None:
    tumor_union = np.zeros((4, 4, 4), dtype=bool)
    perfusion = np.zeros_like(tumor_union)
    perfusion[1:3, 1:3, 1:3] = True

    coverage, fraction_perfused = pilot_v2._tumor_perfusion_fractions(
        tumor_union,
        perfusion,
    )

    assert coverage == 1.0
    assert fraction_perfused == 0.0

