"""
Generate workspace with preview and batch monitoring.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QFileDialog,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.parameter_specs import NUMERIC_SPECS, PERFUSION_MODE_OPTIONS, TUMOR_MODE_OPTIONS
from core.phantom_generator import PhantomConfig, PhantomGenerator, PhantomResult, PreviewOverrides
from core.validation import validate_phantom_config
from ui.app_state import AppState
from ui.i18n import language_manager, tr
from ui.pages.results_page import ResultsPage
from ui.widgets.param_widgets import EnumControl, LabeledCheck, ParamGroup, SliderSpinControl
from ui.widgets.slice_viewer import SliceViewer

_VOLUME_MATRIX_OPTIONS = [64, 96, 128, 192, 256]
_VOLUME_VOXEL_OPTIONS = [1.95, 2.2, 3.9, 4.42, 4.8]


class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


class GenerateWorker(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: PhantomConfig, overrides: PreviewOverrides | None = None, case_id: int = 0):
        super().__init__()
        self.config = config
        self.overrides = overrides
        self.case_id = case_id

    def run(self):
        try:
            gen = PhantomGenerator(self.config)
            result = gen.generate_one(self.case_id, overrides=self.overrides)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class PhantomPage(QWidget):
    phantom_generated = pyqtSignal(object)

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._config = app_state.phantom_config
        self._worker: GenerateWorker | None = None
        self._last_settings_output = app_state.settings.default_output
        self._field_labels: list[tuple[QLabel, str]] = []
        self._build_ui()
        self._apply_config_to_ui(self._config)
        self._update_validation_banner(validate_phantom_config(self._config))
        self._app_state.settings_changed.connect(self._on_settings_changed)
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.preview_tab = self._build_preview_tab()
        self.batch_monitor = ResultsPage(self._app_state, include_output_viewer=False)
        self.tabs.addTab(self.preview_tab, "")
        self.tabs.addTab(self.batch_monitor, "")
        root.addWidget(self.tabs)
        self.retranslate_ui()

    def _build_preview_tab(self) -> QWidget:
        tab = QWidget()
        root = QHBoxLayout(tab)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.addWidget(self._build_param_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([400, 980])
        root.addWidget(splitter)
        return tab

    def _build_param_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(360)
        panel.setMaximumWidth(500)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("page_title")
        layout.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel()
        self.lbl_subtitle.setObjectName("page_subtitle")
        self.lbl_subtitle.setWordWrap(True)
        layout.addWidget(self.lbl_subtitle)

        self.lbl_validation = QLabel("")
        self.lbl_validation.setObjectName("validation_banner")
        self.lbl_validation.setWordWrap(True)
        layout.addWidget(self.lbl_validation)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll_layout = QVBoxLayout(content)
        scroll_layout.setSpacing(10)
        scroll_layout.setContentsMargins(0, 0, 8, 0)

        self.volume_group = ParamGroup("VOLUME")
        self.chk_volume_advanced = LabeledCheck("")
        self.chk_volume_advanced.toggled.connect(self._apply_advanced_states)
        self.volume_group.set_header_widget(self.chk_volume_advanced)
        self.ctrl_matrix = SliderSpinControl(
            NUMERIC_SPECS["matrix_size"],
            self._config.volume_shape[0],
            discrete_values=_VOLUME_MATRIX_OPTIONS,
        )
        self.ctrl_voxel = SliderSpinControl(
            NUMERIC_SPECS["voxel_size_mm"],
            self._config.voxel_size_mm,
            discrete_values=_VOLUME_VOXEL_OPTIONS,
        )
        self.volume_group.add_row("Matrix (NxNxN)", self.ctrl_matrix, NUMERIC_SPECS["matrix_size"].description)
        self.volume_group.add_row("Voxel size (mm)", self.ctrl_voxel, NUMERIC_SPECS["voxel_size_mm"].description)
        scroll_layout.addWidget(self.volume_group)

        self.liver_group = ParamGroup("LIVER GEOMETRY")
        self.chk_liver_advanced = LabeledCheck("")
        self.chk_liver_advanced.toggled.connect(self._apply_advanced_states)
        self.liver_group.set_header_widget(self.chk_liver_advanced)
        self.ctrl_scale = SliderSpinControl(NUMERIC_SPECS["scale_jitter"], self._config.scale_jitter)
        self.ctrl_rot = SliderSpinControl(NUMERIC_SPECS["rot_jitter_deg"], self._config.rot_jitter_deg)
        self.ctrl_shift = SliderSpinControl(NUMERIC_SPECS["global_shift_range"], self._config.global_shift_range)
        self.ctrl_left_ratio = SliderSpinControl(NUMERIC_SPECS["target_left_ratio"], self._config.target_left_ratio)
        self.ctrl_smooth = SliderSpinControl(NUMERIC_SPECS["smooth_sigma"], self._config.smooth_sigma)
        self.liver_group.add_row("Scale jitter", self.ctrl_scale, NUMERIC_SPECS["scale_jitter"].description)
        self.liver_group.add_row("Rotation jitter (°)", self.ctrl_rot, NUMERIC_SPECS["rot_jitter_deg"].description)
        self.liver_group.add_row("Global shift range", self.ctrl_shift, NUMERIC_SPECS["global_shift_range"].description)
        self.liver_group.add_row("Target left ratio", self.ctrl_left_ratio, NUMERIC_SPECS["target_left_ratio"].description)
        self.liver_group.add_row("Smoothing σ (px)", self.ctrl_smooth, NUMERIC_SPECS["smooth_sigma"].description)
        scroll_layout.addWidget(self.liver_group)

        self.tumor_group = ParamGroup("TUMORS")
        self.chk_tumor_advanced = LabeledCheck("")
        self.chk_tumor_advanced.toggled.connect(self._apply_advanced_states)
        self.tumor_group.set_header_widget(self.chk_tumor_advanced)
        self.ctrl_tumor_count = SliderSpinControl(NUMERIC_SPECS["tumor_count"], self._config.tumor_count_min)
        self.ctrl_contrast = SliderSpinControl(NUMERIC_SPECS["tumor_contrast"], self._config.tumor_contrast_min)
        self.ctrl_tumor_mode = EnumControl([(data, tr(label)) for data, label in TUMOR_MODE_OPTIONS])
        self.ctrl_tumor_mode.setToolTip(tr("Choose a fixed tumor morphology for the whole batch or leave it random."))
        self.tumor_group.add_row("Tumor count", self.ctrl_tumor_count, NUMERIC_SPECS["tumor_count"].description)
        self.tumor_group.add_row("Tumor contrast (T/L)", self.ctrl_contrast, NUMERIC_SPECS["tumor_contrast"].description)
        self.tumor_group.add_row("Tumor style", self.ctrl_tumor_mode, "Choose a fixed tumor morphology for the whole batch or leave it random.")
        scroll_layout.addWidget(self.tumor_group)

        self.activity_group = ParamGroup("ACTIVITY")
        self.chk_activity_advanced = LabeledCheck("")
        self.chk_activity_advanced.toggled.connect(self._apply_advanced_states)
        self.activity_group.set_header_widget(self.chk_activity_advanced)
        self.ctrl_counts = SliderSpinControl(NUMERIC_SPECS["total_counts"], self._config.total_counts / 1e4)
        self.ctrl_residual = SliderSpinControl(NUMERIC_SPECS["residual_bg"], self._config.residual_bg)
        self.ctrl_perfusion = EnumControl([(data, tr(label)) for data, label in PERFUSION_MODE_OPTIONS])
        self.ctrl_perfusion.setToolTip(tr("Choose which liver region stays active; random uses the algorithm probabilities."))
        self.activity_group.add_row("Total counts (×10⁴)", self.ctrl_counts, NUMERIC_SPECS["total_counts"].description)
        self.activity_group.add_row("Residual BG", self.ctrl_residual, NUMERIC_SPECS["residual_bg"].description)
        self.activity_group.add_row("Perfusion mode", self.ctrl_perfusion, "Choose which liver region stays active; random uses the algorithm probabilities.")
        scroll_layout.addWidget(self.activity_group)

        scroll_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        self.btn_preview = QPushButton()
        self.btn_preview.setObjectName("primary_btn")
        self.btn_preview.setMinimumHeight(38)
        self.btn_preview.clicked.connect(self._on_preview)
        self.btn_save_cfg = QPushButton()
        self.btn_load_cfg = QPushButton()
        self.btn_save_cfg.clicked.connect(self._save_config)
        self.btn_load_cfg.clicked.connect(self._load_config)
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_save_cfg)
        btn_row.addWidget(self.btn_load_cfg)
        layout.addLayout(btn_row)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        self.lbl_preview_title = QLabel()
        self.lbl_preview_title.setObjectName("page_title")
        title_row.addWidget(self.lbl_preview_title)
        title_row.addStretch()
        layout.addLayout(title_row)

        self.slice_viewer = SliceViewer()
        layout.addWidget(self.slice_viewer, stretch=1)

        batch_bar = QFrame()
        batch_bar.setObjectName("batch_bar")
        batch_layout = QVBoxLayout(batch_bar)
        batch_layout.setContentsMargins(14, 12, 14, 12)
        batch_layout.setSpacing(8)

        form_row = QHBoxLayout()
        form_row.setSpacing(10)
        self.spin_n_cases = _NoWheelSpinBox()
        self.spin_n_cases.setRange(1, 10000)
        self.spin_n_cases.setValue(self._config.n_cases)
        form_row.addWidget(self._field("Number of cases", self.spin_n_cases))

        self.chk_use_seed = QCheckBox()
        seed_box = QWidget()
        seed_box_layout = QHBoxLayout(seed_box)
        seed_box_layout.setContentsMargins(0, 0, 0, 0)
        seed_box_layout.setSpacing(0)
        seed_box_layout.addWidget(self.chk_use_seed)
        seed_box_layout.addStretch()
        form_row.addWidget(self._field("Use fixed seed", seed_box))

        self.edit_seed = QLineEdit(str(self._config.global_seed))
        self.edit_seed.setValidator(QIntValidator(0, 2**31 - 1, self.edit_seed))
        form_row.addWidget(self._field("Global seed", self.edit_seed))
        batch_layout.addLayout(form_row)

        out_row = QHBoxLayout()
        self.edit_output = QLineEdit(self._config.output_dir)
        self.edit_output.setMinimumHeight(34)
        self.btn_output = QPushButton()
        self.btn_output.setMinimumHeight(34)
        self.btn_output.clicked.connect(self._browse_output)
        self.btn_start_batch = QPushButton()
        self.btn_start_batch.setObjectName("success_btn")
        self.btn_start_batch.setMinimumHeight(38)
        self.btn_start_batch.clicked.connect(self._on_start_batch)
        out_row.addWidget(self._field("Output directory", self.edit_output), stretch=1)
        out_row.addWidget(self._field(None, self.btn_output))
        out_row.addWidget(self._field(None, self.btn_start_batch))
        batch_layout.addLayout(out_row)
        layout.addWidget(batch_bar)
        return panel

    def _field(self, label_key: str | None, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        lbl = QLabel(" ")
        lbl.setObjectName("field_label")
        if label_key:
            lbl.setText(tr(label_key))
            lbl.setProperty("tr_key", label_key)
            self._field_labels.append((lbl, label_key))
        layout.addWidget(lbl)
        layout.addWidget(widget)
        return container

    def retranslate_ui(self):
        self.tabs.setTabText(0, tr("Preview"))
        self.tabs.setTabText(1, tr("Batch Monitor"))
        self.lbl_title.setText(tr("Generate"))
        self.lbl_subtitle.setText(tr("Tune one phantom, validate the workflow, then launch the reproducible batch in the second tab."))
        self.lbl_preview_title.setText(tr("Preview"))
        self.btn_preview.setText(tr("⬡  Preview Single Case"))
        self.btn_save_cfg.setText(tr("Save Config"))
        self.btn_load_cfg.setText(tr("Load Config"))
        self.btn_start_batch.setText(tr("▶  Start Batch"))
        self.btn_output.setText(tr("Browse..."))
        self.edit_output.setPlaceholderText(tr("Output directory path..."))

        for chk in [self.chk_volume_advanced, self.chk_liver_advanced, self.chk_tumor_advanced, self.chk_activity_advanced]:
            chk.setText("")
            chk.setToolTip(tr("Enable advanced range override"))

        self.volume_group.set_description("Configure output matrix and voxel size for spatial sampling.")
        self.liver_group.set_description("Adjust global liver geometry randomness and lobe proportion.")
        self.tumor_group.set_description("Set tumor count/contrast and morphology policy for generation.")
        self.activity_group.set_description("Control total counts, residual background, and perfusion strategy.")

        self.ctrl_tumor_mode.clear_and_set_items([(data, tr(label)) for data, label in TUMOR_MODE_OPTIONS])
        self.ctrl_perfusion.clear_and_set_items([(data, tr(label)) for data, label in PERFUSION_MODE_OPTIONS])

        self.volume_group.setTitle(tr("VOLUME"))
        self.liver_group.setTitle(tr("LIVER GEOMETRY"))
        self.tumor_group.setTitle(tr("TUMORS"))
        self.activity_group.setTitle(tr("ACTIVITY"))

        self.volume_group.retranslate_rows()
        self.liver_group.retranslate_rows()
        self.tumor_group.retranslate_rows()
        self.activity_group.retranslate_rows()

        self.ctrl_tumor_mode.setToolTip(tr("Choose a fixed tumor morphology for the whole batch or leave it random."))
        self.ctrl_perfusion.setToolTip(tr("Choose which liver region stays active; random uses the algorithm probabilities."))

        for label, key in self._field_labels:
            label.setText(tr(key))

        self._update_validation_banner(validate_phantom_config(self._collect_config()))

    def _apply_advanced_states(self):
        self.ctrl_matrix.set_advanced(self.chk_volume_advanced.isChecked())
        self.ctrl_voxel.set_advanced(self.chk_volume_advanced.isChecked())

        for ctrl in [self.ctrl_scale, self.ctrl_rot, self.ctrl_shift, self.ctrl_left_ratio, self.ctrl_smooth]:
            ctrl.set_advanced(self.chk_liver_advanced.isChecked())

        for ctrl in [self.ctrl_tumor_count, self.ctrl_contrast]:
            ctrl.set_advanced(self.chk_tumor_advanced.isChecked())

        for ctrl in [self.ctrl_counts, self.ctrl_residual]:
            ctrl.set_advanced(self.chk_activity_advanced.isChecked())

    def _collect_config(self) -> PhantomConfig:
        cfg = PhantomConfig.from_dict(self._config.to_dict())
        n = int(self.ctrl_matrix.value())
        cfg.volume_shape = (n, n, n)
        cfg.voxel_size_mm = float(self.ctrl_voxel.value())
        cfg.scale_jitter = float(self.ctrl_scale.value())
        cfg.rot_jitter_deg = float(self.ctrl_rot.value())
        cfg.global_shift_range = float(self.ctrl_shift.value())
        cfg.target_left_ratio = float(self.ctrl_left_ratio.value())
        cfg.smooth_sigma = float(self.ctrl_smooth.value())

        # Tumor count and contrast sliders are preview-only controls.
        # Batch generation uses tumor_count_min/max and tumor_contrast_min/max
        # from the config as fixed range parameters — sliders do NOT touch them.
        # Guard: if an old bug collapsed min==max, restore the defaults.
        if cfg.tumor_count_max <= cfg.tumor_count_min:
            cfg.tumor_count_max = 5
        if cfg.tumor_contrast_max <= cfg.tumor_contrast_min:
            cfg.tumor_contrast_max = 8.0

        cfg.tumor_mode_policy = self.ctrl_tumor_mode.value()
        cfg.total_counts = float(self.ctrl_counts.value()) * 1e4
        cfg.residual_bg = float(self.ctrl_residual.value())
        cfg.perfusion_mode_policy = self.ctrl_perfusion.value()
        cfg.n_cases = int(self.spin_n_cases.value())
        seed_text = self.edit_seed.text().strip()
        cfg.global_seed = int(seed_text) if seed_text else 0
        cfg.use_global_seed = self.chk_use_seed.isChecked()
        cfg.output_dir = self.edit_output.text().strip()
        return cfg

    def _collect_preview_overrides(self) -> PreviewOverrides:
        perfusion_map = {
            "whole_liver": "Whole Liver",
            "tumor_only": "Tumor Only",
            "left_only": "Left Only",
            "right_only": "Right Only",
        }
        perfusion_value = self.ctrl_perfusion.value()
        return PreviewOverrides(
            exact_tumor_count=int(self.ctrl_tumor_count.value()),
            exact_tumor_contrast=float(self.ctrl_contrast.value()),
            tumor_mode=None if self.ctrl_tumor_mode.value() == "random" else self.ctrl_tumor_mode.value(),
            perfusion_mode=None if perfusion_value == "random" else perfusion_map[perfusion_value],
        )

    def _validate_current_config(self, action: str, preview: bool = False) -> bool:
        self._config = self._collect_config()
        overrides = self._collect_preview_overrides() if preview else None
        report = validate_phantom_config(self._config, preview=overrides)
        self._update_validation_banner(report)
        self._app_state.set_phantom_config(self._config)
        if report.errors:
            title = tr("Preview blocked") if action == "Preview" else tr("Batch start blocked")
            QMessageBox.warning(self, title, report.to_message())
            return False
        return True

    def _update_validation_banner(self, report):
        if report.errors:
            self.lbl_validation.setText(tr("Validation blocked.") + "\n" + report.to_message())
            state = "error"
        elif report.warnings:
            self.lbl_validation.setText(tr("Compatibility warning.") + "\n" + report.to_message())
            state = "warning"
        else:
            self.lbl_validation.setText(tr("Ready. Current phantom configuration is valid for preview and batch generation."))
            state = "ok"

        self.lbl_validation.setProperty("state", state)
        self.lbl_validation.style().unpolish(self.lbl_validation)
        self.lbl_validation.style().polish(self.lbl_validation)

    def _on_preview(self):
        if self._worker and self._worker.isRunning():
            return
        if not self._validate_current_config("Preview", preview=True):
            return
        self.btn_preview.setEnabled(False)
        self.btn_preview.setText(tr("Generating..."))
        overrides = self._collect_preview_overrides()
        self._worker = GenerateWorker(self._config, overrides=overrides, case_id=0)
        self._worker.finished.connect(self._on_preview_done)
        self._worker.error.connect(self._on_preview_error)
        self._worker.start()

    def _on_start_batch(self):
        if not self._validate_current_config("Batch start", preview=False):
            return
        if self._app_state.settings.autosave_config:
            self._autosave_batch_config(self._config)
        self.tabs.setCurrentIndex(1)
        self.batch_monitor.start_batch()

    @pyqtSlot(object)
    def _on_preview_done(self, result: PhantomResult):
        self._app_state.set_preview_result(result)
        self.btn_preview.setEnabled(True)
        self.btn_preview.setText(tr("⬡  Preview Single Case"))
        self.slice_viewer.set_volumes(
            activity=result.activity,
            mu_map=result.mu_map,
            liver_mask=result.liver_mask,
            tumor_masks=result.tumor_masks,
            voxel_size_mm=result.voxel_size_mm,
            liver_volume_ml=result.liver_volume_ml,
        )
        self.slice_viewer.set_meta(result.left_ratio, tr(result.perfusion_mode))
        self.phantom_generated.emit(result)

    @pyqtSlot(str)
    def _on_preview_error(self, msg: str):
        self.btn_preview.setEnabled(True)
        self.btn_preview.setText(tr("⬡  Preview Single Case"))
        QMessageBox.critical(self, tr("Generation Error"), f"{tr('Failed to generate phantom:')}\n{msg}")

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, tr("Select Output Directory"))
        if path:
            self.edit_output.setText(path)

    def _save_config(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Save Configuration"), "", "JSON Files (*.json)")
        if path:
            self._collect_config().save(Path(path))

    def _load_config(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Load Configuration"), "", "JSON Files (*.json)")
        if path:
            try:
                cfg = PhantomConfig.load(Path(path))
                self._apply_config_to_ui(cfg)
                self._config = cfg
                self._app_state.set_phantom_config(cfg)
                self._update_validation_banner(validate_phantom_config(cfg))
            except Exception as exc:
                QMessageBox.critical(self, tr("Load Error"), str(exc))

    def _mark_advanced_from_config(self, cfg: PhantomConfig):
        self.chk_volume_advanced.setChecked(
            cfg.volume_shape[0] not in _VOLUME_MATRIX_OPTIONS
            or all(abs(cfg.voxel_size_mm - v) > 1e-3 for v in _VOLUME_VOXEL_OPTIONS)
        )

        liver_checks = [
            ("scale_jitter", cfg.scale_jitter),
            ("rot_jitter_deg", cfg.rot_jitter_deg),
            ("global_shift_range", cfg.global_shift_range),
            ("target_left_ratio", cfg.target_left_ratio),
            ("smooth_sigma", cfg.smooth_sigma),
        ]
        self.chk_liver_advanced.setChecked(
            any(v < NUMERIC_SPECS[k].recommended_min or v > NUMERIC_SPECS[k].recommended_max for k, v in liver_checks)
        )

        tumor_value_checks = [
            ("tumor_count", float(cfg.tumor_count_min)),
            ("tumor_contrast", float(cfg.tumor_contrast_min)),
        ]
        self.chk_tumor_advanced.setChecked(
            any(v < NUMERIC_SPECS[k].recommended_min or v > NUMERIC_SPECS[k].recommended_max for k, v in tumor_value_checks)
        )

        activity_checks = [
            ("total_counts", cfg.total_counts / 1e4),
            ("residual_bg", cfg.residual_bg),
        ]
        self.chk_activity_advanced.setChecked(
            any(v < NUMERIC_SPECS[k].recommended_min or v > NUMERIC_SPECS[k].recommended_max for k, v in activity_checks)
        )

    def _apply_config_to_ui(self, cfg: PhantomConfig):
        self._mark_advanced_from_config(cfg)
        self._apply_advanced_states()

        self.ctrl_matrix.set_value(cfg.volume_shape[0])
        self.ctrl_voxel.set_value(cfg.voxel_size_mm)
        self.ctrl_scale.set_value(cfg.scale_jitter)
        self.ctrl_rot.set_value(cfg.rot_jitter_deg)
        self.ctrl_shift.set_value(cfg.global_shift_range)
        self.ctrl_left_ratio.set_value(cfg.target_left_ratio)
        self.ctrl_smooth.set_value(cfg.smooth_sigma)
        self.ctrl_tumor_count.set_value(cfg.tumor_count_min)
        self.ctrl_contrast.set_value(cfg.tumor_contrast_min)
        self.ctrl_counts.set_value(cfg.total_counts / 1e4)
        self.ctrl_residual.set_value(cfg.residual_bg)
        self.ctrl_tumor_mode.set_value(cfg.tumor_mode_policy)
        self.ctrl_perfusion.set_value(cfg.perfusion_mode_policy)
        self.spin_n_cases.setValue(cfg.n_cases)
        self.edit_seed.setText(str(cfg.global_seed))
        self.chk_use_seed.setChecked(cfg.use_global_seed)
        self.edit_output.setText(cfg.output_dir)

    def _autosave_batch_config(self, cfg: PhantomConfig):
        output_dir = Path(cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        cfg.save(output_dir / "last_batch_config.json")

    def _on_settings_changed(self, settings):
        current = self.edit_output.text().strip()
        if not current or current == self._last_settings_output:
            self.edit_output.setText(settings.default_output)
        self._last_settings_output = settings.default_output



