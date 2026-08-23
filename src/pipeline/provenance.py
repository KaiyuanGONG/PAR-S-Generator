"""Runtime and scientific-source fingerprints for pipeline runs."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from core.smc_parser import parse_smc
from core.windows_runtime import assess_windows_runtime
from core.windows_v1 import (
    GATE_A_GENERATOR_COMMIT,
    GATE_C_CONFIG_SHA256,
    LIMITED_ACTIVITY_UPSTREAM_SOURCE_SHA256,
)
from pipeline.contracts import CANONICAL_PROJECTION_TRANSFORM, sha256_file

if TYPE_CHECKING:
    from pipeline.runner import PipelineConfig


def build_runtime_provenance(config: PipelineConfig) -> dict:
    src_root = Path(__file__).resolve().parents[1]
    source_files = [
        src_root / "core" / "phantom_generator.py",
        src_root / "core" / "interfile_writer.py",
        src_root / "pipeline" / "provenance.py",
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
        if actual_cross_sections != tuple(value.lower() for value in config.phantom_cross_sections):
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
