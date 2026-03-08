import sys
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from ui.app_state import AppState
from ui.i18n import set_language
from ui.main_window import MainWindow
from ui.pages.phantom_page import PhantomPage
from ui.pages.settings_page import SettingsDialog


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    yield instance


def test_main_window_retranslates_sidebar_and_pages(app, monkeypatch, tmp_path):
    monkeypatch.setenv('PAR_S_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    window = MainWindow()

    set_language('zh')
    assert window.sidebar.btn_settings.text() == '设置'
    assert window.generate_page.tabs.tabText(0) == '预览'
    assert window.generate_page.volume_group.title() == '体积'
    assert window.simulation_page.grp_step4.title() == '步骤4：可视化确认'

    set_language('fr')
    assert window.sidebar.btn_about.text() == 'A propos'
    assert window.generate_page.tabs.tabText(1) == 'Suivi lot'
    assert window.simulation_page.grp_step1.title() == 'Etape 1 : Export binaire brut'

    window.close()


def test_generate_banner_retranslates_immediately(app, monkeypatch, tmp_path):
    monkeypatch.setenv('PAR_S_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    window = MainWindow()

    set_language('en')
    window.generate_page.retranslate_ui()
    assert 'Ready.' in window.generate_page.lbl_validation.text()

    set_language('zh')
    assert '就绪' in window.generate_page.lbl_validation.text()

    window.close()


def test_settings_dialog_retranslates_labels(app, monkeypatch, tmp_path):
    monkeypatch.setenv('PAR_S_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    dialog = SettingsDialog(AppState())

    set_language('zh')
    assert dialog.windowTitle() == '设置'
    assert dialog.page.lbl_theme.text() == '主题:'

    set_language('fr')
    assert dialog.windowTitle() == 'Parametres'
    assert dialog.page.lbl_lang.text() == 'Langue :'

    dialog.close()


def test_tumor_single_value_maps_to_min_max(app, monkeypatch, tmp_path):
    monkeypatch.setenv('PAR_S_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    page = PhantomPage(AppState())

    page.ctrl_tumor_count.set_value(4)
    page.ctrl_contrast.set_value(6.5)
    cfg = page._collect_config()

    assert cfg.tumor_count_min == 4
    assert cfg.tumor_count_max == 4
    assert cfg.tumor_contrast_min == pytest.approx(6.5)
    assert cfg.tumor_contrast_max == pytest.approx(6.5)


def test_volume_discrete_and_advanced_modes(app, monkeypatch, tmp_path):
    monkeypatch.setenv('PAR_S_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    page = PhantomPage(AppState())

    page.chk_volume_advanced.setChecked(False)
    page.ctrl_matrix.set_value(150)
    page.ctrl_voxel.set_value(4.10)
    assert int(page.ctrl_matrix.value()) in {64, 96, 128, 192, 256}
    assert float(page.ctrl_voxel.value()) in {1.95, 2.2, 3.9, 4.42, 4.8}

    page.chk_volume_advanced.setChecked(True)
    page.ctrl_matrix.set_value(150)
    page.ctrl_voxel.set_value(4.10)
    assert int(page.ctrl_matrix.value()) == 150
    assert float(page.ctrl_voxel.value()) == pytest.approx(4.10)
