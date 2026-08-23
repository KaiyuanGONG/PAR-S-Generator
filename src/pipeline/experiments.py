"""Preparation and analysis contracts for the five blocking physics tests.

This module only prepares artifacts and command plans.  It never launches
SIMIND.  Results can be added later and analyzed through the stored criteria.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.stats import chi2

from core.interfile_writer import write_bin
from core.smc_parser import SmcData, parse_smc
from pipeline.contracts import (
    CANONICAL_PROJECTION_TRANSFORM,
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    utc_now,
)
from pipeline.simind import (
    SIMIND_ARTIFACT_SUFFIXES,
    SimindJob,
    artifact_path,
    build_simind_args,
    completion_qc,
    job_record,
    render_batch_script,
    run_job,
)
from pipeline.qc import validate_projection_artifacts


EXPERIMENT_NAMES = (
    "attenuation_ict",
    "asymmetric_fiducial",
    "fov_matrix",
    "point_line_source",
    "rr_nn_ladder",
)

FOV_DETECTOR_VARIANTS = (
    ("legacy_128x128", 128, 128),
    ("index_i_160", 160, 128),
    ("index_j_208", 128, 208),
    ("native_160x208", 160, 208),
    ("swapped_208x160", 208, 160),
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


def _export_density_case(
    input_dir: Path,
    stem: str,
    activity: np.ndarray,
    density_g_cm3: np.ndarray,
) -> dict:
    """Export paired type -1 SIMIND source/density maps."""
    source_path = input_dir / f"{stem}.smi"
    source_values = np.rint(np.asarray(activity, dtype=np.float64))
    if np.any(source_values < 0) or np.any(source_values > np.iinfo(np.uint16).max):
        raise ValueError("Source map cannot be represented as uint16")
    source_values.astype("<u2").tofile(source_path)
    density_path = input_dir / f"{stem}.dmi"
    scaled = np.rint(np.asarray(density_g_cm3, dtype=np.float64) * 1000.0)
    if np.any(scaled < 0) or np.any(scaled > np.iinfo(np.uint16).max):
        raise ValueError("Density map cannot be represented as uint16 density*1000")
    scaled.astype("<u2").tofile(density_path)
    return {
        "stem": stem,
        "source_smi": str(source_path.resolve()),
        "density_dmi": str(density_path.resolve()),
        "source_sha256": sha256_file(source_path),
        "density_sha256": sha256_file(density_path),
        "shape": list(activity.shape),
        "source_dtype": "uint16 little-endian",
        "source_semantic": "relative voxel activity; sum times /NN sets simulated histories",
        "density_dtype": "uint16 little-endian",
        "density_unit": "g/cm3 times 1000",
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
        runtime_switches: tuple[str, ...] = (),
        overrides: tuple[tuple[int, str], ...] = (),
        primary_artifact_suffix: str = ".a00",
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
                overrides=overrides,
                runtime_switches=runtime_switches,
                primary_artifact_suffix=primary_artifact_suffix,
            )
        )

    if name == "attenuation_ict":
        voxel_size_cm = voxel_size_mm / 10.0
        variant = _smc_variant(
            smc_file,
            input_dir / "attenuation_ict.smc",
            values={14: -7, 15: -7},
            flags={11: True, 15: True},
            description_suffix="paired type-7 mu-times-voxel analytic attenuation contract",
        )
        # Type -7 consumes the XCAT attenuation convention: each float32 voxel
        # stores mu[cm^-1] * voxel_width[cm], not direct mu.  A central point
        # inside a transverse water column gives the same radius-long path for
        # every projection.  The stock simind.ini density threshold of 1170
        # suppresses primary attenuation for water-density type-7 voxels in
        # this build; the controlled ladder in validation-v9 located the exact
        # discontinuity.  Runtime threshold 100 separates air from every
        # non-air tissue represented by the current analytic map while both
        # configured phantom cross-section tables remain h2o.  Scattwin /CA:2
        # provides same-run air and primary images for the Beer-Lambert gate.
        atomic_write_text(
            input_dir / "attenuation_ict.win",
            "126.0,154.0,0\n",
            encoding="ascii",
        )
        radius_vox = max(2, min(20, min(shape[1:]) // 4))
        yy, xx = np.ogrid[: shape[1], : shape[2]]
        column = (yy - center[1]) ** 2 + (xx - center[2]) ** 2 <= radius_vox**2
        activity = np.zeros(shape, np.float32)
        activity[center] = 1.0
        analytic_inputs = []
        for mu in (0.0, 0.15):
            stem = f"water_column_mu_{mu:.2f}".replace(".", "p")
            stored_value = mu * voxel_size_cm
            attenuation = np.zeros(shape, np.float32)
            attenuation[:, column] = stored_value
            record = _export_case(input_dir, stem, activity, attenuation)
            record.update(
                {
                    "control": "analytic_water_column",
                    "mu_cm_inverse": mu,
                    "stored_attenuation_value": stored_value,
                    "stored_attenuation_semantic": "mu_cm_inverse_times_voxel_size_cm",
                    "stored_attenuation_unit": "dimensionless_per_voxel_optical_thickness",
                    "source_to_surface_path_vox": radius_vox,
                    "source_to_surface_path_cm": radius_vox * voxel_size_cm,
                }
            )
            inputs.append(record)
            analytic_inputs.append(record)
            add_job(
                stem,
                variant,
                nn=10000,
                rr=9600,
                overrides=((84, "1"),),
                runtime_switches=("/IN:x21,100x", "/IN:x22,3x", "/CA:2"),
                primary_artifact_suffix="_pri_w1.a00",
            )
        specification = {
            "name": name,
            "purpose": "Verify the type -7 mu-times-voxel input contract and Beer-Lambert transport at the target liver-SPECT attenuation.",
            "blocking_decision": "Protocol promotion remains blocked until internal-mu readback and same-run Scattwin primary/air transmission both pass.",
            "voxel_size_mm": voxel_size_mm,
            "voxel_size_cm": voxel_size_cm,
            "controls": [
                "type -7 float32 C-order source and attenuation maps",
                "stored attenuation equals physical mu[cm^-1] times voxel size[cm]",
                "paired mu=0 and mu=0.15 cm^-1 water columns",
                "Flag-11 enables phantom interactions and Flag-15 writes the aligned readback",
                "runtime simind.ini entry 21 is 100 (density x1000) so non-air current-map tissues use the configured h2o cross-section",
                "/IN:x22,3x writes aligned internal mu as float32 cm^-1",
                "/84:1 /CA:2 writes same-run air, total, scatter and primary photopeak images",
                "/NN:10000 (10,000 histories/view for the one-voxel source)",
                "paired controls use the same explicit terminal /RR:9600 stream",
            ],
            "unit_contract_evidence": {
                "simind_input_mode": "generic_xcat_type_minus_7",
                "formula": "stored_value = mu_cm_inverse * voxel_size_cm",
                "target_mu_cm_inverse": 0.15,
                "target_stored_value": 0.15 * voxel_size_cm,
                "xcat_reference_energy_kev": 140.0,
                "simind_assumed_reference_energy_kev": 140.5,
                "runtime_density_threshold_times_1000": 100,
                "phantom_cross_sections": ["h2o", "h2o"],
            },
            "ict_readback": {
                "mode": 3,
                "semantic": "internal_linear_attenuation_coefficient",
                "unit": "cm^-1",
                "target_mu_cm_inverse": 0.15,
                "maximum_absolute_error_cm_inverse": 0.002,
            },
            "analytic_pair": {
                "reference_case": "water_column_mu_0p00",
                "attenuated_case": "water_column_mu_0p15",
                "mu_cm_inverse": 0.15,
                "path_length_cm": analytic_inputs[1]["source_to_surface_path_cm"],
                "path_length_vox": analytic_inputs[1]["source_to_surface_path_vox"],
                "stored_attenuation_value": analytic_inputs[1]["stored_attenuation_value"],
                "air_artifact_suffix": "_air_w1.a00",
                "primary_artifact_suffix": "_pri_w1.a00",
                "primary_observable": "attenuated-run primary sum divided by its same-run air sum",
                "expected_primary_ratio": float(
                    np.exp(-0.15 * analytic_inputs[1]["source_to_surface_path_cm"])
                ),
                "maximum_ratio_relative_error": 0.10,
                "maximum_mu_absolute_error_cm_inverse": 0.02,
            },
            "criteria": [
                {"id": "ict_exists", "rule": "Both paired runs produce typed .ict/.hct readbacks."},
                {"id": "mapping_identified", "rule": "Mode-3 readback recovers 0.15 cm^-1 within 0.002 cm^-1 and zero remains zero."},
                {"id": "analytic_attenuation", "rule": "The same-run Scattwin primary/air ratio agrees with exp(-mu*length) within the preregistered tolerances."},
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
        add_job(stem, variant, nn=1000)
        specification = {
            "name": name,
            "purpose": "Determine binary axis order and the canonical .a00 view/row transform.",
            "blocking_decision": "Replace the current empirical orientation statement with a tested transform.",
            "controls": [
                "three unequal fiducials",
                "zero attenuation",
                "recorded Z,Y,X coordinates",
                "/NN:1000 (11,000 photons/view for the 1:3:7 source)",
            ],
            "criteria": [
                {"id": "axis_order_unique", "rule": "Only one candidate axis/flip mapping matches all fiducials."},
                {"id": "loader_transform", "rule": "The selected mapping is encoded in one loader and regression test."},
            ],
            "orientation_acceptance": {
                "maximum_best_score_px": 2.0,
                "minimum_second_to_best_ratio": 5.0,
                "maximum_row_residual_px": 0.25,
            },
        }
    elif name == "fov_matrix":
        activity = np.zeros(shape, np.float32)
        # A sparse full-volume lattice illuminates the detector aperture in
        # both directions without requiring a dense 128^3 source.  At the
        # production shape this is 16^3 = 4096 histories before /NN.
        stride = max(1, min(shape) // 16)
        activity[::stride, ::stride, ::stride] = 1.0
        attenuation = np.zeros(shape, np.float32)
        source_record = _export_case(input_dir, "fov_source", activity, attenuation)
        source_record.update(
            {
                "source_pattern": "sparse_full_volume_lattice",
                "lattice_stride_vox": stride,
                "source_sum": float(activity.sum(dtype=np.float64)),
            }
        )
        inputs.append(source_record)
        for stem, detector_i, detector_j in FOV_DETECTOR_VARIANTS:
            variant = _smc_variant(
                smc_file,
                input_dir / f"{stem}.smc",
                values={100: detector_i, 101: detector_j},
                description_suffix=f"CZT detector {detector_i}x{detector_j}",
            )
            add_job(stem, variant, input_stem="fov_source", nn=10)
        specification = {
            "name": name,
            "purpose": "Quantify the 128x128 CZT detector workaround against the GE-native 160x208 aperture and capture any real failure.",
            "blocking_decision": "Select detector I/J dimensions only from successful runs, measured support and the GE specification.",
            "controls": [
                "same sparse full-volume lattice source",
                "zero attenuation",
                "Index-100/101 are the only SMC variants",
                "single-axis, native 160x208 and swapped-axis controls",
                "/NN:10",
            ],
            "ge_reference": {
                "native_pitch_mm": 2.46,
                "physical_fov_mm": [393.6, 511.7],
                "expected_native_pixels": [160, 208],
                "source": "DOC2109131 NM/CT 870 CZT product data sheet, pages 2 and 10",
            },
            "criteria": [
                {"id": "exit_and_artifacts", "rule": "Record exit code, stdout/stderr, .res, .mhd and .a00 size for every matrix."},
                {"id": "fov_quantified", "rule": "Report output FOV, native detector aperture and illuminated row/column support for each successful run."},
                {"id": "selection_justified", "rule": "Chosen Index-100/101 axes reproduce the 393.6x511.7-mm GE detector without changing the 128x128, 4.4196-mm acquisition grid."},
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
            add_job(stem, variant, nn=1000)
        detector_distance_mm = float(parse_smc(variant).get_value(12)) * 10.0
        intrinsic_resolution_mm = 2.46
        hole_opening_mm = 2.26
        hole_length_mm = 45.0
        expected_system_fwhm_mm = float(
            np.hypot(
                intrinsic_resolution_mm,
                hole_opening_mm * (detector_distance_mm + hole_length_mm) / hole_length_mm,
            )
        )
        specification = {
            "name": name,
            "purpose": "Measure spatial response, symmetry, centering and collimator/detector behavior.",
            "blocking_decision": "Use measured PSF/line-spread evidence before physics-accuracy claims.",
            "controls": [
                "central one-voxel point",
                "central Z line",
                "zero attenuation",
                "same protocol",
                "/NN:1000",
            ],
            "resolution_reference": {
                "manufacturer_system_fwhm_at_100mm_mm": 7.6,
                "manufacturer_source": "DOC2109131 NM/CT 870 CZT product data sheet, page 10",
                "intrinsic_resolution_mm": intrinsic_resolution_mm,
                "hole_opening_mm": hole_opening_mm,
                "hole_length_mm": hole_length_mm,
                "tested_point_to_collimator_distance_mm": detector_distance_mm,
                "geometric_expected_system_fwhm_mm": expected_system_fwhm_mm,
                "maximum_relative_error": 0.20,
                "maximum_center_offset_px": 1.0,
                "maximum_point_axis_asymmetry": 0.25,
            },
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


def _load_command_records(root: Path) -> list[dict]:
    """Load canonical command lists and the frozen single-command object form."""
    payload = json.loads((root / "commands.json").read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"commands.json must contain one command object or a non-empty list: {root}")
    return payload


def experiment_summary(root: Path) -> dict:
    """Read-only readiness/result summary; no inference from absent artifacts."""
    root = Path(root)
    spec = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    commands = _load_command_records(root)
    result_path = next(
        (
            candidate
            for candidate in (
                root / "results.json",
                root / "analysis.json",
                root / "execution.json",
                root / "results_template.json",
            )
            if candidate.exists()
        ),
        None,
    )
    if result_path is None:
        raise FileNotFoundError(f"No result state found for prepared experiment: {root}")
    results = json.loads(result_path.read_text(encoding="utf-8"))
    outputs = sorted((root / "output").glob("*"))
    return {
        "name": spec["name"],
        "execution_status": results.get("status", "not_run"),
        "result_source": result_path.name,
        "prepared_jobs": len(commands),
        "observed_output_files": len(outputs),
        "criteria": spec["criteria"],
    }


def _job_from_record(record: dict) -> SimindJob:
    """Rebuild a prepared job from its recorded, reviewable command."""
    args = list(record["args"])
    source_stem = ""
    density_stem = ""
    nn_multiplier = 0
    rr_seed = None
    overrides: list[tuple[int, str]] = []
    runtime_switches: list[str] = []
    for token in args[2:]:
        if token.startswith("/FS:"):
            source_stem = token[4:]
        elif token.startswith("/FD:"):
            density_stem = token[4:]
        elif token.startswith("/NN:"):
            nn_multiplier = int(token[4:])
        elif token.startswith("/RR:"):
            rr_seed = int(token[4:])
        else:
            switches = re.findall(r"/[A-Za-z0-9]+(?::[-+A-Za-z0-9.,]+)?", token)
            if not switches or "".join(switches) != token:
                raise ValueError(f"Invalid prepared SIMIND switch bundle: {token}")
            for switch in switches:
                match = re.fullmatch(r"/(\d+):(.*)", switch)
                if match:
                    overrides.append((int(match.group(1)), match.group(2)))
                else:
                    runtime_switches.append(switch)
    if not source_stem or not density_stem:
        raise ValueError(f"Prepared command is missing /FS or /FD: {record['case_id']}")
    job = SimindJob(
        case_id=record["case_id"],
        simind_exe=Path(record["executable"]),
        smc_file=Path(record["smc"]),
        working_dir=Path(record["working_dir"]),
        output_stem=Path(record["output_stem"]),
        source_stem=source_stem,
        density_stem=density_stem,
        nn_multiplier=nn_multiplier,
        rr_seed=rr_seed,
        overrides=tuple(overrides),
        runtime_switches=tuple(runtime_switches),
        primary_artifact_suffix=record.get("primary_artifact_suffix", ".a00"),
    )
    if build_simind_args(job) != args:
        raise ValueError(f"Prepared command cannot be reproduced exactly: {record['case_id']}")
    return job


def _artifact_records(output_stem: Path) -> list[dict]:
    records: list[dict] = []
    for suffix in SIMIND_ARTIFACT_SUFFIXES:
        path = output_stem.parent / f"{output_stem.name}{suffix}"
        if path.exists():
            records.append(
                {
                    "path": str(path.resolve()),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return records


def execute_prepared_experiment(root: Path, *, resume: bool = False) -> dict:
    """Sequentially run a reviewed experiment with resumable evidence capture."""
    root = Path(root).resolve()
    command_records = _load_command_records(root)
    execution_path = root / "execution.json"
    execution = {
        "experiment": json.loads((root / "experiment.json").read_text(encoding="utf-8"))["name"],
        "status": "running",
        "started_utc": utc_now(),
        "completed_utc": None,
        "resume": bool(resume),
        "jobs": [],
    }
    if resume and execution_path.exists():
        previous = json.loads(execution_path.read_text(encoding="utf-8"))
        execution["started_utc"] = previous.get("started_utc", execution["started_utc"])

    logs_dir = root / "logs"
    for record in command_records:
        job = _job_from_record(record)
        projection_path = artifact_path(job.output_stem, job.primary_artifact_suffix)
        log_path = logs_dir / f"{job.case_id}.log"
        reused = False
        if projection_path.exists():
            if not resume:
                raise FileExistsError(
                    f"Output already exists for {job.case_id}; use resume only after review: {projection_path}"
                )
            qc = completion_qc(job)
            qc["exit_code"] = 0
            reused = True
        else:
            qc = run_job(job, log_path)
        execution["jobs"].append(
            {
                "case_id": job.case_id,
                "status": (
                    "reused_verified"
                    if reused and qc.get("status") == "passed"
                    else "reused_failed_qc"
                    if reused
                    else qc.get("status", "failed")
                ),
                "command": [str(job.simind_exe.resolve()), *build_simind_args(job)],
                "working_dir": str(job.working_dir.resolve()),
                "log": str(log_path.resolve()) if log_path.exists() else None,
                "log_provenance": "reused_existing" if reused and log_path.exists() else "current_run" if not reused else "unavailable",
                "qc": qc,
                "artifacts": _artifact_records(job.output_stem),
            }
        )
        atomic_write_json(execution_path, execution)

    failed = [job for job in execution["jobs"] if job["qc"].get("status") != "passed"]
    execution["status"] = "failed" if failed else "completed"
    execution["completed_utc"] = utc_now()
    atomic_write_json(execution_path, execution)
    return execution


def _load_auto_projection(path: Path, *, canonical: bool = True) -> tuple[np.ndarray, int]:
    values = np.fromfile(path, dtype=np.float32)
    for matrix in (128, 160, 208, 64, 256):
        pixels = matrix * matrix
        if values.size % pixels == 0:
            views = values.size // pixels
            raw = values.reshape(views, matrix, matrix)
            return (raw[:, ::-1, :] if canonical else raw), matrix
    raise ValueError(f"Cannot infer projection matrix from {path.name} ({values.size} float32 values)")


def _load_ict_with_header(path: Path) -> tuple[np.ndarray, dict]:
    """Load a SIMIND ICT volume using its Interfile HCT dtype contract."""
    path = Path(path)
    header_path = path.with_suffix(".hct")
    if not header_path.exists():
        raise FileNotFoundError(f"Missing ICT header: {header_path}")
    fields: dict[str, str] = {}
    for line in header_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":=" not in line:
            continue
        key, value = line.split(":=", 1)
        fields[key.strip().lstrip("!").lower()] = value.strip()
    bytes_per_pixel = int(fields["number of bytes per pixel"])
    number_format = fields["number format"].lower()
    byte_order = fields.get("imagedata byte order", "LITTLEENDIAN").upper()
    endian = "<" if "LITTLE" in byte_order else ">"
    if "unsigned integer" in number_format:
        dtype = np.dtype(f"{endian}u{bytes_per_pixel}")
    elif "signed integer" in number_format or number_format == "integer":
        dtype = np.dtype(f"{endian}i{bytes_per_pixel}")
    elif "float" in number_format or "real" in number_format:
        dtype = np.dtype(f"{endian}f{bytes_per_pixel}")
    else:
        raise ValueError(f"Unsupported ICT number format: {number_format}")
    shape = tuple(int(fields[f"matrix size [{axis}]"]) for axis in (3, 2, 1))
    values = np.fromfile(path, dtype=dtype)
    if values.size != int(np.prod(shape)):
        raise ValueError(
            f"ICT size mismatch for {path.name}: expected {int(np.prod(shape))}, got {values.size}"
        )
    return values.reshape(shape), {
        "header": str(header_path.resolve()),
        "dtype": dtype.str,
        "shape": list(shape),
        "number_format": fields["number format"],
        "bytes_per_pixel": bytes_per_pixel,
        "byte_order": byte_order,
        "declared_units": fields.get(";# units of data (ect)"),
    }


def _load_scatter_primary_csv(path: Path) -> dict:
    rows = np.loadtxt(path, skiprows=1, dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] != 2:
        raise ValueError(f"Unexpected Index-85=4 CSV shape for {path}: {rows.shape}")
    return {
        "path": str(path.resolve()),
        "rows": int(rows.shape[0]),
        "scatter_sum": float(rows[:, 0].sum()),
        "primary_sum": float(rows[:, 1].sum()),
        "sha256": sha256_file(path),
    }


def _score_orientation_candidates(
    raw: np.ndarray,
    fiducials_zyx: list[list[float]],
    *,
    start_angle_deg: float = 180.0,
    rotation_step_deg: float = 6.0,
) -> list[dict]:
    """Rank view/row/column flips and X/Y exchange against known fiducials."""
    views, matrix, _ = raw.shape
    center = (matrix - 1) / 2.0
    theta = np.deg2rad(start_angle_deg + rotation_step_deg * np.arange(views))
    z_positions = np.asarray([point[0] for point in fiducials_zyx], dtype=np.float64)
    order = np.argsort(z_positions)
    bounds = [0]
    bounds.extend(
        int((z_positions[order[index]] + z_positions[order[index + 1]]) // 2 + 1)
        for index in range(len(order) - 1)
    )
    bounds.append(matrix)
    bands = {int(order[index]): (bounds[index], bounds[index + 1]) for index in range(len(order))}
    candidates: list[dict] = []
    for view_flip in (False, True):
        for row_flip in (False, True):
            for column_flip in (False, True):
                for xy_swap in (False, True):
                    projection = raw[::-1] if view_flip else raw
                    projection = projection[:, ::-1, :] if row_flip else projection
                    projection = projection[:, :, ::-1] if column_flip else projection
                    row_centroids: list[float] = []
                    column_errors: list[float] = []
                    valid_source_views = 0
                    for index, point in enumerate(fiducials_zyx):
                        lower, upper = bands[index]
                        slab = projection[:, lower:upper, :].astype(np.float64)
                        sums = slab.sum(axis=(1, 2))
                        valid = sums > 0
                        valid_source_views += int(np.count_nonzero(valid))
                        if not np.any(valid) or sums.sum() <= 0:
                            continue
                        rows = np.arange(lower, upper, dtype=np.float64)[None, :, None]
                        columns = np.arange(matrix, dtype=np.float64)[None, None, :]
                        row_centroids.append(float((slab * rows).sum() / sums.sum()))
                        observed = (slab[valid] * columns).sum(axis=(1, 2)) / sums[valid]
                        source_x, source_y = (point[2], point[1]) if not xy_swap else (point[1], point[2])
                        expected = (
                            center
                            + (source_x - center) * np.cos(theta[valid])
                            + (source_y - center) * np.sin(theta[valid])
                        )
                        column_errors.extend((observed - expected).tolist())
                    if len(row_centroids) != len(fiducials_zyx) or not column_errors:
                        row_rms = float("inf")
                        column_rms = float("inf")
                        row_offset = None
                    else:
                        offsets = np.asarray(row_centroids) - z_positions
                        row_offset = float(offsets.mean())
                        row_rms = float(np.sqrt(np.mean((offsets - row_offset) ** 2)))
                        column_rms = float(np.sqrt(np.mean(np.square(column_errors))))
                    score = float(np.hypot(row_rms, column_rms))
                    transform = "raw[{}, {}, {}]".format(
                        "::-1" if view_flip else ":",
                        "::-1" if row_flip else ":",
                        "::-1" if column_flip else ":",
                    ).replace(" ", "")
                    candidates.append(
                        {
                            "transform": transform,
                            "view_flip": view_flip,
                            "row_flip": row_flip,
                            "column_flip": column_flip,
                            "xy_swap": xy_swap,
                            "score_px": score,
                            "row_residual_px": row_rms,
                            "column_residual_px": column_rms,
                            "row_common_offset_px": row_offset,
                            "row_centroids": row_centroids,
                            "valid_source_views": valid_source_views,
                        }
                    )
    return sorted(candidates, key=lambda candidate: candidate["score_px"])


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
    commands = _load_command_records(root)
    name = spec["name"]
    observations: list[dict] = []
    missing: list[str] = []
    analysis_blockers: list[str] = []
    pass_fail_override: dict[str, str] | None = None

    if name == "attenuation_ict":
        sums: dict[str, float] = {}
        primary_artifact_sums: dict[str, float] = {}
        air_artifact_sums: dict[str, float] = {}
        ict_values: dict[str, np.ndarray] = {}
        analytic_spec = spec["analytic_pair"]
        primary_suffix = analytic_spec.get(
            "primary_artifact_suffix",
            analytic_spec.get("primary_component_suffix", ".b02"),
        )
        air_suffix = analytic_spec.get("air_artifact_suffix")
        for command in commands:
            stem = command["case_id"]
            a00 = root / "output" / f"{stem}.a00"
            ict_candidates = sorted((root / "output").glob(f"{stem}*.ict"))
            output_stem = root / "output" / stem
            primary_artifact = artifact_path(output_stem, primary_suffix)
            air_artifact = artifact_path(output_stem, air_suffix) if air_suffix else None
            record = {
                "case_id": stem,
                "a00_exists": a00.exists(),
                "ict_files": [],
                "primary_artifact_suffix": primary_suffix,
                "primary_artifact": None,
                "air_artifact_suffix": air_suffix,
                "air_artifact": None,
            }
            if a00.exists():
                projection, matrix = _load_auto_projection(a00)
                projection_sum = float(projection.sum(dtype=np.float64))
                record.update({"matrix": matrix, "projection_sum": projection_sum})
                sums[stem] = projection_sum
            if primary_artifact.exists():
                component_values = np.fromfile(primary_artifact, dtype=np.float32)
                component_finite = bool(
                    component_values.size > 0 and np.isfinite(component_values).all()
                )
                component_sum = (
                    float(component_values.sum(dtype=np.float64))
                    if component_finite
                    else None
                )
                record["primary_artifact"] = {
                    "path": str(primary_artifact.resolve()),
                    "dtype": "float32",
                    "value_count": int(component_values.size),
                    "finite": component_finite,
                    "sum": component_sum,
                    "sha256": sha256_file(primary_artifact),
                }
                if component_sum is not None:
                    primary_artifact_sums[stem] = component_sum
            if air_artifact is not None and air_artifact.exists():
                air_values = np.fromfile(air_artifact, dtype=np.float32)
                air_finite = bool(air_values.size > 0 and np.isfinite(air_values).all())
                air_sum = float(air_values.sum(dtype=np.float64)) if air_finite else None
                record["air_artifact"] = {
                    "path": str(air_artifact.resolve()),
                    "dtype": "float32",
                    "value_count": int(air_values.size),
                    "finite": air_finite,
                    "sum": air_sum,
                    "sha256": sha256_file(air_artifact),
                }
                if air_sum is not None:
                    air_artifact_sums[stem] = air_sum
            for ict in ict_candidates:
                raw, ict_header = _load_ict_with_header(ict)
                ict_values[stem] = raw
                finite = raw[np.isfinite(raw)]
                positive = finite[finite > 0]
                unique_positive, counts = np.unique(positive, return_counts=True)
                modal_value = (
                    float(unique_positive[int(np.argmax(counts))]) if unique_positive.size else None
                )
                record["ict_files"].append(
                    {
                        "path": str(ict),
                        "bytes": ict.stat().st_size,
                        **ict_header,
                        "value_count": int(raw.size),
                        "finite_fraction": float(finite.size / max(raw.size, 1)),
                        "mean": float(finite.mean()) if finite.size else None,
                        "min": float(finite.min()) if finite.size else None,
                        "max": float(finite.max()) if finite.size else None,
                        "unique_positive_values": unique_positive.astype(float).tolist(),
                        "modal_positive_value": modal_value,
                        "sha256": sha256_file(ict),
                    }
                )
            if (
                not ict_candidates
                or not primary_artifact.exists()
                or (air_artifact is not None and not air_artifact.exists())
            ):
                missing.append(stem)
            observations.append(record)
        readback = spec["ict_readback"]
        reference_stem = spec["analytic_pair"]["reference_case"]
        attenuated_stem = spec["analytic_pair"]["attenuated_case"]
        reference_ict = ict_values.get(reference_stem)
        attenuated_ict = ict_values.get(attenuated_stem)
        reference_max_abs = (
            float(np.max(np.abs(reference_ict))) if reference_ict is not None else None
        )
        attenuated_positive = (
            attenuated_ict[np.isfinite(attenuated_ict) & (attenuated_ict > 0)]
            if attenuated_ict is not None
            else np.asarray([], dtype=np.float32)
        )
        readback_mu = (
            float(np.median(attenuated_positive)) if attenuated_positive.size else None
        )
        readback_error = (
            abs(readback_mu - readback["target_mu_cm_inverse"])
            if readback_mu is not None
            else None
        )
        mapping_identified = bool(
            reference_max_abs is not None
            and reference_max_abs <= 1e-6
            and readback_error is not None
            and readback_error <= readback["maximum_absolute_error_cm_inverse"]
        )
        observations.append(
            {
                "control": "ict_internal_mu_readback",
                "mode": readback["mode"],
                "semantic": readback["semantic"],
                "unit": readback["unit"],
                "reference_max_absolute_value": reference_max_abs,
                "target_mu_cm_inverse": readback["target_mu_cm_inverse"],
                "observed_positive_median_mu_cm_inverse": readback_mu,
                "absolute_error_cm_inverse": readback_error,
                "maximum_absolute_error_cm_inverse": readback[
                    "maximum_absolute_error_cm_inverse"
                ],
                "passed": mapping_identified,
                "input_contract": spec["unit_contract_evidence"],
            }
        )
        analytic = spec["analytic_pair"]
        ref_sum = sums.get(analytic["reference_case"])
        attenuated_sum = sums.get(analytic["attenuated_case"])
        total_ratio = (
            float(attenuated_sum / ref_sum)
            if ref_sum is not None and ref_sum > 0 and attenuated_sum is not None
            else None
        )
        primary_attenuated_sum = primary_artifact_sums.get(analytic["attenuated_case"])
        primary_ref_sum = primary_artifact_sums.get(analytic["reference_case"])
        air_attenuated_sum = air_artifact_sums.get(analytic["attenuated_case"])
        air_ref_sum = air_artifact_sums.get(analytic["reference_case"])
        if air_suffix:
            primary_ratio = (
                float(primary_attenuated_sum / air_attenuated_sum)
                if primary_attenuated_sum is not None
                and air_attenuated_sum is not None
                and air_attenuated_sum > 0
                else None
            )
            reference_primary_air_ratio = (
                float(primary_ref_sum / air_ref_sum)
                if primary_ref_sum is not None and air_ref_sum is not None and air_ref_sum > 0
                else None
            )
        else:
            primary_ratio = (
                float(primary_attenuated_sum / primary_ref_sum)
                if primary_ref_sum is not None
                and primary_ref_sum > 0
                and primary_attenuated_sum is not None
                else None
            )
            reference_primary_air_ratio = None
        ratio = primary_ratio
        observed_mu = (
            float(-np.log(ratio) / analytic["path_length_cm"])
            if ratio is not None and 0 < ratio <= 1
            else None
        )
        ratio_relative_error = (
            abs(ratio - analytic["expected_primary_ratio"]) / analytic["expected_primary_ratio"]
            if ratio is not None
            else None
        )
        mu_absolute_error = (
            abs(observed_mu - analytic["mu_cm_inverse"])
            if observed_mu is not None
            else None
        )
        path_length_vox = float(
            analytic.get(
                "path_length_vox",
                analytic["path_length_cm"] / (float(spec["voxel_size_mm"]) / 10.0),
            )
        )
        stored_attenuation_value = float(
            analytic.get("stored_attenuation_value", analytic["mu_cm_inverse"])
        )
        expected_if_stored_per_voxel = float(np.exp(-stored_attenuation_value * path_length_vox))
        per_voxel_ratio_relative_error = (
            abs(primary_ratio - expected_if_stored_per_voxel) / expected_if_stored_per_voxel
            if primary_ratio is not None
            else None
        )
        primary_control_valid = bool(
            primary_ratio is not None
            and (
                not air_suffix
                or (
                    reference_primary_air_ratio is not None
                    and abs(reference_primary_air_ratio - 1.0) <= 0.02
                )
            )
        )
        execution_path = root / "execution.json"
        if execution_path.exists():
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
            command_failures = [
                failure
                for job in execution.get("jobs", [])
                for failure in job.get("qc", {}).get("failures", [])
                if failure.startswith("res_command_mismatch:")
            ]
            if command_failures:
                primary_control_valid = False
        if not primary_control_valid:
            analysis_blockers.append(
                "analytic_primary_control_invalid: no verified primary observable"
            )
        analytic_passed = bool(
            primary_control_valid
            and mapping_identified
            and ratio_relative_error is not None
            and ratio_relative_error <= analytic["maximum_ratio_relative_error"]
            and mu_absolute_error is not None
            and mu_absolute_error <= analytic["maximum_mu_absolute_error_cm_inverse"]
        )
        observations.append(
            {
                "control": "analytic_water_column_pair",
                "reference_case": analytic["reference_case"],
                "attenuated_case": analytic["attenuated_case"],
                "mu_cm_inverse": analytic["mu_cm_inverse"],
                "path_length_cm": analytic["path_length_cm"],
                "expected_primary_ratio": analytic["expected_primary_ratio"],
                "observed_total_projection_sum_ratio": total_ratio,
                "primary_artifact_suffix": primary_suffix,
                "air_artifact_suffix": air_suffix,
                "primary_observable": analytic.get(
                    "primary_observable",
                    analytic.get("primary_component_definition"),
                ),
                "reference_primary_sum": primary_ref_sum,
                "reference_air_sum": air_ref_sum,
                "reference_primary_air_ratio": reference_primary_air_ratio,
                "attenuated_primary_sum": primary_attenuated_sum,
                "attenuated_air_sum": air_attenuated_sum,
                "observed_primary_ratio": primary_ratio,
                "ratio_used_for_gate": ratio,
                "observed_mu_cm_inverse": observed_mu,
                "ratio_relative_error": ratio_relative_error,
                "mu_absolute_error_cm_inverse": mu_absolute_error,
                "path_length_vox": path_length_vox,
                "stored_attenuation_value": stored_attenuation_value,
                "expected_if_stored_value_is_per_voxel": expected_if_stored_per_voxel,
                "per_voxel_hypothesis_relative_error": per_voxel_ratio_relative_error,
                "maximum_ratio_relative_error": analytic["maximum_ratio_relative_error"],
                "maximum_mu_absolute_error_cm_inverse": analytic[
                    "maximum_mu_absolute_error_cm_inverse"
                ],
                "ict_readback_passed": mapping_identified,
                "primary_control_valid": primary_control_valid,
                "scientific_verdict": (
                    "passed"
                    if analytic_passed
                    else "failed_threshold"
                    if primary_control_valid
                    else "failed_invalid_control"
                ),
            }
        )
        pass_fail_override = {
            "ict_exists": "passed" if not missing else "not_evaluated",
            "mapping_identified": "passed" if mapping_identified else "failed",
            "analytic_attenuation": (
                "passed"
                if analytic_passed
                else "failed_threshold"
                if primary_control_valid
                else "failed_invalid_control"
            ),
        }
    elif name == "asymmetric_fiducial":
        path = root / "output" / "asymmetric_xyz.a00"
        if not path.exists():
            missing.append("asymmetric_xyz")
        else:
            raw, matrix = _load_auto_projection(path, canonical=False)
            input_records = json.loads((root / "inputs.json").read_text(encoding="utf-8"))
            fiducials = input_records[0]["fiducials_zyx"]
            candidates = _score_orientation_candidates(raw, fiducials)
            best = candidates[0]
            second = candidates[1]
            acceptance = spec.get("orientation_acceptance", {})
            ratio = float(second["score_px"] / best["score_px"]) if best["score_px"] > 0 else float("inf")
            orientation_unique = bool(
                best["score_px"] <= acceptance.get("maximum_best_score_px", 2.0)
                and ratio >= acceptance.get("minimum_second_to_best_ratio", 5.0)
                and best["row_residual_px"] <= acceptance.get("maximum_row_residual_px", 0.25)
                and best["transform"] == CANONICAL_PROJECTION_TRANSFORM
                and not best["xy_swap"]
            )
            if not orientation_unique:
                analysis_blockers.append("orientation_candidate_not_unique_or_not_canonical")
            projection = raw[:, ::-1, :]
            per_view = projection.sum(axis=(1, 2), dtype=np.float64)
            nonzero_pixels_per_view = np.count_nonzero(projection, axis=(1, 2))
            statistics_sufficient = bool(
                np.all(per_view > 0) and np.min(nonzero_pixels_per_view) >= 3
            )
            if not statistics_sufficient:
                analysis_blockers.append(
                    "orientation_statistics_insufficient: every view must be nonzero and contain at least three positive pixels"
                )
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
                    "nonzero_view_count": int(np.count_nonzero(per_view > 0)),
                    "zero_view_indices": np.flatnonzero(per_view <= 0).astype(int).tolist(),
                    "nonzero_pixels_total": int(np.count_nonzero(projection)),
                    "nonzero_pixels_per_view_min": int(nonzero_pixels_per_view.min()),
                    "nonzero_pixels_per_view_median": float(np.median(nonzero_pixels_per_view)),
                    "nonzero_pixels_per_view_max": int(nonzero_pixels_per_view.max()),
                    "statistics_sufficient_for_manual_gate": statistics_sufficient,
                    "selected_transform": best["transform"],
                    "selected_xy_swap": best["xy_swap"],
                    "best_score_px": best["score_px"],
                    "second_best_score_px": second["score_px"],
                    "second_to_best_ratio": ratio,
                    "orientation_unique_under_preregistered_thresholds": orientation_unique,
                    "orientation_candidates": candidates,
                    "peak_samples": peak_indices,
                    "manual_gate": "Match peaks/centroids to recorded Z,Y,X fiducials before selecting one axis/flip mapping.",
                }
            )
    elif name == "fov_matrix":
        command_by_case = {command["case_id"]: command for command in commands}
        fov_observations: list[dict] = []
        for stem, requested_i, requested_j in FOV_DETECTOR_VARIANTS:
            path = root / "output" / f"{stem}.a00"
            if not path.exists():
                missing.append(stem)
                continue
            projection, detected = _load_auto_projection(path)
            support = np.any(projection > 0, axis=0)
            variant = parse_smc(Path(command_by_case[stem]["smc"]))
            pitch_cm = float(variant.get_value(95))
            output_pixel_cm = float(variant.get_value(28))
            output_i = int(round(variant.get_value(76)))
            output_j = int(round(variant.get_value(77)))
            detector_i = int(round(variant.get_value(100)))
            detector_j = int(round(variant.get_value(101)))
            fov_observation = {
                    "case_id": stem,
                    "detected_output_matrix": detected,
                    "views": int(projection.shape[0]),
                    "bytes": path.stat().st_size,
                    "nonzero_fraction": float(np.count_nonzero(projection) / projection.size),
                    "structural_zero_fraction": float(1.0 - np.count_nonzero(projection) / projection.size),
                    "output_matrix_i_j": [output_i, output_j],
                    "output_pixel_size_cm": output_pixel_cm,
                    "output_fov_cm": [output_i * output_pixel_cm, output_j * output_pixel_cm],
                    "requested_detector_pixels_i_j": [requested_i, requested_j],
                    "effective_detector_pixels_i_j": [detector_i, detector_j],
                    "detector_native_pitch_cm": pitch_cm,
                    "detector_physical_fov_cm": [detector_i * pitch_cm, detector_j * pitch_cm],
                    "support_rows": np.flatnonzero(np.any(support, axis=1)).tolist(),
                    "support_cols": np.flatnonzero(np.any(support, axis=0)).tolist(),
                    "res_qc": validate_projection_artifacts(path, shape=projection.shape, require_mhd=True),
                }
            observations.append(fov_observation)
            fov_observations.append(fov_observation)
        by_case = {item["case_id"]: item for item in fov_observations}
        all_artifacts_valid = bool(
            len(fov_observations) == len(FOV_DETECTOR_VARIANTS)
            and all(item["res_qc"]["status"] == "passed" for item in fov_observations)
        )
        fov_quantified = bool(
            all_artifacts_valid
            and all(
                item["requested_detector_pixels_i_j"] == item["effective_detector_pixels_i_j"]
                and item["output_matrix_i_j"] == [128, 128]
                and item["detected_output_matrix"] == 128
                for item in fov_observations
            )
        )
        native = by_case.get("native_160x208")
        legacy = by_case.get("legacy_128x128")
        ge_target_cm = [value / 10.0 for value in spec["ge_reference"]["physical_fov_mm"]]
        selection_justified = bool(
            fov_quantified
            and native is not None
            and all(
                abs(actual - target) <= 0.05
                for actual, target in zip(native["detector_physical_fov_cm"], ge_target_cm)
            )
        )
        observations.append(
            {
                "control": "fov_selection",
                "recommended_variant": "native_160x208" if selection_justified else None,
                "recommended_index_100_101": [160, 208] if selection_justified else None,
                "ge_target_detector_fov_cm": ge_target_cm,
                "measured_detector_fov_cm": (
                    native["detector_physical_fov_cm"] if native is not None else None
                ),
                "projection_grid_remains_128x128": bool(
                    native is not None and native["output_matrix_i_j"] == [128, 128]
                ),
                "index_100_expands_projection_columns": bool(
                    native is not None
                    and legacy is not None
                    and len(native["support_cols"]) > len(legacy["support_cols"])
                ),
                "index_101_expands_projection_rows": bool(
                    native is not None
                    and legacy is not None
                    and len(native["support_rows"]) > len(legacy["support_rows"])
                ),
                "native_to_legacy_sensitivity_ratio": (
                    native["res_qc"]["res_effective"]["sensitivity_cps_per_mbq"]
                    / legacy["res_qc"]["res_effective"]["sensitivity_cps_per_mbq"]
                    if native is not None and legacy is not None
                    else None
                ),
                "scope": "GE NM/CT 870 CZT liver SPECT protocol only",
            }
        )
        pass_fail_override = {
            "exit_and_artifacts": "passed" if all_artifacts_valid else "failed",
            "fov_quantified": "passed" if fov_quantified else "failed",
            "selection_justified": "passed" if selection_justified else "failed",
        }
    elif name == "point_line_source":
        point_line_observations: list[dict] = []
        output_pixel_cm = float(parse_smc(Path(spec["base_smc"])).get_value(28))
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
            detector_pitch_cm = res_qc.get("res_effective", {}).get(
                "detector_pitch_cm", parse_smc(Path(spec["base_smc"])).get_value(95)
            )
            fwhm_row = _fwhm(summed.sum(axis=1))
            fwhm_col = _fwhm(summed.sum(axis=0))
            fwtm_row = _fwtm(summed.sum(axis=1))
            fwtm_col = _fwtm(summed.sum(axis=0))
            point_line_observation = {
                    "case_id": stem,
                    "matrix": matrix,
                    "centroid_row": float((summed * rows).sum() / total) if total > 0 else None,
                    "centroid_col": float((summed * cols).sum() / total) if total > 0 else None,
                    "projection_sum": float(total),
                    "fwhm_row_px": fwhm_row,
                    "fwhm_col_px": fwhm_col,
                    "fwhm_row_mm": fwhm_row * output_pixel_cm * 10.0 if fwhm_row is not None else None,
                    "fwhm_col_mm": fwhm_col * output_pixel_cm * 10.0 if fwhm_col is not None else None,
                    "fwtm_row_px": fwtm_row,
                    "fwtm_col_px": fwtm_col,
                    "fwtm_row_mm": fwtm_row * output_pixel_cm * 10.0 if fwtm_row is not None else None,
                    "fwtm_col_mm": fwtm_col * output_pixel_cm * 10.0 if fwtm_col is not None else None,
                    "projection_pixel_size_mm": output_pixel_cm * 10.0,
                    "detector_pitch_mm": detector_pitch_cm * 10.0,
                    "sensitivity_cps_per_mbq_from_res": res_qc.get("res_effective", {}).get(
                        "sensitivity_cps_per_mbq"
                    ),
                    "res_qc": res_qc,
                }
            observations.append(point_line_observation)
            point_line_observations.append(point_line_observation)
        by_case = {item["case_id"]: item for item in point_line_observations}
        point = by_case.get("point_center")
        line = by_case.get("line_z")
        reference = spec.get("resolution_reference", {})
        expected_fwhm = reference.get("geometric_expected_system_fwhm_mm")
        expected_center = 64.0
        center_offset = (
            float(
                np.hypot(
                    point["centroid_row"] - expected_center,
                    point["centroid_col"] - expected_center,
                )
            )
            if point is not None
            else None
        )
        line_fwhm_relative_error = (
            abs(line["fwhm_col_mm"] - expected_fwhm) / expected_fwhm
            if line is not None and line["fwhm_col_mm"] is not None and expected_fwhm
            else None
        )
        point_axis_asymmetry = (
            abs(point["fwhm_row_mm"] - point["fwhm_col_mm"])
            / np.mean([point["fwhm_row_mm"], point["fwhm_col_mm"]])
            if point is not None and point["fwhm_row_mm"] and point["fwhm_col_mm"]
            else None
        )
        artifact_passed = bool(
            len(point_line_observations) == 2
            and all(item["res_qc"]["status"] == "passed" for item in point_line_observations)
        )
        centering_passed = bool(
            artifact_passed
            and center_offset is not None
            and center_offset <= reference.get("maximum_center_offset_px", 1.0)
        )
        fwhm_passed = bool(
            artifact_passed
            and line_fwhm_relative_error is not None
            and line_fwhm_relative_error <= reference.get("maximum_relative_error", 0.20)
        )
        symmetry_passed = bool(
            artifact_passed
            and point_axis_asymmetry is not None
            and point_axis_asymmetry <= reference.get("maximum_point_axis_asymmetry", 0.25)
        )
        observations.append(
            {
                "control": "point_line_acceptance",
                "expected_detector_center_zero_based": [expected_center, expected_center],
                "point_center_offset_px": center_offset,
                "geometric_expected_system_fwhm_mm": expected_fwhm,
                "line_transverse_fwhm_mm": line["fwhm_col_mm"] if line is not None else None,
                "line_fwhm_relative_error": line_fwhm_relative_error,
                "point_axis_fwhm_asymmetry": point_axis_asymmetry,
                "manufacturer_reference": reference.get("manufacturer_source"),
            }
        )
        pass_fail_override = {
            "centering": "passed" if centering_passed else "failed",
            "fwhm": "passed" if fwhm_passed else "failed",
            "sensitivity": "passed" if artifact_passed else "failed",
            "symmetry": "passed" if symmetry_passed else "failed",
        }
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
        stacks = {
            nn: np.stack(repeats)
            for nn, repeats in by_nn.items()
            if len(repeats) >= 2
        }
        union_support = (
            np.logical_or.reduce([stack.mean(axis=0) > 0 for stack in stacks.values()])
            if stacks
            else np.array([], dtype=bool)
        )
        ladder_rows: list[dict] = []
        for nn, repeats in by_nn.items():
            if len(repeats) < 2:
                observations.append({"nn": nn, "repeat_count": len(repeats), "status": "insufficient_repeats"})
                continue
            stack = stacks[nn]
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
            integrated_sums = stack.sum(axis=(1, 2, 3), dtype=np.float64)
            integrated_variance = float(integrated_sums.var(ddof=1))
            fixed_support_mean_variance = (
                float(variance[union_support].mean()) if np.any(union_support) else None
            )
            row = {
                    "nn": nn,
                    "repeat_count": len(repeats),
                    "mean_sum": float(mean.sum()),
                    "integrated_projection_sums": integrated_sums.tolist(),
                    "integrated_sum_variance": integrated_variance,
                    "integrated_sum_variance_95ci": [
                        integrated_variance * ci_factors[0],
                        integrated_variance * ci_factors[1],
                    ],
                    "integrated_sum_cv": float(integrated_sums.std(ddof=1) / integrated_sums.mean()),
                    "mean_voxelwise_variance": float(variance[positive].mean()) if np.any(positive) else None,
                    "fixed_union_support_voxels": int(np.count_nonzero(union_support)),
                    "fixed_union_support_mean_voxelwise_variance": fixed_support_mean_variance,
                    "median_relative_variance": median_relative_variance,
                    "median_relative_variance_95ci": (
                        [median_relative_variance * ci_factors[0], median_relative_variance * ci_factors[1]]
                        if median_relative_variance is not None
                        else None
                    ),
                    "ci_method": "chi-square interval for normal-sampling variance, summarized at the median voxel",
                    "method": "voxelwise variance across independent /RR repeats",
                }
            observations.append(row)
            ladder_rows.append(row)
        repeat_variance_passed = bool(len(ladder_rows) == 3 and all(row["repeat_count"] == 5 for row in ladder_rows))
        nn_values = np.asarray([row["nn"] for row in ladder_rows], dtype=float)
        integrated_variances = np.asarray(
            [row["integrated_sum_variance"] for row in ladder_rows], dtype=float
        )
        fixed_support_variances = np.asarray(
            [row["fixed_union_support_mean_voxelwise_variance"] for row in ladder_rows],
            dtype=float,
        )
        expectation_means = np.asarray([row["mean_sum"] for row in ladder_rows], dtype=float)
        integrated_variance_slope = (
            float(np.polyfit(np.log(nn_values), np.log(integrated_variances), 1)[0])
            if repeat_variance_passed and np.all(integrated_variances > 0)
            else None
        )
        fixed_support_variance_slope = (
            float(np.polyfit(np.log(nn_values), np.log(fixed_support_variances), 1)[0])
            if repeat_variance_passed and np.all(fixed_support_variances > 0)
            else None
        )
        expectation_relative_range = (
            float(np.ptp(expectation_means) / expectation_means.mean())
            if expectation_means.size
            else None
        )
        nn_scaling_passed = bool(
            repeat_variance_passed
            and np.all(np.diff(integrated_variances) < 0)
            and np.all(np.diff(fixed_support_variances) < 0)
            and integrated_variance_slope is not None
            and -1.5 <= integrated_variance_slope <= -0.5
            and fixed_support_variance_slope is not None
            and -1.5 <= fixed_support_variance_slope <= -0.5
            and expectation_relative_range is not None
            and expectation_relative_range <= 0.10
        )
        execution_path = root / "execution.json"
        noninteger_fractions: list[float] = []
        if execution_path.exists():
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
            noninteger_fractions = [
                float(job["qc"]["metrics"]["noninteger_positive_fraction"])
                for job in execution.get("jobs", [])
                if job.get("qc", {}).get("metrics", {}).get("noninteger_positive_fraction") is not None
            ]
        weighted_output_confirmed = bool(
            len(noninteger_fractions) == 15 and min(noninteger_fractions) >= 0.99
        )
        noise_contract_passed = bool(nn_scaling_passed and weighted_output_confirmed)
        observations.append(
            {
                "control": "rr_nn_scaling_summary",
                "integrated_variance_loglog_slope": integrated_variance_slope,
                "fixed_union_support_variance_loglog_slope": fixed_support_variance_slope,
                "expected_inverse_nn_slope": -1.0,
                "accepted_slope_interval": [-1.5, -0.5],
                "expectation_mean_relative_range": expectation_relative_range,
                "maximum_expectation_mean_relative_range": 0.10,
                "integrated_sum_cv_by_nn": {
                    str(row["nn"]): row["integrated_sum_cv"] for row in ladder_rows
                },
                "minimum_noninteger_positive_fraction": (
                    min(noninteger_fractions) if noninteger_fractions else None
                ),
                "output_interpretation": "weighted_mc_expectation_estimator",
                "clinical_poisson_observation_noise_present": False,
                "required_observation_contract": (
                    "Keep SIMIND variance-reduced expectation separate; if count-like observations are required, "
                    "apply an explicitly calibrated observation-noise stage after the expectation image."
                ),
                "physics_scope_limit": (
                    "Noise scaling is conditional on the tested SIMIND setup and is independent of the separately validated type-7 attenuation gate."
                ),
            }
        )
        pass_fail_override = {
            "repeat_variance": "passed" if repeat_variance_passed else "failed",
            "nn_scaling": "passed" if nn_scaling_passed else "failed",
            "noise_contract": "passed" if noise_contract_passed else "failed",
        }

    if missing:
        status = "incomplete_outputs"
    elif analysis_blockers:
        status = "outputs_present_but_analysis_blocked"
    elif pass_fail_override:
        status = (
            "complete_scientific_gate_passed"
            if all(value == "passed" for value in pass_fail_override.values())
            else "complete_scientific_gate_failed"
        )
    else:
        status = "complete_outputs_pending_scientific_gate"
    result = {
        "experiment": name,
        "status": status,
        "analyzed_utc": utc_now(),
        "missing_cases": missing,
        "analysis_blockers": analysis_blockers,
        "observations": observations,
        "pass_fail": pass_fail_override
        or {
            criterion["id"]: (
                "manual_review_required"
                if not missing and not analysis_blockers
                else "not_evaluated"
            )
            for criterion in spec["criteria"]
        },
        "scientific_limit": "Automatic artifact analysis does not itself verify the acquisition protocol or physics claim.",
    }
    atomic_write_json(root / "analysis.json", result)
    return result
