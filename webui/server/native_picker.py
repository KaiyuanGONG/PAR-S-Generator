"""Short-lived GUI-main-thread helper for native Windows path selection."""

from __future__ import annotations

import argparse
import json

from PyQt6.QtWidgets import QApplication, QFileDialog


def select_path(kind: str, initial_path: str) -> str:
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(True)
    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, False)
    dialog.setDirectory(initial_path)
    if kind in {"runs_root", "export_root"}:
        dialog.setWindowTitle("Select folder")
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    else:
        dialog.setWindowTitle("Select file")
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        file_filter = (
            "SIMIND executable (*.exe)"
            if kind == "simind_exe"
            else "SIMIND change file (*.smc)"
        )
        dialog.setNameFilter(file_filter)
    return dialog.selectedFiles()[0] if dialog.exec() and dialog.selectedFiles() else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "kind",
        choices=("simind_exe", "smc", "runs_root", "export_root"),
    )
    parser.add_argument("initial_path")
    args = parser.parse_args(argv)
    print(json.dumps({"path": select_path(args.kind, args.initial_path)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
