"""
Settings and About dialogs.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.app_state import AppSettings, AppState
from ui.i18n import language_manager, set_language, tr
from ui.settings_store import SettingsStore


class SettingsPage(QWidget):
    theme_changed = pyqtSignal(str)

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._build_ui()
        self._load_settings()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        self.title = QLabel()
        self.title.setObjectName("page_title")
        root.addWidget(self.title)

        self.grp_simind = QGroupBox()
        simind_form = QFormLayout(self.grp_simind)
        self.lbl_simind = QLabel()
        self.lbl_smc = QLabel()
        self.edit_simind = QLineEdit()
        self.edit_default_smc = QLineEdit()
        self.btn_simind = QPushButton()
        self.btn_smc = QPushButton()
        self.btn_simind.clicked.connect(lambda: self._browse_file(self.edit_simind, "SIMIND Executable (simind.exe);;All Files (*)"))
        self.btn_smc.clicked.connect(lambda: self._browse_file(self.edit_default_smc, "SIMIND Config (*.smc);;All Files (*)"))
        simind_form.addRow(self.lbl_simind, self._row(self.edit_simind, self.btn_simind))
        simind_form.addRow(self.lbl_smc, self._row(self.edit_default_smc, self.btn_smc))
        root.addWidget(self.grp_simind)

        self.grp_paths = QGroupBox()
        paths_form = QFormLayout(self.grp_paths)
        self.lbl_output = QLabel()
        self.edit_default_output = QLineEdit()
        self.btn_output = QPushButton()
        self.btn_output.clicked.connect(lambda: self._browse_dir(self.edit_default_output))
        self.chk_autosave = QCheckBox()
        paths_form.addRow(self.lbl_output, self._row(self.edit_default_output, self.btn_output))
        paths_form.addRow(QLabel(""), self.chk_autosave)
        root.addWidget(self.grp_paths)

        self.grp_appearance = QGroupBox()
        appearance_form = QFormLayout(self.grp_appearance)
        self.lbl_theme = QLabel()
        self.lbl_lang = QLabel()
        self.combo_theme = QComboBox()
        self.combo_lang = QComboBox()
        self.combo_lang.addItem("English", "en")
        self.combo_lang.addItem("中文", "zh")
        self.combo_lang.addItem("Français", "fr")
        appearance_form.addRow(self.lbl_theme, self.combo_theme)
        appearance_form.addRow(self.lbl_lang, self.combo_lang)
        root.addWidget(self.grp_appearance)

        self.lbl_store = QLabel()
        self.lbl_store.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.lbl_store.setWordWrap(True)
        root.addWidget(self.lbl_store)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_reset = QPushButton()
        self.btn_reset.clicked.connect(self._reset_settings)
        self.btn_save = QPushButton()
        self.btn_save.setObjectName("primary_btn")
        self.btn_save.clicked.connect(self._save_settings)
        btn_row.addWidget(self.btn_reset)
        btn_row.addWidget(self.btn_save)
        root.addLayout(btn_row)
        self.retranslate_ui()

    def _row(self, edit: QLineEdit, btn: QPushButton) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(edit)
        btn.setFixedWidth(90)
        layout.addWidget(btn)
        return w

    def _browse_file(self, edit: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, tr("Select File"), "", filter_str)
        if path:
            edit.setText(path)

    def _browse_dir(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, tr("Select Directory"))
        if path:
            edit.setText(path)

    def _to_settings(self) -> AppSettings:
        return AppSettings(
            simind_exe=self.edit_simind.text().strip(),
            default_smc=self.edit_default_smc.text().strip(),
            default_output=self.edit_default_output.text().strip() or "output/syn3d",
            theme="light" if self.combo_theme.currentIndex() == 1 else "dark",
            language=self.combo_lang.currentData(),
            autosave_config=self.chk_autosave.isChecked(),
        )

    def _save_settings(self):
        current = self._app_state.settings
        new_settings = self._to_settings()
        self._app_state.save_settings(new_settings)
        set_language(new_settings.language)
        if new_settings.theme != current.theme:
            self.theme_changed.emit(new_settings.theme)
        QMessageBox.information(self, tr("Saved"), tr("Settings saved successfully."))

    def _load_settings(self):
        settings = self._app_state.settings
        self.edit_simind.setText(settings.simind_exe)
        self.edit_default_smc.setText(settings.default_smc)
        self.edit_default_output.setText(settings.default_output)
        self.chk_autosave.setChecked(settings.autosave_config)
        self.combo_theme.setCurrentIndex(1 if settings.theme == "light" else 0)
        idx = self.combo_lang.findData(settings.language)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)

    def _reset_settings(self):
        self._app_state.reset_settings()
        self._load_settings()
        set_language(self._app_state.settings.language)
        self.theme_changed.emit(self._app_state.settings.theme)
        QMessageBox.information(self, tr("Reset"), tr("Settings reset to defaults."))

    def retranslate_ui(self):
        current_theme = self.combo_theme.currentData() if self.combo_theme.count() else None
        current_lang = self.combo_lang.currentData() if self.combo_lang.count() else None
        self.title.setText(tr("Settings"))
        self.grp_simind.setTitle(tr("SIMIND CONFIGURATION"))
        self.grp_paths.setTitle(tr("DEFAULT PATHS"))
        self.grp_appearance.setTitle(tr("APPEARANCE"))
        self.lbl_simind.setText(tr("simind.exe:"))
        self.lbl_smc.setText(tr("Default .smc:"))
        self.lbl_output.setText(tr("Output directory") + ":")
        self.lbl_theme.setText(tr("Theme:") )
        self.lbl_lang.setText(tr("Language:"))
        self.btn_simind.setText(tr("Browse"))
        self.btn_smc.setText(tr("Browse"))
        self.btn_output.setText(tr("Browse"))
        self.edit_default_output.setPlaceholderText(tr("Output directory path..."))
        self.chk_autosave.setText(tr("Auto-save config on batch start"))
        self.combo_theme.clear()
        self.combo_theme.addItem(tr("Dark"), "dark")
        self.combo_theme.addItem(tr("Light"), "light")
        if current_theme is not None:
            idx = self.combo_theme.findData(current_theme)
            self.combo_theme.setCurrentIndex(max(idx, 0))
        self.combo_lang.setItemText(0, "English")
        self.combo_lang.setItemText(1, "中文")
        self.combo_lang.setItemText(2, "Français")
        if current_lang is not None:
            idx = self.combo_lang.findData(current_lang)
            self.combo_lang.setCurrentIndex(max(idx, 0))
        self.btn_reset.setText(tr("Reset to Defaults"))
        self.btn_save.setText(tr("Save Settings"))
        self.lbl_store.setText(f"{tr('Settings file')}: {SettingsStore().path}")


class SettingsDialog(QDialog):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self.page = SettingsPage(app_state, self)
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(self.page)
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(tr("Settings"))


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.resize(520, 320)
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setObjectName("page_title")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setStyleSheet("color: #8a9099; font-size: 12px; line-height: 1.6;")
        self.btn_close = QPushButton()
        self.btn_close.clicked.connect(self.accept)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addStretch()
        layout.addWidget(self.btn_close)
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(tr("About"))
        self.title.setText(tr("About"))
        self.body.setText(
            tr("PAR-S Generator is a research-facing liver SPECT phantom workflow that now groups preview and batch monitoring under Generate, and keeps the .a00 viewer inside Simulate.")
        )
        self.btn_close.setText(tr("Close"))
