"""
Slice viewer widget with multi-plane preview, metrics panel, and 3D surface.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import language_manager, tr


pg.setConfigOption("background", "#0d1117")
pg.setConfigOption("foreground", "#8b949e")


class SinglePlaneView(QWidget):
    def __init__(self, plane_key: str = "Axial (Z)", parent=None):
        super().__init__(parent)
        self._plane_key = plane_key
        self._volume: np.ndarray | None = None
        self._liver_overlay: np.ndarray | None = None
        self._tumor_overlay: np.ndarray | None = None
        self._overlay_mode: str = "liver_and_tumors"
        self._levels: tuple[float, float] = (0.0, 1.0)
        self._current_idx = 0
        self._slice_total = 0
        self._build_ui()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        hdr = QHBoxLayout()
        self.lbl_plane = QLabel("")
        self.lbl_plane.setObjectName("plane_label")
        self.lbl_idx = QLabel("")
        self.lbl_idx.setObjectName("plane_index")
        hdr.addWidget(self.lbl_plane)
        hdr.addStretch()
        hdr.addWidget(self.lbl_idx)
        layout.addLayout(hdr)

        self.img_view = pg.ImageView()
        self.img_view.ui.roiBtn.hide()
        self.img_view.ui.menuBtn.hide()
        self.img_view.ui.histogram.hide()
        self.img_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.img_view, stretch=1)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self._update_slice)
        layout.addWidget(self.slider)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.lbl_plane.setText(tr(self._plane_key))
        current = self._current_idx + 1 if self._slice_total else 0
        total = self._slice_total
        self.lbl_idx.setText(tr("Slice: {current} / {total}").format(current=current, total=total))

    def set_volume(self, volume: np.ndarray, liver_overlay=None, tumor_overlay=None, overlay_mode: str = "liver_and_tumors"):
        self._volume = volume
        self._liver_overlay = liver_overlay
        self._tumor_overlay = tumor_overlay
        self._overlay_mode = overlay_mode
        self._levels = self._compute_levels(volume)
        n = volume.shape[0]
        self._slice_total = n
        self.slider.blockSignals(True)
        self.slider.setMaximum(max(n - 1, 0))
        self.slider.setValue(min(self._current_idx, max(n - 1, 0)))
        self.slider.blockSignals(False)
        self._update_slice(self.slider.value())

    def _compute_levels(self, volume: np.ndarray) -> tuple[float, float]:
        data = np.asarray(volume, dtype=np.float32)
        if data.size == 0:
            return 0.0, 1.0
        low = float(np.percentile(data, 1.0))
        high = float(np.percentile(data, 99.5))
        if high <= low:
            low = float(data.min())
            high = float(data.max())
        if high <= low:
            high = low + 1.0
        return low, high

    def _normalize_slice(self, slc: np.ndarray) -> np.ndarray:
        low, high = self._levels
        return np.clip((slc - low) / max(high - low, 1e-6), 0.0, 1.0)

    def _make_edges(self, mask_2d: np.ndarray) -> np.ndarray:
        body = mask_2d.astype(bool)
        if not body.any():
            return body
        eroded = body.copy()
        eroded[1:, :] &= body[:-1, :]
        eroded[:-1, :] &= body[1:, :]
        eroded[:, 1:] &= body[:, :-1]
        eroded[:, :-1] &= body[:, 1:]
        return body & ~eroded

    def _update_slice(self, idx: int):
        if self._volume is None:
            return
        self._current_idx = idx
        n = self._volume.shape[0]
        self._slice_total = n
        self.lbl_idx.setText(tr("Slice: {current} / {total}").format(current=idx + 1, total=n))
        slc = self._volume[idx].T.astype(np.float32)
        slc_norm = self._normalize_slice(slc)
        rgb = np.stack([slc_norm, slc_norm, slc_norm], axis=-1)
        liver = self._liver_overlay[idx].T.astype(bool) if self._liver_overlay is not None else np.zeros_like(slc, dtype=bool)
        tumor = self._tumor_overlay[idx].T.astype(bool) if self._tumor_overlay is not None else np.zeros_like(slc, dtype=bool)
        if self._overlay_mode in {"liver", "liver_and_tumors"} and liver.any():
            rgb[liver, 0] = np.clip(rgb[liver, 0] * 0.5 + 0.2, 0, 1)
            rgb[liver, 1] = np.clip(rgb[liver, 1] * 0.5 + 0.45, 0, 1)
            rgb[liver, 2] = np.clip(rgb[liver, 2] * 0.4, 0, 1)
        if self._overlay_mode in {"tumors", "liver_and_tumors"} and tumor.any():
            rgb[tumor, 0] = 1.0
            rgb[tumor, 1] = np.clip(rgb[tumor, 1] * 0.35, 0, 1)
            rgb[tumor, 2] = np.clip(rgb[tumor, 2] * 0.35, 0, 1)
        if self._overlay_mode == "contours":
            liver_edge = self._make_edges(liver)
            tumor_edge = self._make_edges(tumor)
            if liver_edge.any():
                rgb[liver_edge, :] = np.array([0.15, 0.95, 0.65], dtype=np.float32)
            if tumor_edge.any():
                rgb[tumor_edge, :] = np.array([1.0, 0.35, 0.35], dtype=np.float32)
        self.img_view.setImage((rgb * 255).astype(np.uint8), autoLevels=False, levels=(0, 255))


class Surface3DView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._liver_mask = None
        self._tumor_masks = []
        self._voxel_size_mm = 4.42
        self._liver_volume_ml = 0.0
        self._build_ui()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.fig = Figure(figsize=(5, 5), facecolor="#0d1117")
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.canvas)
        ctrl = QHBoxLayout()
        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("surface_info")
        self.lbl_show = QLabel("")
        self.combo_view = QComboBox()
        self.combo_view.addItem("", "liver_and_tumors")
        self.combo_view.addItem("", "liver")
        self.combo_view.addItem("", "tumors")
        self.combo_view.currentIndexChanged.connect(self._rerender)
        ctrl.addWidget(self.lbl_info)
        ctrl.addStretch()
        ctrl.addWidget(self.lbl_show)
        ctrl.addWidget(self.combo_view)
        layout.addLayout(ctrl)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.lbl_show.setText(tr("Show:"))
        labels = [tr("Liver + Tumors"), tr("Liver Only"), tr("Tumors Only")]
        for idx, label in enumerate(labels):
            self.combo_view.setItemText(idx, label)
        self._refresh_info_label()

    def _refresh_info_label(self):
        self.lbl_info.setText(
            tr("3D info summary").format(
                liver=self._liver_volume_ml,
                tumors=len(self._tumor_masks),
                voxel=self._voxel_size_mm,
            )
        )

    def set_volumes(self, activity, mu_map, liver_mask, tumor_masks, voxel_size_mm: float, liver_volume_ml: float):
        self._liver_mask = liver_mask
        self._tumor_masks = tumor_masks
        self._voxel_size_mm = voxel_size_mm
        self._liver_volume_ml = liver_volume_ml
        self._refresh_info_label()
        self._rerender()

    def _rerender(self):
        if self._liver_mask is None:
            return
        mode = self.combo_view.currentData()
        self.fig.clear()
        ax = self.fig.add_subplot(111, projection="3d", facecolor="#0d1117")
        ax.set_facecolor("#0d1117")
        ax.tick_params(colors="#6b7280", labelsize=7)
        ax.grid(True, color="#2d3139", linewidth=0.5)
        try:
            from skimage.measure import marching_cubes

            if mode in {"liver", "liver_and_tumors"} and self._liver_mask.sum() > 100:
                verts, faces, _, _ = marching_cubes(self._liver_mask.astype(float), level=0.5, step_size=2)
                mesh = Poly3DCollection(verts[faces], alpha=0.22, facecolor="#4fc3f7", edgecolor="none")
                ax.add_collection3d(mesh)
                ax.set_xlim(0, self._liver_mask.shape[0])
                ax.set_ylim(0, self._liver_mask.shape[1])
                ax.set_zlim(0, self._liver_mask.shape[2])
            if mode in {"tumors", "liver_and_tumors"}:
                for tmask in self._tumor_masks:
                    if tmask.sum() > 20:
                        tv, tf, _, _ = marching_cubes(tmask.astype(float), level=0.5, step_size=1)
                        tmesh = Poly3DCollection(tv[tf], alpha=0.8, facecolor="#ff6b6b", edgecolor="none")
                        ax.add_collection3d(tmesh)
            self._refresh_info_label()
        except ImportError:
            ax.text(0.5, 0.5, 0.5, tr("Install scikit-image for 3D view"), ha="center", va="center", color="#6b7280", transform=ax.transAxes)
        ax.set_xlabel("Z", color="#6b7280", fontsize=8)
        ax.set_ylabel("Y", color="#6b7280", fontsize=8)
        ax.set_zlabel("X", color="#6b7280", fontsize=8)
        ax.set_title(tr("3D Phantom"), color="#8b949e", fontsize=10, pad=8)
        self.canvas.draw()


class MetricCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self.setObjectName("metric_card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.value_lbl = QLabel("—")
        self.value_lbl.setObjectName("stat_value")
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("stat_unit")
        layout.addWidget(self.value_lbl)
        layout.addWidget(self.title_lbl)
        self.retranslate_ui()

    def set_value(self, value: str):
        self.value_lbl.setText(value)

    def retranslate_ui(self):
        self.title_lbl.setText(tr(self._title))


class MetricsPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("metrics_panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.title = QLabel("")
        self.title.setObjectName("metrics_panel_title")
        layout.addWidget(self.title)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.cards = {
            "liver": MetricCard("Liver Vol."),
            "left": MetricCard("Left Ratio"),
            "tumors": MetricCard("Tumors"),
            "counts": MetricCard("Total Counts"),
            "perfusion": MetricCard("Perfusion"),
            "geometry": MetricCard("Geometry"),
        }
        order = ["liver", "left", "tumors", "counts", "perfusion", "geometry"]
        positions = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
        for key, (row, col) in zip(order, positions):
            grid.addWidget(self.cards[key], row, col)
        layout.addLayout(grid)
        self.cards["geometry"].set_value("—")
        self.retranslate_ui()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def retranslate_ui(self):
        self.title.setText(tr("Preview Metrics"))
        for card in self.cards.values():
            card.retranslate_ui()


class SliceViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._activity = None
        self._mu_map = None
        self._liver_mask = None
        self._tumor_masks = []
        self._voxel_size_mm = 4.42
        self._liver_volume_ml = 0.0
        self._build_ui()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 0, 0, 8)
        self.lbl_channel = QLabel("")
        self.combo_channel = QComboBox()
        self.combo_channel.addItem("", "activity")
        self.combo_channel.addItem("", "mu")
        self.combo_channel.currentIndexChanged.connect(self._update_all_slices)
        self.lbl_overlay = QLabel("")
        self.combo_overlay = QComboBox()
        for label, data in [
            ("Liver + Tumors", "liver_and_tumors"),
            ("Tumors", "tumors"),
            ("Liver", "liver"),
            ("Contours", "contours"),
            ("No Overlay", "none"),
        ]:
            self.combo_overlay.addItem(label, data)
        self.combo_overlay.currentIndexChanged.connect(self._update_all_slices)
        ctrl.addWidget(self.lbl_channel)
        ctrl.addWidget(self.combo_channel)
        ctrl.addSpacing(12)
        ctrl.addWidget(self.lbl_overlay)
        ctrl.addWidget(self.combo_overlay)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        multi_widget = QWidget()
        multi_layout = QGridLayout(multi_widget)
        multi_layout.setSpacing(4)
        multi_layout.setContentsMargins(0, 0, 0, 0)
        self.axial_view = SinglePlaneView("Axial (Z)")
        self.coronal_view = SinglePlaneView("Coronal (Y)")
        self.sagittal_view = SinglePlaneView("Sagittal (X)")
        self.metrics_panel = MetricsPanel()
        multi_layout.addWidget(self.axial_view, 0, 0)
        multi_layout.addWidget(self.coronal_view, 0, 1)
        multi_layout.addWidget(self.sagittal_view, 1, 0)
        multi_layout.addWidget(self.metrics_panel, 1, 1)
        self.tabs.addTab(multi_widget, "")
        self.surface_view = Surface3DView()
        self.tabs.addTab(self.surface_view, "")
        layout.addWidget(self.tabs, stretch=1)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.lbl_channel.setText(tr("Channel:"))
        self.combo_channel.setItemText(0, tr("Activity Map"))
        self.combo_channel.setItemText(1, tr("mu-map (Attenuation)"))
        self.lbl_overlay.setText(tr("Overlay:"))
        for idx, label in enumerate(["Liver + Tumors", "Tumors", "Liver", "Contours", "No Overlay"]):
            self.combo_overlay.setItemText(idx, tr(label))
        self.tabs.setTabText(0, tr("Multi-Plane"))
        self.tabs.setTabText(1, tr("3D Surface"))
        self.axial_view.retranslate_ui()
        self.coronal_view.retranslate_ui()
        self.sagittal_view.retranslate_ui()
        self.surface_view.retranslate_ui()
        self.metrics_panel.retranslate_ui()

    def _tumor_union(self):
        if not self._tumor_masks:
            return None
        union = np.zeros_like(self._tumor_masks[0], dtype=bool)
        for mask in self._tumor_masks:
            union |= mask.astype(bool)
        return union

    def set_volumes(self, activity, mu_map, liver_mask, tumor_masks, voxel_size_mm: float, liver_volume_ml: float):
        self._activity = activity
        self._mu_map = mu_map
        self._liver_mask = liver_mask
        self._tumor_masks = tumor_masks
        self._voxel_size_mm = voxel_size_mm
        self._liver_volume_ml = liver_volume_ml
        self._update_all_slices()
        self.surface_view.set_volumes(activity, mu_map, liver_mask, tumor_masks, voxel_size_mm, liver_volume_ml)
        self.metrics_panel.cards["liver"].set_value(f"{liver_volume_ml:.0f} {tr('mL')}")
        self.metrics_panel.cards["tumors"].set_value(str(len(tumor_masks)))
        self.metrics_panel.cards["counts"].set_value(f"{activity.sum():.2e}")
        self.metrics_panel.cards["geometry"].set_value(f"{activity.shape[0]}^3 / {voxel_size_mm:.2f} {tr('mm')}")

    def set_meta(self, left_ratio: float, perfusion_mode: str):
        self.metrics_panel.cards["left"].set_value(f"{left_ratio * 100:.1f}%")
        self.metrics_panel.cards["perfusion"].set_value(perfusion_mode)

    def _get_current_volume(self):
        if self.combo_channel.currentData() == "activity":
            return self._activity
        return self._mu_map

    def _update_all_slices(self):
        vol = self._get_current_volume()
        if vol is None:
            return
        liver = self._liver_mask
        tumors = self._tumor_union()
        overlay_mode = self.combo_overlay.currentData()
        self.axial_view.set_volume(vol, liver, tumors, overlay_mode)
        vol_cor = np.transpose(vol, (1, 0, 2))
        liver_cor = np.transpose(liver, (1, 0, 2)) if liver is not None else None
        tumor_cor = np.transpose(tumors, (1, 0, 2)) if tumors is not None else None
        self.coronal_view.set_volume(vol_cor, liver_cor, tumor_cor, overlay_mode)
        vol_sag = np.transpose(vol, (2, 0, 1))
        liver_sag = np.transpose(liver, (2, 0, 1)) if liver is not None else None
        tumor_sag = np.transpose(tumors, (2, 0, 1)) if tumors is not None else None
        self.sagittal_view.set_volume(vol_sag, liver_sag, tumor_sag, overlay_mode)
