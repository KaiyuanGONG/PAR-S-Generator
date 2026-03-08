import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.batch_stats import BatchStats
from core.phantom_generator import PhantomConfig, PhantomGenerator, PreviewOverrides
from ui.app_state import AppSettings, AppState


def test_batch_stats_roundtrip_serialization():
    result = PhantomGenerator(PhantomConfig()).generate_one(1, seed=123)
    stats = BatchStats(total=1)
    stats.update(result)

    payload = stats.to_dict()
    restored = BatchStats.from_dict(payload)

    assert restored.total == 1
    assert restored.completed == 1
    assert restored.case_summaries[0]['case_id'] == result.case_id
    assert restored.case_summaries[0]['n_tumors'] == result.n_tumors
    assert restored.liver_volumes == stats.liver_volumes
    assert restored.left_ratios == stats.left_ratios


def test_settings_store_persists_across_app_state_instances(tmp_path, monkeypatch):
    settings_path = tmp_path / 'settings.json'
    monkeypatch.setenv('PAR_S_SETTINGS_PATH', str(settings_path))

    state = AppState()
    state.save_settings(
        AppSettings(
            simind_exe='C:/simind/simind.exe',
            default_smc='C:/simind/ge870_czt.smc',
            default_output='D:/data/output',
            theme='light',
            language='fr',
            autosave_config=False,
        )
    )

    reloaded = AppState()
    assert reloaded.settings.simind_exe == 'C:/simind/simind.exe'
    assert reloaded.settings.default_smc == 'C:/simind/ge870_czt.smc'
    assert reloaded.settings.default_output == 'D:/data/output'
    assert reloaded.settings.theme == 'light'
    assert reloaded.settings.language == 'fr'
    assert reloaded.settings.autosave_config is False


def test_seed_strategy_keeps_preview_and_batch_reproducible():
    cfg = PhantomConfig(global_seed=100, use_global_seed=True)
    gen = PhantomGenerator(cfg)

    preview = gen.generate_one(0)
    batch_case = gen.generate_one(1)
    batch_case_repeat = gen.generate_one(1)

    assert preview.seed == 100
    assert batch_case.seed == 101
    assert batch_case_repeat.seed == 101
    assert batch_case.total_counts_actual == batch_case_repeat.total_counts_actual
    assert batch_case.tumor_diameters_mm == batch_case_repeat.tumor_diameters_mm


def test_preview_override_exact_tumor_count_is_applied():
    cfg = PhantomConfig(global_seed=123, use_global_seed=True, tumor_count_min=0, tumor_count_max=8)
    result = PhantomGenerator(cfg).generate_one(
        0,
        overrides=PreviewOverrides(exact_tumor_count=1, tumor_mode='ellipsoid'),
    )

    assert result.seed == 123
    assert result.n_tumors == 1
    assert result.tumor_modes_used == ['ellipsoid']
    assert len(result.tumor_diameters_mm) == 1
    assert 10.0 <= result.tumor_diameters_mm[0] <= 60.0
