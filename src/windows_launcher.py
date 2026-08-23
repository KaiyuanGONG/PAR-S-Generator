"""Native Windows launcher for the local-only PAR-S Web workbench."""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


LOOPBACK_HOST = "127.0.0.1"


class AlreadyRunningError(RuntimeError):
    """Raised when another launcher owns the per-user instance lock."""


class SingleInstanceLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        import msvcrt

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            handle.close()
            raise AlreadyRunningError(
                "PAR-S Generator is already running in another process"
            ) from exc
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        import msvcrt

        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None


def select_loopback_port(preferred: int = 8765, *, attempts: int = 31) -> int:
    if not 1 <= preferred <= 65535:
        raise ValueError("port must be between 1 and 65535")
    for offset in range(attempts):
        candidate = preferred + offset
        if candidate > 65535:
            break
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((LOOPBACK_HOST, candidate))
        except OSError:
            continue
        finally:
            probe.close()
        return candidate
    raise RuntimeError(f"No free loopback port found from {preferred} across {attempts} attempts")


def _open_when_ready(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    health = f"{url}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=0.5) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.1)


def run_windows_web(
    repo_root: str | Path,
    *,
    preferred_port: int = 8765,
    open_browser: bool = True,
) -> int:
    import uvicorn

    root = Path(repo_root).resolve()
    port = select_loopback_port(preferred_port)
    url = f"http://{LOOPBACK_HOST}:{port}"
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    config = uvicorn.Config(
        "webui.server.app:app",
        host=LOOPBACK_HOST,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    lock_path = root / ".par-s-generator" / "windows-v1.lock"
    with SingleInstanceLock(lock_path):
        print(f"PAR-S Generator Windows v1: {url}")
        server.run()
    return 0
