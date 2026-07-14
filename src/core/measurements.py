from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError, distance
from skimage import measure


@dataclass(frozen=True)
class LiverMetricsV2:
    voxel_count: int
    volume_ml: float
    centroid_world_mm: tuple[float, float, float]
    extent_mm_zyx: tuple[float, float, float]
    principal_axes_mm: tuple[float, float, float]
    recist_3d_mm: float
    equivalent_diameter_mm: float
    surface_area_mm2: float
    sphericity: float
    surface_roughness: float


@dataclass(frozen=True)
class LesionMetricsV2:
    instance_id: int
    voxel_count: int
    volume_ml: float
    centroid_world_mm: tuple[float, float, float]
    extent_mm_zyx: tuple[float, float, float]
    principal_axes_mm: tuple[float, float, float]
    recist_3d_mm: float
    equivalent_diameter_mm: float
    surface_area_mm2: float
    sphericity: float
    surface_roughness: float


@dataclass(frozen=True)
class PathLengthStatsV2:
    mean_mm: float
    p05_mm: float
    p50_mm: float
    p95_mm: float


@dataclass(frozen=True)
class PathLengthMetricsV2:
    angles_deg: tuple[float, ...]
    body: tuple[PathLengthStatsV2, ...]
    liver: tuple[PathLengthStatsV2, ...]
    support_definition: str = "positive_rays_per_mask"


