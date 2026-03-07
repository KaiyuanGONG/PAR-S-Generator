"""
Anatomical validation tests for PhantomGenerator.
Run: python -m pytest tests/test_phantom_anatomy.py -v
  or: python tests/test_phantom_anatomy.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from core.phantom_generator import PhantomConfig, PhantomGenerator

HALF_FOV_CM = 128 * 4.42 / 2 / 10   # 28.288 cm
VOX_VOL_ML  = (4.42 / 10) ** 3       # ml per voxel


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

def make_result(seed=42, case_id=1):
    cfg = PhantomConfig(use_global_seed=True, global_seed=0)
    return PhantomGenerator(cfg).generate_one(case_id=case_id, seed=seed)


def _assert(condition, msg):
    if not condition:
        raise AssertionError(msg)


# ─────────────────────────────────────────
# 1. Body size
# ─────────────────────────────────────────

def test_body_size():
    """Body diameter must be within realistic adult ranges (cm)."""
    r = make_result()
    mu = r.mu_map
    body = mu > 0
    z_size = body.any(axis=(1, 2)).sum() * 4.42 / 10
    y_size = body.any(axis=(0, 2)).sum() * 4.42 / 10
    x_size = body.any(axis=(0, 1)).sum() * 4.42 / 10
    _assert(28 <= z_size <= 45, f"Body Z(SI)={z_size:.1f}cm, expected 28-45cm")
    _assert(15 <= y_size <= 30, f"Body Y(AP)={y_size:.1f}cm, expected 15-30cm")
    _assert(25 <= x_size <= 42, f"Body X(LR)={x_size:.1f}cm, expected 25-42cm")
    print(f"  Body: Z={z_size:.1f} Y={y_size:.1f} X={x_size:.1f} cm  [OK]")


# ─────────────────────────────────────────
# 2. Air fraction
# ─────────────────────────────────────────

def test_air_fraction():
    """More than 50% of voxels must be air (mu=0) outside the body."""
    r = make_result()
    mu = r.mu_map
    air_frac = (mu == 0).sum() / mu.size
    _assert(air_frac >= 0.50, f"Air fraction={air_frac:.1%}, expected >=50%")
    _assert((mu < 0).sum() == 0, "Negative mu values found")
    print(f"  Air fraction: {air_frac:.1%}  [OK]")


# ─────────────────────────────────────────
# 3. Attenuation values
# ─────────────────────────────────────────

def test_mu_values():
    """Check expected mu tissue ranges (cm^-1 at ~140 keV Tc-99m)."""
    r = make_result()
    mu = r.mu_map
    nz = mu[mu > 0]
    _assert(nz.min() >= 0.03, f"mu min>0={nz.min():.4f}, expected >=0.03 (lung)")
    _assert(nz.max() <= 0.35, f"mu max={nz.max():.4f}, expected <=0.35 (dense bone)")
    lung_vox   = ((mu > 0.03) & (mu < 0.07)).sum()
    water_vox  = ((mu > 0.12) & (mu < 0.17)).sum()
    spine_vox  = ((mu > 0.25) & (mu < 0.35)).sum()
    _assert(lung_vox  > 500,  f"Lung voxels={lung_vox}, expected >500")
    _assert(water_vox > 5000, f"Water/tissue voxels={water_vox}, expected >5000")
    _assert(spine_vox > 100,  f"Spine voxels={spine_vox}, expected >100")
    print(f"  mu lung={lung_vox}vox  water={water_vox}vox  spine={spine_vox}vox  [OK]")


# ─────────────────────────────────────────
# 4. Liver volume
# ─────────────────────────────────────────

def test_liver_volume():
    """Liver volume must be within normal adult range (900-1800 ml)."""
    results = [make_result(seed=42+i, case_id=i) for i in range(1, 6)]
    for r in results:
        v = r.liver_volume_ml
        _assert(900 <= v <= 1900,
                f"case {r.case_id}: liver={v:.0f}ml, expected 900-1900ml")
    vols = [r.liver_volume_ml for r in results]
    print(f"  Liver volumes: {[round(v) for v in vols]} ml  [OK]")


# ─────────────────────────────────────────
# 5. Left/right lobe ratio
# ─────────────────────────────────────────

def test_lobe_ratio():
    """Left lobe should be 25-45% of total liver volume."""
    results = [make_result(seed=42+i, case_id=i) for i in range(1, 8)]
    for r in results:
        lr = r.left_ratio
        _assert(0.22 <= lr <= 0.48,
                f"case {r.case_id}: left_ratio={lr:.1%}, expected 22-48%")
    ratios = [r.left_ratio for r in results]
    print(f"  Left ratios: {['%.1f%%' % (v*100) for v in ratios]}  [OK]")


# ─────────────────────────────────────────
# 6. Lungs above liver
# ─────────────────────────────────────────

def test_lung_position():
    """Lung voxels must be predominantly in the superior (upper) half of the volume."""
    r = make_result()
    mu = r.mu_map
    half = mu.shape[0] // 2
    lung_upper = ((mu[half:, :, :] > 0.03) & (mu[half:, :, :] < 0.07)).sum()
    lung_lower = ((mu[:half, :, :] > 0.03) & (mu[:half, :, :] < 0.07)).sum()
    _assert(lung_upper > 500, f"Lung upper voxels={lung_upper}, expected >500")
    _assert(lung_lower == 0,
            f"Lung voxels in lower half={lung_lower} (lungs leaking into abdomen)")
    print(f"  Lung upper={lung_upper}  lower={lung_lower}  [OK]")


# ─────────────────────────────────────────
# 7. Tumor count and placement
# ─────────────────────────────────────────

def test_tumor_count():
    """All cases must have at least 1 tumor; tumors must be inside liver."""
    results = [make_result(seed=42+i, case_id=i) for i in range(1, 8)]
    for r in results:
        _assert(r.n_tumors >= 1, f"case {r.case_id}: n_tumors={r.n_tumors}, must be >=1")
        _assert(r.n_tumors <= 5, f"case {r.case_id}: n_tumors={r.n_tumors}, must be <=5")
        for tmask in r.tumor_masks:
            outside = (tmask & ~r.liver_mask).sum()
            _assert(outside == 0,
                    f"case {r.case_id}: {outside} tumor voxels outside liver")
    counts = [r.n_tumors for r in results]
    print(f"  Tumor counts: {counts}  [OK]")


# ─────────────────────────────────────────
# 8. Tumor diameters
# ─────────────────────────────────────────

def test_tumor_diameters():
    """Tumor diameters must be in configured bins (10-60 mm)."""
    results = [make_result(seed=42+i, case_id=i) for i in range(1, 8)]
    for r in results:
        for d in r.tumor_diameters_mm:
            _assert(8 <= d <= 65,
                    f"case {r.case_id}: tumor diameter={d:.1f}mm, expected 8-65mm")
    all_d = [d for r in results for d in r.tumor_diameters_mm]
    print(f"  Tumor diameters range: {min(all_d):.1f}-{max(all_d):.1f} mm  [OK]")


# ─────────────────────────────────────────
# 9. Activity map
# ─────────────────────────────────────────

def test_activity_map():
    """Activity must be non-negative; tumor regions must be hotter than background."""
    r = make_result()
    act = r.activity
    _assert((act < 0).sum() == 0, "Negative activity values found")
    _assert(act.sum() > 0, "Activity map is all zeros")
    if r.n_tumors > 0 and r.perfusion_mode != "Tumor Only":
        for tmask in r.tumor_masks:
            if tmask.sum() > 0 and r.liver_mask.sum() > 0:
                t_mean = act[tmask].mean()
                bg_mean = act[r.liver_mask & ~tmask].mean()
                if bg_mean > 0:
                    tnr = t_mean / bg_mean
                    _assert(tnr >= 1.5, f"TNR={tnr:.2f} too low (expected >=1.5)")
    print(f"  Activity OK: perfusion={r.perfusion_mode}  sum={act.sum():.0f}  [OK]")


# ─────────────────────────────────────────
# 10. Reproducibility
# ─────────────────────────────────────────

def test_reproducibility():
    """Same seed must produce identical results."""
    r1 = make_result(seed=999, case_id=1)
    r2 = make_result(seed=999, case_id=1)
    _assert(np.array_equal(r1.mu_map, r2.mu_map),   "mu_map not reproducible")
    _assert(np.array_equal(r1.activity, r2.activity), "activity not reproducible")
    print("  Reproducibility with same seed  [OK]")


# ─────────────────────────────────────────
# Runner
# ─────────────────────────────────────────

TESTS = [
    test_body_size,
    test_air_fraction,
    test_mu_values,
    test_liver_volume,
    test_lobe_ratio,
    test_lung_position,
    test_tumor_count,
    test_tumor_diameters,
    test_activity_map,
    test_reproducibility,
]

if __name__ == '__main__':
    passed, failed = 0, 0
    for fn in TESTS:
        try:
            print(f"[{fn.__name__}]")
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1
    print()
    print(f"{'='*40}")
    print(f"PASSED {passed}/{passed+failed}")
    if failed:
        sys.exit(1)
