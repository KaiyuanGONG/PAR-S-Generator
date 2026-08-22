"""The single end-to-end synthetic-data preparation workflow.

The runner deliberately ends at a finalized dataset package.  It does not
import or call any reconstruction, training, inference, checkpoint, or model
evaluation component.
"""

from __future__ import annotations

import json
import hashlib
import platform
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from core.interfile_writer import convert_npz_to_interfile
from core.phantom_generator import PhantomConfig, PhantomGenerator, PreviewOverrides
from core.seeds import SeedBundle
from core.smc_parser import parse_smc
from core.windows_v1 import (
    GATE_A_GENERATOR_COMMIT,
    GATE_C_CONFIG_SHA256,
    GENERATION_PROFILE,
    LIMITED_ACTIVITY_UPSTREAM_SOURCE_SHA256,
    RUNTIME_BACKEND,
    SCHEMA_VERSION,
    WindowsV1Config,
)
from core.windows_runtime import assess_windows_runtime
from pipeline.contracts import (
    ACTIVITY_TIME_CONTRACT_STATUS,
    CANONICAL_PROJECTION_TRANSFORM,
    CURRENT_DETECTOR_MATRIX_I,
    CURRENT_DETECTOR_MATRIX_J,
    CURRENT_TYPE7_ATTENUATION_CONTRACT_STATUS,
    CURRENT_TYPE7_DENSITY_THRESHOLD_TIMES_1000,
    DEFAULT_EXPOSURE_S_PER_PROJECTION,
    DEFAULT_SIMIND_ACTIVITY_TIME,
    DEFAULT_SOURCE_ACTIVITY_MBQ,
    EMPIRICAL_CLINICAL_ANGULAR_CV_RANGE,
    EMPIRICAL_CLINICAL_TOTAL_COUNTS,
    EMPIRICAL_OBSERVATION_PROTOCOL_STATUS,
    RunLayout,
    RunLedger,
    assign_fixed_splits,
    atomic_write_json,
    sha256_file,
    utc_now,
)
from pipeline.figures import export_run_figures
from pipeline.gate_a_report import write_gate_a_reports
from pipeline.observation import assign_empirical_count_targets, sample_poisson_observation
from pipeline.qc import (
    assess_gate_a_v2_population,
    assess_stage3_phantom_population,
    phantom_qc,
    summarize_phantom_population,
    validate_projection_artifacts,
)
from pipeline.simind import SimindJob, expected_res_tokens, prepare_jobs, run_job


class PipelinePaused(RuntimeError):
    """Raised at a safe case boundary after state has been checkpointed."""


