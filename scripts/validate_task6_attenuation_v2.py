from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
from scipy import ndimage


matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.attenuation_model_v2 import (  # noqa: E402
    AttenuationAnatomyV2,
    generate_attenuation_maps,
    select_simind_attenuation_map,
)
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


def _synthetic_anatomy(shape: tuple[int, int, int] = (64, 64, 64)) -> AttenuationAnatomyV2:
    grid = GridSpecV2(shape=shape)
    z, y, x = np.indices(shape, dtype=np.float32)
    center = 0.5 * (np.asarray(shape, dtype=np.float32) - 1.0)
    body = (
        ((z - center[0]) / 28.0) ** 2
        + ((y - center[1]) / 22.0) ** 2
        + ((x - center[2]) / 27.0) ** 2
        <= 1.0
    )
    liver = (
        ((z - 37.0) / 9.0) ** 2
        + ((y - 37.0) / 9.0) ** 2
        + ((x - 42.0) / 14.0) ** 2
        <= 1.0
    ) & body
    left_lung = (
        ((z - 21.0) / 11.0) ** 2
        + ((y - 30.0) / 8.0) ** 2
        + ((x - 20.0) / 8.0) ** 2
        <= 1.0
    ) & body
    right_lung = (
        ((z - 21.0) / 11.0) ** 2
        + ((y - 30.0) / 8.0) ** 2
        + ((x - 43.0) / 8.0) ** 2
        <= 1.0
    ) & body
    lung = (left_lung | right_lung) & ~liver
    bone = (
        ((y - 43.0) / 4.0) ** 2 + ((x - 31.5) / 4.0) ** 2 <= 1.0
    ) & body & ~liver & ~lung
    inner = ndimage.binary_erosion(body, iterations=3)
    fat = body & ~inner & ~liver & ~lung & ~bone
    return AttenuationAnatomyV2(
        body_mask=body,
        liver_mask=liver,
        lung_mask=lung,
        bone_mask=bone,
        fat_mask=fat,
        affine_4x4=grid.affine_4x4,
    )


def _digest(values: np.ndarray) -> str:
    return hashlib.sha256(values.tobytes(order="C")).hexdigest()


