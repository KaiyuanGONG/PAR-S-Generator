"""Session-scoped filesystem browsing and native Windows file selection.

Roots are an explicit allowlist: repo root, the configured runs root, plus any
extra roots from the PARS_FS_ROOTS environment variable (path separator
delimited). Anything outside resolves to 403.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from core.windows_runtime import WindowsPathError, validate_windows_path


_SESSION_ROOTS: set[Path] = set()
_SESSION_ROOTS_LOCK = threading.Lock()


def allowed_roots(repo_root: Path) -> list[Path]:
    roots = [repo_root.resolve()]
    extra = os.environ.get("PARS_FS_ROOTS", "")
    for token in extra.split(os.pathsep):
        token = token.strip()
        if token and Path(token).exists():
            roots.append(Path(token).resolve())
    with _SESSION_ROOTS_LOCK:
        roots.extend(sorted(_SESSION_ROOTS, key=str))
    return roots


def _native_dialog(kind: str, initial_path: str) -> str:
    """Open one native dialog; kept injectable so API tests never show UI."""
    from PyQt6.QtWidgets import QApplication, QFileDialog

    application = QApplication.instance() or QApplication([])
    if kind in {"runs_root", "export_root"}:
        return QFileDialog.getExistingDirectory(None, "Select folder", initial_path)
    file_filter = "SIMIND executable (*.exe)" if kind == "simind_exe" else "SIMIND change file (*.smc)"
    selected, _ = QFileDialog.getOpenFileName(None, "Select file", initial_path, file_filter)
    # Keep the application referenced until the modal dialog has returned.
    _ = application
    return selected


def pick_native_path(kind: str, initial_path: str, repo_root: Path) -> dict:
    if kind not in {"simind_exe", "smc", "runs_root", "export_root"}:
        return {"error": "unsupported_kind", "kind": kind}
    initial = initial_path or str(repo_root)
    selected = _native_dialog(kind, initial)
    if not selected:
        return {"cancelled": True, "path": None}
    try:
        resolved = validate_windows_path(selected, kind, base=repo_root)
    except WindowsPathError as exc:
        return {"error": "invalid_windows_path", "detail": str(exc), "path": selected}
    authorized_root = resolved if kind in {"runs_root", "export_root"} else resolved.parent
    with _SESSION_ROOTS_LOCK:
        _SESSION_ROOTS.add(authorized_root.resolve())
    return {"cancelled": False, "path": str(resolved)}


def _inside(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def list_dir(path_str: str, repo_root: Path) -> dict:
    roots = allowed_roots(repo_root)
    requested = Path(path_str) if path_str else repo_root
    path = (requested if requested.is_absolute() else repo_root / requested).resolve()
    if not _inside(path, roots):
        return {"error": "outside_allowed_roots", "roots": [str(r) for r in roots]}
    if not path.is_dir():
        return {"error": "not_a_directory", "path": str(path)}
    entries = []
    try:
        children = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        return {"error": str(exc), "path": str(path)}
    for child in children[:500]:
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue
    parent = str(path.parent) if _inside(path.parent, roots) and path.parent != path else None
    return {"path": str(path.resolve()), "parent": parent, "entries": entries,
            "roots": [str(r) for r in roots]}


def validate_path(path_str: str, kind: str, repo_root: Path) -> dict:
    roots = allowed_roots(repo_root)
    path = Path(path_str)
    path = (path if path.is_absolute() else repo_root / path).resolve()
    if not _inside(path, roots):
        return {
            "error": "outside_allowed_roots",
            "path": str(path),
            "roots": [str(root) for root in roots],
        }
    if kind not in {"simind_exe", "smc", "runs_root", "export_root"}:
        return {"error": "unsupported_kind", "path": str(path), "kind": kind}
    try:
        path = validate_windows_path(path, kind, base=repo_root)
    except WindowsPathError as exc:
        return {
            "error": "invalid_windows_path",
            "path": str(path),
            "kind": kind,
            "valid": False,
            "detail": str(exc),
        }
    ok, detail = False, ""
    if kind == "simind_exe":
        ok = path.is_file() and path.suffix.lower() == ".exe"
        detail = "existing .exe file required"
    elif kind == "smc":
        ok = path.is_file() and path.suffix.lower() == ".smc"
        if ok:
            try:
                from core.smc_parser import parse_smc
                parse_smc(path)
            except Exception as exc:   # noqa: BLE001 — surface parser message
                ok, detail = False, f"unparseable smc: {exc}"
    elif kind in {"runs_root", "export_root"}:
        ok = path.is_dir() or (path.parent.is_dir() and not path.exists())
        detail = "existing or creatable directory required"
    return {"path": str(path), "kind": kind, "valid": bool(ok), "detail": detail}
