"""PAR-S Generator native Windows v1 Web entrypoint."""

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from windows_launcher import AlreadyRunningError, run_windows_web


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start PAR-S Generator Windows v1")
    parser.add_argument("--port", type=int, default=8765, help="preferred loopback port")
    parser.add_argument("--no-browser", action="store_true", help="do not open the default browser")
    args = parser.parse_args(argv)
    try:
        return run_windows_web(
            REPO_ROOT,
            preferred_port=args.port,
            open_browser=not args.no_browser,
        )
    except AlreadyRunningError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