@dataclass
class PipelineConfig:
    run_id: str
    runs_root: str = "runs"
    schema_version: str = "legacy_internal_v0"
    generation_profile: str = "legacy_master"
    runtime_backend: str = RUNTIME_BACKEND
    windows_v1: WindowsV1Config | None = None
    phantom: PhantomConfig = field(default_factory=PhantomConfig)
    simind_exe: str = "simind/simind.exe"
    smc_file: str = "simind/ge870_czt.smc"
    nn_multiplier: int = 10
    max_simind_workers: int = 1
    simind_seed_base: int = 930_000
    simind_overrides: list[tuple[int, str]] = field(default_factory=list)
    case_numbers: list[int] | None = None
    projection_shape: tuple[int, int, int] = (60, 128, 128)
    simulation_mode: str = "prepare"  # prepare, mock, execute
    execution_scope: str = "full"  # full, anatomy_only_gate_a
    create_poisson_observation: bool = False
    observation_scale: float = 1.0
    observation_seed_offset: int = 1_000_000
    observation_protocol_status: str = "toy"
    observation_policy: str = "fixed_scale"
    empirical_reference_counts: tuple[int, ...] = EMPIRICAL_CLINICAL_TOTAL_COUNTS
    empirical_angular_cv_range: tuple[float, float] = EMPIRICAL_CLINICAL_ANGULAR_CV_RANGE
    empirical_count_evidence: str = (
        "docs/evidence/clinical_empirical_count_summary_2026-08-18.json"
    )
    pilot_selection_evidence: str | None = None
    type7_density_threshold_times_1000: int = CURRENT_TYPE7_DENSITY_THRESHOLD_TIMES_1000
    detector_matrix_i: int = CURRENT_DETECTOR_MATRIX_I
    detector_matrix_j: int = CURRENT_DETECTOR_MATRIX_J
    phantom_cross_sections: tuple[str, str] = ("h2o", "h2o")
    split_seed: int = 42
    split_fractions: tuple[float, float, float] = (0.8, 0.1, 0.1)
    protocol_label: str = "GE 870 CZT current liver SPECT research protocol"
    protocol_status: str = "stage3_protocol_promoted_pilot_pending"
    source_activity_mbq: float = DEFAULT_SOURCE_ACTIVITY_MBQ
    exposure_time_s_per_projection: float | None = DEFAULT_EXPOSURE_S_PER_PROJECTION
    smc_index25_activity_time: float = DEFAULT_SIMIND_ACTIVITY_TIME
    activity_time_contract_status: str = ACTIVITY_TIME_CONTRACT_STATUS

    def __post_init__(self):
        if self.schema_version == SCHEMA_VERSION:
            if self.generation_profile != GENERATION_PROFILE:
                raise ValueError(f"Windows v1 generation_profile must be {GENERATION_PROFILE}")
            if self.runtime_backend != RUNTIME_BACKEND:
                raise ValueError(f"Windows v1 runtime_backend must be {RUNTIME_BACKEND}")
            if not isinstance(self.windows_v1, WindowsV1Config):
                raise ValueError("Windows v1 requires an authoritative windows_v1 configuration")
            expected_phantom = self.windows_v1.to_phantom_config()
            if json.loads(json.dumps(self.phantom.to_dict())) != json.loads(
                json.dumps(expected_phantom.to_dict())
            ):
                raise ValueError("Windows v1 phantom fields differ from the locked authoritative profile")
            if self.case_numbers is not None:
                raise ValueError("Windows v1 derives case order and roles from the cohort")
            if (
                self.create_poisson_observation
                or self.observation_policy != "fixed_scale"
                or self.observation_protocol_status != "toy"
            ):
                raise ValueError("Windows v1 does not create an offline observation output")
        elif self.windows_v1 is not None:
            raise ValueError("windows_v1 controls require schema_version=windows_v1")
        if (
            isinstance(self.nn_multiplier, bool)
            or not isinstance(self.nn_multiplier, int)
            or not 1 <= self.nn_multiplier <= 1_000_000
        ):
            raise ValueError("nn_multiplier must be an integer between 1 and 1000000")
        if self.simulation_mode not in {"prepare", "mock", "execute"}:
            raise ValueError("simulation_mode must be prepare, mock, or execute")
        if self.execution_scope not in {"full", "anatomy_only_gate_a"}:
            raise ValueError("execution_scope must be full or anatomy_only_gate_a")
        if not 1 <= int(self.max_simind_workers) <= 32:
            raise ValueError("max_simind_workers must be between 1 and 32")
        if int(self.simind_seed_base) < 1:
            raise ValueError("simind_seed_base must be positive")
        if self.phantom.n_cases < 1:
            raise ValueError("phantom.n_cases must be positive")
        if self.execution_scope == "anatomy_only_gate_a":
            if self.phantom.anatomy_model != "v2_population":
                raise ValueError("anatomy_only_gate_a requires anatomy_model=v2_population")
            if self.phantom.n_cases != 100:
                raise ValueError("anatomy_only_gate_a requires exactly 100 cases")
            if self.simulation_mode != "prepare":
                raise ValueError("anatomy_only_gate_a never executes or mocks SIMIND")
            if self.create_poisson_observation:
                raise ValueError("anatomy_only_gate_a cannot create observations")
        if tuple(self.phantom.volume_shape) != (128, 128, 128):
            raise ValueError("Current validated scope requires a 128x128x128 phantom")
        if self.phantom.mu_contract_status != CURRENT_TYPE7_ATTENUATION_CONTRACT_STATUS:
            raise ValueError(
                "Current protocol requires the validated type-7 mu-times-voxel attenuation status"
            )
        if self.case_numbers is not None:
            normalized = [int(value) for value in self.case_numbers]
            if len(normalized) != self.phantom.n_cases:
                raise ValueError("case_numbers length must equal phantom.n_cases")
            if len(set(normalized)) != len(normalized) or any(value < 1 for value in normalized):
                raise ValueError("case_numbers must contain unique positive integers")
            self.case_numbers = normalized
        if self.pilot_selection_evidence is not None:
            selection_path = Path(self.pilot_selection_evidence)
            if not selection_path.is_file():
                raise ValueError(f"Pilot selection evidence does not exist: {selection_path}")
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            selected_numbers = [int(row["case_number"]) for row in selection.get("selected", [])]
            if self.case_numbers is None or selected_numbers != self.case_numbers:
                raise ValueError(
                    "Pilot selection evidence case order must exactly match case_numbers"
                )
        if self.observation_policy not in {"fixed_scale", "empirical_total_counts"}:
            raise ValueError("observation_policy must be fixed_scale or empirical_total_counts")
        if self.observation_policy == "empirical_total_counts":
            if not self.create_poisson_observation:
                raise ValueError("empirical_total_counts requires create_poisson_observation=true")
            if self.observation_protocol_status != EMPIRICAL_OBSERVATION_PROTOCOL_STATUS:
                raise ValueError(
                    "empirical_total_counts requires empirical_protocol_matching status"
                )
            if len(self.empirical_reference_counts) < 2 or any(
                int(value) <= 0 for value in self.empirical_reference_counts
            ):
                raise ValueError("empirical_reference_counts must contain positive values")
            low, high = (float(value) for value in self.empirical_angular_cv_range)
            if low < 0 or high <= low:
                raise ValueError("empirical_angular_cv_range must be increasing and non-negative")
        if self.type7_density_threshold_times_1000 != CURRENT_TYPE7_DENSITY_THRESHOLD_TIMES_1000:
            raise ValueError("Current validated protocol requires type-7 density threshold 100")
        if (self.detector_matrix_i, self.detector_matrix_j) != (
            CURRENT_DETECTOR_MATRIX_I,
            CURRENT_DETECTOR_MATRIX_J,
        ):
            raise ValueError("Current validated GE detector matrix requires Index-100/101=160/208")
        if tuple(value.lower() for value in self.phantom_cross_sections) != ("h2o", "h2o"):
            raise ValueError("Current scoped type-7 contract requires the two h2o cross-section tables")
        if self.exposure_time_s_per_projection is not None:
            product = self.source_activity_mbq * self.exposure_time_s_per_projection
            if not np.isclose(product, self.smc_index25_activity_time, rtol=1e-6, atol=1e-3):
                raise ValueError(
                    "SMC Index-25 must equal source_activity_mbq * "
                    "exposure_time_s_per_projection"
                )
        for index, value in self.simind_overrides:
            if int(index) == 25 and not np.isclose(
                float(value), self.smc_index25_activity_time, rtol=1e-6, atol=1e-3
            ):
                raise ValueError("Custom /25 override conflicts with smc_index25_activity_time")
            if int(index) == 100 and int(float(value)) != self.detector_matrix_i:
                raise ValueError("Custom /100 override conflicts with detector_matrix_i")
            if int(index) == 101 and int(float(value)) != self.detector_matrix_j:
                raise ValueError("Custom /101 override conflicts with detector_matrix_j")

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["phantom"] = self.phantom.to_dict()
        payload["windows_v1"] = self.windows_v1.to_dict() if self.windows_v1 is not None else None
        # Canonicalize tuples and NumPy-compatible scalar values to the exact
        # JSON representation persisted in run.json, so resume comparison is
        # structural rather than Python-container-type dependent.
        return json.loads(json.dumps(payload))

    @classmethod
    def from_dict(cls, payload: dict) -> "PipelineConfig":
        data = dict(payload)
        if data.get("schema_version") == SCHEMA_VERSION:
            allowed = set(cls.__dataclass_fields__)
            unknown = sorted(set(data) - allowed)
            if unknown:
                raise ValueError(f"unknown Windows v1 pipeline fields: {', '.join(unknown)}")
            controls = WindowsV1Config.from_dict(data.get("windows_v1"))
            phantom_payload = data.get("phantom")
            if not isinstance(phantom_payload, dict):
                raise ValueError("Windows v1 requires a serialized phantom object")
            phantom_unknown = sorted(
                set(phantom_payload) - set(PhantomConfig.__dataclass_fields__)
            )
            if phantom_unknown:
                raise ValueError(
                    f"unknown Windows v1 phantom fields: {', '.join(phantom_unknown)}"
                )
            phantom = PhantomConfig.from_dict(phantom_payload)
            if json.loads(json.dumps(phantom.to_dict())) != json.loads(
                json.dumps(controls.to_phantom_config().to_dict())
            ):
                raise ValueError("Windows v1 phantom fields differ from the locked authoritative profile")
            data["windows_v1"] = controls
            data["phantom"] = phantom
        else:
            data["phantom"] = PhantomConfig.from_dict(data.get("phantom", {}))
            data["windows_v1"] = None
        if "simind_overrides" in data:
            data["simind_overrides"] = [
                (int(pair[0]), str(pair[1])) for pair in data["simind_overrides"]
            ]
        for key in (
            "projection_shape",
            "split_fractions",
            "empirical_reference_counts",
            "empirical_angular_cv_range",
            "phantom_cross_sections",
        ):
            if key in data:
                data[key] = tuple(data[key])
        return cls(**data)

    @classmethod
    def for_windows_v1(
        cls,
        *,
        run_id: str,
        windows_v1: WindowsV1Config,
        runs_root: str = "runs",
        **kwargs,
    ) -> "PipelineConfig":
        observation_contract = {
            "create_poisson_observation": False,
            "observation_policy": "fixed_scale",
            "observation_protocol_status": "toy",
        }
        for key, expected in observation_contract.items():
            if key in kwargs and kwargs[key] != expected:
                raise ValueError("Windows v1 does not create an offline observation output")
        kwargs.update(observation_contract)
        return cls(
            run_id=run_id,
            runs_root=runs_root,
            schema_version=SCHEMA_VERSION,
            generation_profile=GENERATION_PROFILE,
            runtime_backend=RUNTIME_BACKEND,
            windows_v1=windows_v1,
            phantom=windows_v1.to_phantom_config(),
            **kwargs,
        )


