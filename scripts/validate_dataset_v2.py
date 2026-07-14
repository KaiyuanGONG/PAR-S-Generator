"""Independent fail-closed Generator gate for a frozen PAR-S V2 pilot."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from freeze_dataset_v2 import load_generation_plan  # noqa: E402

from core.case_writer_v2 import (  # noqa: E402
    CasePayloadV2,
    DatasetContractV2,
    DatasetFreezeRecordV2,
    freeze_dataset,
    load_case_record_v2,
    validate_case_payload_v2,
)
from core.pilot_v2 import (  # noqa: E402
    load_pilot_plan,
    resolve_plan_path,
    validate_boundary_rejections,
)
from core.provenance import atomic_write_bytes, atomic_write_json, sha256_file  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402
from core.seeds import SeedBundle  # noqa: E402


GATE_SCHEMA = "pars_v2_generator_pilot_gate_v1"
METADATA_FIELDS = {
    "seeds",
    "config_hashes",
    "patient",
    "target_metrics",
    "actual_metrics",
    "activity",
    "spatial",
    "acquisition",
    "physics",
    "simulation",
    "quality_control",
}
REQUIRED_ARTIFACTS = (
    "phantom_npz",
    "metadata_json",
    "projection_a00",
    "projection_mhd",
    "projection_res",
    "projection_spe",
    "simind_run_provenance",
    "simind_source_bin",
    "simind_density_bin",
    "pilot_plan",
    "pilot_runtime",
)


class PilotGateError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a frozen Task-12 V2 pilot.")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "pilot3_v2.json",
    )
    parser.add_argument("--simind-exe", type=Path, required=True)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT / "docs" / "reports" / "v2_pilot3_generator_gate.json",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=REPO_ROOT / "docs" / "reports" / "v2_pilot_report.md",
    )
    return parser


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotGateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PilotGateError(f"{path} must contain a JSON object")
    return value


def _artifact_path(root: Path, record: Any, name: str) -> Path:
    return root / record.artifacts[name].relative_path


def _load_payload(root: Path, record: Any) -> tuple[CasePayloadV2, dict[str, Any]]:
    metadata_document = _read_json(_artifact_path(root, record, "metadata_json"))
    if not METADATA_FIELDS.issubset(metadata_document):
        raise PilotGateError(f"{record.case_id}: metadata fields are incomplete")
    with np.load(_artifact_path(root, record, "phantom_npz"), allow_pickle=False) as archive:
        arrays = {name: archive[name].copy() for name in archive.files}
    payload = CasePayloadV2(
        case_id=record.case_id,
        case_family_id=record.case_family_id,
        profile_id=record.profile_id,
        dataset_id=record.dataset_id,
        dataset_version=record.dataset_version,
        dataset_role=record.dataset_role,
        split=record.split,
        population_weight=record.population_weight,
        sampling_probability=record.sampling_probability,
        arrays=arrays,
        metadata={name: metadata_document[name] for name in METADATA_FIELDS},
        extra_artifacts={},
    )
    validate_case_payload_v2(payload)
    return payload, metadata_document


def _canonical_hashes(plan: Mapping[str, object]) -> dict[str, str]:
    paths = {
        "evidence_registry_sha256": resolve_plan_path(
            REPO_ROOT, plan["evidence_registry_path"], "evidence_registry_path"
        ),
        "population_config_sha256": resolve_plan_path(
            REPO_ROOT, plan["profile_path"], "profile_path"
        ),
        "scanner_config_sha256": resolve_plan_path(
            REPO_ROOT, plan["scanner_path"], "scanner_path"
        ),
        "simind_ini_sha256": resolve_plan_path(
            REPO_ROOT, plan["simind_ini_path"], "simind_ini_path"
        ),
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def _validate_case_bindings(
    root: Path,
    record: Any,
    metadata: Mapping[str, Any],
    canonical_hashes: Mapping[str, str],
    plan_case: Mapping[str, Any],
    global_seed: int,
    pilot_plan_sha256: str,
    expected_binary_sha256: str,
    canonical_smc_sha256: str,
    execution: Mapping[str, Any],
    frozen_runtime: Mapping[str, Any],
) -> dict[str, Any]:
    if set(REQUIRED_ARTIFACTS) - set(record.artifacts):
        raise PilotGateError(f"{record.case_id}: retained artifact set is incomplete")
    if dict(metadata["config_hashes"]) != dict(canonical_hashes):
        raise PilotGateError(f"{record.case_id}: metadata does not bind canonical configs")
    expected_seed = SeedBundle.from_case(global_seed, record.case_id)
    if metadata["seeds"]["simind"] != expected_seed.simind:
        raise PilotGateError(f"{record.case_id}: /RR does not match the frozen seed tree")
    if not 1 <= expected_seed.simind <= int(execution["rr_maximum"]):
        raise PilotGateError(f"{record.case_id}: /RR exceeds the versioned practical range")
    if metadata["simulation"]["binary_sha256"] != expected_binary_sha256:
        raise PilotGateError(f"{record.case_id}: SIMIND binary differs from the pilot plan")
    if metadata["simulation"]["smc_snapshot_sha256"] != canonical_smc_sha256:
        raise PilotGateError(f"{record.case_id}: SMC snapshot differs from canonical SMC")
    if metadata["physics"]["base_histories_per_projection"] != int(
        execution["base_histories_per_projection"]
    ):
        raise PilotGateError(f"{record.case_id}: base histories differ from the pilot plan")
    if metadata["physics"]["nn_multiplier"] != int(execution["nn_multiplier"]):
        raise PilotGateError(f"{record.case_id}: /NN differs from the pilot plan")
    inputs = metadata["simulation"]["input_sha256"]
    if sha256_file(_artifact_path(root, record, "simind_source_bin")) != inputs["source"]:
        raise PilotGateError(f"{record.case_id}: retained source hash mismatch")
    if sha256_file(_artifact_path(root, record, "simind_density_bin")) != inputs["density"]:
        raise PilotGateError(f"{record.case_id}: retained density hash mismatch")
    if metadata["simulation"].get("simind_version") != "SIMIND V8.0":
        raise PilotGateError(f"{record.case_id}: SIMIND version is not parsed V8.0")
    provenance = _read_json(_artifact_path(root, record, "simind_run_provenance"))
    if not math.isclose(
        float(provenance.get("timeout_seconds", math.nan)),
        float(execution["timeout_seconds"]),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise PilotGateError(f"{record.case_id}: timeout differs from the pilot plan")
    if sha256_file(_artifact_path(root, record, "pilot_plan")) != pilot_plan_sha256:
        raise PilotGateError(f"{record.case_id}: frozen pilot plan artifact hash mismatch")
    case_runtime = _read_json(_artifact_path(root, record, "pilot_runtime"))
    if case_runtime != dict(frozen_runtime):
        raise PilotGateError(f"{record.case_id}: per-case runtime snapshot mismatch")
    quality = metadata["quality_control"]
    if quality.get("runtime_binding") != dict(frozen_runtime):
        raise PilotGateError(f"{record.case_id}: metadata runtime binding mismatch")
    intended = [float(item["dmax_mm"]) for item in plan_case["lesions"]]
    actual = [
        float(item["recist_3d_mm"])
        for item in metadata["actual_metrics"]["tumors"]["lesions"]
    ]
    tolerance_mm = 0.75 * float(metadata["acquisition"]["voxel_size_mm"])
    if len(actual) != len(intended) or any(
        abs(observed - target) > tolerance_mm
        for observed, target in zip(actual, intended)
    ):
        raise PilotGateError(
            f"{record.case_id}: actual RECIST values {actual} violate targets {intended} "
            f"within {tolerance_mm:g} mm"
        )
    with np.load(
        _artifact_path(root, record, "phantom_npz"), allow_pickle=False
    ) as archive:
        tumor_mask = np.asarray(archive["tumor_union_mask"], dtype=bool)
        liver_mask = np.asarray(archive["liver_mask"], dtype=bool)
    if np.any(tumor_mask & ~liver_mask):
        raise PilotGateError(f"{record.case_id}: tumor containment failed on frozen bytes")
    return {
        "case_id": record.case_id,
        "split": record.split,
        "rr_seed": expected_seed.simind,
        "liver_morphology": metadata["patient"]["liver_morphology"],
        "actual_liver_volume_ml": metadata["actual_metrics"]["liver"]["volume_ml"],
        "target_dmax_mm": intended,
        "actual_recist_3d_mm": actual,
        "lobe_extent": metadata["actual_metrics"]["tumors"]["lobe_extent"],
        "injection_territory": metadata["activity"]["injection_territory"],
        "mismatch_challenge": metadata["activity"]["mismatch_challenge"],
        "projection_weight_sum": metadata["simulation"]["projection_stats"][
            "projection_weight_sum"
        ],
        "status": "pass",
    }


def validate(
    dataset_root: Path,
    config_path: Path,
    simind_exe: Path,
) -> dict[str, Any]:
    root = dataset_root.resolve()
    plan = load_pilot_plan(config_path)
    execution = plan["execution"]
    if not isinstance(execution, Mapping):
        raise PilotGateError("pilot execution contract is invalid")
    generation = load_generation_plan(root)
    if int(generation["case_count"]) != 3:
        raise PilotGateError("generation plan does not contain exactly three cases")
    records = [
        load_case_record_v2(
            root / "cases" / str(entry["case_id"]) / "case_record.json",
            dataset_root=root,
            verify_hashes=True,
        )
        for entry in generation["entries"]
    ]
    marker = DatasetFreezeRecordV2.from_dict(_read_json(root / "DATASET_COMPLETE.json"))
    if set(marker.required_artifact_names) != set(REQUIRED_ARTIFACTS):
        raise PilotGateError("completion marker does not freeze the exact pilot artifact set")
    contract = DatasetContractV2(
        output_root=root,
        dataset_id=str(generation["dataset_id"]),
        dataset_version=str(generation["dataset_version"]),
        dataset_role=str(generation["dataset_role"]),
        expected_case_ids=tuple(str(entry["case_id"]) for entry in generation["entries"]),
        allowed_profile_ids=(str(generation["profile_id"]),),
        split_plan_sha256=str(generation["split_plan_sha256"]),
        required_artifact_names=REQUIRED_ARTIFACTS,
    )
    reaudited = freeze_dataset(records, contract)
    if reaudited != marker:
        raise PilotGateError("idempotent freeze re-audit differs from DATASET_COMPLETE")
    if not simind_exe.is_file() or sha256_file(simind_exe) != plan["expected_simind_binary_sha256"]:
        raise PilotGateError("current SIMIND binary does not match the frozen pilot plan")
    runtime = _read_json(root / "PILOT_RUNTIME.json")
    if runtime.get("pilot_plan_sha256") != sha256_file(config_path):
        raise PilotGateError("PILOT_RUNTIME does not bind the current pilot plan")
    if runtime.get("simind_binary_sha256") != sha256_file(simind_exe):
        raise PilotGateError("PILOT_RUNTIME binary hash mismatch")
    if (
        runtime.get("base_histories_per_projection")
        != int(execution["base_histories_per_projection"])
        or runtime.get("nn_multiplier") != int(execution["nn_multiplier"])
        or runtime.get("rr_allocator") != execution["rr_allocator"]
        or not math.isclose(
            float(runtime.get("timeout_seconds", math.nan)),
            float(execution["timeout_seconds"]),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        raise PilotGateError("PILOT_RUNTIME execution values differ from the pilot plan")

    canonical = _canonical_hashes(plan)
    smc_path = resolve_plan_path(REPO_ROOT, plan["smc_path"], "smc_path")
    canonical_smc_sha = sha256_file(smc_path)
    if runtime.get("smc_sha256") != canonical_smc_sha:
        raise PilotGateError("PILOT_RUNTIME SMC hash mismatch")
    registry_path = resolve_plan_path(
        REPO_ROOT, plan["evidence_registry_path"], "evidence_registry_path"
    )
    profile_path = resolve_plan_path(REPO_ROOT, plan["profile_path"], "profile_path")
    registry = load_evidence_registry(registry_path)
    profile = load_profile(profile_path, registry)
    boundary_results = validate_boundary_rejections(plan, profile)
    if runtime.get("boundary_gates") != boundary_results:
        raise PilotGateError("PILOT_RUNTIME boundary results differ from independent recomputation")
    plan_by_id = {str(item["case_id"]): item for item in plan["cases"]}
    case_results = []
    for record in records:
        _, metadata = _load_payload(root, record)
        case_results.append(
            _validate_case_bindings(
                root,
                record,
                metadata,
                canonical,
                plan_by_id[record.case_id],
                int(plan["global_seed"]),
                sha256_file(config_path),
                str(plan["expected_simind_binary_sha256"]),
                canonical_smc_sha,
                execution,
                runtime,
            )
        )
    if len(
        {
            record.artifacts["pilot_runtime"].sha256
            for record in records
        }
    ) != 1 or len(
        {record.artifacts["pilot_plan"].sha256 for record in records}
    ) != 1:
        raise PilotGateError("per-case pilot plan/runtime artifacts are not identical")
    if len({item["rr_seed"] for item in case_results}) != 3:
        raise PilotGateError("pilot /RR values are not unique")
    if {item["split"] for item in case_results} != {"train", "val", "test"}:
        raise PilotGateError("three-case pilot must cover train, val and test exactly once")
    if {item["liver_morphology"] for item in case_results} != {"normal", "cirrhotic"}:
        raise PilotGateError("pilot does not cover normal and cirrhotic liver morphology")
    if {item["lobe_extent"] for item in case_results} != {"unilobar", "bilobar"}:
        raise PilotGateError("pilot does not cover unilobar and bilobar tumors")
    if len({item["injection_territory"] for item in case_results}) != 3:
        raise PilotGateError("pilot does not cover three distinct injection territories")
    if {item["mismatch_challenge"] for item in case_results} != {False, True}:
        raise PilotGateError("pilot does not cover matched and mismatch perfusion")
    return {
        "schema_version": GATE_SCHEMA,
        "status": "pass",
        "dataset_root": str(root),
        "dataset_id": marker.dataset_id,
        "dataset_version": marker.dataset_version,
        "case_count": marker.case_count,
        "manifest_sha256": marker.manifest_sha256,
        "contract_sha256": marker.contract_sha256,
        "canonical_config_hashes": canonical,
        "simind_binary_sha256": sha256_file(simind_exe),
        "case_results": case_results,
        "boundary_gates": boundary_results,
        "gates": [
            "idempotent_dataset_freeze_reaudit",
            "canonical_config_and_binary_binding",
            "strict_payload_array_and_metadata_revalidation",
            "retained_simind_input_hash_binding",
            "simind_quartet_and_provenance_reaudit",
            "small_medium_large_actual_recist",
            "normal_cirrhotic_unilobar_bilobar_injection_and_mismatch_coverage",
            "200_and_215_mm_expected_structural_rejection",
        ],
        "go_for_15_case_pilot": False,
        "reason": "Task 12B aggregate gates and manual methodology review are required",
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# PAR-S V2 Task 12：首批 3 例 pilot 报告",
        "",
        f"- Generator gate：**{str(report['status']).upper()}**",
        f"- Dataset：`{report.get('dataset_id', 'unknown')}` / `{report.get('dataset_version', 'unknown')}`",
        f"- 冻结病例数：{report.get('case_count', 0)}",
        f"- Manifest SHA-256：`{report.get('manifest_sha256', 'n/a')}`",
        "- `/NN=1` 仅用于 deterministic smoke；不能据此声明临床计数标定完成。",
        "- 200 mm 与 215 mm 为预期结构性拒绝边界，不伪装成可完整 containment 的主人群病例。",
        "",
        "## 病例结果",
        "",
        "| Case | Split | 肝形态 | 目标 Dmax (mm) | 实际 RECIST (mm) | 叶范围 | 注射区 | Mismatch | /RR | 投影权重和 |",
        "|---|---|---|---:|---:|---|---|---|---:|---:|",
    ]
    for item in report.get("case_results", []):
        lines.append(
            "| {case_id} | {split} | {liver_morphology} | {target} | {actual} | "
            "{lobe_extent} | {injection_territory} | {mismatch_challenge} | "
            "{rr_seed} | {projection_weight_sum:.6g} |".format(
                **item,
                target=", ".join(f"{value:.1f}" for value in item["target_dmax_mm"]),
                actual=", ".join(f"{value:.2f}" for value in item["actual_recist_3d_mm"]),
            )
        )
    lines.extend(
        [
            "",
            "## 当前结论",
            "",
            "Generator 端生成、SIMIND、原子 case writer 与 dataset freeze 均已通过。",
            "本报告仅是上游证据，不能单独批准 15 例扩展；最终决定由 Task 12B 聚合门禁和人工方法学审核给出。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = validate(args.dataset_root, args.config, args.simind_exe)
    except Exception as exc:
        report = {
            "schema_version": GATE_SCHEMA,
            "status": "fail",
            "dataset_root": str(args.dataset_root.resolve()),
            "error": f"{type(exc).__name__}: {exc}",
            "go_for_15_case_pilot": False,
        }
    atomic_write_json(args.output_json, report)
    atomic_write_bytes(args.output_md, _markdown(report).encode("utf-8"))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