def _validate_affine(affine_4x4: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    affine = np.asarray(affine_4x4, dtype=np.float64)
    if affine.shape != (4, 4) or not np.isfinite(affine).all():
        raise ValueError("affine_4x4 must be a finite 4x4 matrix")
    if not np.allclose(affine[3], (0.0, 0.0, 0.0, 1.0), atol=1e-10):
        raise ValueError("affine_4x4 must have homogeneous last row [0, 0, 0, 1]")
    linear = affine[:3, :3]
    spacing = np.linalg.norm(linear, axis=0)
    if np.any(spacing <= 0) or abs(np.linalg.det(linear)) <= 1e-12:
        raise ValueError("affine_4x4 must have non-zero voxel spacing")
    unit_columns = linear / spacing
    gram = unit_columns.T @ unit_columns
    if not np.allclose(gram, np.eye(3), atol=1e-6):
        raise ValueError("affine_4x4 voxel axes must be orthogonal")
    return affine, spacing


def _validate_mask(mask: np.ndarray, name: str, require_nonempty: bool = True) -> np.ndarray:
    array = np.asarray(mask)
    if array.ndim != 3:
        raise ValueError(f"{name} must be a 3D array")
    result = array.astype(bool, copy=False)
    if require_nonempty and not result.any():
        raise ValueError(f"{name} is empty")
    return result


def _world_points(indices_zyx: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return indices_zyx @ affine[:3, :3].T + affine[:3, 3]


def _voxel_width_along(direction: np.ndarray, linear: np.ndarray) -> float:
    return float(np.abs(direction @ linear).sum())


def _principal_axes_mm(points: np.ndarray, linear: np.ndarray) -> tuple[float, float, float]:
    if len(points) == 1:
        return tuple(sorted(np.linalg.norm(linear, axis=0), reverse=True))
    covariance = np.cov(points, rowvar=False, bias=True)
    _, eigenvectors = np.linalg.eigh(covariance)
    axes = []
    for direction in eigenvectors.T:
        projections = points @ direction
        axes.append(float(np.ptp(projections) + _voxel_width_along(direction, linear)))
    return tuple(sorted(axes, reverse=True))


def _feret_diameter_mm(mask: np.ndarray, affine: np.ndarray) -> float:
    boundary = mask & ~ndimage.binary_erosion(mask)
    points = _world_points(np.argwhere(boundary), affine)
    if len(points) <= 1:
        return float(np.linalg.norm(affine[:3, :3], axis=0).max())
    if len(points) >= 4:
        try:
            hull = ConvexHull(points)
            points = points[hull.vertices]
        except QhullError:
            pass

    max_sq = -1.0
    pair = (points[0], points[-1])
    chunk_size = 512
    for start in range(0, len(points), chunk_size):
        squared = distance.cdist(points[start : start + chunk_size], points, metric="sqeuclidean")
        flat_index = int(np.argmax(squared))
        value = float(squared.flat[flat_index])
        if value > max_sq:
            local, other = np.unravel_index(flat_index, squared.shape)
            max_sq = value
            pair = (points[start + local], points[other])
    center_distance = math.sqrt(max_sq)
    if center_distance == 0:
        return float(np.linalg.norm(affine[:3, :3], axis=0).max())
    direction = (pair[0] - pair[1]) / center_distance
    return center_distance + _voxel_width_along(direction, affine[:3, :3])


def _ellipsoidal_radial_roughness(points: np.ndarray) -> float:
    """Return RMS radial residual after fitting a centred general ellipsoid."""
    centered = points - points.mean(axis=0)
    design = np.column_stack(
        (
            centered[:, 0] ** 2,
            centered[:, 1] ** 2,
            centered[:, 2] ** 2,
            2.0 * centered[:, 0] * centered[:, 1],
            2.0 * centered[:, 0] * centered[:, 2],
            2.0 * centered[:, 1] * centered[:, 2],
        )
    )
    coefficients, _, _, _ = np.linalg.lstsq(design, np.ones(len(points)), rcond=None)
    quadratic = np.array(
        (
            (coefficients[0], coefficients[3], coefficients[4]),
            (coefficients[3], coefficients[1], coefficients[5]),
            (coefficients[4], coefficients[5], coefficients[2]),
        )
    )
    radial_squared = np.einsum("ni,ij,nj->n", centered, quadratic, centered)
    if not np.isfinite(radial_squared).all() or np.any(radial_squared <= 0):
        return 0.0
    radial = np.sqrt(radial_squared)
    radial /= np.median(radial)
    return float(np.sqrt(np.mean((radial - 1.0) ** 2)))


def _surface_metrics(mask: np.ndarray, affine: np.ndarray, volume_mm3: float) -> tuple[float, float, float]:
    padded = np.pad(mask.astype(np.uint8), 1)
    vertices, faces, _, _ = measure.marching_cubes(padded, level=0.5, allow_degenerate=False)
    vertices -= 1.0
    world_vertices = _world_points(vertices, affine)
    triangles = world_vertices[faces]
    cross_products = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    surface_area = float(0.5 * np.linalg.norm(cross_products, axis=1).sum())
    sphere_area_term = math.pi ** (1.0 / 3.0) * (6.0 * volume_mm3) ** (2.0 / 3.0)
    sphericity = float(np.clip(sphere_area_term / surface_area, 0.0, 1.0))
    radial_residual = _ellipsoidal_radial_roughness(world_vertices)
    roughness = radial_residual + 0.25 * (1.0 - sphericity)
    return surface_area, sphericity, roughness


def _measure_shape(mask: np.ndarray, affine_4x4: np.ndarray) -> dict:
    mask = _validate_mask(mask, "mask")
    affine, spacing = _validate_affine(affine_4x4)
    indices = np.argwhere(mask)
    points = _world_points(indices, affine)
    voxel_count = int(len(indices))
    voxel_volume_mm3 = abs(float(np.linalg.det(affine[:3, :3])))
    volume_mm3 = voxel_count * voxel_volume_mm3
    extents = tuple(float((indices[:, axis].max() - indices[:, axis].min() + 1) * spacing[axis]) for axis in range(3))
    surface_area, sphericity, roughness = _surface_metrics(mask, affine, volume_mm3)
    return {
        "voxel_count": voxel_count,
        "volume_ml": volume_mm3 / 1000.0,
        "centroid_world_mm": tuple(float(value) for value in points.mean(axis=0)),
        "extent_mm_zyx": extents,
        "principal_axes_mm": _principal_axes_mm(points, affine[:3, :3]),
        "recist_3d_mm": _feret_diameter_mm(mask, affine),
        "equivalent_diameter_mm": float((6.0 * volume_mm3 / math.pi) ** (1.0 / 3.0)),
        "surface_area_mm2": surface_area,
        "sphericity": sphericity,
        "surface_roughness": roughness,
    }


def measure_liver(mask: np.ndarray, affine_4x4: np.ndarray) -> LiverMetricsV2:
    """Measure a liver mask from actual voxels; affine maps [z, y, x, 1] to world mm."""
    return LiverMetricsV2(**_measure_shape(mask, affine_4x4))


def measure_lesions(instance_mask: np.ndarray, affine_4x4: np.ndarray) -> list[LesionMetricsV2]:
    """Measure each positive integer instance independently and return IDs in ascending order."""
    instances = np.asarray(instance_mask)
    if instances.ndim != 3 or not np.issubdtype(instances.dtype, np.integer):
        raise ValueError("instance_mask must be a 3D integer array")
    lesions = []
    for instance_id in sorted(int(value) for value in np.unique(instances) if value > 0):
        lesions.append(LesionMetricsV2(instance_id=instance_id, **_measure_shape(instances == instance_id, affine_4x4)))
    return lesions


def signed_distance_mm(mask: np.ndarray, affine_4x4: np.ndarray) -> np.ndarray:
    """Return positive-inside/negative-outside signed distance in millimetres."""
    mask = _validate_mask(mask, "mask")
    _, spacing = _validate_affine(affine_4x4)
    inside = ndimage.distance_transform_edt(mask, sampling=spacing)
    outside = ndimage.distance_transform_edt(~mask, sampling=spacing)
    return (inside - outside).astype(np.float32)


def _path_stats(mask: np.ndarray, angle_deg: float, depth_spacing_mm: float) -> PathLengthStatsV2:
    rotated = ndimage.rotate(
        mask.astype(np.uint8),
        angle=-angle_deg,
        axes=(1, 2),
        reshape=False,
        order=0,
        mode="constant",
        cval=0,
        prefilter=False,
    )
    lengths = rotated.sum(axis=2, dtype=np.float64) * depth_spacing_mm
    positive = lengths[lengths > 0]
    if positive.size == 0:
        raise ValueError("rotated mask has no positive rays")
    p05, p50, p95 = np.percentile(positive, (5, 50, 95))
    return PathLengthStatsV2(
        mean_mm=float(positive.mean()),
        p05_mm=float(p05),
        p50_mm=float(p50),
        p95_mm=float(p95),
    )


def measure_path_lengths(
    body_mask: np.ndarray,
    liver_mask: np.ndarray,
    affine_4x4: np.ndarray,
    *,
    views: int = 60,
    starting_angle_deg: float = 90.0,
    rotation_direction: str = "clockwise",
) -> PathLengthMetricsV2:
    """Measure path distributions in the frozen PAR-S common-projector frame.

    Formal V2 angles start at 90 degrees and increase clockwise.  The labels
    deliberately match ``SPECTProjector.angles_deg``; callers must not pass
    raw SIMIND nominal angles (which start at 180 degrees in its native basis).
    """
    body = _validate_mask(body_mask, "body_mask")
    liver = _validate_mask(liver_mask, "liver_mask")
    if body.shape != liver.shape:
        raise ValueError("body_mask and liver_mask must have the same shape")
    if np.any(liver & ~body):
        raise ValueError("liver_mask must be contained in body_mask")
    _, spacing = _validate_affine(affine_4x4)
    if not math.isclose(float(spacing[1]), float(spacing[2]), rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("transverse voxel spacing must be isotropic for rotation-based path lengths")
    if not isinstance(views, int) or isinstance(views, bool) or views <= 0:
        raise ValueError("views must be a positive integer")
    if rotation_direction not in {"clockwise", "counterclockwise"}:
        raise ValueError(
            "rotation_direction must be 'clockwise' or 'counterclockwise'"
        )
    sign = 1.0 if rotation_direction == "clockwise" else -1.0
    angles = tuple((starting_angle_deg + sign * index * 360.0 / views) % 360.0 for index in range(views))
    body_stats = tuple(_path_stats(body, angle, float(spacing[2])) for angle in angles)
    liver_stats = tuple(_path_stats(liver, angle, float(spacing[2])) for angle in angles)
    return PathLengthMetricsV2(angles_deg=angles, body=body_stats, liver=liver_stats)
