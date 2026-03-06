"""
Simulation Page
===============
Step 2: Format conversion (npz → Interfile) and SIMIND execution.
"""

from __future__ import annotations
import subprocess
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QLabel, QLineEdit, QFileDialog,
    QTextEdit, QProgressBar, QSpinBox, QDoubleSpinBox,
    QFormLayout, QFrame, QSplitter, QCheckBox,
    QMessageBox, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSlot, QProcess
from PyQt6.QtGui import QTextCursor, QColor

from core.interfile_writer import (
    batch_convert_npz_to_interfile, generate_simind_bat
)
from ui.widgets.param_widgets import ParamGroup


class ConvertWorker(QThread):
    """Background thread for npz → Interfile conversion."""
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, npz_dir: str, out_dir: str, voxel_size: float):
        super().__init__()
        self.npz_dir = Path(npz_dir)
        self.out_dir = Path(out_dir)
        self.voxel_size = voxel_size

    def run(self):
        try:
            results = batch_convert_npz_to_interfile(
                self.npz_dir, self.out_dir, self.voxel_size,
                progress_callback=lambda c, t, n: self.progress.emit(c, t, n)
            )
            self.finished.emit(len(results))
        except Exception as e:
            self.error.emit(str(e))


class SimulationPage(QWidget):
    """Page 2: Conversion and SIMIND simulation."""
    simulation_finished = pyqtSignal(str)  # output directory

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Simulation Pipeline")
        title.setObjectName("page_title")
        root.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2d3139; }")

        # Left: config panels
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # ── Step 1: Conversion ──
        conv_grp = QGroupBox("STEP 1 — FORMAT CONVERSION (npz → .bin)")
        conv_form = QFormLayout(conv_grp)
        conv_form.setSpacing(8)

        self.edit_npz_dir = QLineEdit()
        self.edit_npz_dir.setPlaceholderText("Directory containing case_XXXX.npz files...")
        self.edit_npz_dir.textChanged.connect(self._on_npz_dir_changed)
        btn_npz = QPushButton("Browse")
        btn_npz.clicked.connect(lambda: self._browse_dir(self.edit_npz_dir))
        npz_row = self._make_browse_row(self.edit_npz_dir, btn_npz)

        self.edit_interfile_dir = QLineEdit()
        self.edit_interfile_dir.setPlaceholderText("Output directory for .bin binary files...")
        btn_if = QPushButton("Browse")
        btn_if.clicked.connect(lambda: self._browse_dir(self.edit_interfile_dir))
        if_row = self._make_browse_row(self.edit_interfile_dir, btn_if)

        self.spin_voxel = QDoubleSpinBox()
        self.spin_voxel.setRange(0.5, 20.0)
        self.spin_voxel.setValue(4.42)
        self.spin_voxel.setDecimals(2)
        self.spin_voxel.setSuffix(" mm")

        conv_form.addRow("npz directory:", npz_row)
        conv_form.addRow("Interfile output:", if_row)
        conv_form.addRow("Voxel size:", self.spin_voxel)

        self.lbl_npz_count = QLabel("")
        self.lbl_npz_count.setStyleSheet("color: #4fc3f7; font-size: 11px;")
        conv_form.addRow("", self.lbl_npz_count)

        self.btn_convert = QPushButton("Convert All Cases")
        self.btn_convert.setObjectName("primary_btn")
        self.btn_convert.setMinimumHeight(36)
        self.btn_convert.clicked.connect(self._on_convert)
        self.conv_progress = QProgressBar()
        self.conv_progress.setVisible(False)
        self.lbl_conv_status = QLabel("")
        self.lbl_conv_status.setStyleSheet("color: #6b7280; font-size: 12px;")

        conv_layout = QVBoxLayout()
        conv_layout.addWidget(conv_grp)
        conv_layout.addWidget(self.btn_convert)
        conv_layout.addWidget(self.conv_progress)
        conv_layout.addWidget(self.lbl_conv_status)
        left_layout.addLayout(conv_layout)

        # ── Step 2: SIMIND Config ──
        sim_grp = QGroupBox("STEP 2 — SIMIND CONFIGURATION")
        sim_form = QFormLayout(sim_grp)
        sim_form.setSpacing(8)

        self.edit_simind_exe = QLineEdit()
        self.edit_simind_exe.setPlaceholderText("Path to simind.exe (bundled or custom)...")
        btn_simind = QPushButton("Browse")
        btn_simind.clicked.connect(lambda: self._browse_file(
            self.edit_simind_exe, "SIMIND Executable (simind.exe);;All Files (*)"
        ))

        self.edit_smc = QLineEdit()
        self.edit_smc.setPlaceholderText("Path to .smc configuration file...")
        btn_smc = QPushButton("Browse")
        btn_smc.clicked.connect(lambda: self._browse_file(
            self.edit_smc, "SIMIND Config (*.smc);;All Files (*)"
        ))

        self.edit_sim_out = QLineEdit()
        self.edit_sim_out.setPlaceholderText("SIMIND output directory...")
        btn_sim_out = QPushButton("Browse")
        btn_sim_out.clicked.connect(lambda: self._browse_dir(self.edit_sim_out))

        self.spin_photons = QSpinBox()
        self.spin_photons.setRange(100_000, 100_000_000)
        self.spin_photons.setValue(5_000_000)
        self.spin_photons.setSingleStep(1_000_000)
        self.spin_photons.setSuffix("  photons/proj")

        sim_form.addRow("simind.exe:", self._make_browse_row(self.edit_simind_exe, btn_simind))
        sim_form.addRow(".smc config:", self._make_browse_row(self.edit_smc, btn_smc))
        sim_form.addRow("Output dir:", self._make_browse_row(self.edit_sim_out, btn_sim_out))
        sim_form.addRow("Photon histories:", self.spin_photons)

        left_layout.addWidget(sim_grp)

        # ── Step 3: Run ──
        run_grp = QGroupBox("STEP 3 — GENERATE & RUN")
        run_layout = QVBoxLayout(run_grp)

        self.chk_gen_bat = QCheckBox("Generate .bat script only (do not run SIMIND now)")
        self.chk_gen_bat.setChecked(False)

        self.edit_bat_path = QLineEdit()
        self.edit_bat_path.setPlaceholderText("Path to save .bat script...")
        btn_bat = QPushButton("Browse")
        btn_bat.clicked.connect(lambda: self._browse_save(
            self.edit_bat_path, "Batch Script (*.bat)"
        ))
        bat_row = self._make_browse_row(self.edit_bat_path, btn_bat)

        run_layout.addWidget(self.chk_gen_bat)
        run_layout.addWidget(QLabel("Save .bat to:"))
        run_layout.addWidget(bat_row)

        btn_row = QHBoxLayout()
        self.btn_gen_bat = QPushButton("Generate .bat Script")
        self.btn_gen_bat.clicked.connect(self._on_gen_bat)
        self.btn_run_sim = QPushButton("▶  Run SIMIND Now")
        self.btn_run_sim.setObjectName("success_btn")
        self.btn_run_sim.setMinimumHeight(36)
        self.btn_run_sim.clicked.connect(self._on_run_simind)
        btn_row.addWidget(self.btn_gen_bat)
        btn_row.addWidget(self.btn_run_sim)
        run_layout.addLayout(btn_row)

        left_layout.addWidget(run_grp)
        left_layout.addStretch()

        splitter.addWidget(left)

        # Right: log console
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        log_title = QLabel("Console Output")
        log_title.setStyleSheet("color: #6b7280; font-size: 11px; letter-spacing: 1px;")
        right_layout.addWidget(log_title)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumWidth(400)
        right_layout.addWidget(self.log_view, stretch=1)

        # Progress bar for SIMIND
        self.sim_progress = QProgressBar()
        self.sim_progress.setVisible(False)
        self.lbl_sim_status = QLabel("")
        self.lbl_sim_status.setStyleSheet("color: #6b7280; font-size: 12px;")
        right_layout.addWidget(self.sim_progress)
        right_layout.addWidget(self.lbl_sim_status)

        btn_stop = QPushButton("■  Stop")
        btn_stop.setObjectName("danger_btn")
        btn_stop.clicked.connect(self._on_stop)
        right_layout.addWidget(btn_stop)

        splitter.addWidget(right)
        splitter.setSizes([500, 500])
        root.addWidget(splitter, stretch=1)

        # Auto-detect bundled simind
        self._auto_detect_simind()

    # ── Helpers ──────────────────────────────────────────────────────

    def _make_browse_row(self, edit: QLineEdit, btn: QPushButton) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(edit)
        btn.setFixedWidth(70)
        layout.addWidget(btn)
        return w

    def _browse_dir(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select Directory")
        if path:
            edit.setText(path)

    def _browse_file(self, edit: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", filter_str)
        if path:
            edit.setText(path)

    def _browse_save(self, edit: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getSaveFileName(self, "Save File", "", filter_str)
        if path:
            edit.setText(path)

    def _auto_detect_simind(self):
        """Try to find bundled simind.exe."""
        bundled = Path(__file__).parent.parent.parent.parent / "simind" / "simind.exe"
        if bundled.exists():
            self.edit_simind_exe.setText(str(bundled))
            self._log(f"[INFO] Bundled SIMIND detected: {bundled}", color="#4fc3f7")

        # Also try to find a default .smc (ge870_czt.smc created via change.exe)
        bundled_smc = Path(__file__).parent.parent.parent.parent / "simind" / "ge870_czt.smc"
        if bundled_smc.exists():
            self.edit_smc.setText(str(bundled_smc))
            self._log(f"[INFO] Default .smc found: {bundled_smc}", color="#4fc3f7")

    def _on_npz_dir_changed(self, text: str):
        p = Path(text.strip())
        if p.is_dir():
            count = len(list(p.glob("case_*.npz")))
            if count > 0:
                self.lbl_npz_count.setText(f"Found {count} .npz file(s)")
            else:
                self.lbl_npz_count.setText("No case_*.npz files found in this directory")
        else:
            self.lbl_npz_count.setText("")

    def _log(self, message: str, color: str = "#8b949e"):
        self.log_view.setTextColor(QColor(color))
        self.log_view.append(message)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    # ── Actions ──────────────────────────────────────────────────────

    def on_phantom_ready(self, result):
        """Called when phantom page emits phantom_generated."""
        self._log(f"[INFO] Phantom ready: case_0000, {result.n_tumors} tumors, "
                  f"perfusion={result.perfusion_mode}", color="#4fc3f7")

    def _on_convert(self):
        npz_dir = self.edit_npz_dir.text().strip()
        out_dir = self.edit_interfile_dir.text().strip()
        if not npz_dir or not out_dir:
            QMessageBox.warning(self, "Missing Input", "Please specify npz and output directories.")
            return

        self.btn_convert.setEnabled(False)
        self.conv_progress.setVisible(True)
        self.conv_progress.setValue(0)
        self.lbl_conv_status.setText("Converting...")
        self._log("[INFO] Starting format conversion...", color="#4fc3f7")

        self._conv_worker = ConvertWorker(npz_dir, out_dir, self.spin_voxel.value())
        self._conv_worker.progress.connect(self._on_conv_progress)
        self._conv_worker.finished.connect(self._on_conv_done)
        self._conv_worker.error.connect(self._on_conv_error)
        self._conv_worker.start()

    @pyqtSlot(int, int, str)
    def _on_conv_progress(self, current: int, total: int, filename: str):
        if total > 0:
            self.conv_progress.setMaximum(total)
            self.conv_progress.setValue(current + 1)
        self.lbl_conv_status.setText(f"Converting {filename} ({current + 1}/{total})")
        self._log(f"  → {filename}", color="#8b949e")

    @pyqtSlot(int)
    def _on_conv_done(self, count: int):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setVisible(False)
        self.lbl_conv_status.setText(f"Done: {count} cases converted.")
        self._log(f"[OK] Conversion complete: {count} cases.", color="#4caf50")

    @pyqtSlot(str)
    def _on_conv_error(self, msg: str):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setVisible(False)
        self._log(f"[ERROR] {msg}", color="#ff6b6b")
        QMessageBox.critical(self, "Conversion Error", msg)

    def _on_gen_bat(self):
        if not self._validate_sim_inputs():
            return
        bat_path = self.edit_bat_path.text().strip()
        if not bat_path:
            out_dir = self.edit_sim_out.text().strip()
            bat_path = str(Path(out_dir) / "run_simind.bat") if out_dir else "run_simind.bat"
        try:
            generate_simind_bat(
                interfile_dir=Path(self.edit_interfile_dir.text()),
                simind_exe=Path(self.edit_simind_exe.text()),
                smc_file=Path(self.edit_smc.text()),
                output_dir=Path(self.edit_sim_out.text()),
                bat_path=Path(bat_path),
                photons_per_proj=self.spin_photons.value(),
            )
            self._log(f"[OK] .bat script generated: {bat_path}", color="#4caf50")
            QMessageBox.information(self, "Done", f"Script saved to:\n{bat_path}")
        except Exception as e:
            self._log(f"[ERROR] {e}", color="#ff6b6b")
            QMessageBox.critical(self, "Error", str(e))

    def _on_run_simind(self):
        if not self._validate_sim_inputs():
            return

        # Generate bat first, then run it
        bat_path = Path(self.edit_sim_out.text()) / "run_simind.bat"
        try:
            generate_simind_bat(
                interfile_dir=Path(self.edit_interfile_dir.text()),
                simind_exe=Path(self.edit_simind_exe.text()),
                smc_file=Path(self.edit_smc.text()),
                output_dir=Path(self.edit_sim_out.text()),
                bat_path=bat_path,
                photons_per_proj=self.spin_photons.value(),
            )
        except Exception as e:
            self._log(f"[ERROR] Failed to generate .bat: {e}", color="#ff6b6b")
            return

        self._log(f"[INFO] Launching SIMIND batch: {bat_path}", color="#4fc3f7")
        self.sim_progress.setVisible(True)
        self.sim_progress.setRange(0, 0)  # indeterminate
        self.btn_run_sim.setEnabled(False)

        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._on_sim_stdout)
        self._process.readyReadStandardError.connect(self._on_sim_stderr)
        self._process.finished.connect(self._on_sim_finished)
        self._process.start("cmd.exe", ["/c", str(bat_path)])

    def _on_sim_stdout(self):
        data = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(line, color="#8b949e")

    def _on_sim_stderr(self):
        data = self._process.readAllStandardError().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(line, color="#ff6b6b")

    @pyqtSlot(int, QProcess.ExitStatus)
    def _on_sim_finished(self, exit_code: int, status):
        self.sim_progress.setVisible(False)
        self.btn_run_sim.setEnabled(True)
        if exit_code == 0:
            self._log("[OK] SIMIND simulation completed successfully.", color="#4caf50")
            self.simulation_finished.emit(self.edit_sim_out.text())
        else:
            self._log(f"[ERROR] SIMIND exited with code {exit_code}.", color="#ff6b6b")

    def _on_stop(self):
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._log("[WARN] Process terminated by user.", color="#ffa726")

    def _validate_sim_inputs(self) -> bool:
        missing = []
        if not self.edit_simind_exe.text().strip():
            missing.append("simind.exe path")
        if not self.edit_smc.text().strip():
            missing.append(".smc config file")
        if not self.edit_interfile_dir.text().strip():
            missing.append("Interfile directory")
        if not self.edit_sim_out.text().strip():
            missing.append("SIMIND output directory")
        if missing:
            QMessageBox.warning(self, "Missing Input",
                                "Please provide:\n• " + "\n• ".join(missing))
            return False
        return True
