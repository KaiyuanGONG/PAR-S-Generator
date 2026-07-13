from __future__ import annotations

import hashlib
from dataclasses import dataclass


SEED_NAMESPACES = ("patient", "liver", "tumor", "activity", "mu", "simind")
_MAX_INT63 = 2**63 - 1
_MAX_SIMIND_RR = 2_147_483_646


def _derive_seed(global_seed: int, case_id: str, namespace: str, maximum: int) -> int:
    payload = f"pars-syn-v2|{global_seed}|{case_id}|{namespace}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value % maximum + 1


@dataclass(frozen=True)
class SeedBundle:
    global_seed: int
    case_id: str
    case_seed: int
    patient: int
    liver: int
    tumor: int
    activity: int
    mu: int
    simind: int

    @classmethod
    def from_case(cls, global_seed: int, case_id: str) -> "SeedBundle":
        if not isinstance(global_seed, int) or isinstance(global_seed, bool) or global_seed < 0:
            raise ValueError("global_seed must be a non-negative integer")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id must be a non-empty string")
        case_id = case_id.strip()
        values = {
            namespace: _derive_seed(
                global_seed,
                case_id,
                namespace,
                _MAX_SIMIND_RR if namespace == "simind" else _MAX_INT63,
            )
            for namespace in SEED_NAMESPACES
        }
        return cls(
            global_seed=global_seed,
            case_id=case_id,
            case_seed=_derive_seed(global_seed, case_id, "case", _MAX_INT63),
            **values,
        )

    @property
    def child_seeds(self) -> dict[str, int]:
        return {namespace: getattr(self, namespace) for namespace in SEED_NAMESPACES}

    def to_dict(self) -> dict:
        return {
            "global_seed": self.global_seed,
            "case_id": self.case_id,
            "case_seed": self.case_seed,
            "child_seeds": self.child_seeds,
        }

