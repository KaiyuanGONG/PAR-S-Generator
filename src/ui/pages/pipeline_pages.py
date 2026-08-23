"""Six-stage synthetic-data workspace pages around the shared PipelineRunner."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.smc_parser import parse_smc
from pipeline.contracts import read_jsonl
from pipeline.experiments import prepare_all_experiments
from pipeline.runner import PipelineConfig, PipelinePaused, PipelineRunner
from ui.app_state import AppState, PipelineProjectConfig, SimulationConfig
from ui.widgets.simind_viewer import SimindOutputViewer


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _project_path(value: str, fallback: str) -> Path:
    """Resolve bundled defaults independently of the GUI process CWD."""
    candidate = Path(value.strip() or fallback)
    resolved = candidate.resolve() if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()
    bundled = (PROJECT_ROOT / fallback).resolve()
    # A stale installation-relative default may resolve to (for example)
    # C:\simind\..., even though the bundled project file is present.  Only
    # repair the known fallback filename; never redirect an explicit custom
    # path silently.
    if not resolved.exists() and candidate.name.lower() == Path(fallback).name.lower() and bundled.exists():
        return bundled
    return resolved


def _title(title: str, subtitle: str) -> tuple[QLabel, QLabel]:
    heading = QLabel(title)
    heading.setObjectName("page_title")
    detail = QLabel(subtitle)
    detail.setObjectName("page_subtitle")
    detail.setWordWrap(True)
    return heading, detail


def _banner(text: str, state: str = "warning") -> QLabel:
    label = QLabel(text)
    label.setObjectName("validation_banner")
    label.setProperty("state", state)
    label.setWordWrap(True)
    return label


class ProjectProtocolPage(QWidget):
    """Run identity and protocol evidence, not a generic settings dump."""

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        heading, detail = _title(
            "Project / Protocol",
            "Name one immutable run and record the current GE 870 CZT liver SPECT protocol contract.",
        )
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addWidget(
            _banner(
                "Local GE 870 evidence supports 60 MBq × 28.4 s (Index-25 = 1704); orientation, "
                "scoped type−7 attenuation, native detector FOV, response and RR/NN controls passed. "
                "Stage 3 promotion and a corrected pilot are next; formal production is not yet authorized.",
                "warning",
            )
        )

        identity = QGroupBox("RUN IDENTITY")
        form = QFormLayout(identity)
        self.edit_run_id = QLineEdit(app_state.project_config.run_id)
        configured_root = Path(app_state.project_config.runs_root)
        if not configured_root.is_absolute():
            configured_root = (PROJECT_ROOT / configured_root).resolve()
        self.edit_runs_root = QLineEdit(str(configured_root))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse_root)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.edit_runs_root, 1)
        row_layout.addWidget(browse)
        form.addRow("Run ID", self.edit_run_id)
        form.addRow("Runs root", row)
        layout.addWidget(identity)

        protocol = QGroupBox("EFFECTIVE ACQUISITION CONTRACT")
        pform = QFormLayout(protocol)
        self.edit_protocol = QLineEdit(app_state.project_config.protocol_label)
        self.spin_activity = QDoubleSpinBox()
        self.spin_activity.setRange(0.001, 100000.0)
        self.spin_activity.setDecimals(3)
        self.spin_activity.setValue(app_state.project_config.source_activity_mbq)
        exposure = app_state.project_config.exposure_time_s_per_projection
        self.edit_exposure = QLineEdit("" if exposure is None else f"{exposure:g}")
        self.edit_exposure.setPlaceholderText("Seconds per projection")
        self.spin_index25 = QDoubleSpinBox()
        self.spin_index25.setRange(0.0, 1e9)
        self.spin_index25.setDecimals(3)
        self.spin_index25.setValue(app_state.project_config.smc_index25_activity_time)
        pform.addRow("Protocol label", self.edit_protocol)
        pform.addRow("Source activity (MBq)", self.spin_activity)
        pform.addRow("Seconds / projection", self.edit_exposure)
        pform.addRow("SMC Index-25", self.spin_index25)
        layout.addWidget(protocol)

        self.btn_apply = QPushButton("Apply project contract")
        self.btn_apply.setObjectName("primary_btn")
        self.btn_apply.clicked.connect(self.apply)
        layout.addWidget(self.btn_apply)
        layout.addStretch()

    def _browse_root(self):
        path = QFileDialog.getExistingDirectory(self, "Choose runs root")
        if path:
            self.edit_runs_root.setText(path)

    def apply(self):
        run_id = self.edit_run_id.text().strip()
        root = self.edit_runs_root.text().strip()
        if not run_id or not root:
            QMessageBox.warning(self, "Project contract", "Run ID and runs root are required.")
            return
        exposure_text = self.edit_exposure.text().strip()
        try:
            exposure = float(exposure_text) if exposure_text else None
        except ValueError:
            QMessageBox.warning(self, "Project contract", "Seconds / projection must be numeric or blank.")
            return
        activity = float(self.spin_activity.value())
        index25 = float(self.spin_index25.value())
        if exposure is not None and not np.isclose(activity * exposure, index25, rtol=1e-6, atol=1e-3):
            QMessageBox.warning(
                self,
                "Project contract",
                "SMC Index-25 must equal source activity × seconds per projection.",
            )
            return
        nominal_default = bool(
            exposure is not None
            and np.isclose(activity, 60.0)
            and np.isclose(exposure, 28.4)
            and np.isclose(index25, 1704.0)
        )
        contract = PipelineProjectConfig(
            run_id=run_id,
            runs_root=root,
            protocol_label=self.edit_protocol.text().strip(),
            protocol_status="stage3_protocol_promoted_pilot_pending",
            source_activity_mbq=activity,
            exposure_time_s_per_projection=exposure,
            smc_index25_activity_time=index25,
            activity_time_contract_status=(
                "resolved_nominal_60mbq_x_28p4s_index25_1704_local_dicom_supported"
                if nominal_default
                else (
                    "operator_supplied_product_consistent_pending_protocol_validation"
                    if exposure is not None
                    else "missing_exposure_time"
                )
            ),
        )
        self.app_state.set_project_config(contract)
        self.btn_apply.setText("Applied ✓")


class SimulationSetupPage(QWidget):
    """Simulation and observation configuration; execution belongs to Run."""

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        heading, detail = _title(
            "Simulation",
            "Review the exact SIMIND inputs and select how expectation and optional observation artifacts are produced.",
        )
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addWidget(
            _banner(
                "The analytical attenuation map is float32 C-order μ in cm⁻¹ at 140.5 keV. "
                "The tested type−7 export stores μ×voxel width and passed readback/transmission QC. "
                "Production export promotion and a corrected pilot are still required.",
                "warning",
            )
        )

        paths = QGroupBox("SIMIND PROVENANCE")
        form = QFormLayout(paths)
        self.edit_exe = QLineEdit(
            str(_project_path(app_state.simulation_config.simind_exe, "simind/simind.exe"))
        )
        self.edit_smc = QLineEdit(
            str(_project_path(app_state.simulation_config.smc_file, "simind/ge870_czt.smc"))
        )
        self.spin_nn = QSpinBox()
        self.spin_nn.setRange(0, 1000000)
        self.spin_nn.setValue(app_state.simulation_config.nn_multiplier)
        form.addRow("Executable", self._path_row(self.edit_exe, "file"))
        form.addRow("SMC file", self._path_row(self.edit_smc, "smc"))
        form.addRow("History multiplier", self.spin_nn)
        layout.addWidget(paths)

        behavior = QGroupBox("DATA STAGES")
        bform = QFormLayout(behavior)
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Prepare commands only", "prepare")
        self.combo_mode.addItem("Deterministic mock (QC tests only)", "mock")
        self.combo_mode.addItem("Execute SIMIND", "execute")
        current_mode = app_state.simulation_config.simulation_mode
        self.combo_mode.setCurrentIndex(max(0, self.combo_mode.findData(current_mode)))
        self.chk_poisson = QCheckBox("Create a separate offline Poisson observation")
        self.chk_poisson.setChecked(app_state.simulation_config.create_poisson_observation)
        self.combo_obs_policy = QComboBox()
        self.combo_obs_policy.addItem("Empirical total-count distribution", "empirical_total_counts")
        self.combo_obs_policy.addItem("Fixed research scale", "fixed_scale")
        self.combo_obs_policy.setCurrentIndex(
            max(
                0,
                self.combo_obs_policy.findData(
                    app_state.simulation_config.observation_policy
                ),
            )
        )
        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setRange(1e-6, 1e9)
        self.spin_scale.setDecimals(6)
        self.spin_scale.setValue(app_state.simulation_config.observation_scale)
        self.combo_obs_status = QComboBox()
        self.combo_obs_status.addItem(
            "Empirical protocol matching (not absolute cps/MBq)",
            "empirical_protocol_matching",
        )
        self.combo_obs_status.addItem("Toy / pipeline test", "toy")
        self.combo_obs_status.addItem("Research assumption", "research")
        self.combo_obs_status.addItem("Verified protocol", "verified")
        self.combo_obs_status.setCurrentIndex(
            max(
                0,
                self.combo_obs_status.findData(
                    app_state.simulation_config.observation_protocol_status
                ),
            )
        )
        bform.addRow("Expectation backend", self.combo_mode)
        bform.addRow("Observation", self.chk_poisson)
        bform.addRow("Count policy", self.combo_obs_policy)
        bform.addRow("Observation scale", self.spin_scale)
        bform.addRow("Scale status", self.combo_obs_status)
        layout.addWidget(behavior)

        self.smc_summary = QPlainTextEdit()
        self.smc_summary.setReadOnly(True)
        self.smc_summary.setMaximumHeight(190)
        layout.addWidget(self.smc_summary)
        actions = QHBoxLayout()
        self.chk_expert = QCheckBox("Expert mode: show raw SIMIND Index / Flag values")
        self.chk_expert.setChecked(False)
        self.chk_expert.toggled.connect(lambda _: self._refresh_smc_summary())
        self.btn_apply = QPushButton("Apply simulation contract")
        self.btn_apply.setObjectName("primary_btn")
        self.btn_apply.clicked.connect(self.apply)
        self.btn_experiments = QPushButton("Prepare five validation experiments…")
        self.btn_experiments.clicked.connect(self._prepare_experiments)
        actions.addWidget(self.chk_expert)
        actions.addWidget(self.btn_apply)
        actions.addWidget(self.btn_experiments)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self._refresh_smc_summary()

    def _path_row(self, edit: QLineEdit, kind: str) -> QWidget:
        row = QWidget()
        inner = QHBoxLayout(row)
        inner.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("Choose…")
        button.clicked.connect(lambda: self._browse_file(edit, kind))
        inner.addWidget(edit, 1)
        inner.addWidget(button)
        return row

    def _browse_file(self, edit: QLineEdit, kind: str):
        filter_text = "SIMIND config (*.smc)" if kind == "smc" else "Executable (*.exe);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Choose file", "", filter_text)
        if path:
            edit.setText(path)
            self._refresh_smc_summary()

    def _refresh_smc_summary(self):
        try:
            smc_path = _project_path(self.edit_smc.text(), "simind/ge870_czt.smc")
            smc = parse_smc(smc_path)
            activity_time = float(smc.get_value(25))
            activity_note = (
                "locally supported nominal 60 MBq × 28.4 s"
                if np.isclose(activity_time, 1704.0, rtol=1e-6, atol=1e-3)
                else "operator SMC value; protocol reconciliation required"
            )
            lines = [
                "Requested SIMIND configuration",
                f"Source: {smc_path}",
                f"Description: {smc.description}",
                f"Photon energy: {smc.get_value(1):g} keV; window: "
                f"{smc.get_value(21):g}–{smc.get_value(20):g} keV",
                f"Acquisition: {smc.get_value(29):g} views; rotation radius {smc.get_value(12):g} cm",
                f"Source/density sampling: {smc.get_value(81):g} × {smc.get_value(82):g} × "
                f"{smc.get_value(34):g}; {smc.get_value(31):g} cm voxels",
                f"CZT native detector request: {smc.get_value(100):g} × {smc.get_value(101):g}; "
                f"{smc.get_value(95):g} cm pitch",
                "Effective GE detector override: 160 × 208 [Stage-3 promoted]",
                "Type−7 attenuation: stored μ×0.442 cm; runtime density threshold: 100",
                f"Activity–time value: {activity_time:g} [{activity_note}]",
                f"Runtime history multiplier: {self.spin_nn.value():g} [run configuration]",
            ]
            if self.chk_expert.isChecked():
                lines.extend(
                    [
                        "",
                        "Expert raw fields",
                        f"Index-14={smc.get_value(14):g}; Index-15={smc.get_value(15):g}; "
                        f"Index-25={smc.get_value(25):g}; Index-26={smc.get_value(26):g}",
                        f"Index-81={smc.get_value(81):g}; Index-82={smc.get_value(82):g}; "
                        f"Index-100={smc.get_value(100):g}; Index-101={smc.get_value(101):g}",
                        "Enabled Flags: " + ", ".join(
                            str(index) for index, enabled in enumerate(smc.flags, 1) if enabled
                        ),
                    ]
                )
            self.smc_summary.setPlainText("\n".join(lines))
        except Exception as exc:
            self.smc_summary.setPlainText(f"SMC cannot be parsed: {exc}")

    def apply(self):
        config = SimulationConfig(
            simind_exe=self.edit_exe.text().strip(),
            smc_file=self.edit_smc.text().strip(),
            nn_multiplier=int(self.spin_nn.value()),
            simulation_mode=str(self.combo_mode.currentData()),
            create_poisson_observation=self.chk_poisson.isChecked(),
            observation_scale=float(self.spin_scale.value()),
            observation_protocol_status=str(self.combo_obs_status.currentData()),
            observation_policy=str(self.combo_obs_policy.currentData()),
        )
        if (
            config.observation_policy == "empirical_total_counts"
            and config.observation_protocol_status != "empirical_protocol_matching"
        ):
            QMessageBox.warning(
                self,
                "Observation contract",
                "Empirical total-count matching requires the empirical protocol status.",
            )
            return
        if config.observation_protocol_status == "verified" and self.app_state.project_config.protocol_status != "verified":
            QMessageBox.warning(
                self,
                "Observation contract",
                "The project protocol is not verified; the observation scale cannot be labelled verified.",
            )
            return
        self.app_state.set_simulation_config(config)
        self._refresh_smc_summary()
        self.btn_apply.setText("Applied ✓")

    def _prepare_experiments(self):
        destination = QFileDialog.getExistingDirectory(self, "Choose experiment package parent")
        if not destination:
            return
        try:
            roots = prepare_all_experiments(
                Path(destination),
                simind_exe=Path(self.edit_exe.text()),
                smc_file=Path(self.edit_smc.text()),
            )
            QMessageBox.information(
                self,
                "Experiments prepared",
                f"Prepared {len(roots)} read-only command packages. SIMIND was not launched.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Experiment preparation failed", str(exc))


class PipelineWorker(QThread):
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str)
    paused = pyqtSignal(str, str)

    def __init__(self, config: PipelineConfig, resume: bool):
        super().__init__()
        self.config = config
        self.resume = resume
        self.runner: PipelineRunner | None = None

    def request_pause(self):
        if self.runner is not None:
            self.runner.request_pause()

    def run(self):
        try:
            self.runner = PipelineRunner(self.config, resume=self.resume)
            state = self.runner.run_all(finalize=False)
            self.completed.emit(str(self.runner.layout.root), state)
        except PipelinePaused as exc:
            run_path = str(self.runner.layout.root) if self.runner is not None else ""
            self.paused.emit(run_path, str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class RunPage(QWidget):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        self.worker: PipelineWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        heading, detail = _title(
            "Run",
            "One runner creates an isolated directory, generates cases, validates phantoms, exports binaries, and prepares or executes projection jobs.",
        )
        layout.addWidget(heading)
        layout.addWidget(detail)
        self.contract = QPlainTextEdit()
        self.contract.setReadOnly(True)
        layout.addWidget(self.contract, 1)
        actions = QHBoxLayout()
        self.btn_start = QPushButton("Create isolated run")
        self.btn_start.setObjectName("primary_btn")
        self.btn_start.clicked.connect(lambda: self._start(False))
        self.btn_resume = QPushButton("Resume verified run")
        self.btn_resume.clicked.connect(lambda: self._start(True))
        self.btn_pause = QPushButton("Pause after current case")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._pause)
        actions.addWidget(self.btn_start)
        actions.addWidget(self.btn_resume)
        actions.addWidget(self.btn_pause)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = _banner("No run has started.", "warning")
        layout.addWidget(self.status)
        for signal in (
            app_state.project_config_changed,
            app_state.phantom_config_changed,
            app_state.simulation_config_changed,
        ):
            signal.connect(lambda _=None: self.refresh_contract())
        self.refresh_contract()

    def _config(self) -> PipelineConfig:
        project = self.app_state.project_config
        sim = self.app_state.simulation_config
        return PipelineConfig(
            run_id=project.run_id,
            runs_root=project.runs_root,
            phantom=self.app_state.phantom_config,
            simind_exe=sim.simind_exe or "simind/simind.exe",
            smc_file=sim.smc_file or "simind/ge870_czt.smc",
            nn_multiplier=sim.nn_multiplier,
            simind_overrides=list(sim.custom_overrides),
            simulation_mode=sim.simulation_mode,
            create_poisson_observation=sim.create_poisson_observation,
            observation_scale=sim.observation_scale,
            observation_protocol_status=sim.observation_protocol_status,
            observation_policy=sim.observation_policy,
            protocol_label=project.protocol_label,
            protocol_status=project.protocol_status,
            source_activity_mbq=project.source_activity_mbq,
            exposure_time_s_per_projection=project.exposure_time_s_per_projection,
            smc_index25_activity_time=project.smc_index25_activity_time,
            activity_time_contract_status=project.activity_time_contract_status,
        )

    def refresh_contract(self):
        try:
            payload = self._config().to_dict()
            self.contract.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))
        except Exception as exc:
            self.contract.setPlainText(f"Invalid effective contract: {exc}")

    def _start(self, resume: bool):
        if self.worker and self.worker.isRunning():
            return
        config = self._config()
        if config.simulation_mode == "execute":
            answer = QMessageBox.question(
                self,
                "Launch SIMIND jobs?",
                "This will execute the reviewed SIMIND command plan. Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.btn_start.setEnabled(False)
        self.btn_resume.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.status.setText("Run in progress. Artifacts are isolated under the selected run ID.")
        self.worker = PipelineWorker(config, resume)
        self.worker.completed.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.paused.connect(self._paused)
        self.worker.start()

    def _pause(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_pause()
            self.status.setText("Pause requested. The current case will finish before state is saved atomically.")
            self.btn_pause.setEnabled(False)

    def _done(self, path: str, state: dict):
        self.btn_start.setEnabled(True)
        self.btn_resume.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.app_state.set_current_run(path)
        self.status.setProperty("state", "ok")
        self.status.setText(f"Run package prepared: {path}\nUse QC / Dataset to inspect evidence, then Finalize.")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _failed(self, message: str):
        self.btn_start.setEnabled(True)
        self.btn_resume.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.status.setProperty("state", "error")
        self.status.setText(f"Run stopped: {message}")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _paused(self, path: str, message: str):
        self.btn_start.setEnabled(True)
        self.btn_resume.setEnabled(True)
        self.btn_pause.setEnabled(False)
        if path:
            self.app_state.set_current_run(path)
        self.status.setProperty("state", "warning")
        self.status.setText(message)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class QCDatasetPage(QWidget):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        heading, detail = _title(
            "QC / Dataset",
            "Inspect stage gates, immutable case assignments, checksums, and projections in the same canonical orientation used downstream.",
        )
        layout.addWidget(heading)
        layout.addWidget(detail)
        controls = QHBoxLayout()
        self.lbl_run = QLabel("No current run")
        self.lbl_run.setObjectName("value_label")
        refresh = QPushButton("Refresh evidence")
        refresh.clicked.connect(self.refresh)
        controls.addWidget(self.lbl_run, 1)
        controls.addWidget(refresh)
        layout.addLayout(controls)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.stage_table = QTableWidget(0, 3)
        self.stage_table.setHorizontalHeaderLabels(["Stage", "Status", "Evidence"])
        self.stage_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        left_layout.addWidget(self.stage_table)
        self.case_table = QTableWidget(0, 6)
        self.case_table.setHorizontalHeaderLabels(
            ["Case", "Split", "Phantom QC", "Projection QC", "Backend", "Effective .res values"]
        )
        self.case_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        left_layout.addWidget(self.case_table)
        splitter.addWidget(left)
        self.viewer = SimindOutputViewer()
        splitter.addWidget(self.viewer)
        splitter.setSizes([730, 520])
        layout.addWidget(splitter, 1)
        app_state.current_run_changed.connect(lambda _: self.refresh())

    def refresh(self):
        if not self.app_state.current_run:
            return
        root = Path(self.app_state.current_run)
        try:
            run = json.loads((root / "run.json").read_text(encoding="utf-8"))
            cases = read_jsonl(root / "cases.jsonl")
        except Exception as exc:
            QMessageBox.critical(self, "QC read failed", str(exc))
            return
        self.lbl_run.setText(str(root))
        stages = run.get("stages", {})
        self.stage_table.setRowCount(len(stages))
        for row, (stage, evidence) in enumerate(stages.items()):
            self.stage_table.setItem(row, 0, QTableWidgetItem(stage))
            self.stage_table.setItem(row, 1, QTableWidgetItem(str(evidence.get("status", "unknown"))))
            compact = {k: v for k, v in evidence.items() if k not in {"status", "updated_utc"}}
            self.stage_table.setItem(row, 2, QTableWidgetItem(json.dumps(compact, ensure_ascii=False)))
        self.case_table.setRowCount(len(cases))
        first_expectation = None
        for row, case in enumerate(cases):
            expectation = case.get("expectation", {})
            projection_qc = case.get("qc", {}).get("projection", {})
            effective_res = "not produced"
            qc_path = Path(projection_qc.get("path", ""))
            if qc_path.is_file():
                qc_payload = json.loads(qc_path.read_text(encoding="utf-8"))
                effective = qc_payload.get("res_effective", {})
                if effective.get("projection_count"):
                    effective_res = (
                        f"{effective.get('projection_count')} views; "
                        f"{effective.get('detector_matrix_i', '?')}×{effective.get('detector_matrix_j', '?')}; "
                        f"{effective.get('detector_pitch_cm', '?')} cm pitch; "
                        f"{effective.get('photon_energy_kev', '?')} keV; "
                        f"activity–time {effective.get('activity_time_value', '?')}; "
                        f"NN {effective.get('nn_scaling_factor', '?')} [source: .res]"
                    )
                elif expectation.get("backend") == "deterministic_mock_not_simind":
                    effective_res = "mock artifact; no SIMIND effective values"
            values = [
                case["case_id"],
                case.get("split", ""),
                case.get("qc", {}).get("phantom", {}).get("status", "pending"),
                projection_qc.get("status", "pending"),
                expectation.get("backend", "not produced"),
                effective_res,
            ]
            for col, value in enumerate(values):
                self.case_table.setItem(row, col, QTableWidgetItem(str(value)))
            if first_expectation is None and expectation.get("a00"):
                first_expectation = expectation["a00"]
        if first_expectation and Path(first_expectation).exists():
            self.viewer.load_file(first_expectation)


class FinalizePage(QWidget):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        heading, detail = _title(
            "Finalize",
            "Seal a dataset only after required QC gates pass. Finalization writes the stable split and checksum manifest; it does not process images further.",
        )
        layout.addWidget(heading)
        layout.addWidget(detail)
        self.checklist = QPlainTextEdit()
        self.checklist.setReadOnly(True)
        layout.addWidget(self.checklist, 1)
        self.btn_finalize = QPushButton("Finalize dataset")
        self.btn_finalize.setObjectName("success_btn")
        self.btn_finalize.clicked.connect(self.finalize)
        layout.addWidget(self.btn_finalize)
        self.status = _banner("No current run.", "warning")
        layout.addWidget(self.status)
        app_state.current_run_changed.connect(lambda _: self.refresh())

    def refresh(self):
        if not self.app_state.current_run:
            return
        try:
            root = Path(self.app_state.current_run)
            run = json.loads((root / "run.json").read_text(encoding="utf-8"))
            lines = [f"Run: {root}", "", "Stage gates:"]
            for stage, evidence in run.get("stages", {}).items():
                lines.append(f"  {stage:<18} {evidence.get('status', 'unknown')}")
            lines.extend(
                [
                    "",
                    f"Finalized: {run.get('finalized', False)}",
                    "Required output: run.json, cases.jsonl, splits.json, dataset_manifest.json",
                ]
            )
            self.checklist.setPlainText("\n".join(lines))
            self.status.setText("Ready for review. Prepared-only SIMIND plans cannot be finalized as a dataset.")
        except Exception as exc:
            self.checklist.setPlainText(str(exc))

    def finalize(self):
        if not self.app_state.current_run:
            return
        try:
            runner = PipelineRunner.open(Path(self.app_state.current_run))
            state = runner.finalize()
            self.status.setProperty("state", "ok")
            self.status.setText(
                f"Dataset finalized. Manifest SHA-256: {state.get('package_sha256', 'not recorded')}"
            )
            self.refresh()
        except Exception as exc:
            self.status.setProperty("state", "error")
            self.status.setText(f"Finalization blocked: {exc}")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
