"""Plan a V2 dataset before generation and atomically ingest completed case payloads."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Mapping

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    ARRAY_CONTRACT_V2,
    DATASET_COMPLETE_FILENAME,
    CasePayloadV2,
    build_split_plan,
    write_case_v2,
    write_split_plan,
)
from core.provenance import atomic_write_json, sha256_json  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path(
    r"D:\PFE-U\PAR-S-Generator\output\PAR-S-TARE-HCC-NoPVI-SYN-v2-pilot"
)
GENERATION_PLAN_FILENAME = "GENERATION_PLAN.json"
GENERATION_PLAN_SCHEMA_VERSION = "pars_generation_plan_v2"


def _case_entries(
    *,
    case_count: int,
    family_size: int,
    dataset_role: str,
    profile_id: str,
    family_to_split: Mapping[str, str],
) -> list[dict[str, object]]:
    prefix = "negative" if dataset_role == "negative" else "case"
    family_prefix = "negative_family" if dataset_role == "negative" else "family"
    entries: list[dict[str, object]] = []
    for index in range(case_count):
        family_id = f"{family_prefix}_{index // family_size:05d}"
        entries.append(
            {
                "case_id": f"{prefix}_{index:05d}",
                "case_family_id": family_id,
                "profile_id": profile_id,
                "split": family_to_split[family_id],
                "population_weight": 0.0 if dataset_role == "negative" else 1.0,
                "sampling_probability": 1.0 / case_count,
            }
        )
    return entries


def build_generation_plan(
    *,
    dataset_id: str,
    dataset_version: str,
    dataset_role: str,
    profile_id: str,
    case_count: int,
    family_size: int,
    global_seed: int,
    ratios: Mapping[str, float],
) -> tuple[object, dict[str, object]]:
    if case_count < 1 or family_size < 1:
        raise ValueError("case_count and family_size must be positive")
    family_count = math.ceil(case_count / family_size)
    family_prefix = "negative_family" if dataset_role == "negative" else "family"
    families = [f"{family_prefix}_{index:05d}" for index in range(family_count)]
    split_plan = build_split_plan(
        families,
        dataset_id=dataset_id,
        profile_id=profile_id,
        global_seed=global_seed,
        ratios=ratios,
    )
    content = {
        "schema_version": GENERATION_PLAN_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "dataset_role": dataset_role,
        "profile_id": profile_id,
        "case_count": case_count,
        "family_size": family_size,
        "global_seed": global_seed,
        "split_plan_sha256": split_plan.sha256,
        "entries": _case_entries(
            case_count=case_count,
            family_size=family_size,
            dataset_role=dataset_role,
            profile_id=profile_id,
            family_to_split=split_plan.family_to_split,
        ),
    }
    return split_plan, {**content, "sha256": sha256_json(content)}


def write_generation_plan(plan: Mapping[str, object], output_root: Path) -> Path:
    path = output_root / GENERATION_PLAN_FILENAME
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != dict(plan):
            raise RuntimeError("immutable GENERATION_PLAN.json already has different content")
        return path
    atomic_write_json(path, plan)
    return path


def _load_staged_case(
    staging_root: Path,
    entry: Mapping[str, object],
    generation_plan: Mapping[str, object],
) -> CasePayloadV2:
    case_dir = staging_root / str(entry["case_id"])
    npz_path = case_dir / "phantom.npz"
    payload_path = case_dir / "payload.json"
    if not npz_path.is_file() or not payload_path.is_file():
        raise FileNotFoundError(
            f"staged case {entry['case_id']} requires phantom.npz and payload.json"
        )
    raw = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - {"metadata", "extra_artifacts"}:
        raise ValueError(
            f"staged case {entry['case_id']} payload.json allows only metadata/extra_artifacts"
        )
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"staged case {entry['case_id']} metadata must be an object")
    with np.load(npz_path, allow_pickle=False) as archive:
        if set(archive.files) != set(ARRAY_CONTRACT_V2):
            raise ValueError(f"staged case {entry['case_id']} NPZ keys violate V2 contract")
        arrays = {key: archive[key].copy() for key in archive.files}
    raw_artifacts = raw.get("extra_artifacts", {})
    if not isinstance(raw_artifacts, dict):
        raise ValueError(f"staged case {entry['case_id']} extra_artifacts must be an object")
    extra_artifacts = {
        str(name): case_dir / str(relative_path)
        for name, relative_path in raw_artifacts.items()
    }
    return CasePayloadV2(
        case_id=str(entry["case_id"]),
        case_family_id=str(entry["case_family_id"]),
        profile_id=str(entry["profile_id"]),
        dataset_id=str(generation_plan["dataset_id"]),
        dataset_version=str(generation_plan["dataset_version"]),
        dataset_role=str(generation_plan["dataset_role"]),
        split=str(entry["split"]),
        population_weight=float(entry["population_weight"]),
        sampling_probability=float(entry["sampling_probability"]),
        arrays=arrays,
        metadata=metadata,
        extra_artifacts=extra_artifacts,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze family splits before generation, then ingest staged V2 cases."
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--dataset-id", default="PAR-S-TARE-HCC-NoPVI-SYN-v2-pilot"
    )
    parser.add_argument("--dataset-version", default="2.0.0-pilot")
    parser.add_argument("--dataset-role", choices=("main", "negative"), default="main")
    parser.add_argument(
        "--profile-id", default="population_tare_hcc_nopvi_v2"
    )
    parser.add_argument("--case-count", type=int, required=True)
    parser.add_argument(
        "--family-size",
        type=int,
        default=1,
        help="Noise/attenuation replicas per immutable case_family_id.",
    )
    parser.add_argument("--global-seed", type=int, default=20260714)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    parser.add_argument(
        "--staging-root",
        type=Path,
        help="Optional upstream case payload root; omit to write plans only.",
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if (args.output_root / DATASET_COMPLETE_FILENAME).exists():
        raise RuntimeError("dataset is already frozen")
    ratios = {
        "train": args.train_ratio,
        "val": args.val_ratio,
        "test": args.test_ratio,
    }
    split_plan, generation_plan = build_generation_plan(
        dataset_id=args.dataset_id,
        dataset_version=args.dataset_version,
        dataset_role=args.dataset_role,
        profile_id=args.profile_id,
        case_count=args.case_count,
        family_size=args.family_size,
        global_seed=args.global_seed,
        ratios=ratios,
    )
    args.output_root.mkdir(parents=True, exist_ok=True)
    split_path = write_split_plan(split_plan, args.output_root)
    generation_path = write_generation_plan(generation_plan, args.output_root)

    records = []
    if args.staging_root is not None:
        for entry in generation_plan["entries"]:
            payload = _load_staged_case(args.staging_root, entry, generation_plan)
            records.append(write_case_v2(payload, args.output_root, resume=args.resume))
    print(
        json.dumps(
            {
                "status": "written" if args.staging_root is not None else "planned",
                "output_root": str(args.output_root.resolve()),
                "split_plan": str(split_path.resolve()),
                "generation_plan": str(generation_path.resolve()),
                "planned_case_count": len(generation_plan["entries"]),
                "written_case_count": len(records),
                "split_plan_sha256": split_plan.sha256,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

