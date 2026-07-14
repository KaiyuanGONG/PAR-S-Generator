from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


SEED_NAMESPACES = ("patient", "liver", "tumor", "activity", "mu", "simind")
_MAX_INT63 = 2**63 - 1
_SIMIND_RR_PERMUTATION_PRIME = 10_007
_FORMAL_MAIN_RR_CAPACITY = 5_000
_NUMERIC_CASE_ID = re.compile(r"^(?P<prefix>.*?)(?P<index>[0-9]+)$")


def _derive_seed(global_seed: int, case_id: str, namespace: str, maximum: int) -> int:
    payload = f"pars-syn-v2|{global_seed}|{case_id}|{namespace}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value % maximum + 1


def _derive_simind_rr(global_seed: int, case_id: str) -> int:
    """Allocate a deterministic, practical SIMIND random-sequence number.

    SIMIND ``/RR`` is a random-number *sequence selector*, not an arbitrary
    31-bit RNG seed.  With Flag 8 enabled, very large selectors make SIMIND
    spend minutes advancing the sequence before tracing a photon.  Formal V2
    case IDs end in a numeric index.  Main (``case_``/legacy ``main_``) and
    negative-control IDs share one affine permutation but occupy disjoint
    slots, so the formal 500+50 corpus cannot collide.  Keeping ``case_`` as
    the canonical permutation namespace also preserves already frozen pilot
    /RR values.  Other numeric prefixes retain their independent engineering
    namespace; they must not be mixed into one formal SIMIND run.
    """

    match = _NUMERIC_CASE_ID.fullmatch(case_id)
    if match is None:
        return _derive_seed(
            global_seed,
            case_id,
            "simind_rr_non_numeric",
            _SIMIND_RR_PERMUTATION_PRIME,
        )
    index = int(match.group("index"))
    prefix = match.group("prefix")
    if prefix in {"case_", "main_"}:
        if index >= _FORMAL_MAIN_RR_CAPACITY:
            raise ValueError(
                "main numeric case index must be below 5000 for collision-free "
                "SIMIND /RR allocation"
            )
        permutation_prefix = "case_"
        slot = index
    elif prefix in {"negative_", "negative_case_"}:
        negative_capacity = (
            _SIMIND_RR_PERMUTATION_PRIME - _FORMAL_MAIN_RR_CAPACITY
        )
        if index >= negative_capacity:
            raise ValueError(
                "negative numeric case index exceeds the reserved SIMIND /RR range"
            )
        permutation_prefix = "case_"
        slot = _FORMAL_MAIN_RR_CAPACITY + index
    else:
        permutation_prefix = prefix
        slot = index
    if slot >= _SIMIND_RR_PERMUTATION_PRIME:
        raise ValueError(
            "numeric case index must be below 10007 for collision-free "
            "SIMIND /RR allocation"
        )
    payload = (
        f"pars-syn-v2|{global_seed}|{permutation_prefix}|simind_rr_permutation"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    multiplier = int.from_bytes(digest[:4], "big") % (
        _SIMIND_RR_PERMUTATION_PRIME - 1
    ) + 1
    offset = int.from_bytes(digest[4:8], "big") % _SIMIND_RR_PERMUTATION_PRIME
    return (
        multiplier * slot + offset
    ) % _SIMIND_RR_PERMUTATION_PRIME + 1


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
            namespace: (
                _derive_simind_rr(global_seed, case_id)
                if namespace == "simind"
                else _derive_seed(global_seed, case_id, namespace, _MAX_INT63)
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
