import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.phantom_generator import PhantomConfig, PreviewOverrides
from core.validation import validate_phantom_config, validate_simulation_inputs


def test_validate_phantom_config_rejects_invalid_ranges():
    cfg = PhantomConfig(tumor_count_min=5, tumor_count_max=1, tumor_contrast_min=8.0, tumor_contrast_max=2.0)
    report = validate_phantom_config(cfg)
    assert not report.ok
    assert 'tumor_count.min_gt_max' in report.error_codes
    assert 'tumor_contrast.min_gt_max' in report.error_codes


def test_validate_phantom_config_warns_on_custom_geometry():
    cfg = PhantomConfig(volume_shape=(64, 64, 64), voxel_size_mm=6.0)
    report = validate_phantom_config(cfg)
    assert report.ok
    assert 'workflow.geometry_mismatch' in report.warning_codes


def test_validate_phantom_config_warns_when_outside_recommended_range_only():
    cfg = PhantomConfig(target_left_ratio=0.50)
    report = validate_phantom_config(cfg)
    assert report.ok
    assert 'target_left_ratio.recommended_range' in report.warning_codes
    assert not any(code.startswith('target_left_ratio.') for code in report.error_codes)


def test_validate_phantom_config_blocks_hard_bounds_and_preview_override():
    cfg = PhantomConfig(volume_shape=(300, 300, 300))
    report = validate_phantom_config(cfg, preview=PreviewOverrides(exact_tumor_count=20))
    assert not report.ok
    assert 'matrix_size.hard_bounds' in report.error_codes
    assert 'tumor_count.hard_bounds' in report.error_codes


def test_validate_simulation_inputs_blocks_bundled_smc_mismatch(tmp_path):
    npz_dir = tmp_path / 'npz'
    npz_dir.mkdir()
    np.savez_compressed(npz_dir / 'case_0001.npz', activity=np.ones((2, 2, 2), dtype=np.float32), mu_map=np.ones((2, 2, 2), dtype=np.float32))

    interfile_dir = tmp_path / 'bin'
    interfile_dir.mkdir()
    simind_exe = tmp_path / 'simind.exe'
    simind_exe.write_text('stub', encoding='utf-8')
    smc = tmp_path / 'ge870_czt.smc'
    smc.write_text('stub', encoding='utf-8')

    report = validate_simulation_inputs(
        str(npz_dir),
        str(interfile_dir),
        str(simind_exe),
        str(smc),
        str(tmp_path / 'sim_out'),
        phantom_config=PhantomConfig(volume_shape=(64, 64, 64), voxel_size_mm=6.0),
    )

    assert not report.ok
    assert 'simind.bundled.matrix_mismatch' in report.error_codes
    assert 'simind.bundled.voxel_mismatch' in report.error_codes
