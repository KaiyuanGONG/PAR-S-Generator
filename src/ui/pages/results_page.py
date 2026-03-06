"""
Results Page
============
Batch generation runner with real-time progress, statistics charts, and case browser.
"""

from __future__ import annotations
import json
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QProgressBar, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QGroupBox, QFileDialog, QMessageBox,
    QTabWidget, QScrollArea, QSlider, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QThread
from PyQt6.QtGui import QTextCursor, QColor, QFont

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import pyqtgraph as pg

from core.batch_runner import BatchWorker, BatchStats
from core.phantom_generator import PhantomConfig


class SimindOutputViewer(QWidget):
    """
    Viewer for SIMIND .a00 output files.

    Left panel  — Projection Viewer: one projection at a time, slider to navigate.
    Right panel — Sinogram Viewer: proj[:, row, :] across all angles, slider to pick row.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proj_data: np.ndarray | None = None  # shape (N_proj, N_row, N_col)
        self._n_proj = 60
        self._n_row = 128
        self._n_col = 128
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Top bar ──────────────────────────────────────────────────
        top = QHBoxLayout()
        self.btn_load = QPushButton("Load .a00 File")
        self.btn_load.setObjectName("primary_btn")
        self.btn_load.setFixedWidth(140)
        self.btn_load.clicked.connect(self._on_load)

        self.lbl_file = QLabel("No file loaded — click to open a SIMIND .a00 projection file")
        self.lbl_file.setStyleSheet("color: #6b7280; font-size: 11px;")

        self.lbl_stats = QLabel("")
        self.lbl_stats.setStyleSheet(
            "color: #4fc3f7; font-size: 11px; background: #1a3a5c; "
            "border-radius: 4px; padding: 2px 8px;"
        )

        top.addWidget(self.btn_load)
        top.addSpacing(8)
        top.addWidget(self.lbl_file, stretch=1)
        top.addWidget(self.lbl_stats)
        layout.addLayout(top)

        # ── Main splitter: Projection | Sinogram ─────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2d3139; }")

        # LEFT — Projection Viewer
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(6)

        proj_title = QLabel("Projection View  (one angle)")
        proj_title.setStyleSheet(
            "color: #4fc3f7; font-size: 11px; font-weight: bold; "
            "border-bottom: 1px solid #2d3139; padding-bottom: 4px;"
        )
        ll.addWidget(proj_title)

        self.proj_view = pg.ImageView()
        self.proj_view.ui.roiBtn.hide()
        self.proj_view.ui.menuBtn.hide()
        self.proj_view.ui.histogram.hide()
        self.proj_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ll.addWidget(self.proj_view, stretch=1)

        proj_ctrl = QHBoxLayout()
        self.lbl_proj = QLabel("Proj: —  |  Angle: —")
        self.lbl_proj.setStyleSheet("color: #8b949e; font-size: 11px; min-width: 160px;")
        self.slider_proj = QSlider(Qt.Orientation.Horizontal)
        self.slider_proj.setMinimum(0)
        self.slider_proj.setMaximum(59)
        self.slider_proj.setValue(0)
        self.slider_proj.setEnabled(False)
        self.slider_proj.valueChanged.connect(self._on_proj_slider)
        proj_ctrl.addWidget(self.lbl_proj)
        proj_ctrl.addWidget(self.slider_proj, stretch=1)
        ll.addLayout(proj_ctrl)

        splitter.addWidget(left)

        # RIGHT — Sinogram Viewer
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(6)

        sino_title = QLabel("Sinogram  (all angles, one detector row)")
        sino_title.setStyleSheet(
            "color: #4fc3f7; font-size: 11px; font-weight: bold; "
            "border-bottom: 1px solid #2d3139; padding-bottom: 4px;"
        )
        rl.addWidget(sino_title)

        self.sino_view = pg.ImageView()
        self.sino_view.ui.roiBtn.hide()
        self.sino_view.ui.menuBtn.hide()
        self.sino_view.ui.histogram.hide()
        self.sino_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rl.addWidget(self.sino_view, stretch=1)

        sino_ctrl = QHBoxLayout()
        self.lbl_sino = QLabel("Row: —")
        self.lbl_sino.setStyleSheet("color: #8b949e; font-size: 11px; min-width: 100px;")
        self.slider_sino = QSlider(Qt.Orientation.Horizontal)
        self.slider_sino.setMinimum(0)
        self.slider_sino.setMaximum(127)
        self.slider_sino.setValue(63)
        self.slider_sino.setEnabled(False)
        self.slider_sino.valueChanged.connect(self._on_sino_slider)
        sino_ctrl.addWidget(self.lbl_sino)
        sino_ctrl.addWidget(self.slider_sino, stretch=1)
        rl.addLayout(sino_ctrl)

        splitter.addWidget(right)
        splitter.setSizes([500, 500])
        layout.addWidget(splitter, stretch=1)

        # ── Hint label ───────────────────────────────────────────────
        hint = QLabel(
            "Tip: Sinogram shows all 60 angles for a single detector row. "
            "A point source produces a sine curve; uniform liver activity produces a broad band."
        )
        hint.setStyleSheet("color: #3a4049; font-size: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    # ── Actions ──────────────────────────────────────────────────────

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load SIMIND Projection", "",
            "SIMIND Projection (*.a00);;All Files (*)"
        )
        if not path:
            return
        try:
            data = np.fromfile(path, dtype=np.float32)
            n_total = data.size

            # Auto-detect shape: try common matrix sizes
            detected = False
            for n_col in (128, 64, 256):
                if n_total % (n_col * n_col) == 0:
                    n_proj = n_total // (n_col * n_col)
                    self._proj_data = data.reshape(n_proj, n_col, n_col)
                    self._n_proj = n_proj
                    self._n_row = n_col
                    self._n_col = n_col
                    detected = True
                    break

            if not detected:
                raise ValueError(
                    f"File has {n_total} float32 values — cannot match to N×M×M shape.\n"
                    f"Expected 60×128×128 = 983,040 values."
                )

            self.lbl_file.setText(Path(path).name)
            total_counts = float(self._proj_data.sum())
            max_val = float(self._proj_data.max())
            self.lbl_stats.setText(
                f"{self._n_proj} proj  |  {self._n_col}×{self._n_col}  |  "
                f"Max: {max_val:.1f}  |  Total: {total_counts:.3e}"
            )

            self.slider_proj.setMaximum(self._n_proj - 1)
            self.slider_proj.setEnabled(True)
            self.slider_sino.setMaximum(self._n_row - 1)
            self.slider_sino.setValue(self._n_row // 2)
            self.slider_sino.setEnabled(True)

            self.slider_proj.setValue(0)
            self._update_proj(0)
            self._update_sino(self._n_row // 2)

        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load .a00 file:\n{e}")

    def _on_proj_slider(self, val: int):
        if self._proj_data is not None:
            self._update_proj(val)

    def _on_sino_slider(self, val: int):
        if self._proj_data is not None:
            self._update_sino(val)

    def _update_proj(self, idx: int):
        if self._proj_data is None:
            return
        proj = self._proj_data[idx].T.astype(np.float32)
        # Use fixed levels on first load, then let user adjust
        self.proj_view.setImage(proj, autoLevels=False,
                                levels=(0, float(self._proj_data.max())))
        angle = 180.0 + idx * (360.0 / self._n_proj)
        if angle >= 360:
            angle -= 360
        self.lbl_proj.setText(f"Proj: {idx + 1} / {self._n_proj}  |  Angle: {angle:.1f}°")

    def _update_sino(self, row: int):
        if self._proj_data is None:
            return
        # Sinogram: all projections × detector columns for a single row
        # Shape (n_proj, n_col), displayed with angles on Y axis and det. pos on X axis
        sino = self._proj_data[:, row, :].astype(np.float32)
        self.sino_view.setImage(sino.T, autoLevels=True)
        self.lbl_sino.setText(f"Row: {row + 1} / {self._n_row}")


class StatCard(QFrame):
    """A single statistic display card."""

    def __init__(self, title: str, key: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self.setStyleSheet(
            "QFrame { background-color: #252a33; border-radius: 8px; "
            "border: 1px solid #2d3139; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self.val_lbl = QLabel("—")
        self.val_lbl.setStyleSheet("color: #4fc3f7; font-size: 22px; font-weight: bold;")
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel(title + (f"\n({unit})" if unit else ""))
        title_lbl.setStyleSheet("color: #6b7280; font-size: 11px;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.val_lbl)
        layout.addWidget(title_lbl)

    def set_value(self, val):
        self.val_lbl.setText(str(val))


class StatsCharts(QWidget):
    """Matplotlib charts for batch statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.fig = Figure(figsize=(10, 6), facecolor='#1e2128')
        self.canvas = FigureCanvas(self.fig)
        layout.addWidget(self.canvas)

    def update_charts(self, stats: BatchStats):
        self.fig.clear()
        self.fig.patch.set_facecolor('#1e2128')

        axes = self.fig.subplots(2, 3)
        self.fig.subplots_adjust(hspace=0.45, wspace=0.35,
                                  left=0.08, right=0.97, top=0.92, bottom=0.1)

        style = {
            'color': '#c8ccd4', 'edgecolor': '#4fc3f7',
            'facecolor': '#1a3a5c', 'linewidth': 0.8
        }

        def style_ax(ax, title, xlabel, ylabel="Count"):
            ax.set_facecolor('#161a1f')
            ax.set_title(title, color='#8b949e', fontsize=9, pad=6)
            ax.set_xlabel(xlabel, color='#6b7280', fontsize=8)
            ax.set_ylabel(ylabel, color='#6b7280', fontsize=8)
            ax.tick_params(colors='#6b7280', labelsize=7)
            for spine in ax.spines.values():
                spine.set_color('#2d3139')
            ax.grid(True, color='#2d3139', linewidth=0.4, alpha=0.7)

        # 1. Liver volume distribution
        ax = axes[0, 0]
        if stats.liver_volumes:
            ax.hist(stats.liver_volumes, bins=20, **style)
        style_ax(ax, "Liver Volume", "Volume (mL)")

        # 2. Left lobe ratio
        ax = axes[0, 1]
        if stats.left_ratios:
            ax.hist([r * 100 for r in stats.left_ratios], bins=20, **style)
        style_ax(ax, "Left Lobe Ratio", "Ratio (%)")

        # 3. Tumor count distribution
        ax = axes[0, 2]
        if stats.n_tumors_list:
            counts = np.bincount(stats.n_tumors_list)
            ax.bar(range(len(counts)), counts,
                   color='#1a3a5c', edgecolor='#4fc3f7', linewidth=0.8)
        style_ax(ax, "Tumor Count per Case", "Number of Tumors")

        # 4. Tumor diameter distribution
        ax = axes[1, 0]
        if stats.tumor_diameters:
            ax.hist(stats.tumor_diameters, bins=20, **style)
        style_ax(ax, "Tumor Diameter", "Diameter (mm)")

        # 5. Perfusion mode distribution
        ax = axes[1, 1]
        if stats.perfusion_modes:
            labels = list(stats.perfusion_modes.keys())
            values = list(stats.perfusion_modes.values())
            short_labels = [l.replace(" Only", "\nOnly").replace("Whole ", "Whole\n")
                            for l in labels]
            bars = ax.bar(range(len(values)), values,
                          color='#1a3a5c', edgecolor='#4fc3f7', linewidth=0.8)
            ax.set_xticks(range(len(values)))
            ax.set_xticklabels(short_labels, fontsize=7, color='#6b7280')
        style_ax(ax, "Perfusion Mode", "Mode")

        # 6. Generation time
        ax = axes[1, 2]
        if stats.gen_times:
            ax.plot(stats.gen_times, color='#4fc3f7', linewidth=1.0, alpha=0.8)
            ax.axhline(np.mean(stats.gen_times), color='#ff6b6b',
                       linewidth=1.0, linestyle='--', alpha=0.7)
        style_ax(ax, "Generation Time", "Case Index", "Time (s)")

        self.canvas.draw()


