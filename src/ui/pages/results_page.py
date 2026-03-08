"""
Batch monitor page used inside the Generate workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from core.batch_runner import BatchWorker
from core.batch_stats import BatchStats
from core.validation import validate_phantom_config
from ui.app_state import AppState
from ui.i18n import language_manager, tr
from ui.widgets.simind_viewer import SimindOutputViewer


class StatCard(QFrame):
    def __init__(self, title: str, key: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self._title = title
        self._unit = unit
        self.setStyleSheet(
            "QFrame { background-color: #252a33; border-radius: 8px; border: 1px solid #2d3139; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self.val_lbl = QLabel("—")
        self.val_lbl.setStyleSheet("color: #4fc3f7; font-size: 22px; font-weight: bold;")
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.val_lbl)
        layout.addWidget(self.title_lbl)
        self.retranslate_ui()

    def set_value(self, val):
        self.val_lbl.setText(str(val))

    def retranslate_ui(self):
        title = tr(self._title)
        self.title_lbl.setText(title + (f"\n({self._unit})" if self._unit else ""))


class StatsCharts(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fig = Figure(figsize=(10, 6), facecolor="#1e2128")
        self.canvas = FigureCanvas(self.fig)
        layout.addWidget(self.canvas)

    def update_charts(self, stats: BatchStats):
        self.fig.clear()
        self.fig.patch.set_facecolor("#1e2128")
        axes = self.fig.subplots(2, 3)
        self.fig.subplots_adjust(hspace=0.45, wspace=0.35, left=0.08, right=0.97, top=0.92, bottom=0.1)

        style = {"color": "#c8ccd4", "edgecolor": "#4fc3f7", "facecolor": "#1a3a5c", "linewidth": 0.8}

        def style_ax(ax, title, xlabel, ylabel="Count"):
            ax.set_facecolor("#161a1f")
            ax.set_title(title, color="#8b949e", fontsize=9, pad=6)
            ax.set_xlabel(xlabel, color="#6b7280", fontsize=8)
            ax.set_ylabel(ylabel, color="#6b7280", fontsize=8)
            ax.tick_params(colors="#6b7280", labelsize=7)
            for spine in ax.spines.values():
                spine.set_color("#2d3139")
            ax.grid(True, color="#2d3139", linewidth=0.4, alpha=0.7)

        ax = axes[0, 0]
        if stats.liver_volumes:
            ax.hist(stats.liver_volumes, bins=20, **style)
        style_ax(ax, tr("Liver Volume"), tr("Volume (mL)"))

        ax = axes[0, 1]
        if stats.left_ratios:
            ax.hist([r * 100 for r in stats.left_ratios], bins=20, **style)
        style_ax(ax, tr("Left Lobe Ratio"), tr("Ratio (%)"))

        ax = axes[0, 2]
        if stats.n_tumors_list:
            counts = np.bincount(stats.n_tumors_list)
            ax.bar(range(len(counts)), counts, color="#1a3a5c", edgecolor="#4fc3f7", linewidth=0.8)
        style_ax(ax, tr("Tumor Count per Case"), tr("Number of Tumors"))

        ax = axes[1, 0]
        if stats.tumor_diameters:
            ax.hist(stats.tumor_diameters, bins=20, **style)
        style_ax(ax, tr("Tumor Diameter"), tr("Diameter (mm)"))

        ax = axes[1, 1]
        if stats.perfusion_modes:
            labels = list(stats.perfusion_modes.keys())
            values = list(stats.perfusion_modes.values())
            short_labels = [tr(label).replace(" Only", "\nOnly").replace(" Only", "\nOnly") for label in labels]
            ax.bar(range(len(values)), values, color="#1a3a5c", edgecolor="#4fc3f7", linewidth=0.8)
            ax.set_xticks(range(len(values)))
            ax.set_xticklabels(short_labels, fontsize=7, color="#6b7280")
        style_ax(ax, tr("Perfusion Mode"), tr("Mode"))

        ax = axes[1, 2]
        if stats.gen_times:
            ax.plot(stats.gen_times, color="#4fc3f7", linewidth=1.0, alpha=0.8)
            ax.axhline(np.mean(stats.gen_times), color="#ff6b6b", linewidth=1.0, linestyle="--", alpha=0.7)
        style_ax(ax, tr("Generation Time"), tr("Case Index"), tr("Time (s)"))
        self.canvas.draw()


class ResultsPage(QWidget):
    def __init__(self, app_state: AppState, include_output_viewer: bool = False, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._include_output_viewer = include_output_viewer
        self._worker: BatchWorker | None = None
        self._stats: BatchStats | None = None
        self._build_ui()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(16)

        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet(
            "QFrame { background-color: #252a33; border-radius: 8px; border: 1px solid #2d3139; padding: 4px; }"
        )
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(16, 12, 16, 12)
        ctrl_layout.setSpacing(16)

        self.btn_run = QPushButton()
        self.btn_run.setObjectName("success_btn")
        self.btn_run.setMinimumHeight(40)
        self.btn_run.setMinimumWidth(220)
        self.btn_run.clicked.connect(self._on_run)

        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName("danger_btn")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)

        self.btn_load_summary = QPushButton()
        self.btn_load_summary.clicked.connect(self._load_summary)

        prog_col = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumWidth(300)
        self.lbl_progress = QLabel("")
        self.lbl_progress.setStyleSheet("color: #6b7280; font-size: 12px;")
        prog_col.addWidget(self.progress_bar)
        prog_col.addWidget(self.lbl_progress)

        eta_col = QVBoxLayout()
        self.lbl_eta = QLabel("")
        self.lbl_eta.setStyleSheet("color: #6b7280; font-size: 12px;")
        self.lbl_elapsed = QLabel("")
        self.lbl_elapsed.setStyleSheet("color: #6b7280; font-size: 12px;")
        eta_col.addWidget(self.lbl_eta)
        eta_col.addWidget(self.lbl_elapsed)

        ctrl_layout.addWidget(self.btn_run)
        ctrl_layout.addWidget(self.btn_stop)
        ctrl_layout.addWidget(self.btn_load_summary)
        ctrl_layout.addLayout(prog_col, stretch=1)
        ctrl_layout.addLayout(eta_col)
        root.addWidget(ctrl_frame)

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(10)
        self._cards = {}
        for title_c, key, unit in [
            ("Completed", "completed", ""),
            ("Liver Vol. Mean", "liver_vol_mean", "mL"),
            ("Left Ratio Mean", "left_ratio_mean", "%"),
            ("Avg Tumors/Case", "avg_tumors", ""),
            ("Total Tumors", "total_tumors", ""),
            ("Avg Gen. Time", "avg_gen_time", "s"),
        ]:
            card = StatCard(title_c, key, unit)
            self._cards[key] = card
            cards_layout.addWidget(card)
        root.addLayout(cards_layout)

        self.tabs = QTabWidget()
        self.charts = StatsCharts()
        self.tabs.addTab(self.charts, "")
        self.table = self._build_table()
        self.tabs.addTab(self.table, "")
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.tabs.addTab(self.log_view, "")
        self.simind_viewer = None
        if self._include_output_viewer:
            self.simind_viewer = SimindOutputViewer(view_title="SPECT Preview")
            self.tabs.addTab(self.simind_viewer, "")
        root.addWidget(self.tabs, stretch=1)
        self.retranslate_ui()

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, 9)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setStyleSheet("QTableWidget { alternate-background-color: #1a1e26; }")
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    def retranslate_ui(self):
        self.btn_run.setText(tr("▶  Start Batch Generation"))
        self.btn_stop.setText(tr("■  Stop"))
        self.btn_load_summary.setText(tr("Load Existing Summary"))
        self.tabs.setTabText(0, tr("Statistics Charts"))
        self.tabs.setTabText(1, tr("Case Table"))
        self.tabs.setTabText(2, tr("Log"))
        if self._include_output_viewer and self.simind_viewer is not None:
            self.tabs.setTabText(3, tr("SPECT Preview"))
            self.simind_viewer.retranslate_ui()
        headers = [
            tr("Case ID"),
            tr("Seed"),
            tr("Liver Vol (mL)"),
            tr("Left Ratio"),
            tr("N Tumors"),
            tr("Tumor Diameters (mm)"),
            tr("Perfusion"),
            tr("Counts"),
            tr("Time (s)"),
        ]
        self.table.setHorizontalHeaderLabels(headers)
        for card in self._cards.values():
            card.retranslate_ui()
        if self._stats is not None:
            self._populate_from_stats(self._stats, refresh_charts=False)
        elif not self.lbl_progress.text():
            self.lbl_progress.setText(tr("Ready"))
            self.lbl_eta.setText(tr("ETA: —"))
            self.lbl_elapsed.setText(tr("Elapsed: —"))

    def _case_values(self, source) -> list[str]:
        if isinstance(source, dict):
            diams = ", ".join(f"{d:.1f}" for d in source.get("tumor_diameters_mm", [])) or "—"
            return [
                f"{int(source.get('case_id', 0)):04d}",
                str(source.get("seed", "")),
                f"{float(source.get('liver_volume_ml', 0.0)):.1f}",
                f"{float(source.get('left_ratio', 0.0)) * 100:.1f}%",
                str(int(source.get("n_tumors", 0))),
                diams,
                tr(str(source.get("perfusion_mode", ""))),
                f"{float(source.get('total_counts_actual', 0.0)):.2e}",
                f"{float(source.get('generation_time_s', 0.0)):.3f}",
            ]
        diams = ", ".join(f"{d:.1f}" for d in source.tumor_diameters_mm) or "—"
        return [
            f"{source.case_id:04d}",
            str(source.seed),
            f"{source.liver_volume_ml:.1f}",
            f"{source.left_ratio * 100:.1f}%",
            str(source.n_tumors),
            diams,
            tr(source.perfusion_mode),
            f"{source.total_counts_actual:.2e}",
            f"{source.generation_time_s:.3f}",
        ]

    def _add_table_row(self, source):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, val in enumerate(self._case_values(source)):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)

    def _populate_from_stats(self, stats: BatchStats, rebuild_table: bool = False, refresh_charts: bool = True):
        self._stats = stats
        self._app_state.set_batch_stats(stats)
        summary = stats.summary()
        self._cards["completed"].set_value(f"{summary['completed']}/{summary['total']}")
        self._cards["liver_vol_mean"].set_value(f"{summary['liver_vol_mean_ml']:.0f}")
        self._cards["left_ratio_mean"].set_value(f"{summary['left_ratio_mean'] * 100:.1f}%")
        self._cards["avg_tumors"].set_value(f"{summary['avg_tumors']:.2f}")
        self._cards["total_tumors"].set_value(str(summary["total_tumors"]))
        self._cards["avg_gen_time"].set_value(f"{summary['avg_gen_time_s']:.3f}")
        self.lbl_eta.setText(tr("ETA: {seconds:.0f}s").format(seconds=stats.eta))
        self.lbl_elapsed.setText(tr("Elapsed: {seconds:.0f}s").format(seconds=stats.elapsed))
        if refresh_charts:
            self.charts.update_charts(stats)
        if rebuild_table:
            self.table.setRowCount(0)
            for item in stats.case_summaries:
                self._add_table_row(item)

    def _log(self, msg: str, color: str = "#8b949e"):
        self.log_view.setTextColor(QColor(color))
        self.log_view.append(msg)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def on_results_ready(self, output_dir: str):
        if not self._include_output_viewer or self.simind_viewer is None:
            return
        self._log(tr("[INFO] SIMIND output ready: {output_dir}").format(output_dir=output_dir), color="#4fc3f7")
        self.tabs.setCurrentIndex(3)
        a00_files = sorted(Path(output_dir).glob("*.a00"))
        if a00_files:
            self.simind_viewer.load_file(str(a00_files[0]))
            self._log(tr("[INFO] Loaded {name} into SPECT Preview.").format(name=a00_files[0].name), color="#4fc3f7")

    def _on_run(self):
        if self._worker and self._worker.isRunning():
            self._log(tr("[WARN] Batch is already running."), color="#ffa726")
            QMessageBox.information(
                self,
                tr("Batch already running"),
                tr("Batch already running. Stop current batch before starting a new one."),
            )
            return

        config = self._app_state.phantom_config
        report = validate_phantom_config(config)
        if report.errors:
            QMessageBox.warning(self, tr("Batch blocked"), report.to_message())
            return

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(config.n_cases)
        self.lbl_progress.setText("0 / {0} (0.0%)".format(config.n_cases))
        self._log(tr("[INFO] Starting batch: {count} cases").format(count=config.n_cases), color="#4fc3f7")

        self._worker = BatchWorker(config)
        self._worker.case_done.connect(self._on_case_done)
        self._worker.case_failed.connect(self._on_case_failed)
        self._worker.stats_updated.connect(self._on_stats_updated)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.log.connect(lambda msg: self._log(msg))
        self._worker.start()

    @pyqtSlot(int, int, object)
    def _on_case_done(self, idx: int, total: int, result):
        self._add_table_row(result)

    @pyqtSlot(int, str)
    def _on_case_failed(self, case_id: int, msg: str):
        self._log(tr("[ERROR] Case {case_id:04d}: {msg}").format(case_id=case_id, msg=msg), color="#ff6b6b")

    @pyqtSlot(object)
    def _on_stats_updated(self, stats: BatchStats):
        refresh_charts = stats.completed % 5 == 0 or stats.completed == stats.total
        self._populate_from_stats(stats, refresh_charts=refresh_charts)

        total = max(stats.total, 1)
        processed = min(stats.completed + stats.failed, total)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(processed)
        pct = processed / total * 100
        self.lbl_progress.setText(f"{processed} / {total}  ({pct:.1f}%)")

    @pyqtSlot(object)
    def _on_all_done(self, stats: BatchStats):
        self._populate_from_stats(stats, refresh_charts=True)
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

        summary = stats.summary()
        processed = summary["completed"] + summary["failed"]
        self.progress_bar.setMaximum(max(summary["total"], 1))
        self.progress_bar.setValue(processed)
        self.lbl_progress.setText(
            tr("Done: {completed}/{total}  |  Failed: {failed}  |  Elapsed: {elapsed:.1f}s").format(
                completed=summary["completed"],
                total=summary["total"],
                failed=summary["failed"],
                elapsed=summary["elapsed_s"],
            )
        )
        self._log(
            tr("[OK] Batch complete: {completed} cases, {tumors} total tumors, avg {avg:.3f}s/case").format(
                completed=summary["completed"],
                tumors=summary["total_tumors"],
                avg=summary["avg_gen_time_s"],
            ),
            color="#4caf50",
        )
        self._worker = None
        self.tabs.setCurrentIndex(0)

    def _on_stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self.btn_stop.setEnabled(False)
            self._log(tr("[WARN] Stop requested..."), color="#ffa726")

    def _load_summary(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Load Batch Summary"), "", "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            stats = BatchStats.from_dict(payload)
            self._populate_from_stats(stats, rebuild_table=True, refresh_charts=True)
            self.lbl_progress.setText(
                tr("Loaded summary: {completed}/{total} completed, {failed} failed").format(
                    completed=stats.completed,
                    total=stats.total,
                    failed=stats.failed,
                )
            )
            self._log(tr("[INFO] Loaded summary: {path}").format(path=path), color="#4fc3f7")
            self.tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.critical(self, tr("Error"), str(e))

    def start_batch(self):
        self._on_run()

