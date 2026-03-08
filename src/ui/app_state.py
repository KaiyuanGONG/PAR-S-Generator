"""
Shared application state for cross-page workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from core.batch_stats import BatchStats
from core.phantom_generator import PhantomConfig, PhantomResult
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


class AppState(QObject):
    phantom_config_changed = pyqtSignal(object)
    preview_result_changed = pyqtSignal(object)
    simulation_config_changed = pyqtSignal(object)
    batch_stats_changed = pyqtSignal(object)
    settings_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings_store = SettingsStore()
        self._settings = AppSettings()
        self._phantom_config = PhantomConfig()
        self._simulation_config = SimulationConfig()
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

    def _sync_defaults_from_settings(self) -> None:
        self._phantom_config.output_dir = self._settings.default_output or self._phantom_config.output_dir
        self._simulation_config.npz_dir = self._phantom_config.output_dir
        self._simulation_config.simind_exe = self._settings.simind_exe
        self._simulation_config.smc_file = self._settings.default_smc
        if not self._simulation_config.interfile_dir.strip():
            self._simulation_config.interfile_dir = "output/interfile"
        if not self._simulation_config.sim_output_dir.strip():
            self._simulation_config.sim_output_dir = "output/simind"
