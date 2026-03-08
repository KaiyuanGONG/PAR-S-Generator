"""
Phantom Generator Core
======================
3D analytical liver phantom generation.
Ported and enhanced from PAR-S/notebooks/DataCreation_SYN.ipynb
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.ndimage import gaussian_filter

_GRID_CACHE: dict[tuple[int, int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}


# ─────────────────────────────────────────────
# Configuration dataclass
# ─────────────────────────────────────────────

@dataclass
class PhantomConfig:
    """All parameters for phantom generation."""

    # Volume
    volume_shape: tuple = (128, 128, 128)
    voxel_size_mm: float = 4.42

    # μ-map values (cm⁻¹)
    mu_water: float = 0.15
    mu_liver: float = 0.16
    mu_lung: float = 0.05
    mu_spine: float = 0.30
    mu_fat: float = 0.09
    mu_diaphragm: float = 0.15
    mu_noise_amp: float = 0.015
    mu_noise_sigma: float = 2.0

    # Liver base center (Z, Y, X) in normalized [-1,1] coords
    # X=0.10: liver CoM at 2.8cm right of midline (anatomically correct).
    # Right lobe X=[-.02,+.50], left lobe X=[-.16,+.20]: Cantlie plane
    # at X~0.02-0.05 achieves the 35/65 left/right volume split.
    liver_base_center: tuple = (-0.20, 0.10, 0.10)

    # Right lobe -- semi-axes (rz, ry, rx) before intersection with body/dome
    # Physical after clipping: ~835-987 ml
    right_radii: tuple = (0.28, 0.22, 0.26)
    right_shift: tuple = (0.0, 0.0, 0.14)
    right_rot_deg: float = -15.0

    # Left lobe -- semi-axes before intersection
    # Physical after clipping: ~321-379 ml. Negative X shift places
    # left lobe toward patient midline so Cantlie plane can separate lobes.
    left_radii: tuple = (0.18, 0.19, 0.18)
    left_shift: tuple = (0.14, 0.06, -0.08)
    left_rot_deg: float = 10.0

    # Dome / fossa
    # dome top at Z=+0.09 (2.5cm above FOV center); 2.5cm diaphragm gap to lung bottom.
    # fossa_radius=0.14 -> 4cm gallbladder fossa (was 0.23=6.5cm, over-carved liver).
    dome_radius: float = 0.34
    fossa_radius: float = 0.14
    dome_offset: tuple = (-0.05, 0.0, 0.0)
    fossa_offset: tuple = (-0.12, -0.03, 0.0)

    # Jitter ranges
    global_shift_range: float = 0.05
    scale_jitter: float = 0.10
    rot_jitter_deg: float = 5.0
    detail_jitter: float = 0.05

    # Smoothing
    smooth_sigma: float = 1.2
    smooth_thr: float = 0.5

    # Lobe splitting
    target_left_ratio: float = 0.35
    cantlie_tilt_range: tuple = (-6.0, 10.0)
    cantlie_offset_range: tuple = (-0.05, 0.12)  # covers Cantlie plane at X~0.02-0.05
    cantlie_iter_max: int = 12

    # Tumors
    tumor_count_min: int = 1
    tumor_count_max: int = 5
    tumor_size_bins_mm: list = field(default_factory=lambda: [[10, 20], [20, 40], [40, 60]])
    tumor_probs: list = field(default_factory=lambda: [0.45, 0.40, 0.15])
    # Tumor-to-normal liver ratio (TNR) for Tc-99m MAA hepatic arterial scintigraphy:
    # Ho et al. (1997) J Nucl Med: median TNR 3.4, range 1.5–12; practical range 2–8.
    tumor_contrast_min: float = 2.0
    tumor_contrast_max: float = 8.0
    min_edge_dist_px: int = 4
    tumor_modes: list = field(default_factory=lambda: ["ellipsoid", "spiculated"])
    tumor_mode_probs: list = field(default_factory=lambda: [0.7, 0.3])
    tumor_mode_policy: str = "random"

    # Spiculated params
    spiculated_roughness: float = 0.35
    spiculated_spiciness: float = 3.0

    # Perfusion
    perfusion_probs: dict = field(default_factory=lambda: {
        "Whole Liver": 0.05, "Tumor Only": 0.25, "Left Only": 0.35, "Right Only": 0.35
    })
    perfusion_mode_policy: str = "random"
    residual_bg: float = 0.05
    gradient_gain: float = 0.08
    psf_sigma_px: float = 2.5
    total_counts: float = 8e4

    # Batch
    n_cases: int = 10
    global_seed: int = 42
    use_global_seed: bool = True
    output_dir: str = "output/syn3d"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PhantomConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "PhantomConfig":
        with open(path) as f:
            return cls.from_dict(json.load(f))


@dataclass
class PreviewOverrides:
    exact_tumor_count: int | None = None
    exact_tumor_contrast: float | None = None   # overrides the per-tumor contrast range
    tumor_mode: str | None = None
    perfusion_mode: str | None = None


# ─────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────

class Geometry3D:

    @staticmethod
    def get_grid(shape):
        shape_key = tuple(int(v) for v in shape)
        cached = _GRID_CACHE.get(shape_key)
        if cached is not None:
            return cached

        z = np.linspace(-1, 1, shape_key[0], dtype=np.float32)
        y = np.linspace(-1, 1, shape_key[1], dtype=np.float32)
        x = np.linspace(-1, 1, shape_key[2], dtype=np.float32)
        grid = np.meshgrid(z, y, x, indexing='ij', copy=False)

        if len(_GRID_CACHE) >= 4:
            _GRID_CACHE.pop(next(iter(_GRID_CACHE)))
        _GRID_CACHE[shape_key] = grid
        return grid

    @staticmethod
    def create_ellipsoid(shape, center, radii, rotation_deg=0.0,
                         rotation_plane='xz', rng=None, jitter=None):
        if rng is None:
            rng = np.random.default_rng()
        z0, y0, x0 = center
        rz, ry, rx = radii
        jitter = jitter or {}
        cj = jitter.get('center', 0.0)
        rj = jitter.get('radii', 0.0)
        rdeg = jitter.get('rot_deg', 0.0)
        z0 += rng.uniform(-cj, cj)
        y0 += rng.uniform(-cj, cj)
        x0 += rng.uniform(-cj, cj)
        rz *= rng.uniform(1 - rj, 1 + rj)
        ry *= rng.uniform(1 - rj, 1 + rj)
        rx *= rng.uniform(1 - rj, 1 + rj)
        theta = np.radians(rotation_deg + rng.uniform(-rdeg, rdeg))
        Z, Y, X = Geometry3D.get_grid(shape)
        if rotation_plane == 'xz':
            X_rot = (X - x0) * np.cos(theta) - (Z - z0) * np.sin(theta)
            Z_rot = (X - x0) * np.sin(theta) + (Z - z0) * np.cos(theta)
            Y_rot = Y - y0
        else:
            X_rot = (X - x0) * np.cos(theta) - (Y - y0) * np.sin(theta)
            Y_rot = (X - x0) * np.sin(theta) + (Y - y0) * np.cos(theta)
            Z_rot = Z - z0
        mask = (X_rot / rx) ** 2 + (Y_rot / ry) ** 2 + (Z_rot / rz) ** 2 <= 1.0
        return mask

    @staticmethod
    def create_spiculated_tumor(shape, center_idx, radius_vox,
                                roughness=0.35, spiciness=3.0, rng=None):
        if rng is None:
            rng = np.random.default_rng()
        margin = int(radius_vox * 2 + 8)
        cz, cy, cx = center_idx
        z0, z1 = max(0, cz - margin), min(shape[0], cz + margin)
        y0, y1 = max(0, cy - margin), min(shape[1], cy + margin)
        x0, x1 = max(0, cx - margin), min(shape[2], cx + margin)
        ls = (z1 - z0, y1 - y0, x1 - x0)
        if any(s <= 1 for s in ls):
            return np.zeros(shape, dtype=bool)
        zz, yy, xx = np.ogrid[:ls[0], :ls[1], :ls[2]]
        zz = zz - (cz - z0)
        yy = yy - (cy - y0)
        xx = xx - (cx - x0)
        dist = np.sqrt(zz ** 2 + yy ** 2 + xx ** 2)
        noise = rng.random(ls)
        noise = gaussian_filter(noise, sigma=spiciness)
        noise = (noise - 0.5) * 2.0
        eff_r = radius_vox + noise * (radius_vox * roughness)
        local_mask = dist <= eff_r
        full = np.zeros(shape, dtype=bool)
        full[z0:z1, y0:y1, x0:x1] = local_mask
        return full

    @staticmethod
    def create_superellipsoid(shape, center_idx, radius_vox, p=2.6, elong=1.0):
        cz, cy, cx = center_idx
        rz, ry, rx = radius_vox * elong, radius_vox, radius_vox
        z0, z1 = max(0, int(cz - rz - 2)), min(shape[0], int(cz + rz + 2))
        y0, y1 = max(0, int(cy - ry - 2)), min(shape[1], int(cy + ry + 2))
        x0, x1 = max(0, int(cx - rx - 2)), min(shape[2], int(cx + rx + 2))
        Z, Y, X = np.meshgrid(
            np.linspace(-1, 1, z1 - z0),
            np.linspace(-1, 1, y1 - y0),
            np.linspace(-1, 1, x1 - x0),
            indexing='ij'
        )
        body = (np.abs(X) ** p + np.abs(Y) ** p + np.abs(Z / elong) ** p) <= 1.0
        full = np.zeros(shape, dtype=bool)
        full[z0:z1, y0:y1, x0:x1] = body
        return full

    @staticmethod
    def create_noise_threshold(shape, center_idx, radius_vox,
                               corr=1.2, bias=0.2, rng=None):
        if rng is None:
            rng = np.random.default_rng()
        margin = int(radius_vox * 2 + 8)
        cz, cy, cx = center_idx
        z0, z1 = max(0, cz - margin), min(shape[0], cz + margin)
        y0, y1 = max(0, cy - margin), min(shape[1], cy + margin)
        x0, x1 = max(0, cx - margin), min(shape[2], cx + margin)
        ls = (z1 - z0, y1 - y0, x1 - x0)
        if any(s <= 1 for s in ls):
            return np.zeros(shape, dtype=bool)
        noise = rng.random(ls)
        noise = gaussian_filter(noise, sigma=corr)
        thr = np.quantile(noise, 0.65 + bias)
        local_mask = noise > thr
        full = np.zeros(shape, dtype=bool)
        full[z0:z1, y0:y1, x0:x1] = local_mask
        return full

    @staticmethod
    def split_liver_lobes(liver_mask, shape, target_left_ratio=0.35,
                          tilt_deg=5.0, offset=0.0):
        Z, Y, X = Geometry3D.get_grid(shape)
        theta = np.radians(tilt_deg)
        nx, ny, nz = 0, np.sin(theta), np.cos(theta)
        partition = (X * nz + Y * ny + Z * nx) > offset
        right = liver_mask & partition
        left = liver_mask & (~partition)
        return left, right


# ─────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────

@dataclass
class PhantomResult:
    case_id: int
    seed: int
    activity: np.ndarray          # (Z, Y, X) float32
    mu_map: np.ndarray            # (Z, Y, X) float32
    liver_mask: np.ndarray        # bool
    left_mask: np.ndarray         # bool
    right_mask: np.ndarray        # bool
    tumor_masks: list             # list of bool arrays
    tumor_diameters_mm: list      # list of floats (diameter, not radius)
    tumor_modes_used: list        # list of str
    perfusion_mode: str
    total_counts_actual: float
    liver_volume_ml: float
    left_ratio: float
    n_tumors: int
    voxel_size_mm: float
    volume_shape: tuple
    generation_time_s: float

    def save(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        # Stack tumor masks into a single (N, Z, Y, X) bool array.
        # Shape is (0, Z, Y, X) when no tumors are present.
        if self.tumor_masks:
            tumor_masks_arr = np.stack(self.tumor_masks, axis=0)
        else:
            tumor_masks_arr = np.zeros((0, *self.volume_shape), dtype=bool)

        np.savez_compressed(
            output_dir / f"case_{self.case_id:04d}.npz",
            activity=self.activity,
            mu_map=self.mu_map,
            liver_mask=self.liver_mask,
            left_mask=self.left_mask,
            right_mask=self.right_mask,
            tumor_masks=tumor_masks_arr,
        )
        meta = {
            "case_id": self.case_id,
            "seed": self.seed,
            "perfusion_mode": self.perfusion_mode,
            "total_counts_actual": float(self.total_counts_actual),
            "liver_volume_ml": float(self.liver_volume_ml),
            "left_ratio": float(self.left_ratio),
            "n_tumors": self.n_tumors,
            "tumor_diameters_mm": [float(d) for d in self.tumor_diameters_mm],
            "tumor_modes": self.tumor_modes_used,
            "voxel_size_mm": self.voxel_size_mm,
            "volume_shape": list(self.volume_shape),
            "generation_time_s": self.generation_time_s,
        }
        with open(output_dir / f"case_{self.case_id:04d}_meta.json", "w") as f:
            json.dump(meta, f, indent=2)


# ─────────────────────────────────────────────
# Main generator
# ─────────────────────────────────────────────

class PhantomGenerator:
    """Generates synthetic 3D liver SPECT phantoms."""

    PERFUSION_POLICY_MAP = {
        "whole_liver": "Whole Liver",
        "tumor_only": "Tumor Only",
        "left_only": "Left Only",
        "right_only": "Right Only",
    }

    def __init__(self, config: PhantomConfig):
        self.cfg = config

    def _resolve_perfusion_mode(self, rng, overrides: PreviewOverrides | None):
        if overrides and overrides.perfusion_mode in self.PERFUSION_POLICY_MAP.values():
            return overrides.perfusion_mode
        if self.cfg.perfusion_mode_policy != "random":
            return self.PERFUSION_POLICY_MAP[self.cfg.perfusion_mode_policy]
        perf_keys = list(self.cfg.perfusion_probs.keys())
        perf_vals = list(self.cfg.perfusion_probs.values())
        return rng.choice(perf_keys, p=perf_vals)

    def _resolve_tumor_mode(self, rng, overrides: PreviewOverrides | None):
        if overrides and overrides.tumor_mode in self.cfg.tumor_modes:
            return overrides.tumor_mode
        if self.cfg.tumor_mode_policy in self.cfg.tumor_modes:
            return self.cfg.tumor_mode_policy
        return rng.choice(self.cfg.tumor_modes, p=self.cfg.tumor_mode_probs)

    def _resolve_tumor_count(self, rng, placement_indices, overrides: PreviewOverrides | None):
        if len(placement_indices) == 0:
            return 0
        if overrides and overrides.exact_tumor_count is not None:
            return overrides.exact_tumor_count
        return rng.integers(self.cfg.tumor_count_min, self.cfg.tumor_count_max + 1)

    def generate_one(self, case_id: int, seed: Optional[int] = None, overrides: PreviewOverrides | None = None) -> PhantomResult:
        t0 = time.time()
        cfg = self.cfg

        if seed is None:
            if cfg.use_global_seed:
                seed = cfg.global_seed + case_id
            else:
                seed = np.random.randint(0, 2**31)

        rng = np.random.default_rng(seed)
        shape = cfg.volume_shape

        # ── 1. Build liver mask ──
        base_center = np.array(cfg.liver_base_center)
        global_shift = rng.uniform(-cfg.global_shift_range, cfg.global_shift_range, 3)
        center = base_center + global_shift

        jitter = {'center': 0.0, 'radii': cfg.scale_jitter, 'rot_deg': cfg.rot_jitter_deg}

        right_radii = tuple(r * rng.uniform(1 - cfg.scale_jitter, 1 + cfg.scale_jitter)
                            for r in cfg.right_radii)
        right_center = tuple(center + np.array(cfg.right_shift))
        rt = Geometry3D.create_ellipsoid(
            shape, right_center, right_radii,
            rotation_deg=cfg.right_rot_deg, rotation_plane='xz', rng=rng
        )

        left_radii = tuple(r * rng.uniform(1 - cfg.scale_jitter, 1 + cfg.scale_jitter)
                           for r in cfg.left_radii)
        left_center = tuple(center + np.array(cfg.left_shift))
        lt = Geometry3D.create_ellipsoid(
            shape, left_center, left_radii,
            rotation_deg=cfg.left_rot_deg, rotation_plane='xz', rng=rng
        )

        # body inner shell — realistic adult torso (Z=37.9cm, Y=22.1cm, X=33.9cm)
        # Ref: typical adult supine CT dimensions for abdominal SPECT
        body = Geometry3D.create_ellipsoid(shape, (0, 0, 0), (0.67, 0.39, 0.60))

        dome_r = cfg.dome_radius + rng.uniform(-cfg.detail_jitter, cfg.detail_jitter)
        dome = Geometry3D.create_ellipsoid(
            shape, tuple(center + np.array(cfg.dome_offset)), (dome_r,) * 3, rng=rng
        )

        fossa_r = cfg.fossa_radius + rng.uniform(-cfg.detail_jitter, cfg.detail_jitter)
        fossa = Geometry3D.create_ellipsoid(
            shape, tuple(center + np.array(cfg.fossa_offset)), (fossa_r,) * 3, rng=rng
        )

        liver = (rt | lt) & body & dome & ~fossa

        if cfg.smooth_sigma > 0:
            liver = gaussian_filter(liver.astype(float), sigma=cfg.smooth_sigma) > cfg.smooth_thr

        liver_vol = int(liver.sum())
        if liver_vol <= 0:
            raise RuntimeError("Generated liver mask is empty. Adjust geometry parameters and retry.")

        # ── 2. Lobe splitting (Cantlie plane) — bisection method ──
        tilt = rng.uniform(*cfg.cantlie_tilt_range)
        lo, hi = cfg.cantlie_offset_range[0], cfg.cantlie_offset_range[1]

        for _ in range(cfg.cantlie_iter_max):
            mid = (lo + hi) / 2.0
            left_lobe, _ = Geometry3D.split_liver_lobes(liver, shape, tilt_deg=tilt, offset=mid)
            if liver_vol > 0:
                ratio = left_lobe.sum() / liver_vol
                # Higher offset → larger left region; lower offset → smaller left region
                if ratio < cfg.target_left_ratio:
                    lo = mid   # need higher offset to grow left
                else:
                    hi = mid   # need lower offset to shrink left

        best_offset = (lo + hi) / 2.0
        left_mask, right_mask = Geometry3D.split_liver_lobes(liver, shape, tilt_deg=tilt, offset=best_offset)
        actual_left_ratio = left_mask.sum() / liver_vol if liver_vol > 0 else 0.5

        # ── 3. μ-map ──
        mu = np.ones(shape, dtype=np.float32) * cfg.mu_water

        # Lungs — centers at Z=0.38 (10.8cm above FOV center), above the diaphragm.
        # Semi-axes (rz=0.20, ry=0.14, rx=0.18): lung top Z=0.58 < body top 0.69 ✓
        # 2.5cm diaphragm gap between liver dome top (Z=0.09) and lung bottom (Z=0.18) ✓
        lung_r = Geometry3D.create_ellipsoid(shape, (0.38, 0.05, -0.22), (0.20, 0.14, 0.18))
        lung_l = Geometry3D.create_ellipsoid(shape, (0.38, 0.05,  0.22), (0.20, 0.14, 0.18))
        mu[lung_r | lung_l] = cfg.mu_lung

        # Spine — vertebral body ~3.4cm diameter, 8.5cm posterior from FOV center.
        # Y=-0.30 is 73% of body AP semi-axis (0.41) toward posterior — anatomically correct.
        Z, Y, X = Geometry3D.get_grid(shape)
        spine_mask = ((X - 0) ** 2 + (Y + 0.30) ** 2) <= 0.06 ** 2
        mu[spine_mask] = cfg.mu_spine

        # Liver
        mu[liver] = cfg.mu_liver

        # Fat layer (outer body shell) — adds ~0.57cm fat per side to body
        outer_body = Geometry3D.create_ellipsoid(shape, (0, 0, 0), (0.69, 0.41, 0.62))
        fat_layer = outer_body & ~body
        mu[fat_layer] = cfg.mu_fat

        # Noise
        noise = rng.random(shape).astype(np.float32)
        noise = gaussian_filter(noise, sigma=cfg.mu_noise_sigma).astype(np.float32)
        noise = (noise - noise.mean()) * cfg.mu_noise_amp
        mu = np.clip(mu + noise, 0, None)

        # Air: voxels outside the body boundary must be 0.0 (μ_air = 0)
        mu[~outer_body] = 0.0

        # ── 4. Perfusion mode & base activity (determined before tumor placement) ──
        perfusion_mode = self._resolve_perfusion_mode(rng, overrides)

        activity = np.zeros(shape, dtype=np.float32)

        if perfusion_mode == "Whole Liver":
            activity[liver] = 1.0
        elif perfusion_mode == "Left Only":
            activity[left_mask] = 1.0
            activity[right_mask] = cfg.residual_bg
        elif perfusion_mode == "Right Only":
            activity[right_mask] = 1.0
            activity[left_mask] = cfg.residual_bg
        elif perfusion_mode == "Tumor Only":
            activity[liver] = cfg.residual_bg

        # Gradient applied before tumor placement so per-tumor base_val is correct
        if cfg.gradient_gain > 0 and liver_vol > 0:
            Z_grid, _, _ = Geometry3D.get_grid(shape)
            grad = (Z_grid + 1) / 2 * cfg.gradient_gain
            activity += (grad * liver).astype(np.float32)

        # ── 5. Tumors ──
        # Placement region constrained to the active lobe: tumors in the cold lobe
        # produce near-invisible signal and are not useful for training.
        if perfusion_mode == "Left Only":
            placement_indices = np.argwhere(left_mask)
        elif perfusion_mode == "Right Only":
            placement_indices = np.argwhere(right_mask)
        else:
            placement_indices = np.argwhere(liver)

        n_tumors = self._resolve_tumor_count(rng, placement_indices, overrides)

        tumor_masks = []
        tumor_diameters_mm = []
        tumor_modes_used = []
        tumor_centers = []  # (cz, cy, cx, radius_vox)

        for _ in range(n_tumors):
            # Sample size
            bin_idx = rng.choice(len(cfg.tumor_size_bins_mm), p=cfg.tumor_probs)
            r_min_mm, r_max_mm = cfg.tumor_size_bins_mm[bin_idx]
            radius_mm = rng.uniform(r_min_mm / 2, r_max_mm / 2)
            radius_vox = radius_mm / cfg.voxel_size_mm

            # Sample mode
            mode = self._resolve_tumor_mode(rng, overrides)

            # Sample position inside the active lobe, away from edges
            placed = False
            for attempt in range(50):
                idx = placement_indices[rng.integers(len(placement_indices))]
                cz, cy, cx = int(idx[0]), int(idx[1]), int(idx[2])

                # Edge margin: at least radius_vox or min_edge_dist_px, whichever is larger
                margin = max(cfg.min_edge_dist_px, int(np.ceil(radius_vox)))
                edge_ok = (cz >= margin and cz < shape[0] - margin and
                           cy >= margin and cy < shape[1] - margin and
                           cx >= margin and cx < shape[2] - margin)
                if not edge_ok:
                    continue

                # Size-aware center distance: no overlap = r_new + r_prev + 2 vox gap
                center_ok = all(
                    np.sqrt((pc - cz) ** 2 + (pd - cy) ** 2 + (pe - cx) ** 2)
                    >= radius_vox + pr + 2
                    for pc, pd, pe, pr in tumor_centers
                ) if tumor_centers else True

                if not center_ok:
                    continue

                placed = True
                tumor_centers.append((cz, cy, cx, radius_vox))
                break

            if not placed:
                continue

            # Generate tumor mask
            if mode == "spiculated":
                tmask = Geometry3D.create_spiculated_tumor(
                    shape, (cz, cy, cx), radius_vox,
                    roughness=cfg.spiculated_roughness,
                    spiciness=cfg.spiculated_spiciness, rng=rng
                )
            else:  # ellipsoid
                elong = rng.uniform(0.7, 1.3)
                tmask = Geometry3D.create_superellipsoid(shape, (cz, cy, cx), radius_vox, p=2.0, elong=elong)

            tmask = tmask & liver
            if tmask.sum() == 0:
                continue

            # Per-tumor contrast: TNR range 2–8 based on Tc-99m MAA hepatic arterial
            # scintigraphy (Ho et al. 1997, J Nucl Med: median 3.4, range 1.5–12)
            if overrides and overrides.exact_tumor_contrast is not None:
                contrast = overrides.exact_tumor_contrast
            else:
                contrast = rng.uniform(cfg.tumor_contrast_min, cfg.tumor_contrast_max)
            base_val = activity[tmask].mean() if activity[tmask].sum() > 0 else 1.0
            activity[tmask] = base_val * contrast

            tumor_masks.append(tmask)
            tumor_diameters_mm.append(float(radius_mm * 2))
            tumor_modes_used.append(mode)

        # PSF is handled by SIMIND internally (collimator/detector model).
        # Do NOT blur here — SIMIND source input must be the clean activity map.

        # Normalize to total counts — no Poisson noise here.
        # SIMIND uses this as a probability density (source distribution);
        # photon-count statistics are handled internally by Monte Carlo sampling.
        if activity.sum() > 0:
            activity = (activity / activity.sum() * cfg.total_counts).astype(np.float32)

        total_counts_actual = float(activity.sum())

        # ── 6. Metadata ──
        vox_vol_ml = (cfg.voxel_size_mm / 10) ** 3  # cm³ = mL
        liver_volume_ml = float(liver.sum() * vox_vol_ml)

        result = PhantomResult(
            case_id=case_id,
            seed=seed,
            activity=activity,
            mu_map=mu,
            liver_mask=liver,
            left_mask=left_mask,
            right_mask=right_mask,
            tumor_masks=tumor_masks,
            tumor_diameters_mm=tumor_diameters_mm,
            tumor_modes_used=tumor_modes_used,
            perfusion_mode=perfusion_mode,
            total_counts_actual=total_counts_actual,
            liver_volume_ml=liver_volume_ml,
            left_ratio=float(actual_left_ratio),
            n_tumors=len(tumor_masks),
            voxel_size_mm=cfg.voxel_size_mm,
            volume_shape=tuple(shape),
            generation_time_s=time.time() - t0,
        )
        return result



