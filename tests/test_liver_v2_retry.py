from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core import liver_geometry, population_sampler  # noqa: E402
from core.liver_geometry import LiverShapeRejectedError  # noqa: E402
from core.phantom_generator import (  # noqa: E402
    LiverCaseV2,
    LiverShapeRetryExhaustedError,
    PhantomConfig,
    PhantomGenerator,
)


def _generator() -> PhantomGenerator:
    return PhantomGenerator(PhantomConfig(volume_shape=(32, 32, 32), voxel_size_mm=4.42))


def _patch_fixed_sampling(monkeypatch):
    patient = SimpleNamespace(case_id="retry_case", liver_morphology="cirrhotic")
    target = SimpleNamespace(morphology="cirrhotic")
    calls = {"patient": 0, "target": 0}

    def sample_patient(*_args, **_kwargs):
        calls["patient"] += 1
        return patient

    def sample_target(*_args, **_kwargs):
        calls["target"] += 1
        return target

    monkeypatch.setattr(population_sampler, "sample_patient", sample_patient)
    monkeypatch.setattr(population_sampler, "sample_liver_target", sample_target)
    return patient, target, calls


def test_shape_retry_keeps_patient_and_target_and_records_deterministic_attempts(monkeypatch) -> None:
    patient, target, calls = _patch_fixed_sampling(monkeypatch)
    geometry = object()
    fit_calls = []

    def fit(candidate, _grid, *, shape_seed):
        fit_calls.append((candidate, shape_seed))
        if len(fit_calls) <= 2:
            raise LiverShapeRejectedError((f"gate_{len(fit_calls)}",), {"status": "fail"})
        return geometry

    monkeypatch.setattr(liver_geometry, "fit_liver_geometry", fit)
    case = _generator().generate_liver_v2(
        object(),
        np.random.default_rng(5),
        case_id="retry_case",
        liver_seed=12345,
        max_shape_attempts=4,
    )

    assert calls == {"patient": 1, "target": 1}
    assert all(candidate is target for candidate, _ in fit_calls)
    assert len({seed for _, seed in fit_calls}) == 3
    assert case.patient is patient
    assert case.target is target
    assert case.geometry is geometry
    assert case.sampling_provenance is not None
    assert case.sampling_provenance.accepted_attempt_index == 3
    assert case.sampling_provenance.accepted_shape_seed == fit_calls[2][1]
    assert [record.failed_gates for record in case.sampling_provenance.rejected_attempts] == [
        ("gate_1",),
        ("gate_2",),
    ]


def test_only_explicit_shape_rejection_is_retried(monkeypatch) -> None:
    _patch_fixed_sampling(monkeypatch)
    calls = 0

    def fit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("primitive fit failed")

    monkeypatch.setattr(liver_geometry, "fit_liver_geometry", fit)
    with pytest.raises(RuntimeError, match="primitive fit failed"):
        _generator().generate_liver_v2(
            object(),
            np.random.default_rng(5),
            case_id="retry_case",
            liver_seed=1,
        )
    assert calls == 1


def test_shape_retry_exhaustion_is_bounded_and_preserves_last_cause(monkeypatch) -> None:
    _patch_fixed_sampling(monkeypatch)
    calls = 0

    def fit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise LiverShapeRejectedError(("edge_gate",), {"status": "fail"})

    monkeypatch.setattr(liver_geometry, "fit_liver_geometry", fit)
    with pytest.raises(LiverShapeRetryExhaustedError) as caught:
        _generator().generate_liver_v2(
            object(),
            np.random.default_rng(5),
            case_id="retry_case",
            liver_seed=1,
            max_shape_attempts=3,
        )

    assert calls == 3
    assert caught.value.max_shape_attempts == 3
    assert len(caught.value.rejected_attempts) == 3
    assert isinstance(caught.value.__cause__, LiverShapeRejectedError)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, 33])
def test_invalid_shape_attempt_limits_fail_before_sampling(monkeypatch, value) -> None:
    calls = 0

    def sample_patient(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("must not sample")

    monkeypatch.setattr(population_sampler, "sample_patient", sample_patient)
    with pytest.raises(ValueError, match="max_shape_attempts"):
        _generator().generate_liver_v2(
            object(),
            np.random.default_rng(5),
            max_shape_attempts=value,
        )
    assert calls == 0


def test_legacy_liver_case_construction_remains_valid() -> None:
    case = LiverCaseV2(patient=object(), target=object(), geometry=object())
    assert case.sampling_provenance is None
