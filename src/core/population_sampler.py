from __future__ import annotations

import numpy as np

from .schemas_v2 import LiverTargetV2, PatientSampleV2, PopulationProfileV2


MORPHOLOGY_NORMAL = "normal"
MORPHOLOGY_CIRRHOTIC = "cirrhotic"


def _truncated_normal(
    rng: np.random.Generator,
    mean: float,
    sd: float,
    lower: float,
    upper: float,
) -> float:
    if not lower < upper or sd <= 0:
        raise ValueError("truncated-normal bounds and standard deviation must be valid")
    for _ in range(256):
        value = float(rng.normal(mean, sd))
        if lower <= value <= upper:
            return value
    return float(np.clip(mean, lower, upper))


def _model(profile: PopulationProfileV2, name: str) -> dict:
    value = profile.value(name)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a model specification")
    return value


def sample_patient(
    profile: PopulationProfileV2,
    rng: np.random.Generator,
    *,
    case_id: str = "unassigned",
) -> PatientSampleV2:
    """Sample a correlated adult phenotype while preserving evidence provenance."""
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be numpy.random.Generator")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id must be a non-empty string")

    joint = _model(profile, "patient_joint_model")
    male_fraction = float(profile.value("male_fraction_auxiliary"))
    age_center = float(profile.value("age_median_auxiliary_years"))
    sex = "male" if rng.random() < male_fraction else "female"
    age = _truncated_normal(
        rng,
        age_center,
        float(joint["age_sd_years"]),
        *map(float, joint["age_range_years"]),
    )
    prefix = "male" if sex == "male" else "female"
    height = _truncated_normal(
        rng,
        float(joint[f"{prefix}_height_mean_cm"]),
        float(joint[f"{prefix}_height_sd_cm"]),
        *map(float, joint["height_range_cm"]),
    )
    bmi_center = float(joint["bmi_mean"]) + float(joint["bmi_age_slope_per_year"]) * (age - age_center)
    bmi = _truncated_normal(
        rng,
        bmi_center,
        float(joint["bmi_sd"]),
        *map(float, joint["bmi_range"]),
    )
    weight = bmi * (height / 100.0) ** 2
    morphology = (
        MORPHOLOGY_CIRRHOTIC
        if rng.random() < float(profile.value("cirrhosis_prevalence"))
        else MORPHOLOGY_NORMAL
    )

    return PatientSampleV2(
        case_id=case_id.strip(),
        sex=sex,
        age_years=age,
        height_cm=height,
        weight_kg=weight,
        bmi=bmi,
        liver_morphology=morphology,
        evidence_types={
            "sex": profile.parameters["male_fraction_auxiliary"].source_type,
            "age_median": profile.parameters["age_median_auxiliary_years"].source_type,
            "joint_sampling_model": profile.parameters["patient_joint_model"].source_type,
            "morphology": profile.parameters["cirrhosis_prevalence"].source_type,
        },
    )


