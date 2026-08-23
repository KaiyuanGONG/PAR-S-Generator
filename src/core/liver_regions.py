from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np


REGION_LABELS_V2: Mapping[int, str] = {
    1: "s1_caudate_proxy",
    2: "s2_3_left_lateral_proxy",
    3: "s4_left_medial_proxy",
    4: "s5_8_right_anterior_proxy",
    5: "s5_8_right_posterior_proxy",
}


@dataclass(frozen=True)
class LiverRegionsV2:
    labels: np.ndarray
    region_names: Mapping[int, str]
    region_voxel_counts: Mapping[str, int]
    left_fraction: float
    s1_3_to_s4_8_ratio: float
    caudate_fraction: float
    definition: str = "couinaud_proxy_without_vascular_tree"


def _validate_affine(affine_4x4: np.ndarray) -> np.ndarray:
    affine = np.asarray(affine_4x4, dtype=np.float64)
    if affine.shape != (4, 4) or not np.isfinite(affine).all():
        raise ValueError("affine_4x4 must be a finite 4x4 matrix")
    if not np.allclose(affine[3], (0.0, 0.0, 0.0, 1.0), atol=1e-10):
        raise ValueError("affine_4x4 must have a homogeneous last row")
    linear = affine[:3, :3]
    spacing = np.linalg.norm(linear, axis=0)
    if np.any(spacing <= 0) or abs(np.linalg.det(linear)) <= 1e-12:
        raise ValueError("affine_4x4 must be invertible")
    unit = linear / spacing
    if not np.allclose(unit.T @ unit, np.eye(3), atol=1e-6):
        raise ValueError("affine_4x4 voxel axes must be orthogonal")
    return affine


def _assign_lowest_score(labels: np.ndarray, indices: np.ndarray, score: np.ndarray, count: int, label: int) -> None:
    if count <= 0:
        return
    selected = indices[np.argsort(score, kind="stable")[:count]]
    labels[tuple(selected.T)] = label


def build_liver_regions(
    liver_mask: np.ndarray,
    affine_4x4: np.ndarray,
    *,
    target_left_fraction: float,
    target_s1_3_to_s4_8_ratio: float,
    caudate_mask: np.ndarray | None = None,
) -> LiverRegionsV2:
    """Partition a liver into five non-overlapping spatial Couinaud proxies."""
    liver = np.asarray(liver_mask, dtype=bool)
    if liver.ndim != 3 or not liver.any():
        raise ValueError("liver_mask must be a non-empty 3D array")
    affine = _validate_affine(affine_4x4)
    if not 0.10 <= float(target_left_fraction) <= 0.65:
        raise ValueError("target left fraction must be within [0.10, 0.65]")
    if not 0.05 <= float(target_s1_3_to_s4_8_ratio) <= 1.50:
        raise ValueError("target S1-3/S4-8 ratio must be within [0.05, 1.50]")

    if caudate_mask is None:
        caudate = np.zeros_like(liver)
    else:
        caudate = np.asarray(caudate_mask, dtype=bool)
        if caudate.shape != liver.shape:
            raise ValueError("caudate_mask must match liver_mask shape")
        if np.any(caudate & ~liver):
            raise ValueError("caudate_mask must be contained in liver_mask")

    indices = np.argwhere(liver)
    total = int(len(indices))
    target_left_count = int(round(float(target_left_fraction) * total))
    target_s13_fraction = float(target_s1_3_to_s4_8_ratio) / (1.0 + float(target_s1_3_to_s4_8_ratio))
    target_s13_count = int(round(target_s13_fraction * total))
    caudate_count = int(caudate.sum())
    if caudate_count > target_left_count or caudate_count > target_s13_count:
        raise ValueError("caudate_mask is too large for requested left and S1-3 targets")
    if target_s13_count >= target_left_count:
        raise ValueError("S1-3 target must be smaller than target left fraction")

    labels = np.zeros(liver.shape, dtype=np.uint8)
    labels[caudate] = 1
    noncaudate_indices = np.argwhere(liver & ~caudate)
    world = noncaudate_indices @ affine[:3, :3].T + affine[:3, 3]
    cantlie_score = world[:, 2] + 0.08 * world[:, 0]
    left_needed = target_left_count - caudate_count
    left_order = np.argsort(cantlie_score, kind="stable")
    left_indices = noncaudate_indices[left_order[:left_needed]]
    right_indices = noncaudate_indices[left_order[left_needed:]]

    left_world = left_indices @ affine[:3, :3].T + affine[:3, 3]
    lateral_score = left_world[:, 2] + 0.04 * left_world[:, 0]
    s23_needed = target_s13_count - caudate_count
    _assign_lowest_score(labels, left_indices, lateral_score, s23_needed, 2)
    unassigned_left = left_indices[labels[tuple(left_indices.T)] == 0]
    labels[tuple(unassigned_left.T)] = 3

    right_world = right_indices @ affine[:3, :3].T + affine[:3, 3]
    anterior_score = right_world[:, 1] - 0.05 * right_world[:, 0]
    anterior_count = len(right_indices) // 2
    _assign_lowest_score(labels, right_indices, anterior_score, anterior_count, 4)
    unassigned_right = right_indices[labels[tuple(right_indices.T)] == 0]
    labels[tuple(unassigned_right.T)] = 5

    if not np.array_equal(labels > 0, liver):
        raise RuntimeError("region proxy partition did not exactly cover liver_mask")
    counts = {
        name: int(np.count_nonzero(labels == label_id))
        for label_id, name in REGION_LABELS_V2.items()
    }
    left_count = counts[REGION_LABELS_V2[1]] + counts[REGION_LABELS_V2[2]] + counts[REGION_LABELS_V2[3]]
    s13_count = counts[REGION_LABELS_V2[1]] + counts[REGION_LABELS_V2[2]]
    s48_count = total - s13_count
    return LiverRegionsV2(
        labels=labels,
        region_names=dict(REGION_LABELS_V2),
        region_voxel_counts=counts,
        left_fraction=left_count / total,
        s1_3_to_s4_8_ratio=s13_count / s48_count,
        caudate_fraction=caudate_count / total,
    )
