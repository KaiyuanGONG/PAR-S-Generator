"""Adapter from the frozen V2 population anatomy to the master phantom contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .anatomy_v2 import TorsoAnatomyBuildV2, build_torso_anatomy_v2
from .attenuation_model_v2 import generate_attenuation_maps, select_simind_attenuation_map
from .liver_geometry import GridSpecV2, LiverGeometryV2, LiverShapeRejectedError, fit_liver_geometry
from .population_sampler import sample_liver_target, sample_patient
from .schemas_v2 import LiverTargetV2, PatientSampleV2, PopulationProfileV2, load_evidence_registry, load_profile
from .seeds import SeedBundle


INTEGRATION_BASE_COMMIT = "f423b81153f14495cd7c6afbbfe6292ea702f1aa"
V2_REFERENCE_COMMIT = "6f60d6048472be3868a4d533d989149ead751faa"
HYBRID_SCHEMA_VERSION = "pars_hybrid_v2_master_gate_a_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_array(array: np.ndarray) -> str:
    values = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(values.dtype).encode("ascii"))
    digest.update(json.dumps(list(values.shape), separators=(",", ":")).encode("ascii"))
    digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class RejectedLiverShapeAttemptV2:
    attempt_index: int
    shape_seed: int
    failed_gates: tuple[str, ...]


@dataclass(frozen=True)
class LiverSamplingProvenanceV2:
    liver_seed: int
    max_shape_attempts: int
    accepted_attempt_index: int
    accepted_shape_seed: int
    rejected_attempts: tuple[RejectedLiverShapeAttemptV2, ...] = ()


class LiverShapeRetryExhaustedError(RuntimeError):
    def __init__(
        self,
        case_id: str,
        max_shape_attempts: int,
        rejected_attempts: tuple[RejectedLiverShapeAttemptV2, ...],
    ) -> None:
        self.case_id = case_id
        self.max_shape_attempts = max_shape_attempts
        self.rejected_attempts = rejected_attempts
        gates = [record.failed_gates for record in rejected_attempts]
        super().__init__(
            f"case {case_id!r} exhausted {max_shape_attempts} liver shape attempts; "
            f"failed gates by attempt: {gates}"
        )


def _derive_liver_shape_attempt_seed(
    liver_seed: int,
    case_id: str,
    attempt_index: int,
) -> int:
    payload = (
        f"pars-syn-v2|{liver_seed}|{case_id}|liver-shape-attempt|{attempt_index}"
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**63 - 1) + 1


@dataclass(frozen=True)
class HybridAnatomyV2:
    seed_bundle: SeedBundle
    patient: PatientSampleV2
    target: LiverTargetV2
    geometry: LiverGeometryV2
    sampling_provenance: LiverSamplingProvenanceV2
    torso: TorsoAnatomyBuildV2
    mu_map: np.ndarray
    metadata: dict[str, Any]


class HybridV2Adapter:
    """Load V2 evidence once and emit master-compatible anatomy arrays."""

    def __init__(
        self,
        *,
        profile_path: str | Path,
        evidence_registry_path: str | Path,
        volume_shape: tuple[int, int, int],
        voxel_size_mm: float,
        max_shape_attempts: int,
    ) -> None:
        if (
            not isinstance(max_shape_attempts, int)
            or isinstance(max_shape_attempts, bool)
            or not 1 <= max_shape_attempts <= 32
        ):
            raise ValueError("max_shape_attempts must be an integer within [1, 32]")
        self.profile_path = _resolve_project_path(profile_path)
        self.evidence_registry_path = _resolve_project_path(evidence_registry_path)
        registry = load_evidence_registry(self.evidence_registry_path)
        profile = load_profile(self.profile_path, registry)
        if profile.role != "population" or not profile.population_claim:
            raise ValueError("Gate A requires a V2 population profile with population_claim=true")
        self.registry = registry
        self.profile: PopulationProfileV2 = profile
        self.grid = GridSpecV2(
            shape=tuple(int(value) for value in volume_shape),
            voxel_size_mm=float(voxel_size_mm),
        )
        self.max_shape_attempts = max_shape_attempts
        self.profile_sha256 = _sha256_file(self.profile_path)
        self.evidence_registry_sha256 = _sha256_file(self.evidence_registry_path)

    def _generate_liver(
        self,
        seed_bundle: SeedBundle,
    ) -> tuple[PatientSampleV2, LiverTargetV2, LiverGeometryV2, LiverSamplingProvenanceV2]:
        rng = np.random.default_rng(seed_bundle.patient)
        patient = sample_patient(self.profile, rng, case_id=seed_bundle.case_id)
        target = sample_liver_target(patient, self.profile, rng)
        rejected: list[RejectedLiverShapeAttemptV2] = []
        last_shape_error: LiverShapeRejectedError | None = None
        for attempt_index in range(1, self.max_shape_attempts + 1):
            shape_seed = _derive_liver_shape_attempt_seed(
                seed_bundle.liver,
                patient.case_id,
                attempt_index,
            )
            try:
                geometry = fit_liver_geometry(target, self.grid, shape_seed=shape_seed)
            except LiverShapeRejectedError as exc:
                last_shape_error = exc
                rejected.append(
                    RejectedLiverShapeAttemptV2(
                        attempt_index=attempt_index,
                        shape_seed=shape_seed,
                        failed_gates=exc.failed_gates,
                    )
                )
                continue
            provenance = LiverSamplingProvenanceV2(
                liver_seed=seed_bundle.liver,
                max_shape_attempts=self.max_shape_attempts,
                accepted_attempt_index=attempt_index,
                accepted_shape_seed=shape_seed,
                rejected_attempts=tuple(rejected),
            )
            return patient, target, geometry, provenance
        exhausted = LiverShapeRetryExhaustedError(
            patient.case_id,
            self.max_shape_attempts,
            tuple(rejected),
        )
        raise exhausted from last_shape_error

    def generate(self, *, case_id: str, global_seed: int) -> HybridAnatomyV2:
        seed_bundle = SeedBundle.from_case(global_seed, case_id)
        patient, target, geometry, provenance = self._generate_liver(seed_bundle)
        torso = build_torso_anatomy_v2(geometry, self.grid, patient)
        mu_true, mu_input, attenuation_metadata = generate_attenuation_maps(
            torso.anatomy,
            self.profile,
            np.random.default_rng(seed_bundle.mu),
        )
        mu_map = select_simind_attenuation_map("mu_true_140kev", mu_true)
        labels = np.asarray(geometry.region_labels)
        left = np.isin(labels, (1, 2, 3))
        right = np.isin(labels, (4, 5))
        liver = np.asarray(geometry.mask, dtype=bool)
        if np.any(left & right) or not np.array_equal(left | right, liver):
            raise RuntimeError("V2 region adapter must exactly and disjointly cover liver")

        metadata = {
            "schema_version": HYBRID_SCHEMA_VERSION,
            "integration_base_commit": INTEGRATION_BASE_COMMIT,
            "v2_reference_commit": V2_REFERENCE_COMMIT,
            "profile": {
                "profile_id": self.profile.profile_id,
                "role": self.profile.role,
                "population_claim": self.profile.population_claim,
                "path": self.profile_path.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": self.profile_sha256,
                "evidence_registry_id": self.registry.registry_id,
                "evidence_registry_path": self.evidence_registry_path.relative_to(PROJECT_ROOT).as_posix(),
                "evidence_registry_sha256": self.evidence_registry_sha256,
                "cirrhosis_prevalence": float(self.profile.value("cirrhosis_prevalence")),
                "liver_volume_range_ml": [
                    float(value)
                    for value in self.profile.value("liver_geometry_model")["volume_range_ml"]
                ],
                "left_fraction_reference": _jsonable(
                    self.profile.value("left_liver_fraction_reference")
                ),
            },
            "seeds": seed_bundle.to_dict(),
            "patient": _jsonable(asdict(patient)),
            "liver": {
                "target": _jsonable(asdict(target)),
                "actual": _jsonable(dict(geometry.actual_metrics)),
                "continuous_parameters": _jsonable(dict(geometry.continuous_parameters)),
                "evidence_types": _jsonable(dict(geometry.evidence_types)),
                "region_definition": geometry.region_definition,
                "sampling_provenance": _jsonable(asdict(provenance)),
            },
            "torso": _jsonable(torso.metadata.as_dict()),
            "adapters": {
                "left_right": "v2_region_labels_1_2_3_left__4_5_right_v1",
                "activity": "master_perfusion_on_v2_region_partition_v1",
                "attenuation": "v2_mu_true_to_master_mu_map_v1",
            },
            "attenuation": {
                "physical_map_key": "mu_true_140kev",
                "master_npz_key": "mu_map",
                "ct_like_map_key": "mu_input_140kev",
                "ct_like_map_saved_to_npz": False,
                "mu_true_sha256": _sha256_array(mu_true),
                "mu_input_sha256": _sha256_array(mu_input),
                "mu_input_min": float(mu_input.min()),
                "mu_input_max": float(mu_input.max()),
                "degradation": _jsonable(asdict(attenuation_metadata)),
            },
            "contracts": {
                "lesion_generator": "master_f423_physical_measured_strata_v1",
                "tumor_generator_v2_imported": False,
                "npz_contract": "master_required_six_arrays_v1",
                "type7_input_semantics": "physical_mu_cm_inverse_at_140p5kev",
            },
        }
        return HybridAnatomyV2(
            seed_bundle=seed_bundle,
            patient=patient,
            target=target,
            geometry=geometry,
            sampling_provenance=provenance,
            torso=torso,
            mu_map=mu_map,
            metadata=metadata,
        )
