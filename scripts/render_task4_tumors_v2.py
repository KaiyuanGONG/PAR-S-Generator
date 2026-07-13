from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from skimage import measure


REPO_ROOT = Path(__file__).resolve().parents[1]
TUMOR_COLORS = ("#D55E00", "#CC79A7", "#E69F00", "#009E73", "#F0E442")


def _case_count(data) -> int:
    return len([name for name in data.files if name.startswith("liver_")])


def _best_index(tumors: np.ndarray, liver: np.ndarray, axis: int) -> int:
    tumor_areas = np.sum(tumors > 0, axis=tuple(item for item in range(3) if item != axis))
    if tumor_areas.max() > 0:
        return int(np.argmax(tumor_areas))
    liver_areas = np.sum(liver > 0, axis=tuple(item for item in range(3) if item != axis))
    return int(np.argmax(liver_areas))


def _crop(image: np.ndarray, support: np.ndarray, pad: int = 5):
    indices = np.argwhere(support)
    if len(indices) == 0:
        return image, support
    lower = np.maximum(indices.min(axis=0) - pad, 0)
    upper = np.minimum(indices.max(axis=0) + pad + 1, support.shape)
    section = tuple(slice(int(lo), int(hi)) for lo, hi in zip(lower, upper))
    return image[section], support[section]


def render_multiplanar(data, metadata: dict, output_path: Path) -> None:
    case_count = _case_count(data)
    figure, axes = plt.subplots(case_count, 3, figsize=(13.2, 4.0 * case_count), squeeze=False)
    cmap = ListedColormap(("#00000000",) + TUMOR_COLORS * 4)
    for row in range(case_count):
        liver = data[f"liver_{row}"].astype(bool)
        tumors = data[f"tumors_{row}"].astype(np.uint16)
        case = metadata["cases"][row]
        for column, (axis_index, view) in enumerate(((0, "Axial"), (1, "Coronal"), (2, "Sagittal"))):
            index = _best_index(tumors, liver, axis_index)
            liver_slice = np.take(liver, index, axis=axis_index)
            tumor_slice = np.take(tumors, index, axis=axis_index)
            tumor_slice, liver_slice = _crop(tumor_slice, liver_slice)
            axis = axes[row, column]
            axis.imshow(liver_slice, origin="lower", cmap="Greys", alpha=0.42, interpolation="nearest")
            masked = np.ma.masked_where(tumor_slice == 0, tumor_slice)
            axis.imshow(masked, origin="lower", cmap=cmap, vmin=0, vmax=20, interpolation="nearest")
            axis.contour(liver_slice, levels=[0.5], colors=["#303030"], linewidths=0.8)
            for instance_id in np.unique(tumor_slice):
                if instance_id > 0:
                    axis.contour(
                        tumor_slice == instance_id,
                        levels=[0.5],
                        colors=[TUMOR_COLORS[(int(instance_id) - 1) % len(TUMOR_COLORS)]],
                        linewidths=1.3,
                    )
            axis.set_title(f"{case['case_id']} · {view} · slice {index}")
            axis.set_xticks([])
            axis.set_yticks([])
        axes[row, 0].set_ylabel(
            f"{case['liver_morphology']}\n{case['target_count']} lesions · "
            f"Dmax {case['patient_dmax_mm']:.1f} mm\nburden {case['tumor_to_liver_fraction']:.3f}"
        )
    figure.suptitle("Task 4 · complete tumor instances on Task 3 liver geometries", fontsize=14)
    figure.tight_layout()
    figure.savefig(output_path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _surface(axis, mask: np.ndarray, affine: np.ndarray, color: str, alpha: float):
    vertices, faces, _, _ = measure.marching_cubes(
        mask.astype(np.uint8),
        level=0.5,
        spacing=np.diag(affine[:3, :3]),
    )
    vertices += affine[:3, 3]
    xyz = vertices[:, [2, 1, 0]]
    collection = Poly3DCollection(xyz[faces], facecolor=color, edgecolor="none", alpha=alpha)
    axis.add_collection3d(collection)
    return xyz


def _equal_axes(axis, vertices: np.ndarray) -> None:
    lower = vertices.min(axis=0)
    upper = vertices.max(axis=0)
    center = 0.5 * (lower + upper)
    radius = 0.55 * float((upper - lower).max())
    axis.set_xlim(center[0] - radius, center[0] + radius)
    axis.set_ylim(center[1] - radius, center[1] + radius)
    axis.set_zlim(center[2] - radius, center[2] + radius)
    axis.set_box_aspect((1, 1, 1))


def render_3d(data, metadata: dict, output_path: Path) -> None:
    case_count = _case_count(data)
    figure = plt.figure(figsize=(5.2 * case_count, 5.0), constrained_layout=True)
    for index in range(case_count):
        liver = data[f"liver_{index}"].astype(bool)
        tumors = data[f"tumors_{index}"].astype(np.uint16)
        affine = data[f"affine_{index}"].astype(np.float64)
        case = metadata["cases"][index]
        axis = figure.add_subplot(1, case_count, index + 1, projection="3d")
        vertices = _surface(axis, liver, affine, "#4C78A8", 0.20)
        for instance_id in range(1, int(tumors.max()) + 1):
            _surface(
                axis,
                tumors == instance_id,
                affine,
                TUMOR_COLORS[(instance_id - 1) % len(TUMOR_COLORS)],
                0.94,
            )
        _equal_axes(axis, vertices)
        axis.view_init(elev=22, azim=-58)
        axis.set_title(
            f"{case['case_id']}\n{case['strata']['lobe_extent']} · "
            f"{case['target_count']} lesions · attempt {case['accepted_attempt_index']}"
        )
        axis.set_xlabel("LR")
        axis.set_ylabel("AP")
        axis.set_zlabel("SI")
        axis.set_xticks([])
        axis.set_yticks([])
        axis.set_zticks([])
        axis.grid(False)
    figure.suptitle("Task 4 · uncut liver-contained tumor instance surfaces", fontsize=14)
    figure.savefig(output_path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Task 4 tumor visual QA figures.")
    parser.add_argument(
        "--input",
        type=Path,
        default=REPO_ROOT / "docs" / "reports" / "task4_tumor_v2_examples.npz",
    )
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "reports")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with np.load(args.input, allow_pickle=False) as data:
        metadata = json.loads(str(data["metadata_json"]))
        multiplanar = args.output_dir / "task4_tumor_v2_multiplanar.png"
        surface = args.output_dir / "task4_tumor_v2_3d.png"
        render_multiplanar(data, metadata, multiplanar)
        render_3d(data, metadata, surface)
    manifest = {
        "schema_version": "pars_task4_visual_manifest_v2",
        "source": str(args.input.relative_to(REPO_ROOT)).replace("\\", "/"),
        "multiplanar": str(multiplanar.relative_to(REPO_ROOT)).replace("\\", "/"),
        "surface_3d": str(surface.relative_to(REPO_ROOT)).replace("\\", "/"),
        "case_count": metadata["case_count"],
        "status": "pass",
    }
    manifest_path = args.output_dir / "task4_tumor_v2_visual_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
