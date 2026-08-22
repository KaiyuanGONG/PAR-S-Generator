"""Deterministic, tumor-first limited-scope activity construction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import distance_transform_edt


class LimitedActivityError(ValueError):
    """Raised when an activity adapter contract cannot be satisfied."""


@dataclass(frozen=True)
class LimitedActivityOutput:
    activity: np.ndarray
    perfusion_mask: np.ndarray
    selected_territory: str
    tumor_records: list[dict[str, Any]]
    contract: dict[str, Any]


_CANDIDATE_ORDER = ("whole_liver", "right_lobar", "left_lobar")
_AUTO_TERRITORY_POLICY = "auto_equal_feasible"
_TERRITORY_POLICIES = (_AUTO_TERRITORY_POLICY, *_CANDIDATE_ORDER)
_RAW_WEIGHTS = {name: 1.0 / 3.0 for name in _CANDIDATE_ORDER}
_RING_DESCRIPTION = "1-3 voxel Euclidean distance inside territory, excluding all tumors"
_DIAMETER_ALIAS_RTOL = 1e-6
_DIAMETER_ALIAS_ATOL = 1e-6


def _adapter_source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def derive_domain_seed(activity_seed: int, domain: str, index: int = 0) -> int:
    """Derive a deterministic RNG seed without consuming another RNG stream."""
    payload = f"pars-hybrid-v2-limited-activity-v1|{activity_seed}|{domain}|{index}"
    return int.from_bytes(hashlib.sha256(payload.encode("ascii")).digest()[:8], "big")


def _as_mask(name: str, value: np.ndarray, shape: tuple[int, ...] | None = None) -> np.ndarray:
    mask = np.asarray(value, dtype=bool)
    if mask.ndim != 3:
        raise LimitedActivityError(f"{name} must be a three-dimensional mask")
    if shape is not None and mask.shape != shape:
        raise LimitedActivityError(f"{name} shape does not match liver mask")
    return mask


def _candidate_masks(
    liver_mask: np.ndarray, left_mask: np.ndarray, right_mask: np.ndarray
) -> dict[str, np.ndarray]:
    if not np.all(left_mask <= liver_mask) or not np.all(right_mask <= liver_mask):
        raise LimitedActivityError("lobar masks must lie inside liver mask")
    return {
        "whole_liver": liver_mask.copy(),
        "right_lobar": right_mask.copy(),
        "left_lobar": left_mask.copy(),
    }


def _ring_for_tumor(
    tumor_mask: np.ndarray, tumor_union: np.ndarray, territory: np.ndarray
) -> np.ndarray:
    distances = distance_transform_edt(~tumor_mask)
    return (distances >= 1.0) & (distances <= 3.0) & territory & ~tumor_union


def _feasible_candidates(
    candidates: dict[str, np.ndarray], tumor_masks: list[np.ndarray], tumor_union: np.ndarray
) -> list[str]:
    feasible: list[str] = []
    for name in _CANDIDATE_ORDER:
        territory = candidates[name]
        if not np.any(territory):
            continue
        if tumor_masks:
            if not np.all(tumor_union <= territory):
                continue
            if any(not np.any(_ring_for_tumor(tumor, tumor_union, territory)) for tumor in tumor_masks):
                continue
        feasible.append(name)
    return feasible


def _validated_records(tumor_records: list[dict[str, Any]] | None, count: int) -> list[dict[str, Any]]:
    if count == 0:
        if tumor_records not in (None, []):
            raise LimitedActivityError("true-negative cases must have no tumor records")
        return []
    if tumor_records is None:
        raise LimitedActivityError("positive tumor records require effective_diameter_mm")
    if len(tumor_records) != count:
        raise LimitedActivityError("tumor_records must have one record per tumor mask")
    records = [dict(record) for record in tumor_records]
    for record in records:
        if "effective_diameter_mm" not in record:
            raise LimitedActivityError("positive tumor records require effective_diameter_mm")
        try:
            diameter = float(record["effective_diameter_mm"])
        except (TypeError, ValueError) as exc:
            raise LimitedActivityError("tumor effective diameter must be within 10-60 mm") from exc
        if not np.isfinite(diameter) or not 10.0 <= diameter <= 60.0:
            raise LimitedActivityError("tumor effective diameter must be within 10-60 mm")
        for key in ("realized_effective_diameter_mm", "diameter_mm"):
            if key not in record:
                continue
            try:
                alias = float(record[key])
            except (TypeError, ValueError) as exc:
                raise LimitedActivityError("tumor diameter aliases must agree") from exc
            if not np.isfinite(alias) or not np.isclose(
                alias, diameter, rtol=_DIAMETER_ALIAS_RTOL, atol=_DIAMETER_ALIAS_ATOL
            ):
                raise LimitedActivityError("tumor diameter aliases must agree")
    return records


def _validated_tumors(tumors: list[np.ndarray], liver_mask: np.ndarray) -> np.ndarray:
    if len(tumors) > 5:
        raise LimitedActivityError("positive cases must contain 1-5 tumors")
    union = np.zeros(liver_mask.shape, dtype=bool)
    for tumor in tumors:
        if not np.any(tumor):
            raise LimitedActivityError("positive tumor masks must be nonempty")
        if not np.all(tumor <= liver_mask):
            raise LimitedActivityError("tumor masks must lie inside liver mask")
        if np.any(union & tumor):
            raise LimitedActivityError("tumor masks must not overlap")
        union |= tumor
    return union


def _valid_total_counts(value: Any) -> float:
    try:
        total = float(value)
    except (TypeError, ValueError) as exc:
        raise LimitedActivityError("total counts must be finite and positive") from exc
    if not np.isfinite(total) or total <= 0:
        raise LimitedActivityError("total counts must be finite and positive")
    return total


def _persisted_float32_total_bound(activity: np.ndarray) -> float:
    """Return the target-independent cast/summation bound for persisted activity.

    The builder normalizes in float64 then persists float32.  For every finite,
    nonzero persisted value, its pre-cast value can be at most half the larger
    adjacent float32 spacing away.  Summing those half-ULPs gives the complete
    float32 cast-roundoff allowance.  Widening float32 operands to float64 is
    exact, so the only remaining allowance is the standard float64 addition
    bound (gamma_n times the persisted absolute sum, plus one final ULP).
    This is derived solely from the persisted array and deliberately does not
    scale with the requested total count.
    """
    finite_nonzero = activity[np.isfinite(activity) & (activity != 0)]
    if finite_nonzero.size == 0:
        return 0.0
    values64 = finite_nonzero.astype(np.float64, copy=False)
    next_up = np.nextafter(finite_nonzero, np.float32(np.inf)).astype(np.float64)
    next_down = np.nextafter(finite_nonzero, np.float32(-np.inf)).astype(np.float64)
    up_gap = next_up - values64
    down_gap = values64 - next_down
    adjacent_spacing = np.maximum(
        np.where(np.isfinite(up_gap), up_gap, 0.0),
        np.where(np.isfinite(down_gap), down_gap, 0.0),
    )
    cast_bound = 0.5 * float(np.sum(adjacent_spacing, dtype=np.float64))

    absolute_sum = float(np.sum(np.abs(values64), dtype=np.float64))
    unit_roundoff = np.finfo(np.float64).eps / 2.0
    gamma_n = (finite_nonzero.size * unit_roundoff) / (1.0 - finite_nonzero.size * unit_roundoff)
    summation_bound = gamma_n * absolute_sum + abs(float(np.spacing(absolute_sum)))
    return cast_bound + summation_bound


def build_limited_activity(
    *,
    liver_mask: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
    tumor_masks: list[np.ndarray],
    activity_seed: int,
    residual_bg: float,
    gradient_gain: float,
    total_counts: float,
    tumor_records: list[dict[str, Any]] | None = None,
    target_tnrs: list[float] | None = None,
    territory_policy: str = _AUTO_TERRITORY_POLICY,
) -> LimitedActivityOutput:
    """Build one exact, feasible perfusion territory and its normalized activity."""
    liver = _as_mask("liver_mask", liver_mask)
    left = _as_mask("left_mask", left_mask, liver.shape)
    right = _as_mask("right_mask", right_mask, liver.shape)
    tumors = [_as_mask("tumor_mask", tumor, liver.shape) for tumor in tumor_masks]
    tumor_union = _validated_tumors(tumors, liver)
    if not np.isfinite(residual_bg) or residual_bg < 0:
        raise LimitedActivityError("residual background must be finite and non-negative")
    if not np.isfinite(gradient_gain) or gradient_gain < 0:
        raise LimitedActivityError("gradient gain must be finite and non-negative")
    total_counts = _valid_total_counts(total_counts)
    records = _validated_records(tumor_records, len(tumors))
    if target_tnrs is not None and len(target_tnrs) != len(tumors):
        raise LimitedActivityError("target_tnrs must have one value per tumor mask")

    candidates = _candidate_masks(liver, left, right)
    feasible = _feasible_candidates(candidates, tumors, tumor_union)
    if not feasible:
        raise LimitedActivityError("no feasible territory with a background ring")
    conditional_weights = {name: 1.0 / len(feasible) for name in feasible}
    if territory_policy not in _TERRITORY_POLICIES:
        raise LimitedActivityError("unknown territory policy")
    if territory_policy == _AUTO_TERRITORY_POLICY:
        territory_rng = np.random.default_rng(derive_domain_seed(activity_seed, "territory"))
        selected = str(
            territory_rng.choice(feasible, p=[conditional_weights[name] for name in feasible])
        )
    else:
        if territory_policy not in feasible:
            raise LimitedActivityError(f"requested territory is not feasible: {territory_policy}")
        selected = territory_policy
    perfusion_mask = candidates[selected].copy()

    activity = np.zeros(liver.shape, dtype=np.float64)
    axial = np.broadcast_to(
        np.linspace(-1.0, 1.0, liver.shape[0], dtype=np.float64)[:, None, None], liver.shape
    )
    activity[perfusion_mask] = residual_bg * (1.0 + gradient_gain * axial[perfusion_mask])
    if np.any(activity < 0) or not np.all(np.isfinite(activity)):
        raise LimitedActivityError("background activity is not finite and non-negative")

    output_records: list[dict[str, Any]] = []
    for index, (tumor, record) in enumerate(zip(tumors, records)):
        ring = _ring_for_tumor(tumor, tumor_union, perfusion_mask)
        if not np.any(ring):
            raise LimitedActivityError("no feasible background ring for tumor")
        if target_tnrs is None:
            target = float(np.random.default_rng(derive_domain_seed(activity_seed, "tnr", index)).uniform(2.0, 8.0))
        else:
            target = float(target_tnrs[index])
        if not np.isfinite(target) or not 2.0 <= target <= 8.0:
            raise LimitedActivityError("target TNR must be within 2-8")
        ring_mean = float(activity[ring].mean())
        if not np.isfinite(ring_mean) or ring_mean <= 0.0:
            raise LimitedActivityError("background ring has no positive activity")
        # Keep inclusive endpoint requests inside the contractual interval after
        # floating-point mean and final-count normalization operations.
        lesion_ratio = target
        if target == 2.0:
            lesion_ratio = 2.0 + 1e-5
        elif target == 8.0:
            lesion_ratio = 8.0 - 1e-5
        activity[tumor] = lesion_ratio * ring_mean
        enriched = dict(record)
        enriched["target_ring_tnr"] = target
        enriched["actual_ring_tnr"] = float(activity[tumor].mean() / activity[ring].mean())
        output_records.append(enriched)

    total = float(activity.sum())
    if not np.isfinite(total) or total <= 0.0:
        raise LimitedActivityError("activity has no positive support")
    activity *= total_counts / total
    activity = np.ascontiguousarray(activity, dtype=np.float32)
    for tumor, record in zip(tumors, output_records):
        ring = _ring_for_tumor(tumor, tumor_union, perfusion_mask)
        record["actual_ring_tnr"] = float(
            activity[tumor].astype(np.float64).mean() / activity[ring].astype(np.float64).mean()
        )
    contract = {
        "candidates": list(_CANDIDATE_ORDER),
        "raw_weights": dict(_RAW_WEIGHTS),
        "feasible_candidates": feasible,
        "conditional_weights": conditional_weights,
        "selected_territory": selected,
        "territory_policy": territory_policy,
        "coverage_fraction": 1.0,
        "mismatch_challenge": False,
        "background_ring_definition": _RING_DESCRIPTION,
        "is_true_negative": not tumors,
        "total_counts": total_counts,
        "adapter_source_sha256": _adapter_source_sha256(),
    }
    verify_limited_activity(
        liver_mask=liver,
        left_mask=left,
        right_mask=right,
        tumor_masks=tumors,
        tumor_records=output_records,
        activity=activity,
        perfusion_mask=perfusion_mask,
        selected_territory=selected,
        contract=contract,
        total_counts=total_counts,
    )
    return LimitedActivityOutput(activity, perfusion_mask, selected, output_records, contract)


def verify_limited_activity(
    *,
    liver_mask: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
    tumor_masks: list[np.ndarray],
    tumor_records: list[dict[str, Any]],
    activity: np.ndarray,
    perfusion_mask: np.ndarray,
    selected_territory: str,
    contract: dict[str, Any],
    total_counts: float,
) -> None:
    """Recompute the limited-activity requirements from arrays and metadata."""
    liver = _as_mask("liver_mask", liver_mask)
    left = _as_mask("left_mask", left_mask, liver.shape)
    right = _as_mask("right_mask", right_mask, liver.shape)
    tumors = [_as_mask("tumor_mask", tumor, liver.shape) for tumor in tumor_masks]
    perfusion = _as_mask("perfusion_mask", perfusion_mask, liver.shape)
    raw_values = np.asarray(activity)
    if raw_values.shape != liver.shape:
        raise LimitedActivityError("activity shape does not match liver mask")
    if raw_values.dtype != np.float32 or not raw_values.flags.c_contiguous:
        raise LimitedActivityError("activity must be C-contiguous float32")
    values = raw_values.astype(np.float64, copy=False)
    if not np.all(np.isfinite(values)) or np.any(values < 0):
        raise LimitedActivityError("activity must be finite and non-negative")
    candidates = _candidate_masks(liver, left, right)
    if selected_territory not in candidates:
        raise LimitedActivityError("unknown selected territory")
    if not np.array_equal(perfusion, candidates[selected_territory]):
        raise LimitedActivityError("perfusion coverage must exactly match selected territory")
    tumor_union = _validated_tumors(tumors, liver)
    if tumors and not np.all(tumor_union <= perfusion):
        raise LimitedActivityError("tumor coverage is incomplete")
    records = _validated_records(tumor_records, len(tumors))
    if contract.get("is_true_negative") is not (not tumors):
        raise LimitedActivityError("negative semantics disagree with tumor arrays")
    if contract.get("candidates") != list(_CANDIDATE_ORDER):
        raise LimitedActivityError("candidate contract disagrees with adapter")
    if contract.get("raw_weights") != _RAW_WEIGHTS:
        raise LimitedActivityError("raw-weight contract disagrees with adapter")
    feasible = _feasible_candidates(candidates, tumors, tumor_union)
    if contract.get("feasible_candidates") != feasible:
        raise LimitedActivityError("feasible-candidate contract disagrees with arrays")
    expected_conditional = {name: 1.0 / len(feasible) for name in feasible}
    if contract.get("conditional_weights") != expected_conditional:
        raise LimitedActivityError("conditional-weight contract disagrees with arrays")
    if selected_territory not in feasible:
        raise LimitedActivityError("selected territory is not feasible")
    if contract.get("selected_territory") != selected_territory:
        raise LimitedActivityError("metadata selected territory disagrees with arrays")
    territory_policy = contract.get("territory_policy")
    if territory_policy not in _TERRITORY_POLICIES:
        raise LimitedActivityError("metadata territory policy disagrees with adapter contract")
    if territory_policy != _AUTO_TERRITORY_POLICY and territory_policy != selected_territory:
        raise LimitedActivityError("exact territory policy disagrees with selected territory")
    if contract.get("adapter_source_sha256") != _adapter_source_sha256():
        raise LimitedActivityError("adapter source SHA disagrees with current adapter")
    coverage = 1.0 if not tumors else float(np.count_nonzero(tumor_union & perfusion) / np.count_nonzero(tumor_union))
    if contract.get("coverage_fraction") != coverage:
        raise LimitedActivityError("metadata coverage disagrees with arrays")
    if contract.get("mismatch_challenge") is not False:
        raise LimitedActivityError("mismatch challenge must be false")
    if contract.get("background_ring_definition") != _RING_DESCRIPTION:
        raise LimitedActivityError("background ring metadata disagrees with adapter contract")
    authoritative_total = _valid_total_counts(total_counts)
    contract_total = _valid_total_counts(contract.get("total_counts"))
    if contract_total != authoritative_total:
        raise LimitedActivityError("contract total counts disagrees with authoritative total counts")
    array_total = float(np.sum(raw_values, dtype=np.float64))
    total_bound = _persisted_float32_total_bound(raw_values)
    if abs(array_total - authoritative_total) > total_bound:
        raise LimitedActivityError("activity total disagrees with total counts")
    if np.any(values[~perfusion] != 0):
        raise LimitedActivityError("activity exists outside perfusion territory")
    for tumor, record in zip(tumors, records):
        ring = _ring_for_tumor(tumor, tumor_union, perfusion)
        if not np.any(ring):
            raise LimitedActivityError("background ring is empty")
        actual = float(values[tumor].mean() / values[ring].mean())
        target = record.get("target_ring_tnr")
        reported = record.get("actual_ring_tnr")
        if target is None or reported is None:
            raise LimitedActivityError("tumor TNR metadata is missing")
        if not 2.0 <= float(target) <= 8.0 or not 2.0 <= actual <= 8.0:
            raise LimitedActivityError("ring TNR must be within 2-8")
        if abs(actual - float(target)) / float(target) > 0.02:
            raise LimitedActivityError("realized ring TNR exceeds 2 percent error")
        if not np.isclose(float(reported), actual, rtol=1e-10, atol=1e-10):
            raise LimitedActivityError("tumor TNR metadata disagrees with activity arrays")
