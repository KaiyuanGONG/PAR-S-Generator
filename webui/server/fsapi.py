"""Server-side filesystem browsing (replaces native file dialogs).

Roots are an explicit allowlist: repo root, the configured runs root, plus any
extra roots from the PARS_FS_ROOTS environment variable (path separator
delimited). Anything outside resolves to 403.
"""

from __future__ import annotations

import os
from pathlib import Path


def allowed_roots(repo_root: Path) -> list[Path]:
    roots = [repo_root.resolve()]
    extra = os.environ.get("PARS_FS_ROOTS", "")
    for token in extra.split(os.pathsep):
        token = token.strip()
        if token and Path(token).exists():
            roots.append(Path(token).resolve())
    return roots


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
    path = Path(path_str) if path_str else repo_root
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
    path = Path(path_str)
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
    elif kind == "runs_root":
        ok = path.is_dir() or (path.parent.is_dir() and not path.exists())
        detail = "existing or creatable directory required"
    return {"path": str(path), "kind": kind, "valid": bool(ok), "detail": detail}
