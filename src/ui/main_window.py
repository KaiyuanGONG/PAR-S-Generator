"""
Main application window.
"""

from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ui.app_state import AppState
from ui.i18n import language_manager, tr
from ui.pages.phantom_page import PhantomPage
from ui.pages.settings_page import AboutDialog, SettingsDialog
from ui.pages.simulation_page import SimulationPage


class NavButton(QPushButton):
    def __init__(self, icon_text: str, label: str, parent=None):
        super().__init__(parent)
        self._icon_text = icon_text
        self._label = label
        self.setObjectName("nav_btn")
        self.setCheckable(False)
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.refresh_text()

    def refresh_text(self):
        self.setText(f"  {self._icon_text}  {tr(self._label)}")

    def set_active(self, active: bool):
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class Sidebar(QWidget):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._app_state = app_state
        self._buttons: list[NavButton] = []
        self._current = 0
        self._build_ui()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.brand = QFrame()
        self.brand.setObjectName("sidebar_logo")
        brand_layout = QVBoxLayout(self.brand)
        brand_layout.setContentsMargins(16, 18, 16, 18)
        self.lbl_title = QLabel("PAR-S")
        self.lbl_title.setObjectName("sidebar_brand")
        self.lbl_title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        brand_layout.addWidget(self.lbl_title)
        layout.addWidget(self.brand)

        for icon, label in [("⬡", "Generate"), ("▶", "Simulate")]:
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda checked=False, idx=len(self._buttons): self._on_nav_click(idx))
            self._buttons.append(btn)
            layout.addWidget(btn)

        self.project_card = QFrame()
        self.project_card.setObjectName("project_card")
        card_layout = QVBoxLayout(self.project_card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(6)
        self.lbl_version = QLabel("v0.3.0")
        self.lbl_version.setObjectName("version_label")
        card_layout.addWidget(self.lbl_version)
        layout.addStretch()
        layout.addWidget(self.project_card)

        utility = QHBoxLayout()
        utility.setContentsMargins(12, 8, 12, 12)
        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("secondary_btn")
        self.btn_about = QPushButton()
        self.btn_about.setObjectName("secondary_btn")
        utility.addWidget(self.btn_settings)
        utility.addWidget(self.btn_about)
        utility_wrap = QWidget()
        utility_wrap.setLayout(utility)
        layout.addWidget(utility_wrap)

        self._buttons[0].set_active(True)
        self.retranslate_ui()

    def retranslate_ui(self):
        for btn in self._buttons:
            btn.refresh_text()
        self.btn_settings.setText(tr("Settings"))
        self.btn_about.setText(tr("About"))

    def _on_nav_click(self, index: int):
        if index == self._current:
            return
        self._buttons[self._current].set_active(False)
        self._current = index
        self._buttons[self._current].set_active(True)
        if hasattr(self, "page_changed"):
            self.page_changed(index)

    def set_page_changed(self, callback):
        self.page_changed = callback


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.app_state = AppState(self)
        self.setWindowTitle("PAR-S Generator")
        self.setMinimumSize(1260, 800)
        self.resize(1420, 900)
        self._settings_dialog: SettingsDialog | None = None
        self._about_dialog: AboutDialog | None = None
        self._build_ui()
        self._setup_statusbar()
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())
        self.app_state.settings_changed.connect(lambda settings: self._apply_theme(settings.theme))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar(self.app_state)
        self.sidebar.setMinimumWidth(220)
        self.sidebar.setMaximumWidth(236)
        self.sidebar.set_page_changed(self._on_page_changed)
        self.sidebar.btn_settings.clicked.connect(self._open_settings)
        self.sidebar.btn_about.clicked.connect(self._open_about)
        root_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("content_area")
        root_layout.addWidget(self.stack, stretch=1)

        self.generate_page = PhantomPage(self.app_state)
        self.simulation_page = SimulationPage(self.app_state)
        self.stack.addWidget(self.generate_page)
        self.stack.addWidget(self.simulation_page)

    def _setup_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.status_bar.showMessage(f"{tr('Ready')}  |  PAR-S Generator v0.3.0")
        self.sidebar.retranslate_ui()
        if hasattr(self.generate_page, "retranslate_ui"):
            self.generate_page.retranslate_ui()
        if hasattr(self.simulation_page, "retranslate_ui"):
            self.simulation_page.retranslate_ui()
        if self._settings_dialog is not None:
            self._settings_dialog.retranslate_ui()
        if self._about_dialog is not None:
            self._about_dialog.retranslate_ui()

    def _on_page_changed(self, index: int):
        self.stack.setCurrentIndex(index)

    def _open_settings(self):
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self.app_state, self)
            self._settings_dialog.page.theme_changed.connect(self._apply_theme)
        self._settings_dialog.page._load_settings()
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _open_about(self):
        if self._about_dialog is None:
            self._about_dialog = AboutDialog(self)
        self._about_dialog.show()
        self._about_dialog.raise_()
        self._about_dialog.activateWindow()

    def _apply_theme(self, theme: str):
        qss_name = "light_theme.qss" if theme == "light" else "dark_theme.qss"
        qss_path = Path(__file__).parent.parent.parent / "resources" / "styles" / qss_name
        if qss_path.exists():
            QApplication.instance().setStyleSheet(qss_path.read_text(encoding="utf-8"))
