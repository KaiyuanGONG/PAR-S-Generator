"""Preparation and analysis contracts for the five blocking physics tests.

This module only prepares artifacts and command plans.  It never launches
SIMIND.  Results can be added later and analyzed through the stored criteria.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.stats import chi2

from core.interfile_writer import write_bin
from core.smc_parser import SmcData, parse_smc
from pipeline.contracts import atomic_write_json, atomic_write_text, sha256_file, utc_now
from pipeline.simind import SimindJob, build_simind_args, job_record, render_batch_script
from pipeline.qc import validate_projection_artifacts


EXPERIMENT_NAMES = (
    "attenuation_ict",
    "asymmetric_fiducial",
    "fov_matrix",
    "point_line_source",
    "rr_nn_ladder",
)


def _write_smc(path: Path, smc: SmcData) -> Path:
    lines = ["SMCV2", f"{smc.description:<72}"[:72], f"{len(smc.values):6d}  # Basic Change data"]
    for start in range(0, len(smc.values), 5):
        lines.append("".join(f"{value:12.5E}" for value in smc.values[start : start + 5]))
    lines.extend(
        [
            f"{len(smc.flags):6d}  # Simulation flags",
            "".join("T" if flag else "F" for flag in smc.flags),
            f"{len(smc.text_variables):6d}  # Text Variables",
            *smc.text_variables,
            f"{len(smc.data_files):6d}  # Data files",
            *smc.data_files,
        ]
    )
    atomic_write_text(path, "\n".join(lines) + "\n", encoding="ascii")
    # Fail immediately if our canonical writer is not understood by our
    # independent reader; the original .smc remains untouched.
    parse_smc(path)
    return path


def _smc_variant(
    source: Path,
    output: Path,
    *,
    values: dict[int, float] | None = None,
    flags: dict[int, bool] | None = None,
    description_suffix: str = "",
) -> Path:
    parsed = parse_smc(source)
    values_list = list(parsed.values)
    flags_list = list(parsed.flags)
    for index, value in (values or {}).items():
        values_list[index - 1] = float(value)
    for index, value in (flags or {}).items():
        flags_list[index - 1] = bool(value)
    variant = SmcData(
        description=(parsed.description + " " + description_suffix).strip(),
        values=values_list,
        flags=flags_list,
        text_variables=list(parsed.text_variables),
        data_files=list(parsed.data_files),
    )
    return _write_smc(output, variant)


def _export_case(input_dir: Path, stem: str, activity: np.ndarray, attenuation: np.ndarray) -> dict:
    act = write_bin(activity, input_dir / stem, "_act_av")
    atn = write_bin(attenuation, input_dir / stem, "_atn_av")
    return {
        "stem": stem,
        "activity_bin": str(act.resolve()),
        "attenuation_bin": str(atn.resolve()),
        "activity_sha256": sha256_file(act),
        "attenuation_sha256": sha256_file(atn),
        "shape": list(activity.shape),
        "dtype": "float32",
        "order": "C (Z,Y,X)",
    }


def _write_plan(root: Path, jobs: Iterable[SimindJob], specification: dict, inputs: list[dict]) -> Path:
    jobs = list(jobs)
    command_records = [job_record(job) for job in jobs]
    atomic_write_json(root / "experiment.json", specification)
    atomic_write_json(root / "inputs.json", inputs)
    atomic_write_json(root / "commands.json", command_records)
    atomic_write_text(root / "run_prepared_experiment.bat", render_batch_script(jobs), encoding="ascii")
    atomic_write_text(
        root / "analyze_prepared_experiment.bat",
        '@echo off\r\npython -m cli analyze-experiment --experiment "%~dp0"\r\n',
        encoding="ascii",
    )
    atomic_write_json(
        root / "results_template.json",
        {
            "experiment": specification["name"],
            "status": "not_run",
            "operator": None,
            "simind_version": None,
            "started_utc": None,
            "completed_utc": None,
            "observations": [],
            "pass_fail": {criterion["id"]: "not_evaluated" for criterion in specification["criteria"]},
            "notes": "",
        },
    )
    return root


def prepare_experiment(
    name: str,
    destination: Path,
    *,
    simind_exe: Path,
    smc_file: Path,
    shape: tuple[int, int, int] = (128, 128, 128),
    voxel_size_mm: float = 4.42,
) -> Path:
    if name not in EXPERIMENT_NAMES:
        raise ValueError(f"Unknown experiment: {name}")
    simind_exe = Path(simind_exe).resolve()
    smc_file = Path(smc_file).resolve()
    if not simind_exe.exists() or not smc_file.exists():
        raise FileNotFoundError("SIMIND executable and base .smc are required to prepare commands")
    root = Path(destination).resolve() / name
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Experiment directory is not empty: {root}")
    input_dir = root / "input"
    output_dir = root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[SimindJob] = []
    inputs: list[dict] = []
    center = tuple(size // 2 for size in shape)

    def add_job(
        stem: str,
        variant: Path,
        nn: int = 1,
        rr: int | None = None,
        input_stem: str | None = None,
    ):
        input_stem = input_stem or stem
        jobs.append(
            SimindJob(
                case_id=stem,
                simind_exe=simind_exe,
                smc_file=variant,
                working_dir=input_dir,
                output_stem=output_dir / stem,
                source_stem=input_stem,
                density_stem=input_stem,
                nn_multiplier=nn,
                rr_seed=rr,
            )
        )

    if name == "attenuation_ict":
        variant = _smc_variant(
            smc_file,
            input_dir / "attenuation_ict.smc",
            values={19: 0},
            flags={15: True},
            description_suffix="ICT and analytic attenuation contract",
        )
        for mu in (0.05, 0.15, 0.30):
            stem = f"uniform_mu_{mu:.2f}".replace(".", "p")
            activity = np.zeros(shape, np.float32)
            activity[center] = 1.0
            attenuation = np.full(shape, mu, np.float32)
            inputs.append(_export_case(input_dir, stem, activity, attenuation))
            add_job(stem, variant)

        # A zero-attenuation control paired with a known transverse water
        # column provides a direct line-integral test.  With the point source
        # at the cylinder centre, the primary path length is one radius for
        # every transverse view.  Scatter order is set to zero in this copied
        # SMC so the preregistered comparison is exp(-mu * length).
        radius_vox = max(2, min(20, min(shape[1:]) // 4))
        yy, xx = np.ogrid[: shape[1], : shape[2]]
        column = (yy - center[1]) ** 2 + (xx - center[2]) ** 2 <= radius_vox**2
        activity = np.zeros(shape, np.float32)
        activity[center] = 1.0
        analytic_inputs = []
        for mu in (0.0, 0.15):
            stem = f"water_column_mu_{mu:.2f}".replace(".", "p")
            attenuation = np.zeros(shape, np.float32)
            attenuation[:, column] = mu
            record = _export_case(input_dir, stem, activity, attenuation)
            record.update(
                {
                    "control": "analytic_water_column",
                    "mu_cm_inverse": mu,
                    "source_to_surface_path_cm": radius_vox * voxel_size_mm / 10.0,
                }
            )
            inputs.append(record)
            analytic_inputs.append(record)
            add_job(stem, variant)
        specification = {
            "name": name,
            "purpose": "Resolve whether /FD values are interpreted as linear attenuation, density, or another quantity.",
            "blocking_decision": "The production attenuation contract cannot be marked verified until the .ict readback is reconciled.",
            "controls": [
                "three uniform maps for .ict mapping",
                "paired mu=0 and mu=0.15 cm^-1 water columns",
                "central point source and zero scatter order",
                "Flag-15 enabled in copied SMC",
            ],
            "analytic_pair": {
                "reference_case": "water_column_mu_0p00",
                "attenuated_case": "water_column_mu_0p15",
                "mu_cm_inverse": 0.15,
                "path_length_cm": analytic_inputs[1]["source_to_surface_path_cm"],
                "expected_primary_ratio": float(
                    np.exp(-0.15 * analytic_inputs[1]["source_to_surface_path_cm"])
                ),
            },
            "criteria": [
                {"id": "ict_exists", "rule": "An .ict/readback artifact exists for every uniform map."},
                {"id": "mapping_identified", "rule": "Readback values determine a single documented /FD mapping."},
                {"id": "analytic_attenuation", "rule": "Attenuation-on/off or slab ratios agree with exp(-mu*length) within predeclared tolerance."},
            ],
        }
    elif name == "asymmetric_fiducial":
        variant = _smc_variant(smc_file, input_dir / "asymmetric_fiducial.smc", description_suffix="orientation fiducial")
        activity = np.zeros(shape, np.float32)
        # Unequal intensities and non-symmetric locations make every axis and
        # mirror operation identifiable after projection.
        points = [
            ((int(shape[0] * 0.25), int(shape[1] * 0.35), int(shape[2] * 0.70)), 1.0),
            ((int(shape[0] * 0.70), int(shape[1] * 0.78), int(shape[2] * 0.24)), 3.0),
            ((int(shape[0] * 0.58), int(shape[1] * 0.18), int(shape[2] * 0.43)), 7.0),
        ]
        for index, value in points:
            activity[index] = value
        attenuation = np.zeros(shape, np.float32)
        stem = "asymmetric_xyz"
        record = _export_case(input_dir, stem, activity, attenuation)
        record["fiducials_zyx"] = [[*index, value] for index, value in points]
        inputs.append(record)
        add_job(stem, variant)
        specification = {
            "name": name,
            "purpose": "Determine binary axis order and the canonical .a00 view/row transform.",
            "blocking_decision": "Replace the current empirical orientation statement with a tested transform.",
            "controls": ["three unequal fiducials", "zero attenuation", "recorded Z,Y,X coordinates"],
            "criteria": [
                {"id": "axis_order_unique", "rule": "Only one candidate axis/flip mapping matches all fiducials."},
                {"id": "loader_transform", "rule": "The selected mapping is encoded in one loader and regression test."},
            ],
        }
    elif name == "fov_matrix":
        activity = np.zeros(shape, np.float32)
        extent = max(2, min(35, shape[1] // 3))
        activity[center[0], center[1] - extent : center[1] + extent + 1, center[2]] = 1.0
        attenuation = np.zeros(shape, np.float32)
        inputs.append(_export_case(input_dir, "fov_source", activity, attenuation))
        for matrix in (128, 160, 208):
            stem = f"fov_{matrix}"
            variant = _smc_variant(
                smc_file,
                input_dir / f"fov_{matrix}.smc",
                values={100: matrix, 101: matrix},
                description_suffix=f"FOV {matrix}",
            )
            add_job(stem, variant, input_stem="fov_source")
        specification = {
            "name": name,
            "purpose": "Quantify the 128 workaround against 160/208 output matrices and capture any real failure.",
            "blocking_decision": "Select matrix/FOV only from successful runs and measured truncation, not folder naming.",
            "controls": ["same extended line source", "Index-100/101 only variant", "128/160/208 ladder"],
            "criteria": [
                {"id": "exit_and_artifacts", "rule": "Record exit code, stdout/stderr, .res, .mhd and .a00 size for every matrix."},
                {"id": "fov_quantified", "rule": "Report physical FOV and structural-zero fraction for each successful output."},
                {"id": "selection_justified", "rule": "Chosen production matrix is supported by measured coverage and GE 870 protocol constraints."},
            ],
        }
    elif name == "point_line_source":
        variant = _smc_variant(smc_file, input_dir / "point_line_source.smc", description_suffix="resolution QC")
        attenuation = np.zeros(shape, np.float32)
        point = np.zeros(shape, np.float32)
        point[center] = 1.0
        line = np.zeros(shape, np.float32)
        extent = max(2, min(25, shape[0] // 3))
        line[center[0] - extent : center[0] + extent + 1, center[1], center[2]] = 1.0
        for stem, activity in (("point_center", point), ("line_z", line)):
            inputs.append(_export_case(input_dir, stem, activity, attenuation))
            add_job(stem, variant, nn=10)
        specification = {
            "name": name,
            "purpose": "Measure spatial response, symmetry, centering and collimator/detector behavior.",
            "blocking_decision": "Use measured PSF/line-spread evidence before physics-accuracy claims.",
            "controls": ["central one-voxel point", "central Z line", "zero attenuation", "same protocol"],
            "criteria": [
                {"id": "centering", "rule": "Point centroid agrees with expected detector center under tested orientation."},
                {"id": "fwhm", "rule": "FWHM/FWTM are finite, reported in pixels and mm, and compared to protocol expectation."},
                {"id": "sensitivity", "rule": "Projection totals and .res sensitivity (cps/MBq) are reported under the exact tested geometry."},
                {"id": "symmetry", "rule": "Horizontal/vertical response asymmetry is quantified."},
            ],
        }
    else:
        variant = _smc_variant(smc_file, input_dir / "rr_nn_ladder.smc", description_suffix="RR NN noise QC")
        activity = np.zeros(shape, np.float32)
        zz, yy, xx = np.ogrid[: shape[0], : shape[1], : shape[2]]
        sphere = (zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2 <= 10**2
        activity[sphere] = 1.0
        attenuation = np.full(shape, 0.15, np.float32)
        base = _export_case(input_dir, "rrnn_source", activity, attenuation)
        inputs.append(base)
        for nn in (1, 5, 10):
            for rr in (1101, 1102, 1103, 1104, 1105):
                stem = f"rrnn_nn{nn}_rr{rr}"
                add_job(stem, variant, nn=nn, rr=rr, input_stem="rrnn_source")
        specification = {
            "name": name,
            "purpose": "Separate repeated-run Monte Carlo variability from spatial heterogeneity and test /NN scaling.",
            "blocking_decision": "Determine whether weighted SIMIND output is an expectation estimator and how observation noise must be added.",
            "controls": ["5 independent /RR seeds per /NN", "/NN values 1/5/10", "same spherical source and attenuation"],
            "criteria": [
                {"id": "repeat_variance", "rule": "Variance is computed voxelwise across /RR repeats, never from spatial neighbors."},
                {"id": "nn_scaling", "rule": "Relative MC variance trend across /NN is reported with confidence intervals."},
                {"id": "noise_contract", "rule": "Expectation and observation stages receive distinct, evidence-backed labels."},
            ],
        }

    specification.update(
        {
            "prepared_utc": utc_now(),
            "scope": "GE 870 CZT current liver SPECT research protocol",
            "execution_status": "prepared_not_run",
            "base_smc": str(smc_file),
            "base_smc_sha256": sha256_file(smc_file),
            "simind_executable": str(simind_exe),
            "simind_executable_sha256": sha256_file(simind_exe),
            "voxel_size_mm": voxel_size_mm,
        }
    )
    return _write_plan(root, jobs, specification, inputs)


def prepare_all_experiments(destination: Path, *, simind_exe: Path, smc_file: Path) -> list[Path]:
    return [
        prepare_experiment(name, destination, simind_exe=simind_exe, smc_file=smc_file)
        for name in EXPERIMENT_NAMES
    ]


def experiment_summary(root: Path) -> dict:
    """Read-only readiness/result summary; no inference from absent artifacts."""
    root = Path(root)
    spec = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    commands = json.loads((root / "commands.json").read_text(encoding="utf-8"))
    results = json.loads((root / "results_template.json").read_text(encoding="utf-8"))
    outputs = sorted((root / "output").glob("*"))
    return {
        "name": spec["name"],
        "execution_status": results.get("status", "not_run"),
        "prepared_jobs": len(commands),
        "observed_output_files": len(outputs),
        "criteria": spec["criteria"],
    }


def _load_auto_projection(path: Path) -> tuple[np.ndarray, int]:
    values = np.fromfile(path, dtype=np.float32)
    for matrix in (128, 160, 208, 64, 256):
        pixels = matrix * matrix
        if values.size % pixels == 0:
            views = values.size // pixels
            return values.reshape(views, matrix, matrix)[::-1, ::-1, :], matrix
    raise ValueError(f"Cannot infer projection matrix from {path.name} ({values.size} float32 values)")


def _profile_width(profile: np.ndarray, fraction: float) -> float | None:
    profile = np.asarray(profile, dtype=np.float64)
    if profile.size == 0 or not np.isfinite(profile).all() or profile.max() <= 0:
        return None
    threshold = profile.max() * fraction
    positions = np.flatnonzero(profile >= threshold)
    return float(positions[-1] - positions[0] + 1) if positions.size else None


def _fwhm(profile: np.ndarray) -> float | None:
    return _profile_width(profile, 0.5)


def _fwtm(profile: np.ndarray) -> float | None:
    return _profile_width(profile, 0.1)


def analyze_experiment(root: Path) -> dict:
    """Analyze available outputs without upgrading absent evidence to a pass."""
    root = Path(root).resolve()
    spec = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    commands = json.loads((root / "commands.json").read_text(encoding="utf-8"))
    name = spec["name"]
    observations: list[dict] = []
    missing: list[str] = []

    if name == "attenuation_ict":
        sums: dict[str, float] = {}
        for command in commands:
            stem = command["case_id"]
            a00 = root / "output" / f"{stem}.a00"
            ict_candidates = sorted((root / "output").glob(f"{stem}*.ict"))
            record = {"case_id": stem, "a00_exists": a00.exists(), "ict_files": []}
            if a00.exists():
                projection, matrix = _load_auto_projection(a00)
                projection_sum = float(projection.sum(dtype=np.float64))
                record.update({"matrix": matrix, "projection_sum": projection_sum})
                sums[stem] = projection_sum
            for ict in ict_candidates:
                raw = np.fromfile(ict, dtype=np.float32)
                finite = raw[np.isfinite(raw)]
                record["ict_files"].append(
                    {
                        "path": str(ict),
                        "bytes": ict.stat().st_size,
                        "float32_count": int(raw.size),
                        "finite_fraction": float(finite.size / max(raw.size, 1)),
                        "mean": float(finite.mean()) if finite.size else None,
                        "min": float(finite.min()) if finite.size else None,
                        "max": float(finite.max()) if finite.size else None,
                    }
                )
            if not a00.exists() or not ict_candidates:
                missing.append(stem)
            observations.append(record)
        analytic = spec["analytic_pair"]
        ref_sum = sums.get(analytic["reference_case"])
        attenuated_sum = sums.get(analytic["attenuated_case"])
        observations.append(
            {
                "control": "analytic_water_column_pair",
                "reference_case": analytic["reference_case"],
                "attenuated_case": analytic["attenuated_case"],
                "mu_cm_inverse": analytic["mu_cm_inverse"],
                "path_length_cm": analytic["path_length_cm"],
                "expected_primary_ratio": analytic["expected_primary_ratio"],
                "observed_projection_sum_ratio": (
                    float(attenuated_sum / ref_sum)
                    if ref_sum is not None and ref_sum > 0 and attenuated_sum is not None
                    else None
                ),
            }
        )
    elif name == "asymmetric_fiducial":
        path = root / "output" / "asymmetric_xyz.a00"
        if not path.exists():
            missing.append("asymmetric_xyz")
        else:
            projection, matrix = _load_auto_projection(path)
            per_view = projection.sum(axis=(1, 2), dtype=np.float64)
            peak_indices = []
            for view in np.linspace(0, projection.shape[0] - 1, min(6, projection.shape[0]), dtype=int):
                flat = projection[view].ravel()
                top = np.argpartition(flat, -3)[-3:]
                peak_indices.append(
                    {"view": int(view), "top_rows_cols": [list(map(int, np.unravel_index(i, (matrix, matrix)))) for i in top]}
                )
            observations.append(
                {
                    "path": str(path),
                    "matrix": matrix,
                    "views": int(projection.shape[0]),
                    "per_view_sum_min": float(per_view.min()),
                    "per_view_sum_max": float(per_view.max()),
                    "peak_samples": peak_indices,
                    "manual_gate": "Match peaks/centroids to recorded Z,Y,X fiducials before selecting one axis/flip mapping.",
                }
            )
    elif name == "fov_matrix":
        command_by_case = {command["case_id"]: command for command in commands}
        for matrix in (128, 160, 208):
            stem = f"fov_{matrix}"
            path = root / "output" / f"{stem}.a00"
            if not path.exists():
                missing.append(stem)
                continue
            projection, detected = _load_auto_projection(path)
            support = np.any(projection > 0, axis=0)
            variant = parse_smc(Path(command_by_case[stem]["smc"]))
            pitch_cm = float(variant.get_value(95))
            observations.append(
                {
                    "case_id": stem,
                    "detected_matrix": detected,
                    "views": int(projection.shape[0]),
                    "bytes": path.stat().st_size,
                    "nonzero_fraction": float(np.count_nonzero(projection) / projection.size),
                    "structural_zero_fraction": float(1.0 - np.count_nonzero(projection) / projection.size),
                    "detector_pitch_cm": pitch_cm,
                    "physical_fov_cm": [detected * pitch_cm, detected * pitch_cm],
                    "support_rows": np.flatnonzero(np.any(support, axis=1)).tolist(),
                    "support_cols": np.flatnonzero(np.any(support, axis=0)).tolist(),
                    "res_qc": validate_projection_artifacts(path, shape=projection.shape, require_mhd=True),
                }
            )
    elif name == "point_line_source":
        for stem in ("point_center", "line_z"):
            path = root / "output" / f"{stem}.a00"
            if not path.exists():
                missing.append(stem)
                continue
            projection, matrix = _load_auto_projection(path)
            summed = projection.sum(axis=0, dtype=np.float64)
            total = summed.sum()
            rows, cols = np.indices(summed.shape)
            res_qc = validate_projection_artifacts(path, shape=projection.shape, require_mhd=True)
            pitch_cm = res_qc.get("res_effective", {}).get(
                "detector_pitch_cm", parse_smc(Path(spec["base_smc"])).get_value(95)
            )
            fwhm_row = _fwhm(summed.sum(axis=1))
            fwhm_col = _fwhm(summed.sum(axis=0))
            fwtm_row = _fwtm(summed.sum(axis=1))
            fwtm_col = _fwtm(summed.sum(axis=0))
            observations.append(
                {
                    "case_id": stem,
                    "matrix": matrix,
                    "centroid_row": float((summed * rows).sum() / total) if total > 0 else None,
                    "centroid_col": float((summed * cols).sum() / total) if total > 0 else None,
                    "projection_sum": float(total),
                    "fwhm_row_px": fwhm_row,
                    "fwhm_col_px": fwhm_col,
                    "fwhm_row_mm": fwhm_row * pitch_cm * 10.0 if fwhm_row is not None else None,
                    "fwhm_col_mm": fwhm_col * pitch_cm * 10.0 if fwhm_col is not None else None,
                    "fwtm_row_px": fwtm_row,
                    "fwtm_col_px": fwtm_col,
                    "fwtm_row_mm": fwtm_row * pitch_cm * 10.0 if fwtm_row is not None else None,
                    "fwtm_col_mm": fwtm_col * pitch_cm * 10.0 if fwtm_col is not None else None,
                    "detector_pitch_cm": pitch_cm,
                    "sensitivity_cps_per_mbq_from_res": res_qc.get("res_effective", {}).get(
                        "sensitivity_cps_per_mbq"
                    ),
                    "res_qc": res_qc,
                }
            )
    else:
        by_nn: dict[int, list[np.ndarray]] = {1: [], 5: [], 10: []}
        for nn in by_nn:
            for rr in (1101, 1102, 1103, 1104, 1105):
                stem = f"rrnn_nn{nn}_rr{rr}"
                path = root / "output" / f"{stem}.a00"
                if not path.exists():
                    missing.append(stem)
                    continue
                projection, _ = _load_auto_projection(path)
                by_nn[nn].append(projection.astype(np.float64))
        for nn, repeats in by_nn.items():
            if len(repeats) < 2:
                observations.append({"nn": nn, "repeat_count": len(repeats), "status": "insufficient_repeats"})
                continue
            stack = np.stack(repeats)
            mean = stack.mean(axis=0)
            variance = stack.var(axis=0, ddof=1)
            positive = mean > 0
            relative_variance = variance[positive] / mean[positive] ** 2 if np.any(positive) else np.array([])
            median_relative_variance = (
                float(np.median(relative_variance)) if relative_variance.size else None
            )
            degrees_freedom = len(repeats) - 1
            ci_factors = (
                degrees_freedom / chi2.ppf(0.975, degrees_freedom),
                degrees_freedom / chi2.ppf(0.025, degrees_freedom),
            )
            observations.append(
                {
                    "nn": nn,
                    "repeat_count": len(repeats),
                    "mean_sum": float(mean.sum()),
                    "mean_voxelwise_variance": float(variance[positive].mean()) if np.any(positive) else None,
                    "median_relative_variance": median_relative_variance,
                    "median_relative_variance_95ci": (
                        [median_relative_variance * ci_factors[0], median_relative_variance * ci_factors[1]]
                        if median_relative_variance is not None
                        else None
                    ),
                    "ci_method": "chi-square interval for normal-sampling variance, summarized at the median voxel",
                    "method": "voxelwise variance across independent /RR repeats",
                }
            )

    status = "complete_outputs_pending_scientific_gate" if not missing else "incomplete_outputs"
    result = {
        "experiment": name,
        "status": status,
        "analyzed_utc": utc_now(),
        "missing_cases": missing,
        "observations": observations,
        "pass_fail": {
            criterion["id"]: ("manual_review_required" if not missing else "not_evaluated")
            for criterion in spec["criteria"]
        },
        "scientific_limit": "Automatic artifact analysis does not itself verify the acquisition protocol or physics claim.",
    }
    atomic_write_json(root / "analysis.json", result)
    return result
