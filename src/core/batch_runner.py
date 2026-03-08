"""
Batch Runner
============
Background worker for generating multiple phantom cases.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.batch_stats import BatchStats
from core.phantom_generator import PhantomConfig, PhantomGenerator
from ui.i18n import tr


class BatchWorker(QThread):
    """Background thread for batch phantom generation."""

    case_done = pyqtSignal(int, int, object)
    case_failed = pyqtSignal(int, str)
    stats_updated = pyqtSignal(object)
    all_done = pyqtSignal(object)
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
        stats = BatchStats(total=cfg.n_cases)
        output_dir = Path(cfg.output_dir)

        try:
            gen = PhantomGenerator(cfg)
            output_dir.mkdir(parents=True, exist_ok=True)

            self.log.emit(
                tr("[INFO] Starting batch: {count} cases -> {output_dir}").format(
                    count=cfg.n_cases,
                    output_dir=output_dir,
                )
            )

            for i in range(cfg.n_cases):
                if self._stop_flag:
                    self.log.emit(tr("[WARN] Batch stopped by user."))
                    break

                case_id = self.start_id + i
                try:
                    result = gen.generate_one(case_id)
                    result.save(output_dir)
                    stats.update(result)
                    self.case_done.emit(i, cfg.n_cases, result)
                    self.stats_updated.emit(stats.copy(include_case_summaries=False))
                    self.log.emit(
                        tr("[PROGRESS] [{current}/{total}] case_{case_id:04d}: {tumors} tumors, {volume:.0f} mL, {seconds:.2f}s").format(
                            current=i + 1,
                            total=cfg.n_cases,
                            case_id=case_id,
                            tumors=result.n_tumors,
                            volume=result.liver_volume_ml,
                            seconds=result.generation_time_s,
                        )
                    )
                except Exception as exc:
                    stats.failed += 1
                    self.case_failed.emit(case_id, str(exc))
                    self.stats_updated.emit(stats.copy(include_case_summaries=False))
                    self.log.emit(tr("[ERROR] case_{case_id:04d}: {msg}").format(case_id=case_id, msg=exc))

            summary_path = output_dir / "batch_summary.json"
            try:
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(stats.to_dict(), f, indent=2)
                self.log.emit(tr("[OK] Batch complete. Summary saved: {path}").format(path=summary_path))
            except Exception as exc:
                self.log.emit(tr("[ERROR] Failed to write summary: {msg}").format(msg=exc))

        except Exception as exc:
            self.log.emit(tr("[ERROR] Batch initialization failed: {msg}").format(msg=exc))
            if stats.total > 0 and stats.completed == 0 and stats.failed == 0:
                stats.failed = stats.total

        finally:
            self.all_done.emit(stats.copy())
