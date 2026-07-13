from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from skimage import measure


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from core.liver_geometry import GridSpecV2, fit_liver_geometry  # noqa: E402
from core.liver_regions import REGION_LABELS_V2  # noqa: E402
from validate_task3_liver_v2 import load_main_profile, select_representative_targets  # noqa: E402


REGION_COLORS = ["#000000", "#E69F00", "#56B4E9", "#009E73", "#CC79A7", "#0072B2"]


def _representative_geometries():
    profile = load_main_profile(REPO_ROOT)
    selected = select_representative_targets(profile, seed=20_260_714)
    normal_patient, normal_target = next(
        (patient, target)
        for patient, target in selected
        if target.morphology == "normal" and target.caudate_enabled
    )
    cirrhotic_patient, cirrhotic_target = next(
        (patient, target) for patient, target in selected if target.morphology == "cirrhotic"
    )
    grid = GridSpecV2()
    return grid, [
        (normal_patient, normal_target, fit_liver_geometry(normal_target, grid)),
        (cirrhotic_patient, cirrhotic_target, fit_liver_geometry(cirrhotic_target, grid)),
    ]


def _best_slice_index(mask: np.ndarray, axis: int) -> int:
    """Choose a large, connected cross-section for unambiguous visual QA."""
    connected: list[tuple[int, int]] = []
    fallback: list[tuple[int, int, int]] = []
    for index in range(mask.shape[axis]):
        image = np.take(mask, index, axis=axis)
        area = int(image.sum())
        if area == 0:
            continue
        components = int(measure.label(image, connectivity=1).max())
        fallback.append((components, -area, index))
        if components == 1:
            connected.append((area, index))
    if connected:
        return max(connected)[1]
    return min(fallback)[2]