def sample_liver_target(
    patient: PatientSampleV2,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
) -> LiverTargetV2:
    """Sample mutually compatible liver volume, extents, position, and proxy targets."""
    if patient.liver_morphology not in {MORPHOLOGY_NORMAL, MORPHOLOGY_CIRRHOTIC}:
        raise ValueError("patient liver_morphology must be normal or cirrhotic")
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be numpy.random.Generator")

    model = _model(profile, "liver_geometry_model")
    reference = profile.value("liver_volume_reference_ml")
    mean_volume = float(reference["mean"])
    sd_volume = float(reference["sd"])
    weight_z = (patient.weight_kg - float(model["weight_center_kg"])) / float(model["weight_scale_kg"])
    height_z = (patient.height_cm - float(model["height_center_cm"])) / float(model["height_scale_cm"])
    age_z = (patient.age_years - float(model["age_center_years"])) / float(model["age_scale_years"])
    volume_z = (
        float(model["volume_weight_z_coefficient"]) * weight_z
        + float(model["volume_height_z_coefficient"]) * height_z
        + float(model["volume_age_z_coefficient"]) * age_z
        + float(model["volume_residual_z_sd"]) * float(rng.normal())
    )
    volume = float(np.clip(mean_volume + sd_volume * volume_z, *map(float, model["volume_range_ml"])))

    scale = (volume / mean_volume) ** (1.0 / 3.0)
    extent_reference = profile.value("liver_extent_reference_mm_zyx")
    reference_si, reference_ap, reference_lr = map(
        float, extent_reference["mean_at_profile_volume"]
    )
    residual_sd = np.asarray(model["extent_log_residual_sd_zyx"], dtype=np.float64)
    fill_lower, fill_upper = map(float, model["bbox_fill_fraction_range"])
    extents = np.array((reference_si, reference_ap, reference_lr), dtype=np.float64) * scale
    for _ in range(64):
        proposal = extents * np.exp(
            residual_sd * np.clip(rng.normal(size=3), -2.5, 2.5)
        )
        bbox_fill = volume * 1000.0 / float(np.prod(proposal))
        if fill_lower <= bbox_fill <= fill_upper:
            extents = proposal
            break
    si_mm, ap_mm, lr_mm = (float(value) for value in extents)

    left_reference = profile.value("left_liver_fraction_reference")
    left_fraction = _truncated_normal(
        rng,
        float(left_reference["median"]),
        float(model["left_fraction_sd"]),
        *map(float, left_reference["range"]),
    )
    caudate_probability = float(
        model[f"caudate_presence_probability_{patient.liver_morphology}"]
    )
    caudate_enabled = bool(rng.random() < caudate_probability)
    caudate_fraction = float(model["caudate_fraction_normal"]) if caudate_enabled else 0.0
    left_lateral_share = float(model["left_lateral_share"])
    s1_3_fraction = left_fraction * left_lateral_share

    if patient.liver_morphology == MORPHOLOGY_CIRRHOTIC:
        changes = profile.value("cirrhotic_segment_proxy_changes")
        s13_scale = 1.0 + float(changes["s1_to_s3"])
        s48_scale = 1.0 + float(changes["s4_to_s8"])
        denominator = s1_3_fraction * s13_scale + (1.0 - s1_3_fraction) * s48_scale
        s1_3_fraction = s1_3_fraction * s13_scale / denominator
        s4_fraction = (left_fraction * (1.0 - left_lateral_share)) * s48_scale / denominator
        left_fraction = min(
            s1_3_fraction + s4_fraction,
            float(model["cirrhotic_left_fraction_max"]),
        )
        if caudate_enabled:
            caudate_fraction = (
                caudate_fraction * (1.0 + float(changes["caudate"])) / denominator
            )

    ratio = s1_3_fraction / (1.0 - s1_3_fraction)
    centroid_mean = np.asarray(model["centroid_mean_mm_zyx"], dtype=np.float64)
    centroid_sd = np.asarray(model["centroid_sd_mm_zyx"], dtype=np.float64)
    centroid = centroid_mean + centroid_sd * np.clip(rng.normal(size=3), -2.5, 2.5)
    roughness = float(model[f"surface_roughness_target_{patient.liver_morphology}"])
    surface_field_amplitude = float(model[f"surface_field_amplitude_{patient.liver_morphology}"])

    return LiverTargetV2(
        volume_ml=volume,
        lr_mm=lr_mm,
        ap_mm=ap_mm,
        si_mm=si_mm,
        left_fraction=left_fraction,
        centroid_mm=tuple(float(value) for value in centroid),
        morphology=patient.liver_morphology,
        s1_3_to_s4_8_ratio=ratio,
        caudate_fraction=caudate_fraction,
        surface_roughness_target=roughness,
        surface_field_amplitude=surface_field_amplitude,
        caudate_enabled=caudate_enabled,
        evidence_types={
            "volume_reference": profile.parameters["liver_volume_reference_ml"].source_type,
            "volume_model": profile.parameters["liver_geometry_model"].source_type,
            "dimensions_reference": profile.parameters[
                "liver_extent_reference_mm_zyx"
            ].source_type,
            "dimensions_conditional_model": profile.parameters[
                "liver_geometry_model"
            ].source_type,
            "left_fraction": profile.parameters["left_liver_fraction_reference"].source_type,
            "morphology": profile.parameters["cirrhosis_prevalence"].source_type,
            "segment_proxy": profile.parameters["cirrhotic_segment_proxy_changes"].source_type,
            "caudate_presence": profile.parameters["liver_geometry_model"].source_type,
        },
    )
