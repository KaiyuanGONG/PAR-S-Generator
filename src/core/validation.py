"""
Validation helpers for UI-facing workflow checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.parameter_specs import NUMERIC_SPECS
from core.phantom_generator import PhantomConfig, PreviewOverrides
from ui.i18n import tr


DEFAULT_MATRIX = 128
DEFAULT_VOXEL_SIZE_MM = 4.42
DEFAULT_SIMIND_SMC = "ge870_czt.smc"


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    warning_codes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, code: str, message: str) -> None:
        self.error_codes.append(code)
        self.errors.append(message)

    def add_warning(self, code: str, message: str) -> None:
        self.warning_codes.append(code)
        self.warnings.append(message)

    def extend(self, other: "ValidationReport") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.error_codes.extend(other.error_codes)
        self.warning_codes.extend(other.warning_codes)

    def to_message(self) -> str:
        lines: list[str] = []
        if self.errors:
            lines.append(tr("Errors:"))
            lines.extend(f"- {item}" for item in self.errors)
        if self.warnings:
            if lines:
                lines.append("")
            lines.append(tr("Warnings:"))
            lines.extend(f"- {item}" for item in self.warnings)
        return "\n".join(lines).strip()


def validate_phantom_config(config: PhantomConfig, preview: PreviewOverrides | None = None) -> ValidationReport:
    report = ValidationReport()

    _check_range(report, "matrix_size", config.volume_shape[0])
    _check_range(report, "voxel_size_mm", config.voxel_size_mm)
    _check_range(report, "scale_jitter", config.scale_jitter)
    _check_range(report, "rot_jitter_deg", config.rot_jitter_deg)
    _check_range(report, "global_shift_range", config.global_shift_range)
    _check_range(report, "target_left_ratio", config.target_left_ratio)
    _check_range(report, "smooth_sigma", config.smooth_sigma)
    _check_range(report, "tumor_count", config.tumor_count_min)
    _check_range(report, "tumor_count", config.tumor_count_max)
    _check_range(report, "tumor_contrast", config.tumor_contrast_min)
    _check_range(report, "tumor_contrast", config.tumor_contrast_max)
    _check_range(report, "total_counts", config.total_counts / 1e4)
    _check_range(report, "residual_bg", config.residual_bg)

    if preview is not None and preview.exact_tumor_count is not None:
        _check_range(report, "tumor_count", preview.exact_tumor_count)

    if config.tumor_count_min > config.tumor_count_max:
        report.add_error(
            "tumor_count.min_gt_max",
            tr("Min tumors cannot be greater than Max tumors."),
        )

    if config.tumor_contrast_min > config.tumor_contrast_max:
        report.add_error(
            "tumor_contrast.min_gt_max",
            tr("Contrast min cannot be greater than Contrast max."),
        )

    if config.n_cases < 1:
        report.add_error("n_cases.invalid", tr("Number of cases must be at least 1."))

    if not config.output_dir.strip():
        report.add_error("output_dir.required", tr("Output directory is required."))

    if config.volume_shape[0] != config.volume_shape[1] or config.volume_shape[1] != config.volume_shape[2]:
        report.add_error(
            "volume_shape.non_cubic",
            tr("Only cubic volumes are supported by the current workflow."),
        )

    if config.volume_shape[0] != DEFAULT_MATRIX or abs(config.voxel_size_mm - DEFAULT_VOXEL_SIZE_MM) > 1e-6:
        report.add_warning(
            "workflow.geometry_mismatch",
            tr(
                "Bundled GE 870 CZT workflow assumes 128 x 128 x 128 voxels at 4.42 mm. "
                "Custom matrix or voxel size requires a matching .smc configuration."
            ),
        )

    if config.use_global_seed and config.global_seed < 0:
        report.add_error("global_seed.negative", tr("Global seed must be non-negative."))

    return report


def validate_simulation_inputs(
    npz_dir: str,
    interfile_dir: str,
    simind_exe: str,
    smc_path: str,
    sim_output_dir: str,
    phantom_config: PhantomConfig | None = None,
) -> ValidationReport:
    report = ValidationReport()

    npz_path = Path(npz_dir.strip()) if npz_dir.strip() else None
    interfile_path = Path(interfile_dir.strip()) if interfile_dir.strip() else None
    simind_path = Path(simind_exe.strip()) if simind_exe.strip() else None
    smc_file = Path(smc_path.strip()) if smc_path.strip() else None
    sim_out_path = Path(sim_output_dir.strip()) if sim_output_dir.strip() else None

    if npz_path is None:
        report.add_error("npz_dir.required", tr("npz directory is required."))
    elif not npz_path.exists():
        report.add_error("npz_dir.missing", tr("npz directory does not exist."))
    elif not any(npz_path.glob("case_*.npz")):
        report.add_error(
            "npz_dir.empty",
            tr("npz directory does not contain any case_*.npz files."),
        )

    if interfile_path is None:
        report.add_error("interfile_dir.required", tr("Binary output directory is required."))
    elif not interfile_path.exists():
        report.add_error("interfile_dir.missing", tr("Binary output directory does not exist."))
    else:
        act_files = sorted(interfile_path.glob("case_*_act_av.bin"))
        if not act_files:
            report.add_error(
                "interfile_dir.empty",
                tr("Binary output directory does not contain any case_*_act_av.bin files."),
            )
        else:
            missing_atn: list[str] = []
            for act_file in act_files:
                stem = act_file.name.replace("_act_av.bin", "")
                if not (interfile_path / f"{stem}_atn_av.bin").exists():
                    missing_atn.append(stem)
            if missing_atn:
                preview = ", ".join(missing_atn[:8])
                if len(missing_atn) > 8:
                    preview += f", ... (+{len(missing_atn) - 8})"
                report.add_error(
                    "interfile_dir.missing_atn",
                    tr("Binary output directory is missing case_*_atn_av.bin for: {stems}").format(stems=preview),
                )

    if simind_path is None:
        report.add_error("simind_exe.required", tr("simind.exe path is required."))
    elif not simind_path.exists():
        report.add_error("simind_exe.missing", tr("simind.exe path does not exist."))

    if smc_file is None:
        report.add_error("smc.required", tr(".smc config file is required."))
    elif not smc_file.exists():
        report.add_error("smc.missing", tr(".smc config file does not exist."))

    if sim_out_path is None:
        report.add_error("sim_output_dir.required", tr("SIMIND output directory is required."))

    if phantom_config is not None and smc_file is not None and smc_file.name.lower() == DEFAULT_SIMIND_SMC:
        if phantom_config.volume_shape[0] != DEFAULT_MATRIX:
            report.add_error(
                "simind.bundled.matrix_mismatch",
                tr("Bundled ge870_czt.smc only supports 128 x 128 x 128 phantom volumes."),
            )
        if abs(phantom_config.voxel_size_mm - DEFAULT_VOXEL_SIZE_MM) > 1e-6:
            report.add_error(
                "simind.bundled.voxel_mismatch",
                tr("Bundled ge870_czt.smc only supports 4.42 mm voxel size."),
            )

    return report


def _check_range(report: ValidationReport, key: str, value: float) -> None:
    spec = NUMERIC_SPECS[key]
    if value < spec.hard_min or value > spec.hard_max:
        report.add_error(
            f"{key}.hard_bounds",
            tr("{label} must stay within {hard_min:g} to {hard_max:g}.").format(
                label=tr(spec.label),
                hard_min=spec.hard_min,
                hard_max=spec.hard_max,
            ),
        )
    elif value < spec.recommended_min or value > spec.recommended_max:
        report.add_warning(
            f"{key}.recommended_range",
            tr("{label} is outside the recommended range {recommended_min:g} to {recommended_max:g}.").format(
                label=tr(spec.label),
                recommended_min=spec.recommended_min,
                recommended_max=spec.recommended_max,
            ),
        )

