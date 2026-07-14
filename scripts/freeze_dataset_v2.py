"""Verify every planned V2 case/artifact and write DATASET_COMPLETE.json last."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    DatasetContractV2,
    freeze_dataset,
    load_case_record_v2,
)
from core.provenance import sha256_json  # noqa: E402


GENERATION_PLAN_FILENAME = "GENERATION_PLAN.json"
GENERATION_PLAN_SCHEMA_VERSION = "pars_generation_plan_v2"


def load_generation_plan(output_root: Path) -> dict[str, object]:
    path = output_root / GENERATION_PLAN_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != GENERATION_PLAN_SCHEMA_VERSION:
        raise ValueError("GENERATION_PLAN.json has invalid schema")
    content = {key: value for key, value in data.items() if key != "sha256"}
    if set(data) != set(content) | {"sha256"} or data["sha256"] != sha256_json(content):
        raise ValueError("GENERATION_PLAN.json SHA-256 mismatch")
    entries = data.get("entries")
    if not isinstance(entries, list) or len(entries) != data.get("case_count"):
        raise ValueError("GENERATION_PLAN.json entries do not match case_count")
    return data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze exactly the cases declared before V2 generation."
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--required-artifact",
        action="append",
        default=[],
        help=(
            "Additional artifact name required for every case, e.g. projection_a00. "
            "phantom_npz and metadata_json are always required."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    generation_plan = load_generation_plan(args.output_root)
    records = []
    for entry in generation_plan["entries"]:
        case_id = str(entry["case_id"])
        record_path = args.output_root / "cases" / case_id / "case_record.json"
        records.append(
            load_case_record_v2(
                record_path,
                dataset_root=args.output_root,
                verify_hashes=True,
            )
        )
    required = tuple(
        sorted(
            {
                "phantom_npz",
                "metadata_json",
                "projection_a00",
                "projection_mhd",
                "projection_res",
                "projection_spe",
                "simind_run_provenance",
                *args.required_artifact,
            }
        )
    )
    contract = DatasetContractV2(
        output_root=args.output_root,
        dataset_id=str(generation_plan["dataset_id"]),
        dataset_version=str(generation_plan["dataset_version"]),
        dataset_role=str(generation_plan["dataset_role"]),
        expected_case_ids=tuple(str(entry["case_id"]) for entry in generation_plan["entries"]),
        allowed_profile_ids=(str(generation_plan["profile_id"]),),
        split_plan_sha256=str(generation_plan["split_plan_sha256"]),
        required_artifact_names=required,
    )
    frozen = freeze_dataset(records, contract)
    print(json.dumps(frozen.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
