"""
Shared application state for cross-page workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from core.batch_stats import BatchStats
from core.phantom_generator import PhantomConfig, PhantomResult
from pipeline.contracts import (
    ACTIVITY_TIME_CONTRACT_STATUS,
    DEFAULT_EXPOSURE_S_PER_PROJECTION,
    DEFAULT_SIMIND_ACTIVITY_TIME,
    DEFAULT_SOURCE_ACTIVITY_MBQ,
    EMPIRICAL_OBSERVATION_PROTOCOL_STATUS,
)
from ui.settings_store import SettingsStore


@dataclass
class AppSettings:
    simind_exe: str = ""
    default_smc: str = ""
    default_output: str = "output/syn3d"
    theme: str = "dark"
    language: str = "en"
    autosave_config: bool = True


@dataclass
class SimulationConfig:
    npz_dir: str = ""
    interfile_dir: str = "output/interfile"
    simind_exe: str = ""
    smc_file: str = ""
    sim_output_dir: str = "output/simind"
    nn_multiplier: int = 10
    max_parallel: int = 3
    case_start: int = 0
    case_end: int = 99999
    skip_completed: bool = True
    custom_overrides: list = field(default_factory=list)
    simulation_mode: str = "prepare"
    create_poisson_observation: bool = True
    observation_scale: float = 1.0
    observation_protocol_status: str = EMPIRICAL_OBSERVATION_PROTOCOL_STATUS
    observation_policy: str = "empirical_total_counts"


@dataclass
class PipelineProjectConfig:
    run_id: str = "liver-spect-run"
    runs_root: str = "runs"
    protocol_label: str = "GE 870 CZT current liver SPECT research protocol"
    protocol_status: str = "stage3_protocol_promoted_pilot_pending"
    source_activity_mbq: float = DEFAULT_SOURCE_ACTIVITY_MBQ
    exposure_time_s_per_projection: float | None = DEFAULT_EXPOSURE_S_PER_PROJECTION
    smc_index25_activity_time: float = DEFAULT_SIMIND_ACTIVITY_TIME
    activity_time_contract_status: str = ACTIVITY_TIME_CONTRACT_STATUS


class AppState(QObject):
    phantom_config_changed = pyqtSignal(object)
    preview_result_changed = pyqtSignal(object)
    simulation_config_changed = pyqtSignal(object)
    batch_stats_changed = pyqtSignal(object)
    settings_changed = pyqtSignal(object)
    project_config_changed = pyqtSignal(object)
    current_run_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings_store = SettingsStore()
        self._settings = AppSettings()
        self._phantom_config = PhantomConfig()
        self._simulation_config = SimulationConfig()
        self._project_config = PipelineProjectConfig()
        self._current_run = ""
        self._last_preview: PhantomResult | None = None
        self._batch_stats: BatchStats | None = None
        self.load_settings()
        self._sync_defaults_from_settings()

    @staticmethod
    def _as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def phantom_config(self) -> PhantomConfig:
        return self._phantom_config

    @property
    def simulation_config(self) -> SimulationConfig:
        return self._simulation_config

    @property
    def project_config(self) -> PipelineProjectConfig:
        return self._project_config

    @property
    def current_run(self) -> str:
        return self._current_run

    @property
    def last_preview(self) -> PhantomResult | None:
        return self._last_preview

    @property
    def batch_stats(self) -> BatchStats | None:
        return self._batch_stats

    def load_settings(self) -> AppSettings:
        payload = self._settings_store.load()
        self._settings = AppSettings(
            simind_exe=str(payload["simind"].get("exe", "")),
            default_smc=str(payload["simind"].get("default_smc", "")),
            default_output=str(payload["paths"].get("default_output", "output/syn3d")),
            theme=str(payload["appearance"].get("theme", "dark")),
            language=str(payload["appearance"].get("language", "en")),
            autosave_config=self._as_bool(payload["perf"].get("autosave", True)),
        )
        self.settings_changed.emit(self._settings)
        return self._settings

    def save_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        sim_cfg = self._simulation_config
        self._settings_store.save(
            {
                "simind": {
                    "exe": settings.simind_exe,
                    "default_smc": settings.default_smc,
                },
                "paths": {
                    "default_output": settings.default_output,
                },
                "appearance": {
                    "theme": settings.theme,
                    "language": settings.language,
                },
                "perf": {
                    "autosave": settings.autosave_config,
                },
                "simulation": {
                    "nn_multiplier": sim_cfg.nn_multiplier,
                    "max_parallel": sim_cfg.max_parallel,
                    "skip_completed": sim_cfg.skip_completed,
                    "custom_overrides": list(sim_cfg.custom_overrides),
                },
            }
        )
        self._sync_defaults_from_settings()
        self.settings_changed.emit(self._settings)

    def reset_settings(self) -> AppSettings:
        self._settings_store.clear()
        return self.load_settings()

    def set_phantom_config(self, config: PhantomConfig) -> None:
        self._phantom_config = config
        self._simulation_config.npz_dir = config.output_dir
        if not self._simulation_config.interfile_dir.strip():
            self._simulation_config.interfile_dir = str(Path(config.output_dir).parent / "interfile")
        self.phantom_config_changed.emit(config)
        self.simulation_config_changed.emit(self._simulation_config)

    def set_preview_result(self, result: PhantomResult | None) -> None:
        self._last_preview = result
        self.preview_result_changed.emit(result)

    def set_simulation_config(self, config: SimulationConfig) -> None:
        self._simulation_config = config
        self.simulation_config_changed.emit(config)

    def set_batch_stats(self, stats: BatchStats | None) -> None:
        self._batch_stats = stats
        self.batch_stats_changed.emit(stats)

    def set_project_config(self, config: PipelineProjectConfig) -> None:
        self._project_config = config
        self.project_config_changed.emit(config)

    def set_current_run(self, path: str) -> None:
        self._current_run = str(path)
        self.current_run_changed.emit(self._current_run)

    def _sync_defaults_from_settings(self) -> None:
        self._phantom_config.output_dir = self._settings.default_output or self._phantom_config.output_dir
        self._simulation_config.npz_dir = self._phantom_config.output_dir
        self._simulation_config.simind_exe = self._settings.simind_exe
        self._simulation_config.smc_file = self._settings.default_smc
        if not self._simulation_config.interfile_dir.strip():
            self._simulation_config.interfile_dir = "output/interfile"
        if not self._simulation_config.sim_output_dir.strip():
            self._simulation_config.sim_output_dir = "output/simind"
        # Load simulation runtime defaults from settings
        payload = self._settings_store.load()
        sim = payload.get("simulation", {})
        self._simulation_config.nn_multiplier = int(sim.get("nn_multiplier", 10))
        self._simulation_config.max_parallel = int(sim.get("max_parallel", 3))
        self._simulation_config.skip_completed = self._as_bool(sim.get("skip_completed", True))
        raw_ov = sim.get("custom_overrides", [])
        self._simulation_config.custom_overrides = [
            (int(pair[0]), str(pair[1]))
            for pair in raw_ov
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        ]
