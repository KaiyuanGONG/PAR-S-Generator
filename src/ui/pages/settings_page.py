"""
Settings Page
=============
Application settings: SIMIND path, default directories, etc.
"""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QLabel, QLineEdit, QFileDialog,
    QFormLayout, QSpinBox, QCheckBox, QComboBox,
    QFrame, QScrollArea, QMessageBox
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QFont


class SettingsPage(QWidget):
    """Page 4: Application settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("PAR-S", "Generator")
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("page_title")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(16)
        content_layout.setContentsMargins(0, 0, 8, 0)

        # ── SIMIND ──
        simind_grp = QGroupBox("SIMIND CONFIGURATION")
        simind_form = QFormLayout(simind_grp)
        simind_form.setSpacing(10)

        self.edit_simind = QLineEdit()
        self.edit_simind.setPlaceholderText("Path to simind.exe...")
        btn_simind = QPushButton("Browse")
        btn_simind.clicked.connect(lambda: self._browse_file(
            self.edit_simind, "SIMIND Executable (simind.exe);;All Files (*)"
        ))
        simind_form.addRow("simind.exe:", self._row(self.edit_simind, btn_simind))

        self.edit_default_smc = QLineEdit()
        self.edit_default_smc.setPlaceholderText("Default .smc configuration file...")
        btn_smc = QPushButton("Browse")
        btn_smc.clicked.connect(lambda: self._browse_file(
            self.edit_default_smc, "SIMIND Config (*.smc);;All Files (*)"
        ))
        simind_form.addRow("Default .smc:", self._row(self.edit_default_smc, btn_smc))
        content_layout.addWidget(simind_grp)

        # ── Paths ──
        paths_grp = QGroupBox("DEFAULT PATHS")
        paths_form = QFormLayout(paths_grp)
        paths_form.setSpacing(10)

        self.edit_default_output = QLineEdit()
        self.edit_default_output.setPlaceholderText("Default output directory...")
        btn_out = QPushButton("Browse")
        btn_out.clicked.connect(lambda: self._browse_dir(self.edit_default_output))
        paths_form.addRow("Output directory:", self._row(self.edit_default_output, btn_out))
        content_layout.addWidget(paths_grp)

        # ── Performance ──
        perf_grp = QGroupBox("PERFORMANCE")
        perf_form = QFormLayout(perf_grp)
        perf_form.setSpacing(10)

        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 32)
        self.spin_threads.setValue(4)
        self.spin_threads.setToolTip("Number of parallel threads for batch generation")
        perf_form.addRow("Batch threads:", self.spin_threads)

        self.chk_autosave = QCheckBox("Auto-save config on batch start")
        self.chk_autosave.setChecked(True)
        perf_form.addRow("", self.chk_autosave)
        content_layout.addWidget(perf_grp)

        # ── About ──
        about_grp = QGroupBox("ABOUT")
        about_layout = QVBoxLayout(about_grp)
        about_text = QLabel(
            "<b>PAR-S Generator</b> v0.1.0<br>"
            "Synthetic liver SPECT phantom generator for PAR-S deep learning research.<br><br>"
            "Based on PAR-S project by Kaiyuan Gong.<br>"
            "Uses SIMIND Monte Carlo simulation (Ljungberg, Lund University).<br><br>"
            "<a href='https://github.com/KaiyuanGONG/PAR-S' style='color:#4fc3f7;'>"
            "github.com/KaiyuanGONG/PAR-S</a>"
        )
        about_text.setOpenExternalLinks(True)
        about_text.setStyleSheet("color: #8a9099; font-size: 12px; line-height: 1.6;")
        about_text.setWordWrap(True)
        about_layout.addWidget(about_text)
        content_layout.addWidget(about_grp)

        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        # Save / Reset buttons
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save Settings")
        btn_save.setObjectName("primary_btn")
        btn_save.clicked.connect(self._save_settings)
        btn_reset = QPushButton("Reset to Defaults")
        btn_reset.clicked.connect(self._reset_settings)
        btn_row.addStretch()
        btn_row.addWidget(btn_reset)
        btn_row.addWidget(btn_save)
        root.addLayout(btn_row)

    def _row(self, edit: QLineEdit, btn: QPushButton) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(edit)
        btn.setFixedWidth(70)
        layout.addWidget(btn)
        return w

    def _browse_file(self, edit: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", filter_str)
        if path:
            edit.setText(path)

    def _browse_dir(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select Directory")
        if path:
            edit.setText(path)

    def _save_settings(self):
        self._settings.setValue("simind/exe", self.edit_simind.text())
        self._settings.setValue("simind/default_smc", self.edit_default_smc.text())
        self._settings.setValue("paths/default_output", self.edit_default_output.text())
        self._settings.setValue("perf/threads", self.spin_threads.value())
        self._settings.setValue("perf/autosave", self.chk_autosave.isChecked())
        QMessageBox.information(self, "Saved", "Settings saved successfully.")

    def _load_settings(self):
        self.edit_simind.setText(self._settings.value("simind/exe", ""))
        self.edit_default_smc.setText(self._settings.value("simind/default_smc", ""))
        self.edit_default_output.setText(self._settings.value("paths/default_output", ""))
        self.spin_threads.setValue(int(self._settings.value("perf/threads", 4)))
        self.chk_autosave.setChecked(bool(self._settings.value("perf/autosave", True)))

    def _reset_settings(self):
        self._settings.clear()
        self._load_settings()
        QMessageBox.information(self, "Reset", "Settings reset to defaults.")