class ResultsPage(QWidget):
    """Page 3: Batch generation and results."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: PhantomConfig | None = None
        self._config_getter = None   # callable: () -> PhantomConfig, set by MainWindow
        self._worker: BatchWorker | None = None
        self._stats: BatchStats | None = None
        self._build_ui()

    def set_config_getter(self, getter):
        """Set a callable that returns the current PhantomConfig from the Phantom page."""
        self._config_getter = getter

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel("Batch Generation & Results")
        title.setObjectName("page_title")
        title_row.addWidget(title)
        title_row.addStretch()

        self.btn_load_summary = QPushButton("Load Existing Summary")
        self.btn_load_summary.clicked.connect(self._load_summary)
        title_row.addWidget(self.btn_load_summary)
        root.addLayout(title_row)

        # ── Top: Controls + Progress ──
        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet(
            "QFrame { background-color: #252a33; border-radius: 8px; "
            "border: 1px solid #2d3139; padding: 4px; }"
        )
        ctrl_layout = QHBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(16, 12, 16, 12)
        ctrl_layout.setSpacing(16)

        # Run controls
        self.btn_run = QPushButton("▶  Start Batch Generation")
        self.btn_run.setObjectName("success_btn")
        self.btn_run.setMinimumHeight(40)
        self.btn_run.setMinimumWidth(220)
        self.btn_run.clicked.connect(self._on_run)

        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("danger_btn")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)

        # Progress
        prog_col = QVBoxLayout()
        prog_col.setSpacing(4)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumWidth(300)
        self.lbl_progress = QLabel("Ready")
        self.lbl_progress.setStyleSheet("color: #6b7280; font-size: 12px;")
        prog_col.addWidget(self.progress_bar)
        prog_col.addWidget(self.lbl_progress)

        # ETA
        eta_col = QVBoxLayout()
        eta_col.setSpacing(4)
        self.lbl_eta = QLabel("ETA: —")
        self.lbl_eta.setStyleSheet("color: #6b7280; font-size: 12px;")
        self.lbl_elapsed = QLabel("Elapsed: —")
        self.lbl_elapsed.setStyleSheet("color: #6b7280; font-size: 12px;")
        eta_col.addWidget(self.lbl_eta)
        eta_col.addWidget(self.lbl_elapsed)

        ctrl_layout.addWidget(self.btn_run)
        ctrl_layout.addWidget(self.btn_stop)
        ctrl_layout.addLayout(prog_col, stretch=1)
        ctrl_layout.addLayout(eta_col)
        root.addWidget(ctrl_frame)

        # ── Stat Cards ──
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(10)
        self._cards = {}
        card_defs = [
            ("Completed", "completed", ""),
            ("Liver Vol. Mean", "liver_vol_mean", "mL"),
            ("Left Ratio Mean", "left_ratio_mean", ""),
            ("Avg Tumors/Case", "avg_tumors", ""),
            ("Total Tumors", "total_tumors", ""),
            ("Avg Gen. Time", "avg_gen_time", "s"),
        ]
        for title_c, key, unit in card_defs:
            card = StatCard(title_c, key, unit)
            self._cards[key] = card
            cards_layout.addWidget(card)
        root.addLayout(cards_layout)

        # ── Main content: tabs ──
        self.tabs = QTabWidget()

        # Tab 1: Charts
        self.charts = StatsCharts()
        self.tabs.addTab(self.charts, "Statistics Charts")

        # Tab 2: Case table
        self.table = self._build_table()
        self.tabs.addTab(self.table, "Case Table")

        # Tab 3: Log
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.tabs.addTab(self.log_view, "Log")

        # Tab 4: SIMIND Output Viewer
        self.simind_viewer = SimindOutputViewer()
        self.tabs.addTab(self.simind_viewer, "SIMIND Output")

        root.addWidget(self.tabs, stretch=1)

    def _build_table(self) -> QTableWidget:
        cols = ["Case ID", "Seed", "Liver Vol (mL)", "Left Ratio",
                "N Tumors", "Tumor Diameters (mm)", "Perfusion", "Counts", "Time (s)"]
        table = QTableWidget(0, len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(
            "QTableWidget { alternate-background-color: #1a1e26; }"
        )
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    def _add_table_row(self, result):
        row = self.table.rowCount()
        self.table.insertRow(row)
        diams_str = ", ".join(f"{d:.1f}" for d in result.tumor_radii_mm) or "—"
        values = [
            f"{result.case_id:04d}",
            str(result.seed),
            f"{result.liver_volume_ml:.1f}",
            f"{result.left_ratio:.3f}",
            str(result.n_tumors),
            diams_str,
            result.perfusion_mode,
            f"{result.total_counts_actual:.2e}",
            f"{result.generation_time_s:.3f}",
        ]
        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)

    def _log(self, msg: str, color: str = "#8b949e"):
        self.log_view.setTextColor(QColor(color))
        self.log_view.append(msg)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _update_cards(self, stats: BatchStats):
        s = stats.summary()
        self._cards["completed"].set_value(f"{s['completed']}/{s['total']}")
        self._cards["liver_vol_mean"].set_value(f"{s['liver_vol_mean_ml']:.0f}")
        self._cards["left_ratio_mean"].set_value(f"{s['left_ratio_mean']:.3f}")
        self._cards["avg_tumors"].set_value(f"{s['avg_tumors']:.2f}")
        self._cards["total_tumors"].set_value(str(s['total_tumors']))
        self._cards["avg_gen_time"].set_value(f"{s['avg_gen_time_s']:.3f}")

    # ── Actions ──────────────────────────────────────────────────────

    def on_results_ready(self, output_dir: str):
        """Called from simulation page when SIMIND finishes."""
        self._log(f"[INFO] SIMIND output ready: {output_dir}", color="#4fc3f7")
        # Switch to SIMIND Output tab and hint user to load first .a00
        self.tabs.setCurrentIndex(3)
        a00_files = sorted(Path(output_dir).glob("*.a00"))
        if a00_files:
            self._log(
                f"[INFO] Found {len(a00_files)} .a00 file(s). "
                f"Open 'SIMIND Output' tab and click 'Load .a00 File'.",
                color="#4fc3f7"
            )

    def _on_run(self):
        # Always get the freshest config from Phantom page (avoids stale output path/n_cases)
        if self._config_getter is not None:
            self._config = self._config_getter()
        if self._config is None:
            QMessageBox.information(
                self, "No Config",
                "Please configure phantom parameters on the Phantom page first."
            )
            return

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(self._config.n_cases)
        self._log(f"[INFO] Starting batch: {self._config.n_cases} cases", color="#4fc3f7")

        self._worker = BatchWorker(self._config)
        self._worker.case_done.connect(self._on_case_done)
        self._worker.case_failed.connect(self._on_case_failed)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.log.connect(lambda msg: self._log(msg))
        self._worker.start()

    @pyqtSlot(int, int, object)
    def _on_case_done(self, idx: int, total: int, result):
        self.progress_bar.setValue(idx + 1)
        self._add_table_row(result)

        # Update stats every 5 cases or at end
        if self._worker and (idx % 5 == 0 or idx == total - 1):
            pass  # stats updated in all_done

        # Update ETA
        if self._worker and hasattr(self._worker, '_stats_ref'):
            stats = self._worker._stats_ref
            eta = stats.eta
            elapsed = stats.elapsed
            self.lbl_eta.setText(f"ETA: {eta:.0f}s")
            self.lbl_elapsed.setText(f"Elapsed: {elapsed:.0f}s")

        pct = (idx + 1) / total * 100
        self.lbl_progress.setText(f"{idx + 1} / {total}  ({pct:.1f}%)")

    @pyqtSlot(int, str)
    def _on_case_failed(self, idx: int, msg: str):
        self._log(f"[ERROR] Case {idx}: {msg}", color="#ff6b6b")

    @pyqtSlot(object)
    def _on_all_done(self, stats: BatchStats):
        self._stats = stats
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(stats.completed)
        s = stats.summary()
        self.lbl_progress.setText(
            f"Done: {s['completed']}/{s['total']}  |  "
            f"Failed: {s['failed']}  |  "
            f"Elapsed: {s['elapsed_s']:.1f}s"
        )
        self._update_cards(stats)
        self.charts.update_charts(stats)
        self._log(
            f"[OK] Batch complete: {s['completed']} cases, "
            f"{s['total_tumors']} total tumors, "
            f"avg {s['avg_gen_time_s']:.3f}s/case",
            color="#4caf50"
        )
        self.tabs.setCurrentIndex(0)  # Show charts

    def _on_stop(self):
        if self._worker:
            self._worker.stop()
        self.btn_stop.setEnabled(False)
        self._log("[WARN] Stop requested...", color="#ffa726")

    def _load_summary(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Batch Summary", "", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            with open(path) as f:
                s = json.load(f)
            self._log(f"[INFO] Loaded summary: {path}", color="#4fc3f7")
            self._log(json.dumps(s, indent=2), color="#8b949e")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def set_config(self, config: PhantomConfig):
        """Called when phantom page config is updated."""
        self._config = config

    def on_phantom_config_ready(self, config: PhantomConfig):
        self._config = config
