"""
File-backed settings store used instead of QSettings.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "simind": {
        "exe": "",
        "default_smc": "",
    },
    "paths": {
        "default_output": "output/syn3d",
    },
    "appearance": {
        "theme": "dark",
        "language": "en",
    },
    "perf": {
        "autosave": True,
    },
}


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or self.default_path()

    @staticmethod
    def default_path() -> Path:
        override = os.getenv("PAR_S_SETTINGS_PATH")
        if override:
            return Path(override)
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / "PAR-S Generator" / "settings.json"
        return SettingsStore.fallback_path()

    @staticmethod
    def fallback_path() -> Path:
        return Path.cwd() / ".par-s-generator" / "settings.json"

    @staticmethod
    def _exists(path: Path) -> bool:
        try:
            return path.exists()
        except PermissionError:
            return False

    def candidate_paths(self) -> list[Path]:
        paths = [self.path]
        fallback = self.fallback_path()
        if fallback not in paths:
            paths.append(fallback)
        return paths

    def load(self) -> dict[str, Any]:
        data = json.loads(json.dumps(DEFAULT_SETTINGS))
        for candidate in self.candidate_paths():
            if not self._exists(candidate):
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (PermissionError, OSError, json.JSONDecodeError):
                continue
            self.path = candidate
            return _deep_merge(data, payload)
        return data

    def save(self, data: dict[str, Any]) -> None:
        for candidate in self.candidate_paths():
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                self.path = candidate
                return
            except PermissionError:
                continue
        raise PermissionError("Unable to write settings file to any supported location.")

    def clear(self) -> None:
        for candidate in self.candidate_paths():
            try:
                if self._exists(candidate):
                    candidate.unlink()
            except PermissionError:
                continue


def _deep_merge(base: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    for key, value in payload.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base