class PipelineRunner:
    """Synchronous, resumable orchestration shared by CLI and GUI workers."""

    def __init__(self, config: PipelineConfig, *, resume: bool = False):
        self.config = config
        provenance = self._runtime_provenance(config)
        run_root = Path(config.runs_root).resolve() / config.run_id
        if resume:
            self.layout = RunLayout.open(run_root)
            self.ledger = RunLedger(self.layout)
            recorded = self.ledger.load().get("effective_config", {})
            if recorded != config.to_dict():
                raise RuntimeError("Resume rejected: effective configuration differs from run.json")
            recorded_provenance = self.ledger.load().get("provenance", {})
            # Runs created before software fingerprints were introduced remain
            # readable.  Every new run records them and must not mix artifacts
            # produced by different source/SMC/executable revisions.
            if recorded_provenance.get("software_sha256") and recorded_provenance != provenance:
                raise RuntimeError("Resume rejected: software, SMC, or SIMIND provenance differs from run.json")
        else:
            self.layout = RunLayout.create(config.runs_root, config.run_id)
            self.ledger = RunLedger(self.layout)
            self.ledger.initialize(
                run_id=config.run_id,
                effective_config=config.to_dict(),
                provenance=provenance,
            )
        self._pause_requested = False

    @staticmethod
    def _runtime_provenance(config: PipelineConfig) -> dict:
        src_root = Path(__file__).resolve().parents[1]
        source_files = [
            src_root / "core" / "phantom_generator.py",
            src_root / "core" / "interfile_writer.py",
            src_root / "pipeline" / "runner.py",
            src_root / "pipeline" / "qc.py",
            src_root / "pipeline" / "simind.py",
            src_root / "pipeline" / "observation.py",
            src_root / "pipeline" / "pilot.py",
        ]
        v2_inputs = None
        if config.phantom.anatomy_model == "v2_population":
            source_files.extend(
                src_root / relative
                for relative in (
                    "core/anatomy_v2.py",
                    "core/attenuation_model_v2.py",
                    "core/hybrid_v2_adapter.py",
                    "core/liver_geometry.py",
                    "core/liver_regions.py",
                    "core/measurements.py",
                    "core/population_sampler.py",
                    "core/schemas_v2.py",
                    "core/seeds.py",
                    "pipeline/gate_a_report.py",
                )
            )
            if config.phantom.activity_model == "limited_v1":
                source_files.extend(
                    (
                        src_root / "core" / "limited_activity.py",
                        src_root / "core" / "windows_v1.py",
                    )
                )
            project_root = src_root.parent
            profile_path = Path(config.phantom.v2_population_profile)
            registry_path = Path(config.phantom.v2_evidence_registry)
            if not profile_path.is_absolute():
                profile_path = project_root / profile_path
            if not registry_path.is_absolute():
                registry_path = project_root / registry_path
            profile_path = profile_path.resolve()
            registry_path = registry_path.resolve()
            v2_inputs = {
                "population_profile": {
                    "path": str(profile_path),
                    "sha256": sha256_file(profile_path),
                },
                "evidence_registry": {
                    "path": str(registry_path),
                    "sha256": sha256_file(registry_path),
                },
            }
        exe = Path(config.simind_exe).resolve()
        smc = Path(config.smc_file).resolve()
        evidence = Path(config.empirical_count_evidence).resolve()
        pilot_selection = (
            Path(config.pilot_selection_evidence).resolve()
            if config.pilot_selection_evidence is not None
            else None
        )
        if smc.is_file():
            parsed = parse_smc(smc)
            actual_cross_sections = tuple(value.lower() for value in parsed.data_files[:2])
            if actual_cross_sections != tuple(
                value.lower() for value in config.phantom_cross_sections
            ):
                raise ValueError(
                    "SMC cross-section tables do not match the effective type-7 contract: "
                    f"{actual_cross_sections}"
                )
            if int(round(parsed.get_value(14))) != -7 or int(round(parsed.get_value(15))) != -7:
                raise ValueError("Current protocol requires SIMIND phantom/source type -7")
            if not parsed.get_flag(11):
                raise ValueError("Current protocol requires Flag-11 phantom interactions")
            if not np.isclose(
                parsed.get_value(31), config.phantom.voxel_size_mm / 10.0, atol=1e-6
            ):
                raise ValueError("SMC density voxel size conflicts with the phantom voxel size")
        return {
            "generator": "PhantomGenerator.generate_one",
            "schema_version": config.schema_version,
            "generation_profile": config.generation_profile,
            "runtime_backend": config.runtime_backend,
            "scientific_authority": {
                "gate_a_generator_commit": GATE_A_GENERATOR_COMMIT,
                "limited_activity_upstream_source_sha256": LIMITED_ACTIVITY_UPSTREAM_SOURCE_SHA256,
                "gate_c_config_sha256": GATE_C_CONFIG_SHA256,
            },
            "windows_platform": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "python": sys.version,
            },
            "projection_orientation": CANONICAL_PROJECTION_TRANSFORM,
            "protocol_scope": "liver_only_current_protocol",
            "software_sha256": {
                path.relative_to(src_root).as_posix(): sha256_file(path) for path in source_files
            },
            "v2_inputs": v2_inputs,
            "simind_executable": {
                "path": str(exe),
                "sha256": sha256_file(exe) if exe.is_file() else None,
            },
            "smc": {
                "path": str(smc),
                "sha256": sha256_file(smc) if smc.is_file() else None,
            },
            "windows_runtime": assess_windows_runtime(exe, smc).to_dict(),
            "type7_attenuation": {
                "stored_formula": "mu_cm_inverse * density_voxel_size_cm",
                "density_threshold_times_1000": config.type7_density_threshold_times_1000,
                "phantom_cross_sections": list(config.phantom_cross_sections),
                "validation_evidence": "experiments/validation-v10/attenuation_ict/analysis.json",
            },
            "empirical_count_evidence": {
                "path": str(evidence),
                "sha256": sha256_file(evidence) if evidence.is_file() else None,
                "absolute_cps_per_mbq_claim": False,
            },
            "pilot_selection_evidence": (
                {
                    "path": str(pilot_selection),
                    "sha256": sha256_file(pilot_selection),
                }
                if pilot_selection is not None
                else None
            ),
        }

    def request_pause(self) -> None:
        """Pause after the currently running case/stage boundary."""
        self._pause_requested = True

    def _pause_if_requested(self, stage: str, **evidence) -> None:
        if self._pause_requested:
            self.ledger.update_stage(stage, "paused", **evidence)
            raise PipelinePaused(f"Pipeline paused safely during {stage}; resume this run to continue.")

    @classmethod
    def open(cls, run_root: Path) -> "PipelineRunner":
        layout = RunLayout.open(run_root)
        config = PipelineConfig.from_dict(RunLedger(layout).load()["effective_config"])
        return cls(config, resume=True)

    def _stage_is_passed(self, stage: str) -> bool:
        return self.ledger.load().get("stages", {}).get(stage, {}).get("status") == "passed"

    def _case_ids(self) -> list[str]:
        return [spec["case_id"] for spec in self._case_specs()]

    def _case_specs(self) -> list[dict]:
        numbers = self.config.case_numbers or list(range(1, self.config.phantom.n_cases + 1))
        roles = (
            self.config.windows_v1.case_roles()
            if self.config.windows_v1 is not None
            else ["legacy"] * len(numbers)
        )
        return [
            {
                "case_id": f"case_{number:04d}",
                "numeric_id": int(number),
                "case_role": role,
                "split_role": (
                    "independent_test_control" if role == "true_negative" else "dataset_member"
                ),
            }
            for number, role in zip(numbers, roles, strict=True)
        ]

    def _assert_hash(self, path: Path, expected: str, context: str) -> None:
        if not path.is_file() or not expected or sha256_file(path) != expected:
            raise RuntimeError(f"Resume rejected: corrupt or missing {context}: {path}")

    def _relative(self, path: Path) -> str:
        """Return a portable run-relative path for packaged provenance."""
        return Path(path).resolve().relative_to(self.layout.root).as_posix()

    def generate(self, progress: Callable[[str], None] | None = None) -> list[dict]:
        if self._stage_is_passed("generate"):
            cases = self.ledger.read_cases()
            for record in cases:
                self._assert_hash(Path(record["phantom"]["npz"]), record["phantom"]["npz_sha256"], record["case_id"])
                self._assert_hash(Path(record["phantom"]["meta"]), record["phantom"]["meta_sha256"], record["case_id"])
            return cases

        self.ledger.update_stage("generate", "running")
        generator = PhantomGenerator(self.config.phantom)
        case_specs = self._case_specs()
        case_ids = [spec["case_id"] for spec in case_specs]
        dataset_case_ids = [
            spec["case_id"] for spec in case_specs if spec["case_role"] != "true_negative"
        ]
        splits = (
            assign_fixed_splits(dataset_case_ids, self.config.split_seed, self.config.split_fractions)
            if dataset_case_ids
            else {}
        )
        existing = {record["case_id"]: record for record in self.ledger.read_cases()}
        cases: list[dict] = []
        try:
            for index, spec in enumerate(case_specs, 1):
                case_id = spec["case_id"]
                self._pause_if_requested("generate", completed_cases=len(cases))
                prior = existing.get(case_id)
                if prior:
                    artifact = prior.get("phantom", {})
                    npz = Path(artifact.get("npz", ""))
                    meta = Path(artifact.get("meta", ""))
                    if (
                        npz.is_file()
                        and meta.is_file()
                        and sha256_file(npz) == artifact.get("npz_sha256")
                        and sha256_file(meta) == artifact.get("meta_sha256")
                    ):
                        cases.append(prior)
                        continue
                numeric_id = int(spec["numeric_id"])
                overrides = (
                    PreviewOverrides(exact_tumor_count=0)
                    if spec["case_role"] == "true_negative"
                    else None
                )
                result = generator.generate_one(numeric_id, overrides=overrides)
                result.save(self.layout.subdir("phantom"))
                npz = self.layout.subdir("phantom") / f"{case_id}.npz"
                meta = self.layout.subdir("phantom") / f"{case_id}_meta.json"
                meta_payload = json.loads(meta.read_text(encoding="utf-8"))
                meta_payload.update(
                    {
                        "schema_version": self.config.schema_version,
                        "generation_profile": self.config.generation_profile,
                        "runtime_backend": self.config.runtime_backend,
                        "case_role": spec["case_role"],
                        "split_role": spec["split_role"],
                    }
                )
                atomic_write_json(meta, meta_payload)
                cases.append(
                    {
                        "case_id": case_id,
                        "phantom_id": case_id,
                        "case_role": spec["case_role"],
                        "split_role": spec["split_role"],
                        "split": (
                            "test" if spec["case_role"] == "true_negative" else splits[case_id]
                        ),
                        "seed": int(result.seed),
                        "phantom": {
                            "npz": str(npz.resolve()),
                            "meta": str(meta.resolve()),
                            "npz_relpath": self._relative(npz),
                            "meta_relpath": self._relative(meta),
                            "npz_sha256": sha256_file(npz),
                            "meta_sha256": sha256_file(meta),
                        },
                    }
                )
                self.ledger.write_cases(cases)
                if progress:
                    progress(f"Generated {case_id} ({index}/{len(case_ids)})")
            self.ledger.update_stage("generate", "passed", case_count=len(cases))
            return cases
        except Exception as exc:
            if isinstance(exc, PipelinePaused):
                raise
            self.ledger.update_stage("generate", "failed", error=str(exc))
            raise

    def run_phantom_qc(self) -> list[dict]:
        cases = self.generate()
        if self._stage_is_passed("phantom_qc"):
            for record in cases:
                qc_record = record.get("qc", {}).get("phantom", {})
                self._assert_hash(Path(qc_record["path"]), qc_record["sha256"], f"{record['case_id']} phantom QC")
            return cases
        self.ledger.update_stage("phantom_qc", "running")
        failed: list[str] = []
        for record in cases:
            self._pause_if_requested("phantom_qc")
            prior_qc = record.get("qc", {}).get("phantom", {})
            prior_path = Path(prior_qc.get("path", ""))
            if (
                prior_qc.get("status") == "passed"
                and prior_path.is_file()
                and sha256_file(prior_path) == prior_qc.get("sha256")
            ):
                continue
            result = phantom_qc(Path(record["phantom"]["npz"]), Path(record["phantom"]["meta"]))
            qc_path = self.layout.subdir("qc") / f"{record['case_id']}_phantom_qc.json"
            atomic_write_json(qc_path, result)
            record.setdefault("qc", {})["phantom"] = {
                "status": result["status"],
                "path": str(qc_path.resolve()),
                "relpath": self._relative(qc_path),
                "sha256": sha256_file(qc_path),
            }
            if result["status"] != "passed":
                failed.append(record["case_id"])
        self.ledger.write_cases(cases)
        qc_records = [
            json.loads(
                Path(record["qc"]["phantom"]["path"]).read_text(encoding="utf-8")
            )
            for record in cases
        ]
        summary = summarize_phantom_population(qc_records)
        summary["failed_cases"] = failed
        if len(cases) == 100 and self.config.phantom.anatomy_model == "v2_population":
            summary["stage3_population_acceptance"] = {
                "status": "not_applicable",
                "enforced": False,
                "reason": "Legacy fixed-volume/fixed-left-ratio anatomy gates do not define V2 semantics",
            }
            summary["gate_a_population_acceptance"] = assess_gate_a_v2_population(
                summary,
                size_bins_mm=self.config.phantom.tumor_size_bins_mm,
                size_probabilities=self.config.phantom.tumor_probs,
                tumor_count_min=self.config.phantom.tumor_count_min,
                tumor_count_max=self.config.phantom.tumor_count_max,
                mode_probabilities=dict(zip(
                    self.config.phantom.tumor_modes,
                    self.config.phantom.tumor_mode_probs,
                )),
                target_contrast_range=(
                    self.config.phantom.tumor_contrast_min,
                    self.config.phantom.tumor_contrast_max,
                ),
                central_margin_mm=self.config.phantom.tumor_min_liver_margin_mm,
            )
            if summary["gate_a_population_acceptance"]["status"] != "passed":
                summary["status"] = "failed"
                failed.append("gate_a_population_distribution_gate")
                summary["failed_cases"] = failed
        elif len(cases) == 100:
            summary["gate_a_population_acceptance"] = {
                "status": "not_applicable",
                "enforced": False,
                "reason": "Gate A V2 checks require anatomy_model=v2_population",
            }
            summary["stage3_population_acceptance"] = assess_stage3_phantom_population(
                summary,
                size_bins_mm=self.config.phantom.tumor_size_bins_mm,
                size_probabilities=self.config.phantom.tumor_probs,
                tumor_count_min=self.config.phantom.tumor_count_min,
                tumor_count_max=self.config.phantom.tumor_count_max,
                mode_probabilities=dict(zip(
                    self.config.phantom.tumor_modes,
                    self.config.phantom.tumor_mode_probs,
                )),
                target_left_ratio=self.config.phantom.target_left_ratio,
                target_contrast_range=(
                    self.config.phantom.tumor_contrast_min,
                    self.config.phantom.tumor_contrast_max,
                ),
                central_margin_mm=self.config.phantom.tumor_min_liver_margin_mm,
            )
            if summary["stage3_population_acceptance"]["status"] != "passed":
                summary["status"] = "failed"
                failed.append("population_distribution_gate")
                summary["failed_cases"] = failed
        else:
            summary["stage3_population_acceptance"] = {
                "status": "not_enforced",
                "enforced": False,
                "reason": "Stage-3 population gates require exactly 100 generated cases",
            }
            summary["gate_a_population_acceptance"] = {
                "status": "not_enforced",
                "enforced": False,
                "reason": "Gate A population gates require exactly 100 generated cases",
            }
        atomic_write_json(self.layout.subdir("qc") / "phantom_qc_summary.json", summary)
        self.ledger.update_stage(
            "phantom_qc",
            summary["status"],
            failed_cases=failed,
            case_count=len(cases),
        )
        if failed:
            raise RuntimeError(f"Phantom QC failed: {', '.join(failed)}")
        return cases

    def export(self) -> list[dict]:
        cases = self.run_phantom_qc()
        if self._stage_is_passed("export"):
            for record in cases:
                artifact = record["simind_input"]
                self._assert_hash(Path(artifact["activity_bin"]), artifact["activity_sha256"], record["case_id"])
                self._assert_hash(Path(artifact["attenuation_bin"]), artifact["attenuation_sha256"], record["case_id"])
            return cases
        self.ledger.update_stage("export", "running")
        try:
            for record in cases:
                self._pause_if_requested("export")
                prior = record.get("simind_input", {})
                act_prior = Path(prior.get("activity_bin", ""))
                atn_prior = Path(prior.get("attenuation_bin", ""))
                if (
                    prior.get("readback_verified") is True
                    and act_prior.is_file()
                    and atn_prior.is_file()
                    and sha256_file(act_prior) == prior.get("activity_sha256")
                    and sha256_file(atn_prior) == prior.get("attenuation_sha256")
                ):
                    continue
                result = convert_npz_to_interfile(
                    Path(record["phantom"]["npz"]),
                    self.layout.subdir("simind_input"),
                    voxel_size_mm=self.config.phantom.voxel_size_mm,
                )
                record["simind_input"] = {
                    "activity_bin": str(result["act_bin"].resolve()),
                    "attenuation_bin": str(result["atn_bin"].resolve()),
                    "activity_relpath": self._relative(result["act_bin"]),
                    "attenuation_relpath": self._relative(result["atn_bin"]),
                    "activity_sha256": result["act_sha256"],
                    "attenuation_sha256": result["atn_sha256"],
                    "dtype": result["dtype"],
                    "shape": result["shape"],
                    "order": result["order"],
                    "readback_verified": result["readback_verified"],
                    "density_voxel_size_cm": result["voxel_size_cm"],
                    "type7_stored_semantic": result["type7_stored_semantic"],
                    "type7_stored_unit": result["type7_stored_unit"],
                    "type7_conversion_formula": result["type7_conversion_formula"],
                    "type7_conversion_scale": result["type7_conversion_scale"],
                    "type7_roundtrip_max_abs_error_cm_inverse": result[
                        "type7_roundtrip_max_abs_error_cm_inverse"
                    ],
                    "analytical_mu_range_cm_inverse": result[
                        "analytical_mu_range_cm_inverse"
                    ],
                    "type7_stored_value_range": result["type7_stored_value_range"],
                    "mu_contract": {
                        "analytical_semantic": "linear_attenuation_coefficient",
                        "analytical_unit": self.config.phantom.mu_unit,
                        "simind_stored_semantic": result["type7_stored_semantic"],
                        "simind_stored_unit": result["type7_stored_unit"],
                        "reference_energy_kev": self.config.phantom.mu_reference_energy_kev,
                        "status": self.config.phantom.mu_contract_status,
                        "validation_evidence": (
                            "experiments/validation-v10/attenuation_ict/analysis.json"
                        ),
                    },
                }
            self.ledger.write_cases(cases)
            self.ledger.update_stage("export", "passed", case_count=len(cases), readback_verified=True)
            return cases
        except Exception as exc:
            if isinstance(exc, PipelinePaused):
                raise
            self.ledger.update_stage("export", "failed", error=str(exc))
            raise

    def _jobs(self, cases: list[dict]) -> list[SimindJob]:
        exe = Path(self.config.simind_exe).resolve()
        smc = Path(self.config.smc_file).resolve()
        provenance = self.ledger.load().get("provenance", {})
        expected_executable_sha256 = provenance.get("simind_executable", {}).get("sha256")
        expected_smc_sha256 = provenance.get("smc", {}).get("sha256")
        overrides = list(self.config.simind_overrides)
        if not any(int(index) == 25 for index, _ in overrides):
            overrides.append((25, f"{self.config.smc_index25_activity_time:g}"))
        if not any(int(index) == 100 for index, _ in overrides):
            overrides.append((100, str(self.config.detector_matrix_i)))
        if not any(int(index) == 101 for index, _ in overrides):
            overrides.append((101, str(self.config.detector_matrix_j)))
        return [
            SimindJob(
                case_id=record["case_id"],
                simind_exe=exe,
                smc_file=smc,
                working_dir=self.layout.subdir("simind_input"),
                output_stem=self.layout.subdir("expectation") / record["case_id"],
                source_stem=record["case_id"],
                density_stem=record["case_id"],
                nn_multiplier=self.config.nn_multiplier,
                rr_seed=(
                    SeedBundle.from_case(
                        self.config.windows_v1.seed,
                        record["case_id"],
                    ).simind
                    if self.config.windows_v1 is not None
                    else self.config.simind_seed_base
                    + int(record["case_id"].rsplit("_", 1)[1])
                ),
                overrides=tuple(overrides),
                runtime_switches=(
                    f"/IN:x21,{self.config.type7_density_threshold_times_1000}x",
                ),
                expected_executable_sha256=expected_executable_sha256,
                expected_smc_sha256=expected_smc_sha256,
            )
            for record in cases
        ]

    def prepare_simind(self) -> list[SimindJob]:
        cases = self.export()
        jobs = self._jobs(cases)
        plan = prepare_jobs(jobs, self.layout.subdir("logs"))
        status = "passed" if self.config.simulation_mode in {"mock", "execute"} else "prepared"
        self.ledger.update_stage(
            "simind_plan",
            status,
            mode=self.config.simulation_mode,
            plan=str(plan.resolve()),
            job_count=len(jobs),
        )
        return jobs

    def _write_mock_expectation(self, record: dict) -> None:
        """Create a deterministic shape-valid test artifact, clearly marked mock."""
        with np.load(record["phantom"]["npz"]) as payload:
            activity = np.asarray(payload["activity"], dtype=np.float32)
        base = activity.sum(axis=0, dtype=np.float32)
        projections = np.empty(self.config.projection_shape, dtype=np.float32)
        for view in range(self.config.projection_shape[0]):
            projections[view] = np.roll(base, view - self.config.projection_shape[0] // 2, axis=1)
        a00 = self.layout.subdir("expectation") / f"{record['case_id']}.a00"
        temp = a00.with_name(f".{a00.name}.tmp")
        projections.tofile(temp)
        temp.replace(a00)
        res = a00.with_suffix(".res")
        res.write_text(
            "PAR-S deterministic mock projection; NOT SIMIND physics output.\nSimulation stopped.: mock\n",
            encoding="utf-8",
        )

    def simulate_or_mock(self) -> list[dict]:
        cases = self.export()
        jobs = self.prepare_simind()
        jobs_by_case = {job.case_id: job for job in jobs}
        case_ids = {record["case_id"] for record in cases}
        if len(jobs_by_case) != len(jobs) or set(jobs_by_case) != case_ids:
            raise RuntimeError("SIMIND jobs must map one-to-one to case_id values")
        if self.config.simulation_mode == "prepare":
            self.ledger.update_stage(
                "expectation", "skipped", reason="SIMIND commands prepared but not executed"
            )
            return cases
        if self._stage_is_passed("expectation"):
            for record in cases:
                job = jobs_by_case[record["case_id"]]
                expectation = record.get("expectation", {})
                a00 = Path(expectation.get("a00", ""))
                res = Path(expectation.get("res", ""))
                self._assert_hash(a00, expectation.get("a00_sha256"), f"{record['case_id']} expectation")
                self._assert_hash(res, expectation.get("res_sha256"), f"{record['case_id']} result log")
                expected_tokens = (
                    () if expectation.get("backend") == "deterministic_mock_not_simind"
                    else expected_res_tokens(job)
                )
                qc = validate_projection_artifacts(
                    a00,
                    shape=self.config.projection_shape,
                    require_mhd=expectation.get("backend") == "simind",
                    expected_command_tokens=expected_tokens,
                )
                if qc["status"] != "passed":
                    raise RuntimeError(f"Resume rejected: invalid expectation for {record['case_id']}: {qc['failures']}")
            return cases
        self.ledger.update_stage("expectation", "running", backend=self.config.simulation_mode)
        failed: list[str] = []
        pending_execute: list[tuple[dict, SimindJob]] = []

        def persist_qc(record: dict, job: SimindJob, qc: dict) -> None:
            qc_path = self.layout.subdir("qc") / f"{record['case_id']}_projection_qc.json"
            atomic_write_json(qc_path, qc)
            record.setdefault("qc", {})["projection"] = {
                "status": qc["status"],
                "path": str(qc_path.resolve()),
                "relpath": self._relative(qc_path),
                "sha256": sha256_file(qc_path),
            }
            record["expectation"] = {
                "a00": str(job.output_stem.with_suffix(".a00").resolve()),
                "res": str(job.output_stem.with_suffix(".res").resolve()),
                "a00_relpath": self._relative(job.output_stem.with_suffix(".a00")),
                "res_relpath": self._relative(job.output_stem.with_suffix(".res")),
                "backend": qc["backend"],
                "rr_seed": job.rr_seed,
                "a00_sha256": qc.get("sha256", {}).get("a00"),
                "res_sha256": qc.get("sha256", {}).get("res"),
            }
            if qc["status"] != "passed":
                failed.append(record["case_id"])

        for record in cases:
            job = jobs_by_case[record["case_id"]]
            self._pause_if_requested("expectation")
            prior = record.get("expectation", {})
            prior_a00 = Path(prior.get("a00", ""))
            prior_res = Path(prior.get("res", ""))
            prior_hashes_match = (
                prior_a00.is_file()
                and prior_res.is_file()
                and bool(prior.get("a00_sha256"))
                and bool(prior.get("res_sha256"))
                and sha256_file(prior_a00) == prior.get("a00_sha256")
                and sha256_file(prior_res) == prior.get("res_sha256")
            )
            if prior_hashes_match:
                existing_qc = validate_projection_artifacts(
                    prior_a00,
                    shape=self.config.projection_shape,
                    require_mhd=prior.get("backend") == "simind",
                    expected_command_tokens=(
                        () if prior.get("backend") == "deterministic_mock_not_simind"
                        else expected_res_tokens(job)
                    ),
                )
                if existing_qc["status"] == "passed":
                    continue
            if self.config.simulation_mode == "mock":
                self._write_mock_expectation(record)
                qc = validate_projection_artifacts(
                    job.output_stem.with_suffix(".a00"), shape=self.config.projection_shape
                )
                qc["backend"] = "deterministic_mock_not_simind"
                persist_qc(record, job, qc)
            else:
                pending_execute.append((record, job))

        if pending_execute:
            workers = min(self.config.max_simind_workers, len(pending_execute))
            self.ledger.update_stage(
                "expectation",
                "running",
                backend="execute",
                max_simind_workers=workers,
                deterministic_rr_seeds=True,
            )
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="simind") as executor:
                futures = {
                    executor.submit(
                        run_job,
                        job,
                        self.layout.subdir("logs") / f"{job.case_id}_simind_runtime.log",
                        shape=self.config.projection_shape,
                    ): (record, job)
                    for record, job in pending_execute
                }
                for future in as_completed(futures):
                    record, job = futures[future]
                    try:
                        qc = future.result()
                    except Exception as exc:
                        qc = {
                            "status": "failed",
                            "failures": [f"execution_exception:{exc}"],
                            "sha256": {},
                        }
                    qc["backend"] = "simind"
                    persist_qc(record, job, qc)
                    # Checkpoint completed cases from the coordinator thread;
                    # worker processes never write the shared case ledger.
                    self.ledger.write_cases(cases)
        self.ledger.write_cases(cases)
        status = "passed" if not failed else "failed"
        self.ledger.update_stage("expectation", status, backend=self.config.simulation_mode, failed_cases=failed)
        self.ledger.update_stage("projection_qc", status, failed_cases=failed)
        if failed:
            raise RuntimeError(f"Projection QC failed: {', '.join(failed)}")
        return cases

    def create_observations(self) -> list[dict]:
        cases = self.simulate_or_mock()
        if self.config.simulation_mode == "prepare":
            self.ledger.update_stage("observation", "skipped", reason="no expectation artifact")
            return cases
        if not self.config.create_poisson_observation:
            self.ledger.update_stage("observation", "skipped", reason="disabled by effective config")
            return cases
        if self._stage_is_passed("observation"):
            for record in cases:
                observation = record.get("observation", {})
                self._assert_hash(
                    Path(observation.get("observation", "")),
                    observation.get("sha256"),
                    f"{record['case_id']} observation",
                )
                qc_record = record.get("qc", {}).get("observation", {})
                if qc_record:
                    self._assert_hash(
                        Path(qc_record.get("path", "")),
                        qc_record.get("sha256"),
                        f"{record['case_id']} observation QC",
                    )
            return cases
        self.ledger.update_stage("observation", "running")
        empirical_targets = (
            assign_empirical_count_targets(
                [record["case_id"] for record in cases],
                self.config.empirical_reference_counts,
                seed=self.config.split_seed + self.config.observation_seed_offset,
            )
            if self.config.observation_policy == "empirical_total_counts"
            else {}
        )
        failed: list[str] = []
        for record in cases:
            self._pause_if_requested("observation")
            prior = record.get("observation", {})
            prior_path = Path(prior.get("observation", ""))
            prior_qc = record.get("qc", {}).get("observation", {})
            prior_qc_path = Path(prior_qc.get("path", ""))
            if (
                prior_path.is_file()
                and sha256_file(prior_path) == prior.get("sha256")
                and prior_qc.get("status") == "passed"
                and prior_qc_path.is_file()
                and sha256_file(prior_qc_path) == prior_qc.get("sha256")
            ):
                continue
            seed = int(record["seed"]) + self.config.observation_seed_offset
            out = self.layout.subdir("observation") / f"{record['case_id']}.a00"
            result = sample_poisson_observation(
                Path(record["expectation"]["a00"]),
                out,
                seed=seed,
                scale=self.config.observation_scale,
                target_total_counts=empirical_targets.get(record["case_id"]),
                shape=self.config.projection_shape,
                protocol_status=self.config.observation_protocol_status,
            )
            result.update(
                {
                    "realization_id": f"{record['case_id']}_poisson_0001",
                    "parent_phantom_id": record["phantom_id"],
                    "split": record["split"],
                    "observation_relpath": self._relative(out),
                    "expectation_relpath": self._relative(Path(record["expectation"]["a00"])),
                }
            )
            record["observation"] = result
            low_cv, high_cv = self.config.empirical_angular_cv_range
            empirical_policy = self.config.observation_policy == "empirical_total_counts"
            count_passed = bool(
                not empirical_policy
                or (
                    result.get("target_relative_error") is not None
                    and result["target_relative_error"] <= 0.01
                )
            )
            angular_cv_passed = bool(
                not empirical_policy
                or (
                    result.get("angular_cv") is not None
                    and float(low_cv) <= result["angular_cv"] <= float(high_cv)
                )
            )
            observation_qc = {
                "status": "passed" if count_passed and angular_cv_passed else "failed",
                "policy": self.config.observation_policy,
                "total_count_relative_error_maximum": 0.01,
                "total_count_passed": count_passed,
                "angular_cv_empirical_range": [float(low_cv), float(high_cv)],
                "angular_cv_passed": angular_cv_passed,
                "target_total_counts": result.get("target_total_counts"),
                "observed_total_counts": result["sum"],
                "target_relative_error": result.get("target_relative_error"),
                "angular_cv": result.get("angular_cv"),
                "claim_boundary": result.get("claim_boundary"),
            }
            qc_path = self.layout.subdir("qc") / f"{record['case_id']}_observation_qc.json"
            atomic_write_json(qc_path, observation_qc)
            record.setdefault("qc", {})["observation"] = {
                "status": observation_qc["status"],
                "path": str(qc_path.resolve()),
                "relpath": self._relative(qc_path),
                "sha256": sha256_file(qc_path),
            }
            if observation_qc["status"] != "passed":
                failed.append(record["case_id"])
        self.ledger.write_cases(cases)
        self.ledger.update_stage(
            "observation",
            "passed" if not failed else "failed",
            transform="offline_poisson",
            protocol_status=self.config.observation_protocol_status,
            policy=self.config.observation_policy,
            empirical_reference_counts=list(self.config.empirical_reference_counts),
            empirical_angular_cv_range=list(self.config.empirical_angular_cv_range),
            failed_cases=failed,
            absolute_cps_per_mbq_claim=False,
        )
        if failed:
            raise RuntimeError(
                "Observation QC failed without angular-profile warping: "
                + ", ".join(failed)
            )
        return cases

    def package_anatomy_only(self) -> Path:
        """Package Gate A without export, SIMIND, observations, training or evaluation."""
        if self.config.execution_scope != "anatomy_only_gate_a":
            raise RuntimeError("package_anatomy_only requires anatomy_only_gate_a scope")
        existing_state = self.ledger.load()
        if existing_state.get("finalized"):
            manifest = self.layout.root / "dataset_manifest.json"
            self._assert_hash(
                manifest,
                existing_state.get("package_sha256"),
                "finalized anatomy-only manifest",
            )
            return manifest
        try:
            cases = self.run_phantom_qc()
        except Exception as exc:
            cases = self.ledger.read_cases()
            summary_path = self.layout.subdir("qc") / "phantom_qc_summary.json"
            failure_path = self.layout.root / "gate_a_failures.json"
            if summary_path.is_file() and len(cases) >= 5:
                try:
                    write_gate_a_reports(self.layout.root, cases, self.config)
                except Exception as report_exc:
                    if not failure_path.is_file():
                        atomic_write_json(
                            failure_path,
                            {
                                "schema_version": "pars_gate_a_v2_failures_v1",
                                "status": "failed",
                                "failure_count": 1,
                                "failures": [{
                                    "kind": "report_generation",
                                    "error": str(report_exc),
                                    "upstream_error": str(exc),
                                }],
                            },
                        )
            elif not failure_path.is_file():
                atomic_write_json(
                    failure_path,
                    {
                        "schema_version": "pars_gate_a_v2_failures_v1",
                        "status": "failed",
                        "failure_count": 1,
                        "failures": [{
                            "kind": "pipeline",
                            "error": str(exc),
                            "generated_case_count": len(cases),
                        }],
                    },
                )
            raise
        summary_path = self.layout.subdir("qc") / "phantom_qc_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("gate_a_population_acceptance", {}).get("status") != "passed":
            raise RuntimeError("Cannot package anatomy-only pilot: Gate A did not pass")

        splits = {name: [] for name in ("train", "val", "test")}
        for record in cases:
            splits[record["split"]].append(record["case_id"])
        atomic_write_json(
            self.layout.root / "splits.json",
            {
                "seed": self.config.split_seed,
                "fractions": list(self.config.split_fractions),
                "assignment_unit": "phantom_id",
                "splits": splits,
            },
        )
        report = write_gate_a_reports(self.layout.root, cases, self.config)

        inventory: list[dict] = []
        excluded = {"dataset_manifest.json", "run.json"}
        for path in sorted(
            path
            for path in self.layout.root.rglob("*")
            if path.is_file() and path.name not in excluded
        ):
            inventory.append(
                {
                    "path": path.relative_to(self.layout.root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        manifest = {
            "dataset_id": self.config.run_id,
            "created_utc": utc_now(),
            "scope": "synthetic_liver_anatomy_only_gate_a",
            "execution_scope": self.config.execution_scope,
            "anatomy_model": self.config.phantom.anatomy_model,
            "simulation_mode": "not_run_anatomy_only",
            "case_count": len(cases),
            "cases_manifest": "cases.jsonl",
            "split_manifest": "splits.json",
            "gate_a_report": "gate_a_report.json",
            "gate_a_report_status": report["status"],
            "gate_a_failure_list": "gate_a_failures.json",
            "npz_contract": {
                "required_keys": [
                    "activity",
                    "mu_map",
                    "liver_mask",
                    "left_mask",
                    "right_mask",
                    "tumor_masks",
                ],
                "metadata_suffix": "_meta.json",
                "case_hashes_in_cases_jsonl": True,
            },
            "v2_profile": {
                "path": self.config.phantom.v2_population_profile,
                "evidence_registry": self.config.phantom.v2_evidence_registry,
            },
            "projection_orientation": CANONICAL_PROJECTION_TRANSFORM,
            "attenuation_contract_status": self.config.phantom.mu_contract_status,
            "type7_attenuation_contract": {
                "status": "preserved_not_executed",
                "stored_formula": "mu_cm_inverse * density_voxel_size_cm",
                "density_threshold_times_1000": self.config.type7_density_threshold_times_1000,
                "phantom_cross_sections": list(self.config.phantom_cross_sections),
            },
            "detector_contract": {
                "status": "preserved_not_executed",
                "index_100_101": [self.config.detector_matrix_i, self.config.detector_matrix_j],
                "native_fov_cm": [39.36, 51.168],
                "nn_multiplier": self.config.nn_multiplier,
            },
            "observation_contract": {
                "enabled": False,
                "status": "not_run_anatomy_only",
            },
            "prohibited_stages": [
                "simind",
                "gpu",
                "e_cal",
                "pars_training",
                "sealed_evaluation",
                "formal550",
            ],
            "files": inventory,
        }
        manifest_path = self.layout.root / "dataset_manifest.json"
        atomic_write_json(manifest_path, manifest)
        self.ledger.update_stage(
            "package",
            "passed",
            manifest=str(manifest_path.resolve()),
            manifest_sha256=sha256_file(manifest_path),
            file_count=len(inventory),
            execution_scope=self.config.execution_scope,
        )
        return manifest_path

    def package(self) -> Path:
        existing_state = self.ledger.load()
        if existing_state.get("finalized"):
            manifest = self.layout.root / "dataset_manifest.json"
            self._assert_hash(
                manifest,
                existing_state.get("package_sha256"),
                "finalized dataset manifest",
            )
            return manifest
        cases = (
            self.simulate_or_mock()
            if self.config.schema_version == SCHEMA_VERSION
            else self.create_observations()
        )
        required_data_stage = "expectation"
        if self.config.simulation_mode == "prepare":
            required_data_stage = "simind_plan"
        stages = self.ledger.load()["stages"]
        if stages.get(required_data_stage, {}).get("status") not in {"passed", "prepared"}:
            raise RuntimeError(f"Cannot package: {required_data_stage} is incomplete")
        splits = {name: [] for name in ("train", "val", "test")}
        for record in cases:
            splits[record["split"]].append(record["case_id"])
        atomic_write_json(self.layout.root / "splits.json", {
            "seed": self.config.split_seed,
            "fractions": list(self.config.split_fractions),
            "assignment_unit": "phantom_id",
            "splits": splits,
        })
        figure_evidence = export_run_figures(self.layout.root, cases)

        inventory: list[dict] = []
        # ``run.json`` is a mutable ledger that is finalized immediately after
        # packaging, so it must not be represented as an immutable payload.
        excluded = {"dataset_manifest.json", "run.json"}
        for path in sorted(p for p in self.layout.root.rglob("*") if p.is_file() and p.name not in excluded):
            inventory.append(
                {
                    "path": path.relative_to(self.layout.root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        manifest = {
            "dataset_id": self.config.run_id,
            "created_utc": utc_now(),
            "schema_version": self.config.schema_version,
            "generation_profile": self.config.generation_profile,
            "runtime_backend": self.config.runtime_backend,
            "windows_v1": (
                self.config.windows_v1.to_dict()
                if self.config.windows_v1 is not None
                else None
            ),
            "effective_config_sha256": hashlib.sha256(
                json.dumps(
                    self.config.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "scope": "synthetic_liver_spect_data_preparation_only",
            "protocol_label": self.config.protocol_label,
            "protocol_status": self.config.protocol_status,
            "simulation_mode": self.config.simulation_mode,
            "case_count": len(cases),
            "case_roles": {
                record["case_id"]: record.get("case_role", "legacy") for record in cases
            },
            "case_artifacts": {
                record["case_id"]: {
                    "case_role": record.get("case_role"),
                    "split_role": record.get("split_role"),
                    "seed": record.get("seed"),
                    "simind_input": record.get("simind_input"),
                    "expectation": record.get("expectation"),
                    "qc": record.get("qc"),
                }
                for record in cases
            },
            "cases_manifest": "cases.jsonl",
            "split_manifest": "splits.json",
            "projection_orientation": CANONICAL_PROJECTION_TRANSFORM,
            "attenuation_contract_status": self.config.phantom.mu_contract_status,
            "type7_attenuation_contract": {
                "stored_formula": "mu_cm_inverse * density_voxel_size_cm",
                "density_threshold_times_1000": (
                    self.config.type7_density_threshold_times_1000
                ),
                "phantom_cross_sections": list(self.config.phantom_cross_sections),
                "validation_evidence": (
                    "experiments/validation-v10/attenuation_ict/analysis.json"
                ),
            },
            "detector_contract": {
                "index_100_101": [
                    self.config.detector_matrix_i,
                    self.config.detector_matrix_j,
                ],
                "native_fov_cm": [39.36, 51.168],
            },
            "observation_contract": {
                "enabled": self.config.create_poisson_observation,
                "policy": self.config.observation_policy,
                "protocol_status": self.config.observation_protocol_status,
                "absolute_cps_per_mbq_claim": False,
            },
            "scientific_authority": self.ledger.load()
            .get("provenance", {})
            .get("scientific_authority"),
            "windows_runtime": self.ledger.load()
            .get("provenance", {})
            .get("windows_runtime"),
            "windows_platform": self.ledger.load()
            .get("provenance", {})
            .get("windows_platform"),
            "simind_jobs": json.loads(
                (self.layout.subdir("logs") / "simind_jobs.json").read_text(encoding="utf-8")
            ),
            "files": inventory,
            "figure_evidence": figure_evidence,
        }
        manifest_path = self.layout.root / "dataset_manifest.json"
        atomic_write_json(manifest_path, manifest)
        self.ledger.update_stage(
            "package",
            "passed",
            manifest=str(manifest_path.resolve()),
            manifest_sha256=sha256_file(manifest_path),
            file_count=len(inventory),
        )
        return manifest_path

    def finalize(self) -> dict:
        state = self.ledger.load()
        if state.get("finalized"):
            manifest = self.layout.root / "dataset_manifest.json"
            self._assert_hash(manifest, state.get("package_sha256"), "finalized dataset manifest")
            return state
        if self.config.simulation_mode == "prepare":
            raise RuntimeError(
                "Cannot finalize a data set with prepared-only SIMIND jobs; execute/import and QC expectations first."
            )
        manifest = self.package()
        return self.ledger.finalize(package_sha256=sha256_file(manifest))

    def run_all(self, *, finalize: bool = True) -> dict:
        state = self.ledger.load()
        if state.get("finalized"):
            manifest = self.layout.root / "dataset_manifest.json"
            self._assert_hash(manifest, state.get("package_sha256"), "finalized dataset manifest")
            return state
        if self.config.execution_scope == "anatomy_only_gate_a":
            manifest = self.package_anatomy_only()
            if finalize:
                return self.ledger.finalize(package_sha256=sha256_file(manifest))
            return self.ledger.load()
        if finalize:
            return self.finalize()
        self.package()
        return self.ledger.load()