def _write_figure(
    path: Path,
    anatomy: AttenuationAnatomyV2,
    mu_true: np.ndarray,
    mu_input: np.ndarray,
) -> None:
    difference = mu_input - mu_true
    z_index = int(np.argmax(anatomy.liver_mask.sum(axis=(1, 2))))
    fig, axes = plt.subplots(1, 4, figsize=(14.0, 3.8), constrained_layout=True)
    common = {"cmap": "magma", "vmin": 0.0, "vmax": 0.30, "origin": "lower"}
    first = axes[0].imshow(mu_true[z_index], **common)
    axes[0].set_title(r"$\mu_{true}$ (140 keV)")
    axes[1].imshow(mu_input[z_index], **common)
    axes[1].set_title(r"$\mu_{input}$ (CT-like)")
    limit = max(float(np.max(np.abs(difference[z_index]))), 1e-6)
    third = axes[2].imshow(
        difference[z_index], cmap="coolwarm", vmin=-limit, vmax=limit, origin="lower"
    )
    axes[2].set_title("Input - true")
    axes[3].hist(
        difference[anatomy.body_mask], bins=80, color="#3478b8", alpha=0.9
    )
    axes[3].axvline(0.0, color="black", linewidth=0.8)
    axes[3].set_title("Body difference")
    axes[3].set_xlabel(r"$\Delta\mu$ (cm$^{-1}$)")
    axes[3].set_ylabel("voxels")
    for axis in axes[:3]:
        axis.set_axis_off()
    fig.colorbar(first, ax=axes[:2], shrink=0.72, label=r"$\mu$ (cm$^{-1}$)")
    fig.colorbar(third, ax=axes[2], shrink=0.72, label=r"$\Delta\mu$ (cm$^{-1}$)")
    fig.suptitle(f"Task 6 attenuation separation audit (axial z={z_index})", fontsize=12)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def build_report(*, seeds: tuple[int, ...], output_dir: Path) -> dict:
    if len(seeds) < 2:
        raise ValueError("at least two seeds are required")
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    profile = load_profile(
        REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json",
        registry,
    )
    anatomy = _synthetic_anatomy()
    generated = [
        generate_attenuation_maps(anatomy, profile, np.random.default_rng(seed))
        for seed in seeds
    ]
    true_maps = [item[0] for item in generated]
    input_maps = [item[1] for item in generated]
    metadata = generated[0][2]
    true_hashes = [_digest(values) for values in true_maps]
    input_hashes = [_digest(values) for values in input_maps]
    first_true, first_input = true_maps[0], input_maps[0]
    body_difference = first_input[anatomy.body_mask] - first_true[anatomy.body_mask]
    coefficients = metadata.tissue_coefficients_cm1
    simind_selected = select_simind_attenuation_map("mu_true_140kev", first_true)
    rejected_input = False
    try:
        select_simind_attenuation_map("mu_input_140kev", first_input)
    except ValueError:
        rejected_input = True
    gates = {
        "mu_true_seed_invariant": len(set(true_hashes)) == 1,
        "mu_input_seed_sensitive": len(set(input_hashes)) == len(seeds),
        "fat_mu_exact_0_146_cm1": bool(
            np.all(first_true[anatomy.fat_mask] == np.float32(0.146))
        ),
        "true_and_input_float32": all(
            values.dtype == np.float32 for values in true_maps + input_maps
        ),
        "finite_and_nonnegative": all(
            np.isfinite(values).all() and np.all(values >= 0.0)
            for values in true_maps + input_maps
        ),
        "outside_body_zero": all(
            np.all(values[~anatomy.body_mask] == 0.0)
            for values in true_maps + input_maps
        ),
        "ct_degradation_changes_input_only": bool(
            np.any(first_input != first_true)
            and metadata.degradation_applied_only_to_mu_input
        ),
        "ct_degradation_declared_uncalibrated": metadata.uncalibrated_ct_degradation,
        "simind_selects_true_identity": simind_selected is first_true,
        "simind_rejects_mu_input": rejected_input,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_path = output_dir / "task6_attenuation_v2_comparison.png"
    _write_figure(figure_path, anatomy, first_true, first_input)
    return {
        "schema_version": "pars_task6_attenuation_validation_v2",
        "profile_id": profile.profile_id,
        "seeds": list(seeds),
        "status": "pass" if all(gates.values()) else "fail",
        "map_contract": {
            "physical_map": metadata.mu_true_semantic_key,
            "network_input_map": metadata.mu_input_semantic_key,
            "simind_allowed_map": metadata.simind_allowed_map_key,
            "unit": metadata.unit,
        },
        "physical_coefficients_cm1": dict(coefficients),
        "ct_degradation": {
            "hu_conversion": metadata.hu_conversion,
            "blur_sigma_mm": metadata.blur_sigma_mm,
            "hu_noise_sd": metadata.hu_noise_sd,
            "hu_bias_field_sd": metadata.hu_bias_field_sd,
            "hu_bias_correlation_length_mm": metadata.hu_bias_correlation_length_mm,
            "uncalibrated_ct_degradation": metadata.uncalibrated_ct_degradation,
        },
        "observed": {
            "shape": list(first_true.shape),
            "body_voxels": int(np.count_nonzero(anatomy.body_mask)),
            "mu_true_sha256": true_hashes,
            "mu_input_sha256": input_hashes,
            "input_minus_true_body_mean_cm1": float(body_difference.mean()),
            "input_minus_true_body_sd_cm1": float(body_difference.std(ddof=0)),
            "input_minus_true_body_mae_cm1": float(np.abs(body_difference).mean()),
            "input_minus_true_body_max_abs_cm1": float(np.abs(body_difference).max()),
        },
        "figure": str(figure_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "gates": gates,
    }


def _markdown(report: dict) -> str:
    observed = report["observed"]
    degradation = report["ct_degradation"]
    lines = [
        "# PAR-S V2 Task 6 衰减图分离验证",
        "",
        f"- Profile: `{report['profile_id']}`",
        f"- 总门禁: **{report['status'].upper()}**",
        f"- 验证随机种子: `{report['seeds']}`",
        "",
        "## 语义契约",
        "",
        "| 用途 | 唯一允许的数组 | 单位 |",
        "|---|---|---|",
        f"| 物理/SIMIND | `{report['map_contract']['physical_map']}` | {report['map_contract']['unit']} |",
        f"| 网络输入 | `{report['map_contract']['network_input_map']}` | {report['map_contract']['unit']} |",
        "",
        "## 观察结果",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 体内 Input - True 均值 | {observed['input_minus_true_body_mean_cm1']:.6f} cm⁻¹ |",
        f"| 体内 Input - True SD | {observed['input_minus_true_body_sd_cm1']:.6f} cm⁻¹ |",
        f"| 体内 Input - True MAE | {observed['input_minus_true_body_mae_cm1']:.6f} cm⁻¹ |",
        f"| 体内最大绝对差 | {observed['input_minus_true_body_max_abs_cm1']:.6f} cm⁻¹ |",
        "",
        "`mu_true_140kev` 在所有种子下字节级一致；`mu_input_140kev` 在 HU 域施加模糊、低频偏置与噪声后再转换为 μ，因此随种子变化，但不会反向污染真实物理图。",
        "",
        f"当前 CT 样退化参数明确标记为 **uncalibrated={str(degradation['uncalibrated_ct_degradation']).lower()}**；它是 Task 8 本地无 PHI 校准之前的保守工程占位，不应被表述为真实扫描仪噪声分布。",
        "",
        "![Task 6 attenuation comparison](task6_attenuation_v2_comparison.png)",
        "",
        "## 自动门禁",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] `{name}`" for name, passed in report["gates"].items())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Task 6 attenuation separation.")
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=(101, 202, 303, 404, 505, 606, 707, 808),
    )
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "reports")
    args = parser.parse_args()
    report = build_report(seeds=tuple(args.seeds), output_dir=args.output_dir)
    json_path = args.output_dir / "task6_attenuation_v2_validation.json"
    markdown_path = args.output_dir / "task6_attenuation_v2_validation.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "json": str(json_path),
                "markdown": str(markdown_path),
                "figure": str(args.output_dir / "task6_attenuation_v2_comparison.png"),
            }
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
