"""Thin Qt-free batch orchestration around :mod:`core.simind_exec`."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

from .simind_exec import SimindRunResult, SimindRunSpec, run_simind_case


@dataclass(frozen=True)
class SimindBatchResult:
    results: tuple[SimindRunResult, ...]

    @property
    def success(self) -> bool:
        return bool(self.results) and all(item.success for item in self.results)

    @property
    def failed_case_ids(self) -> tuple[str, ...]:
        return tuple(item.case_id for item in self.results if not item.success)


def run_simind_batch(
    specs: Iterable[SimindRunSpec],
    *,
    max_parallel: int = 1,
) -> SimindBatchResult:
    frozen = tuple(specs)
    if not frozen:
        raise ValueError("specs must not be empty")
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel <= 0:
        raise ValueError("max_parallel must be a positive integer")
    rr_seeds = [item.rr_seed for item in frozen]
    if len(set(rr_seeds)) != len(rr_seeds):
        raise ValueError("parallel SIMIND specs must use collision-free /RR seeds")
    case_ids = [item.case_id for item in frozen]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("SIMIND batch contains duplicate case_id values")
    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        results = tuple(executor.map(run_simind_case, frozen))
    return SimindBatchResult(results=results)