def render_multiplanar(cases, output_path: Path) -> None:
    cmap = ListedColormap(REGION_COLORS)
    norm = BoundaryNorm(np.arange(-0.5, 6.5, 1.0), cmap.N)
    figure, axes = plt.subplots(2, 3, figsize=(13.5, 8.2), constrained_layout=True)
    for row, (_, target, geometry) in enumerate(cases):
        z = _best_slice_index(geometry.mask, axis=0)
        y = _best_slice_index(geometry.mask, axis=1)
        x = _best_slice_index(geometry.mask, axis=2)
        slices = [
            (geometry.region_labels[z, :, :], "Axial", "L–R", "P–A"),
            (geometry.region_labels[:, y, :], "Coronal", "L–R", "I–S"),
            (geometry.region_labels[:, :, x], "Sagittal", "P–A", "I–S"),
        ]
        for column, (image, view, xlabel, ylabel) in enumerate(slices):
            axis = axes[row, column]
            masked = np.ma.masked_where(image == 0, image)
            axis.imshow(masked, origin="lower", interpolation="nearest", cmap=cmap, norm=norm)
            axis.contour(image > 0, levels=[0.5], colors=["#303030"], linewidths=0.8)
            if np.any(image == 1):
                axis.contour(image == 1, levels=[0.5], colors=["#FFFFFF"], linewidths=1.2)
            axis.set_title(f"{target.morphology.capitalize()} · {view}")
            axis.set_xlabel(xlabel)
            axis.set_ylabel(ylabel)
            axis.set_xticks([])
            axis.set_yticks([])
        actual = geometry.actual_metrics
        axes[row, 0].text(
            0.01,
            -0.16,
            (
                f"V={actual['volume_ml']:.1f} mL · left={actual['left_fraction']:.3f} · "
                f"S1–3/S4–8={actual['s1_3_to_s4_8_ratio']:.3f} · rough={actual['surface_roughness']:.3f}"
            ),
            transform=axes[row, 0].transAxes,
            fontsize=9,
        )
    handles = [
        plt.Line2D([0], [0], marker="s", linestyle="", color=REGION_COLORS[label], label=name, markersize=9)
        for label, name in REGION_LABELS_V2.items()
    ]
    figure.legend(handles=handles, loc="outside lower center", ncol=3, frameon=False)
    figure.suptitle("Task 3 V2 liver region proxies on representative physical slices", fontsize=14)
    figure.savefig(output_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _set_equal_3d(axis, vertices: np.ndarray) -> None:
    minimum = vertices.min(axis=0)
    maximum = vertices.max(axis=0)
    center = 0.5 * (minimum + maximum)
    radius = 0.55 * float((maximum - minimum).max())
    axis.set_xlim(center[2] - radius, center[2] + radius)
    axis.set_ylim(center[1] - radius, center[1] + radius)
    axis.set_zlim(center[0] - radius, center[0] + radius)
    axis.set_box_aspect((1, 1, 1))


def _surface(axis, mask: np.ndarray, affine: np.ndarray, color: str, alpha: float) -> np.ndarray:
    spacing = np.diag(affine[:3, :3])
    vertices, faces, _, _ = measure.marching_cubes(mask.astype(np.uint8), level=0.5, spacing=spacing)
    vertices += affine[:3, 3]
    triangles = vertices[faces][:, :, [2, 1, 0]]
    collection = Poly3DCollection(triangles, facecolor=color, edgecolor="none", alpha=alpha)
    axis.add_collection3d(collection)
    return vertices


def render_3d(cases, output_path: Path) -> None:
    figure = plt.figure(figsize=(12.5, 6.2), constrained_layout=True)
    for index, (_, target, geometry) in enumerate(cases, start=1):
        axis = figure.add_subplot(1, 2, index, projection="3d")
        surface_color = "#4C78A8" if target.morphology == "normal" else "#E45756"
        vertices = _surface(axis, geometry.mask, geometry.affine_4x4, surface_color, 0.52)
        caudate = geometry.region_labels == 1
        if caudate.any():
            _surface(axis, caudate, geometry.affine_4x4, "#F2CF5B", 0.95)
        _set_equal_3d(axis, vertices)
        actual = geometry.actual_metrics
        axis.set_title(
            f"{target.morphology.capitalize()}\n"
            f"rough={actual['surface_roughness']:.3f}, caudate={actual['caudate_fraction']:.3f}"
        )
        axis.set_xlabel("LR (mm)")
        axis.set_ylabel("AP (mm)")
        axis.set_zlabel("SI (mm)")
        axis.view_init(elev=24, azim=132)
        axis.grid(False)
    figure.suptitle("Task 3 constrained double-ellipsoid liver surfaces · caudate highlighted", fontsize=14)
    figure.savefig(output_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def main() -> int:
    output_dir = REPO_ROOT / "docs" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    grid, cases = _representative_geometries()
    multiplanar = output_dir / "task3_liver_v2_multiplanar.png"
    surface = output_dir / "task3_liver_v2_3d.png"
    manifest = output_dir / "task3_liver_v2_visual_manifest.json"
    render_multiplanar(cases, multiplanar)
    render_3d(cases, surface)
    records = []
    for patient, target, geometry in cases:
        records.append(
            {
                "case_id": patient.case_id,
                "morphology": target.morphology,
                "caudate_enabled": bool(target.caudate_enabled),
                "target_metrics": dict(geometry.target_metrics),
                "actual_metrics": {
                    key: geometry.actual_metrics[key]
                    for key in (
                        "volume_ml",
                        "extent_mm_zyx",
                        "centroid_world_mm",
                        "left_fraction",
                        "s1_3_to_s4_8_ratio",
                        "caudate_fraction",
                        "surface_roughness",
                        "sphericity",
                    )
                },
            }
        )
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "pars_task3_visual_confirmation_v1",
                "seed": 20_260_714,
                "grid": {"shape": list(grid.shape), "voxel_size_mm": grid.voxel_size_mm},
                "files": [multiplanar.name, surface.name],
                "cases": records,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"multiplanar": str(multiplanar), "surface_3d": str(surface), "manifest": str(manifest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
