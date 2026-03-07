"""
Phantom Page
============
Parameter configuration and single-case preview for phantom generation.
"""

from __future__ import annotations
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QPushButton, QLabel, QGroupBox, QScrollArea,
    QFormLayout, QSpinBox, QDoubleSpinBox, QComboBox,
    QLineEdit, QFileDialog, QMessageBox, QFrame,
    QSizePolicy, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSlot
from PyQt6.QtGui import QFont

from core.phantom_generator import PhantomGenerator, PhantomConfig, PhantomResult
from ui.widgets.slice_viewer import SliceViewer
from ui.widgets.param_widgets import ParamGroup, SpinRow, DoubleSpinRow, RangeRow, LabelRow
from ui.i18n import tr


class GenerateWorker(QThread):
    """Background thread for single phantom generation."""
    finished = pyqtSignal(object)   # PhantomResult
    error = pyqtSignal(str)

    def __init__(self, config: PhantomConfig, case_id: int = 0):
        super().__init__()
        self.config = config
        self.case_id = case_id

    def run(self):
        try:
            gen = PhantomGenerator(self.config)
            result = gen.generate_one(self.case_id)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class PhantomPage(QWidget):
    """Page 1: Phantom parameter configuration and preview."""
    phantom_generated = pyqtSignal(object)      # PhantomResult
    start_batch_requested = pyqtSignal()        # emitted when user clicks Start Batch

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = PhantomConfig()
        self._current_result: PhantomResult | None = None
        self._worker: GenerateWorker | None = None
        self._build_ui()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2d3139; }")

        splitter.addWidget(self._build_param_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([420, 860])
        root.addWidget(splitter)

    def _build_param_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(380)
        panel.setMaximumWidth(480)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel(tr("Phantom Configuration"))
        title.setObjectName("page_title")
        layout.addWidget(title)

        # Scrollable params
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(10)
        scroll_layout.setContentsMargins(0, 0, 8, 0)

        # ── Volume ──
        vol_grp = ParamGroup(tr("VOLUME"))
        self.spin_nx = SpinRow("Matrix size", 32, 512, self._config.volume_shape[0])
        self.spin_nx.setToolTip(
            tr("Volume matrix size (NxNxN).\n"
               "Default 128 \u2192 128\xb3 voxels at 4.42 mm = ~56 cm FOV.")
        )
        self.spin_vox = DoubleSpinRow("Voxel size (mm)", 0.5, 10.0, self._config.voxel_size_mm, 2)
        self.spin_vox.setToolTip(
            tr("Isotropic voxel size in mm.\n"
               "4.42 mm matches standard SPECT acquisition (GE 870 CZT).")
        )
        vol_grp.add_row(tr("Matrix (NxNxN)"), self.spin_nx)
        vol_grp.add_row(tr("Voxel size (mm)"), self.spin_vox)
        scroll_layout.addWidget(vol_grp)

        # ── Liver Geometry ──
        liver_grp = ParamGroup(tr("LIVER GEOMETRY"))

        self.spin_scale_jitter = DoubleSpinRow(
            "Scale jitter", 0.0, 0.5, self._config.scale_jitter, 2)
        self.spin_scale_jitter.setToolTip(
            tr("Random scaling of liver shape per case (\xb1fraction).\n"
               "0.10 means \xb110% size variation across cases.")
        )
        self.spin_rot_jitter = DoubleSpinRow(
            "Rotation jitter (\xb0)", 0.0, 30.0, self._config.rot_jitter_deg, 1)
        self.spin_rot_jitter.setToolTip(
            tr("Random rotation of liver shape per case (degrees).\n"
               "Adds anatomical variety in orientation.")
        )
        self.spin_shift_range = DoubleSpinRow(
            "Global shift range", 0.0, 0.3, self._config.global_shift_range, 3)
        self.spin_shift_range.setToolTip(
            tr("Random translation of the entire phantom (normalized units).\n"
               "0.05 \u2248 1.4 cm shift. Simulates patient positioning variation.")
        )
        self.spin_left_ratio = DoubleSpinRow(
            "Target left lobe ratio", 0.1, 0.6, self._config.target_left_ratio, 2)
        self.spin_left_ratio.setToolTip(
            tr("Target fraction of liver volume assigned to the left lobe (Cantlie plane).\n"
               "Normal adult range: 0.25\u20130.35. Actual value varies per case via jitter.")
        )
        self.spin_smooth = DoubleSpinRow(
            "Smoothing \u03c3 (px)", 0.0, 5.0, self._config.smooth_sigma, 1)
        self.spin_smooth.setToolTip(
            tr("Gaussian smoothing of tissue boundaries (pixels).\n"
               "0 = sharp edges, 1.0 = realistic smooth liver surface.")
        )

        liver_grp.add_row(tr("Scale jitter"), self.spin_scale_jitter)
        liver_grp.add_row(tr("Rotation jitter (\xb0)"), self.spin_rot_jitter)
        liver_grp.add_row(tr("Global shift range"), self.spin_shift_range)
        liver_grp.add_row(tr("Target left ratio"), self.spin_left_ratio)
        liver_grp.add_row(tr("Smoothing \u03c3 (px)"), self.spin_smooth)
        scroll_layout.addWidget(liver_grp)

        # ── Tumors ──
        tumor_grp = ParamGroup(tr("TUMORS"))

        self.spin_tumor_min = SpinRow("Min tumors", 0, 10, self._config.tumor_count_min)
        self.spin_tumor_min.setToolTip(
            tr("Minimum number of tumors per case.\n"
               "Setting to 1 ensures every case contains at least one tumor\n"
               "for supervised deep-learning training.")
        )
        self.spin_tumor_max = SpinRow("Max tumors", 0, 20, self._config.tumor_count_max)
        self.spin_tumor_max.setToolTip(
            tr("Maximum number of tumors per case.\n"
               "Drawn uniformly from [Min, Max] for each case.")
        )
        self.spin_contrast_min = DoubleSpinRow(
            "Contrast min", 1.0, 20.0, self._config.tumor_contrast_min, 1)
        self.spin_contrast_min.setToolTip(
            tr("Minimum tumor-to-normal liver uptake ratio (T/N).\n"
               "Ho et al. 1997 hepatocellular carcinoma data: typical T/N = 2.0\u20138.0.")
        )
        self.spin_contrast_max = DoubleSpinRow(
            "Contrast max", 1.0, 20.0, self._config.tumor_contrast_max, 1)
        self.spin_contrast_max.setToolTip(
            tr("Maximum tumor-to-normal liver uptake ratio (T/N).\n"
               "Each tumor independently samples from [Contrast min, Contrast max].")
        )

        tumor_grp.add_row(tr("Min tumors"), self.spin_tumor_min)
        tumor_grp.add_row(tr("Max tumors"), self.spin_tumor_max)
        tumor_grp.add_row(tr("Contrast min (T/L)"), self.spin_contrast_min)
        tumor_grp.add_row(tr("Contrast max (T/L)"), self.spin_contrast_max)
        scroll_layout.addWidget(tumor_grp)

        # ── Activity ──
        act_grp = ParamGroup(tr("ACTIVITY"))

        self.spin_counts = DoubleSpinRow(
            "Total counts (\xd710\u2074)", 1.0, 500.0, self._config.total_counts / 1e4, 1)
        self.spin_counts.setToolTip(
            tr("Total activity counts in the phantom (\xd710\u2074).\n"
               "Scales the activity map amplitude. Does not affect SIMIND photon histories.")
        )
        self.spin_psf = DoubleSpinRow("PSF \u03c3 (px)", 0.0, 8.0, self._config.psf_sigma_px, 1)
        self.spin_psf.setToolTip(
            tr("Point spread function blur applied to the activity map (pixels).\n"
               "0 = no blur. SIMIND handles physical PSF; set to 0 for physics-accurate simulation.")
        )
        self.spin_residual = DoubleSpinRow(
            "Residual BG", 0.0, 0.5, self._config.residual_bg, 2)
        self.spin_residual.setToolTip(
            tr("Fraction of mean liver activity added as uniform whole-body background.\n"
               "Models non-specific Tc-99m uptake in surrounding tissue (0.05\u20130.15 typical).")
        )

        act_grp.add_row(tr("Total counts (\xd710\u2074)"), self.spin_counts)
        act_grp.add_row(tr("PSF \u03c3 (px)"), self.spin_psf)
        act_grp.add_row(tr("Residual BG"), self.spin_residual)
        scroll_layout.addWidget(act_grp)

        # ── Batch ──
        batch_grp = ParamGroup(tr("BATCH GENERATION"))

        self.spin_n_cases = SpinRow("Number of cases", 1, 10000, self._config.n_cases)
        self.spin_n_cases.setToolTip(
            tr("Total cases to generate in this batch.\n"
               "Each case gets a unique random seed and independent anatomy.")
        )
        self.spin_seed = SpinRow("Global seed", 0, 999999, self._config.global_seed)
        self.spin_seed.setToolTip(
            tr("Base random seed for reproducibility.\n"
               "Same seed + same config \u2192 identical batch output.")
        )
        self.chk_use_seed = QCheckBox(tr("Use fixed seed"))
        self.chk_use_seed.setChecked(self._config.use_global_seed)
        self.chk_use_seed.setToolTip(
            tr("If checked, use the Global seed for deterministic output.\n"
               "Uncheck for fully random batches (seed changes each run).")
        )

        self.edit_output = QLineEdit(self._config.output_dir)
        self.edit_output.setPlaceholderText("Output directory path...")
        self.edit_output.setToolTip(
            tr("Directory where case_XXXX.npz files will be saved.\n"
               "Created automatically if it does not exist.")
        )
        btn_browse = QPushButton(tr("Browse..."))
        btn_browse.clicked.connect(self._browse_output)
        out_row = QHBoxLayout()
        out_row.addWidget(self.edit_output)
        out_row.addWidget(btn_browse)
        out_widget = QWidget()
        out_widget.setLayout(out_row)

        batch_grp.add_row(tr("Number of cases"), self.spin_n_cases)
        batch_grp.add_row(tr("Global seed"), self.spin_seed)
        batch_grp.add_widget(self.chk_use_seed)
        batch_grp.add_row(tr("Output directory"), out_widget)
        scroll_layout.addWidget(batch_grp)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        # ── Buttons ──
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_preview = QPushButton(tr("⬡  Preview Single Case"))
        self.btn_preview.setObjectName("primary_btn")
        self.btn_preview.setMinimumHeight(38)
        self.btn_preview.setToolTip(
            tr("Generate one phantom with the current settings and display it in the viewer.\n"
               "Uses case_id=0 and the configured seed.")
        )
        self.btn_preview.clicked.connect(self._on_preview)

        self.btn_start_batch = QPushButton(tr("▶  Start Batch"))
        self.btn_start_batch.setObjectName("success_btn")
        self.btn_start_batch.setMinimumHeight(38)
        self.btn_start_batch.setToolTip(
            tr("Navigate to Results page and start generating all cases.\n"
               "Number of cases is set by 'Number of cases' above.")
        )
        self.btn_start_batch.clicked.connect(self._on_start_batch)

        btn_io = QHBoxLayout()
        btn_save_cfg = QPushButton(tr("Save Config"))
        btn_load_cfg = QPushButton(tr("Load Config"))
        btn_save_cfg.clicked.connect(self._save_config)
        btn_load_cfg.clicked.connect(self._load_config)
        btn_io.addWidget(btn_save_cfg)
        btn_io.addWidget(btn_load_cfg)

        btn_layout.addWidget(self.btn_preview)
        btn_layout.addWidget(self.btn_start_batch)
        btn_layout.addLayout(btn_io)
        layout.addLayout(btn_layout)

        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Title row
        title_row = QHBoxLayout()
        title = QLabel(tr("Preview"))
        title.setObjectName("page_title")
        self.lbl_case_info = QLabel(tr("No phantom generated yet"))
        self.lbl_case_info.setStyleSheet("color: #6b7280; font-size: 12px;")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.lbl_case_info)
        layout.addLayout(title_row)

        # Slice viewer
        self.slice_viewer = SliceViewer()
        layout.addWidget(self.slice_viewer, stretch=1)

        # Stats row
        self.stats_bar = self._build_stats_bar()
        layout.addWidget(self.stats_bar)

        return panel

    def _build_stats_bar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet("background-color: #252a33; border-radius: 8px; padding: 4px;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(32)

        self.stat_labels = {}
        stats = [
            (tr("Liver Vol."), "vol", "mL"),
            (tr("Left Ratio"), "left", "%"),
            (tr("Tumors"), "n_tumors", ""),
            (tr("Total Counts"), "counts", ""),
            (tr("Gen. Time"), "time", "s"),
        ]
        for name, key, unit in stats:
            col = QVBoxLayout()
            col.setSpacing(2)
            val_lbl = QLabel("\u2014")
            val_lbl.setObjectName("stat_value")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl = QLabel(f"{name}" + (f" ({unit})" if unit else ""))
            name_lbl.setObjectName("stat_unit")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(val_lbl)
            col.addWidget(name_lbl)
            self.stat_labels[key] = val_lbl
            layout.addLayout(col)
            if name != stats[-1][0]:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("background-color: #2d3139;")
                layout.addWidget(sep)

        return bar

    # ── Actions ──────────────────────────────────────────────────────

    def _collect_config(self) -> PhantomConfig:
        cfg = PhantomConfig()
        n = self.spin_nx.value()
        cfg.volume_shape = (n, n, n)
        cfg.voxel_size_mm = self.spin_vox.value()
        cfg.scale_jitter = self.spin_scale_jitter.value()
        cfg.rot_jitter_deg = self.spin_rot_jitter.value()
        cfg.global_shift_range = self.spin_shift_range.value()
        cfg.target_left_ratio = self.spin_left_ratio.value()
        cfg.smooth_sigma = self.spin_smooth.value()
        cfg.tumor_count_min = self.spin_tumor_min.value()
        cfg.tumor_count_max = self.spin_tumor_max.value()
        cfg.tumor_contrast_min = self.spin_contrast_min.value()
        cfg.tumor_contrast_max = self.spin_contrast_max.value()
        cfg.total_counts = self.spin_counts.value() * 1e4
        cfg.psf_sigma_px = self.spin_psf.value()
        cfg.residual_bg = self.spin_residual.value()
        cfg.n_cases = self.spin_n_cases.value()
        cfg.global_seed = self.spin_seed.value()
        cfg.use_global_seed = self.chk_use_seed.isChecked()
        cfg.output_dir = self.edit_output.text()
        return cfg

    def _on_preview(self):
        if self._worker and self._worker.isRunning():
            return
        self._config = self._collect_config()
        self.btn_preview.setEnabled(False)
        self.btn_preview.setText(tr("Generating..."))
        self._worker = GenerateWorker(self._config, case_id=0)
        self._worker.finished.connect(self._on_preview_done)
        self._worker.error.connect(self._on_preview_error)
        self._worker.start()

    def _on_start_batch(self):
        """Collect config and signal MainWindow to navigate to Results + start batch."""
        self._config = self._collect_config()
        self.start_batch_requested.emit()

    @pyqtSlot(object)
    def _on_preview_done(self, result: PhantomResult):
        self._current_result = result
        self.btn_preview.setEnabled(True)
        self.btn_preview.setText(tr("⬡  Preview Single Case"))

        self.slice_viewer.set_volumes(
            activity=result.activity,
            mu_map=result.mu_map,
            liver_mask=result.liver_mask,
            tumor_masks=result.tumor_masks
        )
        self.slice_viewer.set_meta(result.left_ratio, result.perfusion_mode)

        self.stat_labels["vol"].setText(f"{result.liver_volume_ml:.0f}")
        self.stat_labels["left"].setText(f"{result.left_ratio * 100:.1f}")
        self.stat_labels["n_tumors"].setText(str(result.n_tumors))
        self.stat_labels["counts"].setText(f"{result.total_counts_actual:.2e}")
        self.stat_labels["time"].setText(f"{result.generation_time_s:.2f}")

        self.lbl_case_info.setText(
            f"Seed: {result.seed}  |  Perfusion: {result.perfusion_mode}  |  "
            f"Tumors: {result.n_tumors}"
        )

        self.phantom_generated.emit(result)

    @pyqtSlot(str)
    def _on_preview_error(self, msg: str):
        self.btn_preview.setEnabled(True)
        self.btn_preview.setText(tr("⬡  Preview Single Case"))
        QMessageBox.critical(self, "Generation Error", f"Failed to generate phantom:\n{msg}")

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if path:
            self.edit_output.setText(path)

    def _save_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Configuration", "", "JSON Files (*.json)"
        )
        if path:
            cfg = self._collect_config()
            cfg.save(Path(path))

    def _load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Configuration", "", "JSON Files (*.json)"
        )
        if path:
            try:
                cfg = PhantomConfig.load(Path(path))
                self._apply_config_to_ui(cfg)
            except Exception as e:
                QMessageBox.critical(self, "Load Error", str(e))

    def _apply_config_to_ui(self, cfg: PhantomConfig):
        self.spin_nx.setValue(cfg.volume_shape[0])
        self.spin_vox.setValue(cfg.voxel_size_mm)
        self.spin_scale_jitter.setValue(cfg.scale_jitter)
        self.spin_rot_jitter.setValue(cfg.rot_jitter_deg)
        self.spin_shift_range.setValue(cfg.global_shift_range)
        self.spin_left_ratio.setValue(cfg.target_left_ratio)
        self.spin_smooth.setValue(cfg.smooth_sigma)
        self.spin_tumor_min.setValue(cfg.tumor_count_min)
        self.spin_tumor_max.setValue(cfg.tumor_count_max)
        self.spin_contrast_min.setValue(cfg.tumor_contrast_min)
        self.spin_contrast_max.setValue(cfg.tumor_contrast_max)
        self.spin_counts.setValue(cfg.total_counts / 1e4)
        self.spin_psf.setValue(cfg.psf_sigma_px)
        self.spin_residual.setValue(cfg.residual_bg)
        self.spin_n_cases.setValue(cfg.n_cases)
        self.spin_seed.setValue(cfg.global_seed)
        self.chk_use_seed.setChecked(cfg.use_global_seed)
        self.edit_output.setText(cfg.output_dir)
