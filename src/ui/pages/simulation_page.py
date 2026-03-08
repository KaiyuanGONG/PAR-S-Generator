"""
Simulation workspace.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QProcess, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.interfile_writer import batch_convert_npz_to_interfile, generate_simind_bat
from core.validation import ValidationReport, validate_simulation_inputs
from ui.app_state import AppState, SimulationConfig
from ui.i18n import language_manager, tr
from ui.widgets.simind_viewer import SimindOutputViewer


class ConvertWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, npz_dir: str, out_dir: str):
        super().__init__()
        self.npz_dir = Path(npz_dir)
        self.out_dir = Path(out_dir)

    def run(self):
        try:
            results = batch_convert_npz_to_interfile(
                self.npz_dir,
                self.out_dir,
                progress_callback=lambda c, t, n: self.progress.emit(c, t, n),
            )
            self.finished.emit(len(results))
        except Exception as exc:
            self.error.emit(str(exc))


class SimulationPage(QWidget):
    simulation_finished = pyqtSignal(str)

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._process: QProcess | None = None
        self._build_ui()
        self._auto_detect_simind()
        self._sync_from_state(self._app_state.simulation_config)
        self._app_state.simulation_config_changed.connect(self._sync_from_state)
        self._app_state.preview_result_changed.connect(self._on_preview_result)
        self._app_state.settings_changed.connect(lambda _: self._sync_from_state(self._app_state.simulation_config))
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("page_title")
        root.addWidget(self.lbl_title)

        self.lbl_validation = QLabel("")
        self.lbl_validation.setWordWrap(True)
        self.lbl_validation.setStyleSheet(
            "background-color: #252a33; border: 1px solid #2d3139; border-radius: 8px; padding: 10px;"
            "color: #8b949e; font-size: 12px;"
        )
        root.addWidget(self.lbl_validation)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2d3139; }")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self.grp_step1 = QGroupBox()
        form1 = QFormLayout(self.grp_step1)
        self.lbl_npz_dir = QLabel()
        self.lbl_interfile_dir = QLabel()
        self.lbl_source = QLabel()
        self.edit_npz_dir = QLineEdit()
        self.edit_npz_dir.textChanged.connect(self._on_npz_dir_changed)
        self.btn_npz = QPushButton()
        self.btn_npz.clicked.connect(lambda: self._browse_dir(self.edit_npz_dir))
        self.edit_interfile_dir = QLineEdit()
        self.btn_interfile = QPushButton()
        self.btn_interfile.clicked.connect(lambda: self._browse_dir(self.edit_interfile_dir))
        self.lbl_source_meta = QLabel("")
        self.lbl_source_meta.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.lbl_npz_count = QLabel("")
        self.lbl_npz_count.setStyleSheet("color: #4fc3f7; font-size: 11px;")
        form1.addRow(self.lbl_npz_dir, self._browse_row(self.edit_npz_dir, self.btn_npz))
        form1.addRow(self.lbl_interfile_dir, self._browse_row(self.edit_interfile_dir, self.btn_interfile))
        form1.addRow(self.lbl_source, self.lbl_source_meta)
        form1.addRow(QLabel(""), self.lbl_npz_count)
        left_layout.addWidget(self.grp_step1)

        self.btn_convert = QPushButton()
        self.btn_convert.setObjectName("primary_btn")
        self.btn_convert.clicked.connect(self._on_convert)
        self.conv_progress = QProgressBar()
        self.conv_progress.setVisible(False)
        self.lbl_conv_status = QLabel("")
        self.lbl_conv_status.setStyleSheet("color: #6b7280; font-size: 12px;")
        left_layout.addWidget(self.btn_convert)
        left_layout.addWidget(self.conv_progress)
        left_layout.addWidget(self.lbl_conv_status)

        self.grp_step2 = QGroupBox()
        form2 = QFormLayout(self.grp_step2)
        self.lbl_simind_exe = QLabel()
        self.lbl_smc = QLabel()
        self.lbl_sim_out = QLabel()
        self.edit_simind_exe = QLineEdit()
        self.btn_simind = QPushButton()
        self.btn_simind.clicked.connect(lambda: self._browse_file(self.edit_simind_exe, "SIMIND Executable (simind.exe);;All Files (*)"))
        self.edit_smc = QLineEdit()
        self.btn_smc = QPushButton()
        self.btn_smc.clicked.connect(lambda: self._browse_file(self.edit_smc, "SIMIND Config (*.smc);;All Files (*)"))
        self.edit_sim_out = QLineEdit()
        self.btn_sim_out = QPushButton()
        self.btn_sim_out.clicked.connect(lambda: self._browse_dir(self.edit_sim_out))
        self.lbl_sim_note = QLabel("")
        self.lbl_sim_note.setWordWrap(True)
        self.lbl_sim_note.setStyleSheet("color: #6b7280; font-size: 11px;")
        form2.addRow(self.lbl_simind_exe, self._browse_row(self.edit_simind_exe, self.btn_simind))
        form2.addRow(self.lbl_smc, self._browse_row(self.edit_smc, self.btn_smc))
        form2.addRow(self.lbl_sim_out, self._browse_row(self.edit_sim_out, self.btn_sim_out))
        form2.addRow(QLabel(""), self.lbl_sim_note)
        left_layout.addWidget(self.grp_step2)

        self.grp_step3 = QGroupBox()
        run_layout = QVBoxLayout(self.grp_step3)
        self.lbl_bat_path = QLabel()
        self.edit_bat_path = QLineEdit()
        self.btn_bat = QPushButton()
        self.btn_bat.clicked.connect(lambda: self._browse_save(self.edit_bat_path, "Batch Script (*.bat)"))
        run_layout.addWidget(self._labeled_field(self.lbl_bat_path, self._browse_row(self.edit_bat_path, self.btn_bat)))
        self.lbl_bat_note = QLabel("")
        self.lbl_bat_note.setWordWrap(True)
        self.lbl_bat_note.setStyleSheet("color: #6b7280; font-size: 11px;")
        run_layout.addWidget(self.lbl_bat_note)
        btn_row = QHBoxLayout()
        self.btn_gen_bat = QPushButton()
        self.btn_gen_bat.clicked.connect(self._on_gen_bat)
        self.btn_run_sim = QPushButton()
        self.btn_run_sim.setObjectName("success_btn")
        self.btn_run_sim.clicked.connect(self._on_run_simind)
        btn_row.addWidget(self.btn_gen_bat)
        btn_row.addWidget(self.btn_run_sim)
        run_layout.addLayout(btn_row)
        left_layout.addWidget(self.grp_step3)

        self.grp_step4 = QGroupBox()
        step4_layout = QVBoxLayout(self.grp_step4)
        self.lbl_step4_note = QLabel("")
        self.lbl_step4_note.setWordWrap(True)
        self.lbl_step4_note.setStyleSheet("color: #8a9099; font-size: 11px;")
        step4_layout.addWidget(self.lbl_step4_note)
        left_layout.addWidget(self.grp_step4)
        left_layout.addStretch()
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        self.right_tabs = QTabWidget()
        self.console_widget = QWidget()
        console_layout = QVBoxLayout(self.console_widget)
        console_layout.setContentsMargins(0, 4, 0, 0)
        console_layout.setSpacing(6)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        console_layout.addWidget(self.log_view, stretch=1)
        self.sim_progress = QProgressBar()
        self.sim_progress.setVisible(False)
        self.lbl_sim_status = QLabel("")
        self.lbl_sim_status.setStyleSheet("color: #6b7280; font-size: 12px;")
        console_layout.addWidget(self.sim_progress)
        console_layout.addWidget(self.lbl_sim_status)
        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName("danger_btn")
        self.btn_stop.clicked.connect(self._on_stop)
        console_layout.addWidget(self.btn_stop)
        self.right_tabs.addTab(self.console_widget, "")
        self.spect_preview = SimindOutputViewer(view_title="SPECT Preview")
        self.right_tabs.addTab(self.spect_preview, "")
        right_layout.addWidget(self.right_tabs)
        splitter.addWidget(right)
        splitter.setSizes([520, 560])
        root.addWidget(splitter, stretch=1)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.lbl_title.setText(tr("Simulate"))
        self.grp_step1.setTitle(tr("Step 1: Raw Binary Export"))
        self.grp_step2.setTitle(tr("Step 2: SIMIND Configuration"))
        self.grp_step3.setTitle(tr("Step 3: Script or Run"))
        self.grp_step4.setTitle(tr("Step 4: Visual Check"))
        self.lbl_npz_dir.setText(tr("npz directory:"))
        self.lbl_interfile_dir.setText(tr("Binary output:"))
        self.lbl_source.setText(tr("Phantom source"))
        self.lbl_simind_exe.setText(tr("simind.exe:"))
        self.lbl_smc.setText(tr(".smc config:"))
        self.lbl_sim_out.setText(tr("Output directory") + ":")
        self.lbl_bat_path.setText(tr("Save .bat to:"))
        self.btn_npz.setText(tr("Browse"))
        self.btn_interfile.setText(tr("Browse"))
        self.btn_simind.setText(tr("Browse"))
        self.btn_smc.setText(tr("Browse"))
        self.btn_sim_out.setText(tr("Browse"))
        self.btn_bat.setText(tr("Browse"))
        self.btn_convert.setText(tr("Convert All Cases"))
        self.btn_gen_bat.setText(tr("Generate .bat Script"))
        self.btn_run_sim.setText(tr("▶  Run SIMIND Now"))
        self.btn_stop.setText(tr("■  Stop"))
        self.lbl_sim_note.setText(tr("Photon histories remain controlled by the selected .smc file, not by a fake UI slider."))
        self.lbl_bat_note.setText(tr("Use the .bat script when you want to inspect or run SIMIND outside the application."))
        self.lbl_step4_note.setText(tr("After a successful run, the first .a00 file will be loaded automatically into SPECT Preview for a quick visual check."))
        self.edit_npz_dir.setPlaceholderText(tr("Directory containing case_*.npz files"))
        self.edit_interfile_dir.setPlaceholderText(tr("Binary output directory"))
        self.edit_sim_out.setPlaceholderText(tr("SIMIND output directory"))
        self.right_tabs.setTabText(0, tr("Console"))
        self.right_tabs.setTabText(1, tr("SPECT Preview"))
        self.spect_preview.retranslate_ui()
        self._sync_from_state(self._app_state.simulation_config)

    def _browse_row(self, edit: QLineEdit, btn: QPushButton) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(edit)
        btn.setFixedWidth(90)
        layout.addWidget(btn)
        return w

    def _labeled_field(self, label: QLabel, widget: QWidget) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label.setStyleSheet("color: #8a9099; font-size: 11px;")
        layout.addWidget(label)
        layout.addWidget(widget)
        return wrap

    def _browse_dir(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, tr("Select Directory"))
        if path:
            edit.setText(path)
            self._update_state_from_ui()

    def _browse_file(self, edit: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, tr("Select File"), "", filter_str)
        if path:
            edit.setText(path)
            self._update_state_from_ui()

    def _browse_save(self, edit: QLineEdit, filter_str: str):
        path, _ = QFileDialog.getSaveFileName(self, tr("Save File"), "", filter_str)
        if path:
            edit.setText(path)

    def _auto_detect_simind(self):
        sim_cfg = self._app_state.simulation_config
        if not sim_cfg.simind_exe:
            bundled = Path(__file__).parent.parent.parent.parent / "simind" / "simind.exe"
            if bundled.exists():
                sim_cfg.simind_exe = str(bundled)
        if not sim_cfg.smc_file:
            bundled_smc = Path(__file__).parent.parent.parent.parent / "simind" / "ge870_czt.smc"
            if bundled_smc.exists():
                sim_cfg.smc_file = str(bundled_smc)
        self._app_state.set_simulation_config(sim_cfg)

    def _update_state_from_ui(self):
        config = SimulationConfig(
            npz_dir=self.edit_npz_dir.text().strip(),
            interfile_dir=self.edit_interfile_dir.text().strip() or "output/interfile",
            simind_exe=self.edit_simind_exe.text().strip(),
            smc_file=self.edit_smc.text().strip(),
            sim_output_dir=self.edit_sim_out.text().strip() or "output/simind",
        )
        self._app_state.set_simulation_config(config)

    def _sync_from_state(self, config: SimulationConfig):
        self.edit_npz_dir.setText(config.npz_dir)
        self.edit_interfile_dir.setText(config.interfile_dir)
        self.edit_simind_exe.setText(config.simind_exe)
        self.edit_smc.setText(config.smc_file)
        self.edit_sim_out.setText(config.sim_output_dir)
        phantom = self._app_state.phantom_config
        self.lbl_source_meta.setText(
            tr("Matrix/Voxel summary").format(
                matrix=phantom.volume_shape[0],
                voxel=phantom.voxel_size_mm,
                output=phantom.output_dir,
            )
        )
        report = validate_simulation_inputs(
            self.edit_npz_dir.text(),
            self.edit_interfile_dir.text(),
            self.edit_simind_exe.text(),
            self.edit_smc.text(),
            self.edit_sim_out.text(),
            phantom_config=phantom,
        )
        self._update_validation_banner(report)
        self._on_npz_dir_changed(self.edit_npz_dir.text())

    def _on_preview_result(self, result):
        if result is None:
            return
        self._log(
            tr("Preview ready log").format(voxel=result.voxel_size_mm, output=self._app_state.phantom_config.output_dir),
            color="#4fc3f7",
        )
        self._sync_from_state(self._app_state.simulation_config)

    def _update_validation_banner(self, report: ValidationReport):
        if report.errors:
            self.lbl_validation.setText(report.to_message())
            self.lbl_validation.setStyleSheet(
                "background-color: #3a1d24; border: 1px solid #80343f; border-radius: 8px; padding: 10px;"
                "color: #ffb3b8; font-size: 12px;"
            )
        elif report.warnings:
            self.lbl_validation.setText(report.to_message())
            self.lbl_validation.setStyleSheet(
                "background-color: #3b2b18; border: 1px solid #8a5a20; border-radius: 8px; padding: 10px;"
                "color: #ffd39b; font-size: 12px;"
            )
        else:
            self.lbl_validation.setText(tr("Ready. Simulation inputs and bundled compatibility checks passed."))
            self.lbl_validation.setStyleSheet(
                "background-color: #1d3227; border: 1px solid #26653f; border-radius: 8px; padding: 10px;"
                "color: #9fe3b6; font-size: 12px;"
            )

    def _on_npz_dir_changed(self, text: str):
        p = Path(text.strip())
        if p.is_dir():
            count = len(list(p.glob("case_*.npz")))
            if count > 0:
                self.lbl_npz_count.setText(tr("Found {count} .npz file(s)").format(count=count))
            else:
                self.lbl_npz_count.setText(tr("No case_*.npz files found in this directory"))
        else:
            self.lbl_npz_count.setText("")

    def _log(self, message: str, color: str = "#8b949e"):
        self.log_view.setTextColor(QColor(color))
        self.log_view.append(message)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _validate_for_conversion(self) -> bool:
        self._update_state_from_ui()
        if not self.edit_npz_dir.text().strip() or not self.edit_interfile_dir.text().strip():
            QMessageBox.warning(self, tr("Missing Input"), tr("Please specify npz and binary output directories."))
            return False
        npz_dir = Path(self.edit_npz_dir.text().strip())
        if not npz_dir.exists():
            QMessageBox.warning(self, tr("Missing Input"), tr("npz directory does not exist."))
            return False
        if not any(npz_dir.glob("case_*.npz")):
            QMessageBox.warning(self, tr("Missing Input"), tr("No case_*.npz files found in the selected directory."))
            return False
        return True

    def _validate_for_simind(self) -> bool:
        self._update_state_from_ui()
        report = validate_simulation_inputs(
            self.edit_npz_dir.text(),
            self.edit_interfile_dir.text(),
            self.edit_simind_exe.text(),
            self.edit_smc.text(),
            self.edit_sim_out.text(),
            phantom_config=self._app_state.phantom_config,
        )
        self._update_validation_banner(report)
        if not report.ok:
            QMessageBox.warning(self, tr("SIMIND blocked"), report.to_message())
            return False
        return True

    def _on_convert(self):
        if not self._validate_for_conversion():
            return
        self.btn_convert.setEnabled(False)
        self.conv_progress.setVisible(True)
        self.conv_progress.setValue(0)
        self.lbl_conv_status.setText(tr("Converting..."))
        self._log(tr("[INFO] Starting binary export..."), color="#4fc3f7")
        self._conv_worker = ConvertWorker(self.edit_npz_dir.text().strip(), self.edit_interfile_dir.text().strip())
        self._conv_worker.progress.connect(self._on_conv_progress)
        self._conv_worker.finished.connect(self._on_conv_done)
        self._conv_worker.error.connect(self._on_conv_error)
        self._conv_worker.start()

    @pyqtSlot(int, int, str)
    def _on_conv_progress(self, current: int, total: int, filename: str):
        if total > 0:
            self.conv_progress.setMaximum(total)
            self.conv_progress.setValue(current + 1)
        self.lbl_conv_status.setText(tr("Converting {filename} ({current}/{total})").format(filename=filename, current=current + 1, total=total))
        self._log(tr("  -> {filename}").format(filename=filename), color="#8b949e")

    @pyqtSlot(int)
    def _on_conv_done(self, count: int):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setVisible(False)
        self.lbl_conv_status.setText(tr("Done: {count} cases converted.").format(count=count))
        self._log(tr("[OK] Binary export complete: {count} cases.").format(count=count), color="#4caf50")

    @pyqtSlot(str)
    def _on_conv_error(self, msg: str):
        self.btn_convert.setEnabled(True)
        self.conv_progress.setVisible(False)
        self._log(f"[ERROR] {msg}", color="#ff6b6b")
        QMessageBox.critical(self, tr("Conversion Error"), msg)

    def _resolve_bat_path(self) -> Path:
        bat_path = self.edit_bat_path.text().strip()
        if bat_path:
            return Path(bat_path)
        out_dir = self.edit_sim_out.text().strip() or "output/simind"
        return Path(out_dir) / "run_simind.bat"

    def _on_gen_bat(self):
        if not self._validate_for_simind():
            return
        bat_path = self._resolve_bat_path()
        try:
            generate_simind_bat(
                interfile_dir=Path(self.edit_interfile_dir.text()),
                simind_exe=Path(self.edit_simind_exe.text()),
                smc_file=Path(self.edit_smc.text()),
                output_dir=Path(self.edit_sim_out.text()),
                bat_path=bat_path,
            )
            self._log(tr("[OK] .bat script generated: {path}").format(path=bat_path), color="#4caf50")
            QMessageBox.information(self, tr("Done"), tr("Script saved to:\n{path}").format(path=bat_path))
        except Exception as exc:
            self._log(f"[ERROR] {exc}", color="#ff6b6b")
            QMessageBox.critical(self, tr("Error"), str(exc))

    def _on_run_simind(self):
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._log(tr("[WARN] SIMIND process is already running."), color="#ffa726")
            return

        if not self._validate_for_simind():
            return

        bat_path = self._resolve_bat_path()
        try:
            generate_simind_bat(
                interfile_dir=Path(self.edit_interfile_dir.text()),
                simind_exe=Path(self.edit_simind_exe.text()),
                smc_file=Path(self.edit_smc.text()),
                output_dir=Path(self.edit_sim_out.text()),
                bat_path=bat_path,
            )
        except Exception as exc:
            self._log(tr("[ERROR] Failed to generate .bat: {msg}").format(msg=exc), color="#ff6b6b")
            QMessageBox.critical(self, tr("Error"), str(exc))
            return

        self._log(tr("[INFO] Launching SIMIND batch: {path}").format(path=bat_path), color="#4fc3f7")
        self.sim_progress.setVisible(True)
        self.sim_progress.setRange(0, 0)
        self.lbl_sim_status.setText(tr("SIMIND is running..."))
        self.btn_run_sim.setEnabled(False)

        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._on_sim_stdout)
        self._process.readyReadStandardError.connect(self._on_sim_stderr)
        self._process.errorOccurred.connect(self._on_sim_error)
        self._process.finished.connect(self._on_sim_finished)
        self._process.start("cmd.exe", ["/c", str(bat_path)])

    def _on_sim_stdout(self):
        if not self._process:
            return
        data = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(line, color="#8b949e")

    def _on_sim_stderr(self):
        if not self._process:
            return
        data = self._process.readAllStandardError().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self._log(line, color="#ff6b6b")

    @pyqtSlot(QProcess.ProcessError)
    def _on_sim_error(self, process_error):
        if process_error != QProcess.ProcessError.FailedToStart:
            return

        msg = self._process.errorString() if self._process else ""
        self.sim_progress.setVisible(False)
        self.btn_run_sim.setEnabled(True)
        self.lbl_sim_status.setText(tr("SIMIND failed to start."))
        self._log(tr("[ERROR] SIMIND process failed to start: {msg}").format(msg=msg), color="#ff6b6b")
        QMessageBox.critical(self, tr("Error"), tr("SIMIND process failed to start: {msg}").format(msg=msg))

    @pyqtSlot(int, QProcess.ExitStatus)
    def _on_sim_finished(self, exit_code: int, status):
        self.sim_progress.setVisible(False)
        self.btn_run_sim.setEnabled(True)
        self.lbl_sim_status.setText("" if exit_code == 0 else tr("SIMIND exited with code {code}.").format(code=exit_code))
        if exit_code == 0:
            self._log(tr("[OK] SIMIND simulation completed successfully."), color="#4caf50")
            self.simulation_finished.emit(self.edit_sim_out.text())
            out_dir = Path(self.edit_sim_out.text())
            a00_files = sorted(out_dir.glob("*.a00"))
            if a00_files:
                self.right_tabs.setCurrentIndex(1)
                self.spect_preview.load_file(str(a00_files[0]))
                self._log(tr("[INFO] Auto-loaded first .a00: {name}").format(name=a00_files[0].name), color="#4fc3f7")
        else:
            self._log(tr("[ERROR] SIMIND exited with code {code}.").format(code=exit_code), color="#ff6b6b")

        self._process = None

    def _on_stop(self):
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self.btn_run_sim.setEnabled(True)
            self.sim_progress.setVisible(False)
            self.lbl_sim_status.setText(tr("Stopped by user."))
            self._log(tr("[WARN] Process terminated by user."), color="#ffa726")

