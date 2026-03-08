"""
Shared viewer for SIMIND .a00 output files.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import language_manager, tr


class SimindOutputViewer(QWidget):
    def __init__(self, view_title: str = "SPECT Preview", parent=None):
        super().__init__(parent)
        self._view_title = view_title
        self._proj_data: np.ndarray | None = None
        self._n_proj = 60
        self._n_row = 128
        self._n_col = 128
        self._build_ui()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.btn_load = QPushButton()
        self.btn_load.setObjectName("primary_btn")
        self.btn_load.setFixedWidth(150)
        self.btn_load.clicked.connect(self._on_load)

        self.lbl_file = QLabel("")
        self.lbl_file.setStyleSheet("color: #6b7280; font-size: 11px;")

        self.lbl_stats = QLabel("")
        self.lbl_stats.setStyleSheet(
            "color: #4fc3f7; font-size: 11px; background: #1a3a5c; border-radius: 4px; padding: 2px 8px;"
        )

        top.addWidget(self.btn_load)
        top.addSpacing(8)
        top.addWidget(self.lbl_file, stretch=1)
        top.addWidget(self.lbl_stats)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2d3139; }")

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(6)
        self.proj_title = QLabel("")
        self.proj_title.setStyleSheet(
            "color: #4fc3f7; font-size: 11px; font-weight: bold; border-bottom: 1px solid #2d3139; padding-bottom: 4px;"
        )
        ll.addWidget(self.proj_title)
        self.proj_view = pg.ImageView()
        self.proj_view.ui.roiBtn.hide()
        self.proj_view.ui.menuBtn.hide()
        self.proj_view.ui.histogram.hide()
        self.proj_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ll.addWidget(self.proj_view, stretch=1)

        proj_ctrl = QHBoxLayout()
        self.lbl_proj = QLabel("")
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

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(6)
        self.sino_title = QLabel("")
        self.sino_title.setStyleSheet(
            "color: #4fc3f7; font-size: 11px; font-weight: bold; border-bottom: 1px solid #2d3139; padding-bottom: 4px;"
        )
        rl.addWidget(self.sino_title)
        self.sino_view = pg.ImageView()
        self.sino_view.ui.roiBtn.hide()
        self.sino_view.ui.menuBtn.hide()
        self.sino_view.ui.histogram.hide()
        self.sino_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rl.addWidget(self.sino_view, stretch=1)

        sino_ctrl = QHBoxLayout()
        self.lbl_sino = QLabel("")
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

        self.hint = QLabel("")
        self.hint.setStyleSheet("color: #3a4049; font-size: 10px;")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.btn_load.setText(tr("Load .a00 File"))
        if self._proj_data is None:
            self.lbl_file.setText(tr("No file loaded — click to open a SIMIND .a00 projection file"))
            self.lbl_stats.setText("")
        self.proj_title.setText(tr("Projection View  (one angle)"))
        self.sino_title.setText(tr("Sinogram  (all angles, one detector row)"))
        self.hint.setText(tr("Tip: Sinogram shows all angles for a single detector row. A point source produces a sine curve; uniform liver activity produces a broad band."))
        if self._proj_data is None:
            self.lbl_proj.setText(tr("Proj: —  |  Angle: —"))
            self.lbl_sino.setText(tr("Row: —"))
        else:
            self._update_proj(self.slider_proj.value())
            self._update_sino(self.slider_sino.value())
            self._update_stats_label()

    def _update_stats_label(self):
        if self._proj_data is None:
            self.lbl_stats.setText("")
            return
        total_counts = float(self._proj_data.sum())
        max_val = float(self._proj_data.max())
        self.lbl_stats.setText(
            tr("Projection stats summary").format(
                proj=self._n_proj,
                size=self._n_col,
                max=max_val,
                total=total_counts,
            )
        )

    def _on_load(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Load SIMIND Projection"), "", "SIMIND Projection (*.a00);;All Files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> bool:
        try:
            data = np.fromfile(path, dtype=np.float32)
            n_total = data.size
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
                raise ValueError(f"File has {n_total} float32 values - cannot match to N x M x M shape.")
            self.lbl_file.setText(Path(path).name)
            self._update_stats_label()
            self.slider_proj.setMaximum(self._n_proj - 1)
            self.slider_proj.setEnabled(True)
            self.slider_sino.setMaximum(self._n_row - 1)
            self.slider_sino.setValue(self._n_row // 2)
            self.slider_sino.setEnabled(True)
            self.slider_proj.setValue(0)
            self._update_proj(0)
            self._update_sino(self._n_row // 2)
            return True
        except Exception as e:
            QMessageBox.critical(self, tr("Load Error"), f"{tr('Failed to load .a00 file:')}\n{e}")
            return False

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
        self.proj_view.setImage(proj, autoLevels=False, levels=(0, float(self._proj_data.max())))
        angle = 180.0 + idx * (360.0 / self._n_proj)
        if angle >= 360:
            angle -= 360
        self.lbl_proj.setText(tr("Proj: {index} / {total}  |  Angle: {angle:.1f}°").format(index=idx + 1, total=self._n_proj, angle=angle))

    def _update_sino(self, row: int):
        if self._proj_data is None:
            return
        sino = self._proj_data[:, row, :].astype(np.float32)
        self.sino_view.setImage(sino.T, autoLevels=True)
        self.lbl_sino.setText(tr("Row: {index} / {total}").format(index=row + 1, total=self._n_row))
