"""
Pure batch statistics container used by the UI and workers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from core.phantom_generator import PhantomResult


@dataclass
class BatchStats:
    total: int = 0
    completed: int = 0
    failed: int = 0
    start_time: float = field(default_factory=time.time)
    liver_volumes: list[float] = field(default_factory=list)
    left_ratios: list[float] = field(default_factory=list)
    n_tumors_list: list[int] = field(default_factory=list)
    tumor_diameters: list[float] = field(default_factory=list)
    perfusion_modes: dict[str, int] = field(default_factory=dict)
    gen_times: list[float] = field(default_factory=list)
    case_summaries: list[dict] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def eta(self) -> float:
        if self.completed == 0:
            return 0.0
        rate = self.completed / max(self.elapsed, 1e-9)
        remaining = self.total - self.completed
        return remaining / rate if rate > 0 else 0.0

    def update(self, result: PhantomResult):
        self.completed += 1
        self.liver_volumes.append(result.liver_volume_ml)
        self.left_ratios.append(result.left_ratio)
        self.n_tumors_list.append(result.n_tumors)
        self.tumor_diameters.extend(result.tumor_diameters_mm)
        mode = result.perfusion_mode
        self.perfusion_modes[mode] = self.perfusion_modes.get(mode, 0) + 1
        self.gen_times.append(result.generation_time_s)
        self.case_summaries.append(
            {
                "case_id": result.case_id,
                "seed": result.seed,
                "liver_volume_ml": result.liver_volume_ml,
                "left_ratio": result.left_ratio,
                "n_tumors": result.n_tumors,
                "tumor_diameters_mm": list(result.tumor_diameters_mm),
                "perfusion_mode": result.perfusion_mode,
                "total_counts_actual": result.total_counts_actual,
                "generation_time_s": result.generation_time_s,
            }
        )

    def summary(self) -> dict:
        vols = self.liver_volumes
        ratios = self.left_ratios
        tumors = self.n_tumors_list
        diams = self.tumor_diameters
        return {
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "elapsed_s": round(self.elapsed, 1),
            "eta_s": round(self.eta, 1),
            "avg_gen_time_s": round(float(np.mean(self.gen_times)), 3) if self.gen_times else 0,
            "liver_vol_mean_ml": round(float(np.mean(vols)), 1) if vols else 0,
            "liver_vol_std_ml": round(float(np.std(vols)), 1) if vols else 0,
            "liver_vol_min_ml": round(float(np.min(vols)), 1) if vols else 0,
            "liver_vol_max_ml": round(float(np.max(vols)), 1) if vols else 0,
            "left_ratio_mean": round(float(np.mean(ratios)), 3) if ratios else 0,
            "left_ratio_std": round(float(np.std(ratios)), 3) if ratios else 0,
            "avg_tumors": round(float(np.mean(tumors)), 2) if tumors else 0,
            "total_tumors": int(sum(tumors)),
            "tumor_diam_mean_mm": round(float(np.mean(diams)), 1) if diams else 0,
            "tumor_diam_std_mm": round(float(np.std(diams)), 1) if diams else 0,
            "perfusion_modes": dict(self.perfusion_modes),
        }

    def to_dict(self) -> dict:
        payload = self.summary()
        payload.update(
            {
                "liver_volumes": list(self.liver_volumes),
                "left_ratios": list(self.left_ratios),
                "n_tumors_list": list(self.n_tumors_list),
                "tumor_diameters": list(self.tumor_diameters),
                "gen_times": list(self.gen_times),
                "case_summaries": list(self.case_summaries),
            }
        )
        return payload

    def copy(self, include_case_summaries: bool = True) -> "BatchStats":
        clone = BatchStats(total=self.total, completed=self.completed, failed=self.failed, start_time=self.start_time)
        clone.liver_volumes = list(self.liver_volumes)
        clone.left_ratios = list(self.left_ratios)
        clone.n_tumors_list = list(self.n_tumors_list)
        clone.tumor_diameters = list(self.tumor_diameters)
        clone.perfusion_modes = dict(self.perfusion_modes)
        clone.gen_times = list(self.gen_times)
        if include_case_summaries:
            clone.case_summaries = [dict(item) for item in self.case_summaries]
        return clone

    @classmethod
    def from_dict(cls, data: dict) -> "BatchStats":
        stats = cls(
            total=int(data.get("total", 0)),
            completed=int(data.get("completed", 0)),
            failed=int(data.get("failed", 0)),
            start_time=time.time() - float(data.get("elapsed_s", 0)),
        )
        stats.liver_volumes = list(data.get("liver_volumes", []))
        stats.left_ratios = list(data.get("left_ratios", []))
        stats.n_tumors_list = list(data.get("n_tumors_list", []))
        stats.tumor_diameters = list(data.get("tumor_diameters", []))
        stats.perfusion_modes = dict(data.get("perfusion_modes", {}))
        stats.gen_times = list(data.get("gen_times", []))
        stats.case_summaries = list(data.get("case_summaries", []))
        return stats
