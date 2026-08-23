"""Deterministic chart-data and lightweight QC figure exports."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from pipeline.contracts import atomic_write_json, atomic_write_text, read_jsonl


def _save_figure(fig, base: Path) -> list[str]:
    base.parent.mkdir(parents=True, exist_ok=True)
    png = base.with_suffix(".png")
    svg = base.with_suffix(".svg")
    fig.savefig(png, dpi=220, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return [str(png.resolve()), str(svg.resolve())]


def _csv_text(fieldnames: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_data_flow_svg(path: Path) -> Path:
    """Create an editable vector flow diagram without external layout tools."""
    stages = [
        ("Protocol", "values · units · status"),
        ("Generate", "anatomy · lesions · activity · μ"),
        ("Phantom QC", "geometry · masks · distributions"),
        ("Export", "float32 C-order · read-back"),
        ("SIMIND", "weighted expectation"),
        ("Projection QC", "shape · finite · .res · orientation"),
        ("Observation", "optional seeded Poisson"),
        ("Finalize", "split · manifest · checksums"),
    ]
    width, height = 1500, 260
    box_w, box_h, gap = 160, 92, 22
    x0, y = 24, 78
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f9fc"/>',
        '<text x="24" y="38" font-family="Bahnschrift,Segoe UI,sans-serif" font-size="22" font-weight="700" fill="#17243a">PAR-S synthetic liver SPECT data preparation</text>',
        '<text x="24" y="60" font-family="Cascadia Mono,Consolas,monospace" font-size="11" fill="#687386">GE 870 CZT · current research protocol · evidence-gated</text>',
    ]
    for index, (title, detail) in enumerate(stages):
        x = x0 + index * (box_w + gap)
        color = "#0d6efd" if index not in {4, 6} else "#8a5a20"
        parts.extend(
            [
                f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="8" fill="#ffffff" stroke="{color}" stroke-width="2"/>',
                f'<text x="{x + 12}" y="{y + 29}" font-family="Bahnschrift,Segoe UI,sans-serif" font-size="15" font-weight="700" fill="#17243a">{title}</text>',
                f'<text x="{x + 12}" y="{y + 53}" font-family="Segoe UI,sans-serif" font-size="10" fill="#687386">{detail}</text>',
                f'<text x="{x + 12}" y="{y + 76}" font-family="Cascadia Mono,Consolas,monospace" font-size="10" fill="{color}">{index + 1:02d}</text>',
            ]
        )
        if index < len(stages) - 1:
            x1 = x + box_w
            x2 = x + box_w + gap - 4
            mid = y + box_h // 2
            parts.append(f'<path d="M{x1 + 3},{mid} L{x2},{mid}" stroke="#8290a3" stroke-width="2"/>')
            parts.append(f'<path d="M{x2 - 6},{mid - 5} L{x2},{mid} L{x2 - 6},{mid + 5}" fill="none" stroke="#8290a3" stroke-width="2"/>')
    parts.extend(
        [
            '<text x="24" y="216" font-family="Segoe UI,sans-serif" font-size="11" fill="#8a5a20">Amber stages require explicit physics/protocol status; unknown values remain pending rather than inferred.</text>',
            '<text x="24" y="238" font-family="Segoe UI,sans-serif" font-size="11" fill="#687386">Software boundary: finalized synthetic data package.</text>',
            "</svg>",
        ]
    )
    atomic_write_text(Path(path), "\n".join(parts) + "\n")
    return Path(path)


def export_run_figures(run_root: Path, cases: list[dict]) -> dict:
    run_root = Path(run_root)
    figures = run_root / "figures"
    figures.mkdir(exist_ok=True)
    phantom_rows: list[dict] = []
    projection_rows: list[dict] = []
    for case in cases:
        phantom_ref = case.get("qc", {}).get("phantom", {})
        if Path(phantom_ref.get("path", "")).is_file():
            qc = json.loads(Path(phantom_ref["path"]).read_text(encoding="utf-8"))
            metrics = qc["metrics"]
            if metrics["tumors"]:
                for tumor in metrics["tumors"]:
                    phantom_rows.append(
                        {
                            "case_id": case["case_id"],
                            "split": case["split"],
                            "liver_volume_ml": metrics["liver_volume_ml"],
                            "left_ratio": metrics["left_ratio"],
                            "n_tumors": metrics["n_tumors"],
                            "lesion_index": tumor["index"],
                            "lesion_effective_diameter_mm": tumor["effective_diameter_mm"],
                            "lesion_surface_margin_mm": tumor["surface_margin_mm"],
                            "tnr_from_saved_activity": tumor["tnr_from_saved_activity"],
                        }
                    )
            else:
                phantom_rows.append(
                    {
                        "case_id": case["case_id"],
                        "split": case["split"],
                        "liver_volume_ml": metrics["liver_volume_ml"],
                        "left_ratio": metrics["left_ratio"],
                        "n_tumors": 0,
                    }
                )
        projection_ref = case.get("qc", {}).get("projection", {})
        if Path(projection_ref.get("path", "")).is_file():
            qc = json.loads(Path(projection_ref["path"]).read_text(encoding="utf-8"))
            metrics = qc.get("metrics", {})
            projection_rows.append(
                {
                    "case_id": case["case_id"],
                    "split": case["split"],
                    "backend": case.get("expectation", {}).get("backend"),
                    "sum": metrics.get("sum"),
                    "max": metrics.get("max"),
                    "nonzero_fraction": metrics.get("nonzero_fraction"),
                    "noninteger_positive_fraction": metrics.get("noninteger_positive_fraction"),
                    "view_sum_min": metrics.get("view_sum_min"),
                    "view_sum_median": metrics.get("view_sum_median"),
                    "view_sum_max": metrics.get("view_sum_max"),
                    "angular_cv": metrics.get("angular_cv"),
                    "support_row_count": len(metrics.get("support_rows", [])),
                    "support_col_count": len(metrics.get("support_cols", [])),
                }
            )

    phantom_fields = [
        "case_id", "split", "liver_volume_ml", "left_ratio", "n_tumors", "lesion_index",
        "lesion_effective_diameter_mm", "lesion_surface_margin_mm", "tnr_from_saved_activity",
    ]
    projection_fields = [
        "case_id", "split", "backend", "sum", "max", "nonzero_fraction",
        "noninteger_positive_fraction", "view_sum_min", "view_sum_median",
        "view_sum_max", "angular_cv", "support_row_count", "support_col_count",
    ]
    atomic_write_text(figures / "phantom_distribution_data.csv", _csv_text(phantom_fields, phantom_rows))
    atomic_write_text(figures / "projection_qc_data.csv", _csv_text(projection_fields, projection_rows))
    write_data_flow_svg(figures / "data_flow.svg")

    outputs = [str((figures / "data_flow.svg").resolve())]
    if phantom_rows:
        def values(key):
            return [float(row[key]) for row in phantom_rows if row.get(key) not in {None, ""}]

        fig, axes = plt.subplots(2, 2, figsize=(9.0, 6.8), constrained_layout=True)
        panels = [
            ("liver_volume_ml", "Liver volume", "mL"),
            ("left_ratio", "Left-lobe fraction", "fraction"),
            ("lesion_effective_diameter_mm", "Lesion effective diameter", "mm"),
            ("lesion_surface_margin_mm", "Lesion-to-surface margin", "mm"),
        ]
        for axis, (key, title, unit) in zip(axes.flat, panels, strict=True):
            data = values(key)
            if data:
                bins = min(18, max(4, int(np.sqrt(len(data)))))
                axis.hist(data, bins=bins, color="#2878b5", edgecolor="white", linewidth=0.6)
                axis.axvline(np.median(data), color="#d28b28", linewidth=1.5, label="median")
            axis.set_title(title, loc="left", fontweight="bold")
            axis.set_xlabel(unit)
            axis.set_ylabel("records")
            axis.spines[["top", "right"]].set_visible(False)
        fig.suptitle("Synthetic phantom QC distributions", x=0.02, ha="left", fontsize=15, fontweight="bold")
        outputs.extend(_save_figure(fig, figures / "phantom_distributions"))

    if projection_rows:
        x = np.arange(len(projection_rows))
        nonzero = np.array([float(row["nonzero_fraction"]) for row in projection_rows])
        noninteger = np.array([float(row["noninteger_positive_fraction"]) for row in projection_rows])
        fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.6), constrained_layout=True)
        axes[0].plot(x, nonzero, "o-", color="#2878b5", markersize=3)
        axes[0].set_title("Non-zero projection fraction", loc="left", fontweight="bold")
        axes[1].plot(x, noninteger, "o-", color="#d28b28", markersize=3)
        axes[1].set_title("Non-integer positive fraction", loc="left", fontweight="bold")
        for axis in axes:
            axis.set_xlabel("case index")
            axis.set_ylim(-0.02, 1.02)
            axis.spines[["top", "right"]].set_visible(False)
        fig.suptitle("Projection artifact QC", x=0.02, ha="left", fontsize=15, fontweight="bold")
        outputs.extend(_save_figure(fig, figures / "projection_qc"))

    metadata = {
        "scope": "synthetic_liver_spect_data_preparation_only",
        "phantom_rows": len(phantom_rows),
        "projection_rows": len(projection_rows),
        "artifacts": outputs,
        "note": "Figures are automated QC evidence, not a claim of clinical or physics validation.",
    }
    atomic_write_json(figures / "figure_manifest.json", metadata)
    return metadata


def export_legacy_figures(manifest_root: Path) -> dict:
    """Export distributions from frozen legacy masks/metadata without touching source data."""
    manifest_root = Path(manifest_root)
    figures = manifest_root / "figures"
    figures.mkdir(exist_ok=True)
    cases = read_jsonl(manifest_root / "cases.jsonl")
    summary = json.loads((manifest_root / "qc_summary.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    case_rows: list[dict] = []
    for case in cases:
        case_rows.append(
            {
                "case_id": case["case_id"],
                "liver_volume_ml": case["qc"]["liver_volume_ml"],
                "left_ratio": case["qc"]["left_ratio"],
            }
        )
        tumors = case.get("qc", {}).get("tumors", [])
        for tumor in tumors:
            nominal_values = case["qc"].get("nominal_lesion_diameters_mm", [])
            index = int(tumor["index"])
            nominal = nominal_values[index] if index < len(nominal_values) else None
            rows.append(
                {
                    "case_id": case["case_id"],
                    "split": case["split"],
                    "liver_volume_ml": case["qc"]["liver_volume_ml"],
                    "left_ratio": case["qc"]["left_ratio"],
                    "lesion_index": index,
                    "mode": tumor.get("mode"),
                    "nominal_diameter_mm": nominal,
                    "effective_diameter_mm": tumor.get("effective_diameter_mm"),
                    "actual_to_nominal_ratio": (
                        float(tumor["effective_diameter_mm"]) / float(nominal) if nominal else None
                    ),
                    "surface_margin_mm": tumor.get("surface_margin_mm"),
                    "tnr_from_saved_activity": tumor.get("tnr_from_saved_activity"),
                    "overlap_previous_vox": tumor.get("overlap_previous_vox"),
                }
            )
    fields = [
        "case_id", "split", "liver_volume_ml", "left_ratio", "lesion_index", "mode",
        "nominal_diameter_mm", "effective_diameter_mm", "actual_to_nominal_ratio",
        "surface_margin_mm", "tnr_from_saved_activity", "overlap_previous_vox",
    ]
    atomic_write_text(figures / "legacy_phantom_distribution_data.csv", _csv_text(fields, rows))
    write_data_flow_svg(figures / "data_flow.svg")

    def values(key, mode=None):
        if key in {"liver_volume_ml", "left_ratio"}:
            return [float(row[key]) for row in case_rows]
        return [
            float(row[key]) for row in rows
            if row.get(key) not in {None, ""} and (mode is None or row.get("mode") == mode)
        ]

    fig, axes = plt.subplots(2, 3, figsize=(12.0, 7.2), constrained_layout=True)
    panels = [
        ("liver_volume_ml", "Liver volume", "mL", None),
        ("left_ratio", "Left-lobe fraction", "fraction", None),
        ("effective_diameter_mm", "Lesion effective diameter", "mm", None),
        ("actual_to_nominal_ratio", "Ellipsoid actual / nominal", "ratio", "ellipsoid"),
        ("surface_margin_mm", "Lesion-to-surface margin", "mm", None),
        ("tnr_from_saved_activity", "TNR from saved activity", "ratio", None),
    ]
    for axis, (key, title, unit, mode) in zip(axes.flat, panels, strict=True):
        data = values(key, mode)
        bins = min(32, max(8, int(np.sqrt(len(data))))) if data else 8
        axis.hist(data, bins=bins, color="#2878b5", edgecolor="white", linewidth=0.35)
        if data:
            axis.axvline(np.median(data), color="#d28b28", linewidth=1.4)
        axis.set_title(title, loc="left", fontweight="bold")
        axis.set_xlabel(unit)
        axis.set_ylabel("lesion records" if "Lesion" in title or "TNR" in title or "Ellipsoid" in title else "records")
        axis.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Legacy-v1 frozen QC distributions", x=0.015, ha="left", fontsize=15, fontweight="bold")
    outputs = [str((figures / "data_flow.svg").resolve())]
    outputs.extend(_save_figure(fig, figures / "legacy_distributions"))

    fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.5), constrained_layout=True)
    integrity = summary["lesion_integrity"]
    axes[0].bar(
        ["surface contact", "overlap", "outside"],
        [summary["lesion_surface_contact_count"], integrity["overlap_with_previous_lesion_count"], integrity["outside_liver_count"]],
        color=["#d28b28", "#bd3e3e", "#2878b5"],
    )
    axes[0].set_title("Legacy lesion integrity flags", loc="left", fontweight="bold")
    support = summary["projection_support_patterns"][:3]
    axes[1].bar(range(len(support)), [item["count"] for item in support], color="#2878b5")
    axes[1].set_title("Projection support patterns", loc="left", fontweight="bold")
    axes[1].set_xlabel("pattern rank")
    labels = ["non-zero", "non-integer+"]
    values_qc = [
        summary["projection_nonzero_fraction"]["mean"],
        summary["projection_noninteger_positive_fraction"]["mean"],
    ]
    axes[2].bar(labels, values_qc, color=["#2878b5", "#d28b28"])
    axes[2].set_ylim(0, 1.05)
    axes[2].set_title("Projection value QC", loc="left", fontweight="bold")
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(axis="x", rotation=20)
    fig.suptitle("Legacy-v1 limitations captured by frozen evidence", x=0.015, ha="left", fontsize=15, fontweight="bold")
    outputs.extend(_save_figure(fig, figures / "legacy_qc_flags"))

    metadata = {
        "dataset_id": "legacy-v1-weighted-mc",
        "row_count": len(rows),
        "artifacts": outputs,
        "source": "frozen cases.jsonl and qc_summary.json",
        "note": "Legacy QC evidence; not a claim of valid physics or clinical noise.",
    }
    atomic_write_json(figures / "figure_manifest.json", metadata)
    return metadata
