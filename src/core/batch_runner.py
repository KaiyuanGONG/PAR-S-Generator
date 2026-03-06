"""
Batch Runner
============
Background worker for generating multiple phantom cases.
"""

from __future__ import annotations
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from core.phantom_generator import PhantomGenerator, PhantomConfig, PhantomResult


@dataclass
class BatchStats:
    total: int = 0
    completed: int = 0
    failed: int = 0
    start_time: float = field(default_factory=time.time)
    liver_volumes: list = field(default_factory=list)
    left_ratios: list = field(default_factory=list)
    n_tumors_list: list = field(default_factory=list)
    tumor_diameters: list = field(default_factory=list)
    perfusion_modes: dict = field(default_factory=dict)
    gen_times: list = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def eta(self) -> float:
        if self.completed == 0:
            return 0.0
        rate = self.completed / self.elapsed
        remaining = self.total - self.completed
        return remaining / rate if rate > 0 else 0.0

    def update(self, result: PhantomResult):
        self.completed += 1
        self.liver_volumes.append(result.liver_volume_ml)
        self.left_ratios.append(result.left_ratio)
        self.n_tumors_list.append(result.n_tumors)
        self.tumor_diameters.extend(result.tumor_radii_mm)
        mode = result.perfusion_mode
        self.perfusion_modes[mode] = self.perfusion_modes.get(mode, 0) + 1
        self.gen_times.append(result.generation_time_s)

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
            "perfusion_modes": self.perfusion_modes,
        }


class BatchWorker(QThread):
    """Background thread for batch phantom generation."""

    case_done = pyqtSignal(int, int, object)   # (case_idx, total, PhantomResult)
    case_failed = pyqtSignal(int, str)          # (case_idx, error_msg)
    all_done = pyqtSignal(object)               # BatchStats
    log = pyqtSignal(str)

    def __init__(self, config: PhantomConfig, start_id: int = 1):
        super().__init__()
        self.config = config
        self.start_id = start_id
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        cfg = self.config
        gen = PhantomGenerator(cfg)
        output_dir = Path(cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        stats = BatchStats(total=cfg.n_cases)
        self._stats_ref = stats
        self.log.emit(f"[INFO] Starting batch: {cfg.n_cases} cases → {output_dir}")

        for i in range(cfg.n_cases):
            if self._stop_flag:
                self.log.emit("[WARN] Batch stopped by user.")
                break

            case_id = self.start_id + i
            try:
                result = gen.generate_one(case_id)
                result.save(output_dir)
                stats.update(result)
                self.case_done.emit(i, cfg.n_cases, result)
                self.log.emit(
                    f"  [{i + 1}/{cfg.n_cases}] case_{case_id:04d}: "
                    f"{result.n_tumors} tumors, {result.liver_volume_ml:.0f} mL, "
                    f"{result.generation_time_s:.2f}s"
                )
            except Exception as e:
                stats.failed += 1
                self.case_failed.emit(i, str(e))
                self.log.emit(f"  [ERROR] case_{case_id:04d}: {e}")

        # Save summary JSON
        summary = stats.summary()
        summary_path = output_dir / "batch_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        self.log.emit(f"[OK] Batch complete. Summary saved: {summary_path}")
        self.all_done.emit(stats)
