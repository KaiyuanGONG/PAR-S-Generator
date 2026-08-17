"""The single end-to-end synthetic-data preparation workflow.

The runner deliberately ends at a finalized dataset package.  It does not
import or call any reconstruction, training, inference, checkpoint, or model
evaluation component.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from core.interfile_writer import convert_npz_to_interfile
from core.phantom_generator import PhantomConfig, PhantomGenerator
from pipeline.contracts import (
    RunLayout,
    RunLedger,
    assign_fixed_splits,
    atomic_write_json,
    sha256_file,
    utc_now,
)
from pipeline.figures import export_run_figures
from pipeline.observation import sample_poisson_observation
from pipeline.qc import phantom_qc, validate_projection_artifacts
from pipeline.simind import SimindJob, expected_res_tokens, prepare_jobs, run_job


class PipelinePaused(RuntimeError):
    """Raised at a safe case boundary after state has been checkpointed."""


@dataclass
class PipelineConfig:
    run_id: str
    runs_root: str = "runs"
    phantom: PhantomConfig = field(default_factory=PhantomConfig)
    simind_exe: str = "simind/simind.exe"
    smc_file: str = "simind/ge870_czt.smc"
    nn_multiplier: int = 10
    simind_overrides: list[tuple[int, str]] = field(default_factory=list)
    projection_shape: tuple[int, int, int] = (60, 128, 128)
    simulation_mode: str = "prepare"  # prepare, mock, execute
    create_poisson_observation: bool = False
    observation_scale: float = 1.0
    observation_seed_offset: int = 1_000_000
    observation_protocol_status: str = "toy"
    split_seed: int = 42
    split_fractions: tuple[float, float, float] = (0.8, 0.1, 0.1)
    protocol_label: str = "GE 870 CZT current liver SPECT research protocol"
    protocol_status: str = "pending_physics_validation"
    source_activity_mbq: float = 60.0
    exposure_time_s_per_projection: float | None = None
    smc_index25_activity_time: float = 1704.0
    activity_time_contract_status: str = "unresolved_60mbq_x_20s_vs_smc_index25_1704"

    def __post_init__(self):
        if self.simulation_mode not in {"prepare", "mock", "execute"}:
            raise ValueError("simulation_mode must be prepare, mock, or execute")
        if self.phantom.n_cases < 1:
            raise ValueError("phantom.n_cases must be positive")
        if tuple(self.phantom.volume_shape) != (128, 128, 128):
            raise ValueError("Current validated scope requires a 128x128x128 phantom")

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["phantom"] = self.phantom.to_dict()
        # Canonicalize tuples and NumPy-compatible scalar values to the exact
        # JSON representation persisted in run.json, so resume comparison is
        # structural rather than Python-container-type dependent.
        return json.loads(json.dumps(payload))

    @classmethod
    def from_dict(cls, payload: dict) -> "PipelineConfig":
        data = dict(payload)
        data["phantom"] = PhantomConfig.from_dict(data.get("phantom", {}))
        if "simind_overrides" in data:
            data["simind_overrides"] = [
                (int(pair[0]), str(pair[1])) for pair in data["simind_overrides"]
            ]
        return cls(**data)


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
        source_files = (
            src_root / "core" / "phantom_generator.py",
            src_root / "pipeline" / "runner.py",
            src_root / "pipeline" / "qc.py",
            src_root / "pipeline" / "simind.py",
            src_root / "pipeline" / "observation.py",
        )
        exe = Path(config.simind_exe).resolve()
        smc = Path(config.smc_file).resolve()
        return {
            "generator": "PhantomGenerator.generate_one",
            "projection_orientation": "raw[::-1,::-1,:]",
            "protocol_scope": "liver_only_current_protocol",
            "software_sha256": {
                path.relative_to(src_root).as_posix(): sha256_file(path) for path in source_files
            },
            "simind_executable": {
                "path": str(exe),
                "sha256": sha256_file(exe) if exe.is_file() else None,
            },
            "smc": {
                "path": str(smc),
                "sha256": sha256_file(smc) if smc.is_file() else None,
            },
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
        return [f"case_{index:04d}" for index in range(1, self.config.phantom.n_cases + 1)]

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
        case_ids = self._case_ids()
        splits = assign_fixed_splits(case_ids, self.config.split_seed, self.config.split_fractions)
        existing = {record["case_id"]: record for record in self.ledger.read_cases()}
        cases: list[dict] = []
        try:
            for index, case_id in enumerate(case_ids, 1):
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
                numeric_id = int(case_id.rsplit("_", 1)[1])
                result = generator.generate_one(numeric_id)
                result.save(self.layout.subdir("phantom"))
                npz = self.layout.subdir("phantom") / f"{case_id}.npz"
                meta = self.layout.subdir("phantom") / f"{case_id}_meta.json"
                cases.append(
                    {
                        "case_id": case_id,
                        "phantom_id": case_id,
                        "split": splits[case_id],
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
        summary = {"status": "passed" if not failed else "failed", "failed_cases": failed, "case_count": len(cases)}
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
                    "mu_contract": {
                        "semantic": "linear_attenuation_coefficient",
                        "unit": self.config.phantom.mu_unit,
                        "reference_energy_kev": self.config.phantom.mu_reference_energy_kev,
                        "status": self.config.phantom.mu_contract_status,
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
                overrides=tuple(self.config.simind_overrides),
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
        if self.config.simulation_mode == "prepare":
            self.ledger.update_stage(
                "expectation", "skipped", reason="SIMIND commands prepared but not executed"
            )
            return cases
        if self._stage_is_passed("expectation"):
            for record, job in zip(cases, jobs, strict=True):
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
        for record, job in zip(cases, jobs, strict=True):
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
            else:
                qc = run_job(
                    job,
                    self.layout.subdir("logs") / "simind_runtime.log",
                    shape=self.config.projection_shape,
                )
                qc["backend"] = "simind"
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
                "a00_sha256": qc.get("sha256", {}).get("a00"),
                "res_sha256": qc.get("sha256", {}).get("res"),
            }
            if qc["status"] != "passed":
                failed.append(record["case_id"])
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
            return cases
        self.ledger.update_stage("observation", "running")
        for record in cases:
            self._pause_if_requested("observation")
            prior = record.get("observation", {})
            prior_path = Path(prior.get("observation", ""))
            if prior_path.is_file() and sha256_file(prior_path) == prior.get("sha256"):
                continue
            seed = int(record["seed"]) + self.config.observation_seed_offset
            out = self.layout.subdir("observation") / f"{record['case_id']}.a00"
            result = sample_poisson_observation(
                Path(record["expectation"]["a00"]),
                out,
                seed=seed,
                scale=self.config.observation_scale,
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
        self.ledger.write_cases(cases)
        self.ledger.update_stage(
            "observation",
            "passed",
            transform="offline_poisson",
            protocol_status=self.config.observation_protocol_status,
        )
        return cases

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
        cases = self.create_observations()
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
            "scope": "synthetic_liver_spect_data_preparation_only",
            "protocol_label": self.config.protocol_label,
            "protocol_status": self.config.protocol_status,
            "simulation_mode": self.config.simulation_mode,
            "case_count": len(cases),
            "cases_manifest": "cases.jsonl",
            "split_manifest": "splits.json",
            "projection_orientation": "raw[::-1,::-1,:]",
            "attenuation_contract_status": self.config.phantom.mu_contract_status,
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
        if finalize:
            return self.finalize()
        self.package()
        return self.ledger.load()
