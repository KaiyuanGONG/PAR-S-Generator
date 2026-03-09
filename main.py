"""
PAR-S Generator entrypoint.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from ui.i18n import init_language
from ui.main_window import MainWindow
from ui.settings_store import SettingsStore


def load_stylesheet(app: QApplication) -> None:
    settings = SettingsStore().load()
    theme = settings["appearance"].get("theme", "dark")
    qss_name = "light_theme.qss" if theme == "light" else "dark_theme.qss"
    qss_path = Path(__file__).parent / "resources" / "styles" / qss_name
    if not qss_path.exists():
        qss_path = Path(__file__).parent / "resources" / "styles" / "dark_theme.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))


def main():
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    init_language()
    app = QApplication(sys.argv)
    app.setApplicationName("PAR-S Generator")
    app.setApplicationVersion("0.4.0")
    app.setFont(QFont("Segoe UI", 10))
    load_stylesheet(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
