from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.attenuation_model_v2 import (  # noqa: E402
    AttenuationAnatomyV2,
    generate_attenuation_maps,
    hu_to_mu,
    mu_to_hu,
    select_simind_attenuation_map,
)
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.phantom_generator import PhantomConfig, PhantomGenerator  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


@pytest.fixture(scope="module")
def profile():
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    return load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


def _anatomy(shape: tuple[int, int, int] = (48, 48, 48)) -> AttenuationAnatomyV2:
    grid = GridSpecV2(shape=shape)
    z, y, x = np.indices(shape, dtype=np.float32)
    center = 0.5 * (np.asarray(shape, dtype=np.float32) - 1.0)
    body = (
        ((z - center[0]) / 20.0) ** 2
        + ((y - center[1]) / 16.0) ** 2
        + ((x - center[2]) / 19.0) ** 2
        <= 1.0
    )
    liver = (
        ((z - 26.0) / 7.0) ** 2
        + ((y - 27.0) / 7.0) ** 2
        + ((x - 31.0) / 10.0) ** 2
        <= 1.0
    ) & body
    left_lung = (
        ((z - 15.0) / 7.0) ** 2
        + ((y - 23.0) / 6.0) ** 2
        + ((x - 15.0) / 6.0) ** 2
        <= 1.0
    ) & body
    right_lung = (
        ((z - 15.0) / 7.0) ** 2
        + ((y - 23.0) / 6.0) ** 2
        + ((x - 32.0) / 6.0) ** 2
        <= 1.0
    ) & body
    lung = (left_lung | right_lung) & ~liver
    bone = (
        ((y - 32.0) / 3.0) ** 2 + ((x - 23.5) / 3.0) ** 2 <= 1.0
    ) & body & ~liver & ~lung
    inner = ndimage.binary_erosion(body, iterations=2)
    fat = body & ~inner & ~liver & ~lung & ~bone
    return AttenuationAnatomyV2(
        body_mask=body,
        liver_mask=liver,
        lung_mask=lung,
        bone_mask=bone,
        fat_mask=fat,
        affine_4x4=grid.affine_4x4,
    )


def test_mu_true_uses_correct_tissue_values_and_is_deterministic(profile) -> None:
    anatomy = _anatomy()
    first_true, first_input, metadata = generate_attenuation_maps(
        anatomy, profile, np.random.default_rng(1)
    )
    second_true, second_input, _ = generate_attenuation_maps(
        anatomy, profile, np.random.default_rng(999)
    )

    assert np.array_equal(first_true, second_true)
    assert not np.array_equal(first_input, second_input)
    assert np.all(first_true[anatomy.fat_mask] == np.float32(0.146))
    assert np.all(first_true[anatomy.liver_mask] == np.float32(0.16))
    assert np.all(first_true[anatomy.lung_mask] == np.float32(0.05))
    assert np.all(first_true[anatomy.bone_mask] == np.float32(0.30))
    assert np.all(first_true[~anatomy.body_mask] == 0.0)
    assert metadata.tissue_coefficients_cm1["fat"] == 0.146


def test_mu_input_is_seeded_reproducible_and_cannot_modify_mu_true(profile) -> None:
    anatomy = _anatomy()
    true_a, input_a, metadata = generate_attenuation_maps(
        anatomy, profile, np.random.default_rng(77)
    )
    true_b, input_b, _ = generate_attenuation_maps(
        anatomy, profile, np.random.default_rng(77)
    )

    assert np.array_equal(true_a, true_b)
    assert np.array_equal(input_a, input_b)
    assert not np.array_equal(true_a, input_a)
    assert metadata.degradation_applied_only_to_mu_input
    assert metadata.uncalibrated_ct_degradation
    assert metadata.hu_conversion == "single_energy_linear_water_reference"
    assert metadata.simind_allowed_map_key == "mu_true_140kev"


@pytest.mark.parametrize("seed", [0, 4, 19])
def test_both_maps_are_finite_nonnegative_float32_and_zero_outside_body(profile, seed: int) -> None:
    anatomy = _anatomy()
    mu_true, mu_input, _ = generate_attenuation_maps(
        anatomy, profile, np.random.default_rng(seed)
    )
    for values in (mu_true, mu_input):
        assert values.dtype == np.float32
        assert np.isfinite(values).all()
        assert np.all(values >= 0.0)
        assert np.all(values[~anatomy.body_mask] == 0.0)


def test_hu_conversion_round_trip_uses_water_reference() -> None:
    values = np.asarray((0.0, 0.05, 0.146, 0.15, 0.16, 0.30), dtype=np.float32)
    hu = mu_to_hu(values, 0.15)
    restored = hu_to_mu(hu, 0.15)
    assert hu[3] == pytest.approx(0.0, abs=1e-5)
    assert restored == pytest.approx(values, abs=2e-7)


def test_invalid_anatomy_overlap_or_outside_body_is_rejected(profile) -> None:
    anatomy = _anatomy()
    with pytest.raises(ValueError, match="must not overlap"):
        generate_attenuation_maps(
            replace(anatomy, fat_mask=anatomy.fat_mask | anatomy.liver_mask),
            profile,
            np.random.default_rng(1),
        )
    outside = np.zeros_like(anatomy.body_mask)
    outside[0, 0, 0] = True
    with pytest.raises(ValueError, match="contained"):
        generate_attenuation_maps(
            replace(anatomy, bone_mask=outside),
            profile,
            np.random.default_rng(1),
        )


def test_simind_semantic_gate_accepts_only_mu_true(profile) -> None:
    anatomy = _anatomy()
    mu_true, mu_input, _ = generate_attenuation_maps(
        anatomy, profile, np.random.default_rng(5)
    )
    assert select_simind_attenuation_map("mu_true_140kev", mu_true) is mu_true
    with pytest.raises(ValueError, match="only mu_true_140kev"):
        select_simind_attenuation_map("mu_input_140kev", mu_input)


def test_phantom_generator_v2_attenuation_adapter(profile) -> None:
    generator = PhantomGenerator(PhantomConfig())
    result = generator.generate_attenuation_v2(
        _anatomy(), profile, np.random.default_rng(88)
    )
    assert result.mu_true_140kev.shape == result.mu_input_140kev.shape
    assert result.degradation_metadata.mu_true_semantic_key == "mu_true_140kev"
