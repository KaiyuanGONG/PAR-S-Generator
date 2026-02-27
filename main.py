"""
PAR-S Generator
===============
A Windows desktop application for generating synthetic liver SPECT data.
Integrates: phantom generation → format conversion → SIMIND simulation → visualization.

Author: Kaiyuan Gong
License: MIT
"""

import sys
import os
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QIcon, QFont

from ui.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    qss_path = Path(__file__).parent / "resources" / "styles" / "dark_theme.qss"
    if qss_path.exists():
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


def main():
    # High-DPI support
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("PAR-S Generator")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("PAR-S")

    # Font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # Style
    load_stylesheet(app)

    # Main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
