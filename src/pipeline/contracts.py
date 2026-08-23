"""Minimal, filesystem-backed run and case contracts."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np


CONTRACT_VERSION = 1
CANONICAL_PROJECTION_TRANSFORM = "raw[:,::-1,:]"
LEGACY_PAR_S2_PROJECTION_TRANSFORM = "raw[::-1,::-1,:]"
CURRENT_TYPE7_ATTENUATION_CONTRACT_STATUS = (
    "verified_type7_mu_times_voxel_v10_current_h2o_protocol"
)
CURRENT_TYPE7_DENSITY_THRESHOLD_TIMES_1000 = 100
CURRENT_DETECTOR_MATRIX_I = 160
CURRENT_DETECTOR_MATRIX_J = 208
EMPIRICAL_OBSERVATION_PROTOCOL_STATUS = "empirical_protocol_matching"
EMPIRICAL_CLINICAL_TOTAL_COUNTS = (
    2_042_094,
    2_285_728,
    2_376_211,
    2_557_727,
    2_748_718,
    3_218_979,
    3_359_875,
    4_112_706,
)
EMPIRICAL_CLINICAL_ANGULAR_CV_RANGE = (0.33360226698745143, 0.6201742992846553)
DEFAULT_SOURCE_ACTIVITY_MBQ = 60.0
DEFAULT_EXPOSURE_S_PER_PROJECTION = 28.4
DEFAULT_SIMIND_ACTIVITY_TIME = 1704.0
ACTIVITY_TIME_CONTRACT_STATUS = (
    "resolved_nominal_60mbq_x_28p4s_index25_1704_local_dicom_supported"
)
RUN_SUBDIRS = (
    "phantom",
    "simind_input",
    "expectation",
    "observation",
    "qc",
    "logs",
    "figures",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding=encoding)
    temp.replace(path)


def atomic_write_json(path: Path, payload: dict | list) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def write_jsonl(path: Path, records: Iterable[dict]) -> None:
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


def assign_fixed_splits(
    case_ids: Iterable[str],
    seed: int = 42,
    fractions: tuple[float, float, float] = (0.8, 0.1, 0.1),
) -> dict[str, str]:
    """Reproduce the existing sorted-list + default_rng split exactly.

    Splits are assigned at phantom-id level.  Observation realizations must
    reference the parent phantom and inherit this mapping.
    """
    ids = sorted(dict.fromkeys(str(case_id) for case_id in case_ids))
    if not ids:
        return {}
    if len(fractions) != 3 or any(value < 0 for value in fractions):
        raise ValueError("fractions must contain three non-negative values")
    total_fraction = float(sum(fractions))
    if not np.isclose(total_fraction, 1.0):
        raise ValueError("split fractions must sum to 1")

    permutation = np.random.default_rng(seed).permutation(len(ids)).tolist()
    n_train = int(fractions[0] * len(ids))
    n_val = int(fractions[1] * len(ids))
    boundaries = (n_train, n_train + n_val)
    mapping: dict[str, str] = {}
    for position, index in enumerate(permutation):
        split = "train" if position < boundaries[0] else "val" if position < boundaries[1] else "test"
        mapping[ids[index]] = split
    return mapping


@dataclass(frozen=True)
class RunLayout:
    root: Path

    @classmethod
    def create(cls, runs_root: Path, run_id: str, allow_existing: bool = False) -> "RunLayout":
        if not run_id or any(char in run_id for char in "\\/:*?\"<>|"):
            raise ValueError("run_id must be a non-empty filesystem-safe name")
        root = Path(runs_root).resolve() / run_id
        if root.exists() and not allow_existing:
            raise FileExistsError(f"Run already exists: {root}")
        root.mkdir(parents=True, exist_ok=True)
        for name in RUN_SUBDIRS:
            (root / name).mkdir(exist_ok=True)
        return cls(root=root)

    @classmethod
    def open(cls, root: Path) -> "RunLayout":
        root = Path(root).resolve()
        if not (root / "run.json").exists():
            raise FileNotFoundError(f"Not a pipeline run: {root}")
        return cls(root=root)

    def subdir(self, name: str) -> Path:
        if name not in RUN_SUBDIRS:
            raise KeyError(name)
        return self.root / name

    @property
    def run_json(self) -> Path:
        return self.root / "run.json"

    @property
    def cases_jsonl(self) -> Path:
        return self.root / "cases.jsonl"


class RunLedger:
    """Atomic state manager for the intentionally small run contract."""

    def __init__(self, layout: RunLayout):
        self.layout = layout

    def initialize(self, *, run_id: str, effective_config: dict, provenance: dict | None = None) -> dict:
        if self.layout.run_json.exists():
            raise FileExistsError(self.layout.run_json)
        payload = {
            "contract_version": CONTRACT_VERSION,
            "run_id": run_id,
            "created_utc": utc_now(),
            "updated_utc": utc_now(),
            "scope": "synthetic_liver_spect_data_preparation_only",
            "effective_config": effective_config,
            "provenance": provenance or {},
            "runtime": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
            "stages": {},
            "finalized": False,
        }
        atomic_write_json(self.layout.run_json, payload)
        return payload

    def load(self) -> dict:
        return json.loads(self.layout.run_json.read_text(encoding="utf-8"))

    def update_stage(self, stage: str, status: str, **evidence) -> dict:
        if status not in {"pending", "running", "passed", "failed", "skipped", "prepared", "paused"}:
            raise ValueError(f"Unsupported stage status: {status}")
        payload = self.load()
        payload["stages"][stage] = {
            "status": status,
            "updated_utc": utc_now(),
            **evidence,
        }
        payload["updated_utc"] = utc_now()
        atomic_write_json(self.layout.run_json, payload)
        return payload

    def write_cases(self, cases: list[dict]) -> None:
        ids = [record.get("case_id") for record in cases]
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Each case must have one unique non-empty case_id")
        write_jsonl(self.layout.cases_jsonl, sorted(cases, key=lambda item: item["case_id"]))

    def read_cases(self) -> list[dict]:
        return read_jsonl(self.layout.cases_jsonl) if self.layout.cases_jsonl.exists() else []

    def finalize(self, *, package_sha256: str | None = None) -> dict:
        payload = self.load()
        execution_scope = (
            payload.get("effective_config", {}).get("execution_scope", "full")
        )
        if execution_scope == "anatomy_only_gate_a":
            required = {"generate", "phantom_qc", "package"}
            prohibited = {
                "export",
                "simind_plan",
                "simulation",
                "observation",
                "projection_qc",
                "figures",
            }
            executed = [
                stage
                for stage in sorted(prohibited)
                if payload["stages"].get(stage, {}).get("status")
                not in {None, "skipped"}
            ]
            if executed:
                raise RuntimeError(
                    "Cannot finalize anatomy-only scope; prohibited stages were entered: "
                    + ", ".join(executed)
                )
        else:
            required = {"generate", "phantom_qc", "export", "package"}
        missing = [stage for stage in sorted(required) if payload["stages"].get(stage, {}).get("status") != "passed"]
        if missing:
            raise RuntimeError(f"Cannot finalize; required stages not passed: {', '.join(missing)}")
        payload["finalized"] = True
        payload["finalized_utc"] = utc_now()
        if package_sha256:
            payload["package_sha256"] = package_sha256
        payload["updated_utc"] = utc_now()
        atomic_write_json(self.layout.run_json, payload)
        return payload
