"""Main application window with sidebar navigation."""

from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QSizePolicy,
    QStatusBar, QFrame, QApplication
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QSettings
from PyQt6.QtGui import QFont, QIcon

from ui.pages.phantom_page import PhantomPage
from ui.pages.simulation_page import SimulationPage
from ui.pages.results_page import ResultsPage
from ui.pages.settings_page import SettingsPage
from ui.i18n import tr


class NavButton(QPushButton):
    """Sidebar navigation button."""

    def __init__(self, icon_text: str, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("nav_btn")
        self.setText(f"  {icon_text}  {label}")
        self.setCheckable(False)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._active = False

    def set_active(self, active: bool):
        self._active = active
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class Sidebar(QWidget):
    """Left navigation sidebar."""
    page_changed = pyqtSignal(int)

    @property
    def NAV_ITEMS(self):
        return [
            ("\u2b21", tr("Phantom")),
            ("\u25b6", tr("Simulation")),
            ("\u25c8", tr("Results")),
            ("\u2699", tr("Settings")),
        ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._buttons: list[NavButton] = []
        self._current = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Logo area
        logo_widget = QWidget()
        logo_widget.setObjectName("sidebar_logo")
        logo_layout = QVBoxLayout(logo_widget)
        logo_layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("PAR-S")
        title.setObjectName("sidebar_logo")
        title_font = QFont("Segoe UI", 18, QFont.Weight.Bold)
        title.setFont(title_font)
        title.setStyleSheet("color: #4fc3f7; letter-spacing: 2px;")

        subtitle = QLabel("Generator")
        subtitle.setStyleSheet("color: #6b7280; font-size: 11px; letter-spacing: 1px;")

        logo_layout.addWidget(title)
        logo_layout.addWidget(subtitle)
        layout.addWidget(logo_widget)

        # Version label
        version_label = QLabel("v0.1.0")
        version_label.setStyleSheet("color: #3a4049; font-size: 10px; padding: 4px 16px;")
        layout.addWidget(version_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #2d3139; margin: 4px 0;")
        layout.addWidget(sep)

        layout.addSpacing(8)

        # Nav section label
        nav_label = QLabel(tr("WORKFLOW"))
        nav_label.setStyleSheet("color: #3a4049; font-size: 10px; padding: 4px 16px; letter-spacing: 1.5px;")
        layout.addWidget(nav_label)

        # Navigation buttons
        for i, (icon, label) in enumerate(self.NAV_ITEMS):
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda checked, idx=i: self._on_nav_click(idx))
            self._buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # Bottom info
        bottom_label = QLabel("© 2025 PAR-S Project")
        bottom_label.setStyleSheet("color: #2d3139; font-size: 10px; padding: 8px 16px;")
        layout.addWidget(bottom_label)

        # Set initial active
        self._buttons[0].set_active(True)

    def _on_nav_click(self, index: int):
        if index == self._current:
            return
        self._buttons[self._current].set_active(False)
        self._current = index
        self._buttons[self._current].set_active(True)
        self.page_changed.emit(index)

    def set_page(self, index: int):
        self._on_nav_click(index)


class MainWindow(QMainWindow):
    """Application main window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PAR-S Generator")
        self.setMinimumSize(1280, 800)
        self.resize(1440, 900)
        self._build_ui()
        self._setup_statusbar()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Sidebar
        self.sidebar = Sidebar()
        self.sidebar.page_changed.connect(self._on_page_changed)
        root_layout.addWidget(self.sidebar)

        # Content stack
        self.stack = QStackedWidget()
        self.stack.setObjectName("content_area")
        root_layout.addWidget(self.stack, stretch=1)

        # Pages
        self.phantom_page = PhantomPage()
        self.simulation_page = SimulationPage()
        self.results_page = ResultsPage()
        self.settings_page = SettingsPage()

        self.stack.addWidget(self.phantom_page)
        self.stack.addWidget(self.simulation_page)
        self.stack.addWidget(self.results_page)
        self.stack.addWidget(self.settings_page)

        # Connect signals between pages
        self.phantom_page.phantom_generated.connect(self.simulation_page.on_phantom_ready)
        # Always read config fresh from phantom_page when batch starts (avoids stale path/n_cases)
        self.results_page.set_config_getter(self.phantom_page._collect_config)
        self.simulation_page.simulation_finished.connect(self.results_page.on_results_ready)
        # Start Batch button on Phantom page -> navigate to Results + start
        self.phantom_page.start_batch_requested.connect(self._on_start_batch_from_phantom)
        # Theme toggle from Settings
        self.settings_page.theme_changed.connect(self._apply_theme)

    def _setup_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready  |  PAR-S Generator v0.1.0")

    def _on_page_changed(self, index: int):
        self.stack.setCurrentIndex(index)

    def show_status(self, message: str):
        self.status_bar.showMessage(message)

    def _on_start_batch_from_phantom(self):
        """Navigate to Results page and start batch generation."""
        self.sidebar.set_page(2)   # Results is index 2
        self.results_page.start_batch()

    def _apply_theme(self, theme: str):
        """Switch QSS theme immediately without restart."""
        if theme == "light":
            qss_path = Path(__file__).parent.parent.parent / "resources" / "styles" / "light_theme.qss"
        else:
            qss_path = Path(__file__).parent.parent.parent / "resources" / "styles" / "dark_theme.qss"
        if qss_path.exists():
            with open(qss_path, "r", encoding="utf-8") as f:
                QApplication.instance().setStyleSheet(f.read())
