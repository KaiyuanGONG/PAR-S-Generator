from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_validation_module():
    path = REPO_ROOT / "scripts" / "validate_task3_liver_v2.py"
    spec = importlib.util.spec_from_file_location("validate_task3_liver_v2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_population_report_is_deterministic_evidence_aware_and_gated() -> None:
    module = _load_validation_module()
    profile = module.load_main_profile(REPO_ROOT)

    first = module.build_population_statistics(profile, sample_count=2000, seed=20260713)
    second = module.build_population_statistics(profile, sample_count=2000, seed=20260713)

    assert first == second
    assert first["sample_count"] == 2000
    assert first["profile_id"] == profile.profile_id
    assert first["expected"]["male_fraction"]["source_type"] == "literature_population"
    assert first["expected"]["cirrhosis_fraction"]["source_type"] == "engineering_prior"
    assert first["checks"]["banned_upper_limit_equation_used"] is False
    assert all(first["gates"].values())

