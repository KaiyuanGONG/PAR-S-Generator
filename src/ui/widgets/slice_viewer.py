"""
Slice Viewer Widget
===================
Multi-planar (Axial / Coronal / Sagittal) + 3D surface viewer for phantom volumes.
Uses pyqtgraph for 2D slices and a lightweight matplotlib 3D for surface view.
"""

from __future__ import annotations
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QSlider, QLabel, QComboBox, QSizePolicy, QFrame,
    QPushButton, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal

import pyqtgraph as pg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

# pyqtgraph dark background
pg.setConfigOption('background', '#0d1117')
pg.setConfigOption('foreground', '#8b949e')


class SinglePlaneView(QWidget):
    """One-plane slice viewer with slider."""
    slice_changed = pyqtSignal(int)

    def __init__(self, plane: str = "Axial", parent=None):
        super().__init__(parent)
        self.plane = plane
        self._volume: np.ndarray | None = None
        self._overlay: np.ndarray | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header
        hdr = QHBoxLayout()
        self.lbl_plane = QLabel(self.plane)
        self.lbl_plane.setStyleSheet("color: #4fc3f7; font-size: 11px; font-weight: bold;")
        self.lbl_idx = QLabel("Slice: 0 / 0")
        self.lbl_idx.setStyleSheet("color: #6b7280; font-size: 11px;")
        hdr.addWidget(self.lbl_plane)
        hdr.addStretch()
        hdr.addWidget(self.lbl_idx)
        layout.addLayout(hdr)

        # Image view
        self.img_view = pg.ImageView()
        self.img_view.ui.roiBtn.hide()
        self.img_view.ui.menuBtn.hide()
        self.img_view.ui.histogram.hide()
        self.img_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.img_view, stretch=1)

        # Slider
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self.slider)

    def set_volume(self, volume: np.ndarray, overlay: np.ndarray | None = None):
        """Set volume (Z, Y, X) and optional overlay mask."""
        self._volume = volume
        self._overlay = overlay
        n = volume.shape[0]
        self.slider.setMaximum(n - 1)
        self.slider.setValue(n // 2)
        self._update_slice(n // 2)

    def _on_slider(self, val: int):
        self._update_slice(val)
        self.slice_changed.emit(val)

    def _update_slice(self, idx: int):
        if self._volume is None:
            return
        n = self._volume.shape[0]
        self.lbl_idx.setText(f"Slice: {idx + 1} / {n}")
        slc = self._volume[idx].T.astype(np.float32)

        # Normalize
        vmin, vmax = slc.min(), slc.max()
        if vmax > vmin:
            slc_norm = (slc - vmin) / (vmax - vmin)
        else:
            slc_norm = slc

        # Build RGB image
        rgb = np.stack([slc_norm, slc_norm, slc_norm], axis=-1)

        # Overlay (liver mask = green tint, tumor = red tint)
        if self._overlay is not None:
            ov = self._overlay[idx].T
            if ov.any():
                rgb[ov > 0.5, 0] = np.clip(rgb[ov > 0.5, 0] * 0.5 + 0.3, 0, 1)
                rgb[ov > 0.5, 1] = np.clip(rgb[ov > 0.5, 1] * 0.5 + 0.5, 0, 1)
                rgb[ov > 0.5, 2] = np.clip(rgb[ov > 0.5, 2] * 0.5, 0, 1)

        self.img_view.setImage(
            (rgb * 255).astype(np.uint8),
            autoLevels=False,
            levels=(0, 255)
        )

    def set_slice(self, idx: int):
        self.slider.setValue(idx)


class Surface3DView(QWidget):
    """Lightweight 3D surface view using matplotlib."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.fig = Figure(figsize=(5, 5), facecolor='#0d1117')
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.canvas)

        # Controls
        ctrl = QHBoxLayout()
        self.lbl_info = QLabel("No data")
        self.lbl_info.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.combo_view = QComboBox()
        self.combo_view.addItems(["Activity", "μ-map", "Liver Mask"])
        self.combo_view.currentIndexChanged.connect(self._rerender)
        ctrl.addWidget(self.lbl_info)
        ctrl.addStretch()
        ctrl.addWidget(QLabel("Show:"))
        ctrl.addWidget(self.combo_view)
        layout.addLayout(ctrl)

        self._activity = None
        self._mu_map = None
        self._liver_mask = None
        self._tumor_masks = []

    def set_volumes(self, activity, mu_map, liver_mask, tumor_masks):
        self._activity = activity
        self._mu_map = mu_map
        self._liver_mask = liver_mask
        self._tumor_masks = tumor_masks
        self._rerender()

    def _rerender(self):
        if self._liver_mask is None:
            return
        self.fig.clear()
        ax = self.fig.add_subplot(111, projection='3d', facecolor='#0d1117')
        ax.set_facecolor('#0d1117')
        for spine in ax.spines.values():
            spine.set_color('#2d3139')
        ax.tick_params(colors='#6b7280', labelsize=7)
        ax.xaxis.pane.fill = False
        ax.yaxis.pane.fill = False
        ax.zaxis.pane.fill = False
        ax.xaxis.pane.set_edgecolor('#2d3139')
        ax.yaxis.pane.set_edgecolor('#2d3139')
        ax.zaxis.pane.set_edgecolor('#2d3139')
        ax.grid(True, color='#2d3139', linewidth=0.5)

        try:
            from skimage.measure import marching_cubes
            # Liver surface
            liver_smooth = self._liver_mask.astype(float)
            if liver_smooth.sum() > 100:
                verts, faces, _, _ = marching_cubes(liver_smooth, level=0.5, step_size=2)
                mesh = Poly3DCollection(verts[faces], alpha=0.25,
                                        facecolor='#4fc3f7', edgecolor='none')
                ax.add_collection3d(mesh)
                ax.set_xlim(0, liver_smooth.shape[0])
                ax.set_ylim(0, liver_smooth.shape[1])
                ax.set_zlim(0, liver_smooth.shape[2])

            # Tumor surfaces
            for i, tmask in enumerate(self._tumor_masks):
                if tmask.sum() > 20:
                    try:
                        tv, tf, _, _ = marching_cubes(tmask.astype(float), level=0.5, step_size=1)
                        tmesh = Poly3DCollection(tv[tf], alpha=0.8,
                                                  facecolor='#ff6b6b', edgecolor='none')
                        ax.add_collection3d(tmesh)
                    except Exception:
                        pass

            n_tumors = len(self._tumor_masks)
            self.lbl_info.setText(
                f"Liver: {self._liver_mask.sum() * (4.2**3 / 1000):.0f} mL  |  "
                f"Tumors: {n_tumors}"
            )
        except ImportError:
            ax.text(0.5, 0.5, 0.5, "Install scikit-image\nfor 3D view",
                    ha='center', va='center', color='#6b7280', transform=ax.transAxes)

        ax.set_xlabel('Z', color='#6b7280', fontsize=8)
        ax.set_ylabel('Y', color='#6b7280', fontsize=8)
        ax.set_zlabel('X', color='#6b7280', fontsize=8)
        ax.set_title("3D Phantom", color='#8b949e', fontsize=10, pad=8)

        self.canvas.draw()


class SliceViewer(QWidget):
    """
    Full slice viewer: tabs for Axial/Coronal/Sagittal + 3D surface.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._activity: np.ndarray | None = None
        self._mu_map: np.ndarray | None = None
        self._liver_mask: np.ndarray | None = None
        self._tumor_masks: list = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Channel selector
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 0, 0, 8)
        self.combo_channel = QComboBox()
        self.combo_channel.addItems(["Activity Map", "μ-map (Attenuation)"])
        self.combo_channel.currentIndexChanged.connect(self._update_all_slices)
        self.chk_overlay = QPushButton("Liver Overlay: ON")
        self.chk_overlay.setCheckable(True)
        self.chk_overlay.setChecked(True)
        self.chk_overlay.setStyleSheet(
            "QPushButton { background: #1a3a5c; color: #4fc3f7; border: 1px solid #4fc3f7; "
            "border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            "QPushButton:!checked { background: #252a33; color: #6b7280; border-color: #3a4049; }"
        )
        self.chk_overlay.toggled.connect(self._update_all_slices)
        ctrl.addWidget(QLabel("Channel:"))
        ctrl.addWidget(self.combo_channel)
        ctrl.addStretch()
        ctrl.addWidget(self.chk_overlay)
        layout.addLayout(ctrl)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # Multi-plane tab
        multi_widget = QWidget()
        multi_layout = QGridLayout(multi_widget)
        multi_layout.setSpacing(4)
        multi_layout.setContentsMargins(0, 0, 0, 0)

        self.axial_view = SinglePlaneView("Axial (Z)")
        self.coronal_view = SinglePlaneView("Coronal (Y)")
        self.sagittal_view = SinglePlaneView("Sagittal (X)")

        multi_layout.addWidget(self.axial_view, 0, 0)
        multi_layout.addWidget(self.coronal_view, 0, 1)
        multi_layout.addWidget(self.sagittal_view, 1, 0)

        # Mini stats panel bottom-right
        self.mini_stats = self._build_mini_stats()
        multi_layout.addWidget(self.mini_stats, 1, 1)

        self.tabs.addTab(multi_widget, "Multi-Plane")

        # 3D tab
        self.surface_view = Surface3DView()
        self.tabs.addTab(self.surface_view, "3D Surface")

        layout.addWidget(self.tabs, stretch=1)

    def _build_mini_stats(self) -> QWidget:
        w = QFrame()
        w.setStyleSheet("background-color: #252a33; border-radius: 6px;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("PHANTOM INFO")
        title.setStyleSheet("color: #3a4049; font-size: 10px; letter-spacing: 1px;")
        layout.addWidget(title)

        self.info_labels = {}
        rows = [
            ("Volume shape", "shape"),
            ("Voxel size", "voxel"),
            ("Liver volume", "liver_vol"),
            ("Left lobe ratio", "left_ratio"),
            ("No. of tumors", "n_tumors"),
            ("Perfusion mode", "perfusion"),
            ("Total counts", "counts"),
        ]
        for label, key in rows:
            row = QHBoxLayout()
            lbl_name = QLabel(label)
            lbl_name.setStyleSheet("color: #6b7280; font-size: 11px;")
            lbl_val = QLabel("—")
            lbl_val.setStyleSheet("color: #c8ccd4; font-size: 11px;")
            lbl_val.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(lbl_name)
            row.addStretch()
            row.addWidget(lbl_val)
            layout.addLayout(row)
            self.info_labels[key] = lbl_val

        layout.addStretch()
        return w

    def set_volumes(self, activity: np.ndarray, mu_map: np.ndarray,
                    liver_mask: np.ndarray, tumor_masks: list):
        self._activity = activity
        self._mu_map = mu_map
        self._liver_mask = liver_mask
        self._tumor_masks = tumor_masks
        self._update_all_slices()
        self.surface_view.set_volumes(activity, mu_map, liver_mask, tumor_masks)
        self._update_info()

    def _get_current_volume(self) -> np.ndarray | None:
        if self.combo_channel.currentIndex() == 0:
            return self._activity
        return self._mu_map

    def _get_overlay(self) -> np.ndarray | None:
        if not self.chk_overlay.isChecked():
            return None
        return self._liver_mask

    def _update_all_slices(self):
        vol = self._get_current_volume()
        if vol is None:
            return
        overlay = self._get_overlay()

        # Axial: Z axis (vol[z, :, :])
        self.axial_view.set_volume(vol, overlay)

        # Coronal: Y axis → transpose to (Y, Z, X)
        vol_cor = np.transpose(vol, (1, 0, 2))
        ov_cor = np.transpose(overlay, (1, 0, 2)) if overlay is not None else None
        self.coronal_view.set_volume(vol_cor, ov_cor)

        # Sagittal: X axis → transpose to (X, Z, Y)
        vol_sag = np.transpose(vol, (2, 0, 1))
        ov_sag = np.transpose(overlay, (2, 0, 1)) if overlay is not None else None
        self.sagittal_view.set_volume(vol_sag, ov_sag)

    def _update_info(self):
        if self._activity is None:
            return
        shape = self._activity.shape
        self.info_labels["shape"].setText(f"{shape[0]}×{shape[1]}×{shape[2]}")
        self.info_labels["voxel"].setText("4.20 mm")
        if self._liver_mask is not None:
            vol_ml = self._liver_mask.sum() * (4.2 ** 3 / 1000)
            self.info_labels["liver_vol"].setText(f"{vol_ml:.0f} mL")
        self.info_labels["n_tumors"].setText(str(len(self._tumor_masks)))
        counts = self._activity.sum()
        self.info_labels["counts"].setText(f"{counts:.2e}")
