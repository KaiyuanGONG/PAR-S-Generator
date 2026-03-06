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
    liver_base_center: tuple = (-0.2, 0.1, 0.2)

    # Right lobe
    right_radii: tuple = (0.38, 0.30, 0.30)
    right_shift: tuple = (0.0, 0.0, 0.10)
    right_rot_deg: float = -15.0

    # Left lobe
    left_radii: tuple = (0.20, 0.26, 0.26)
    left_shift: tuple = (0.18, 0.07, 0.00)
    left_rot_deg: float = 10.0

    # Dome / fossa
    dome_radius: float = 0.46
    fossa_radius: float = 0.34
    dome_offset: tuple = (-0.07, 0.0, 0.0)
    fossa_offset: tuple = (-0.22, -0.04, 0.0)

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
    cantlie_offset_range: tuple = (-0.12, 0.12)
    cantlie_iter_max: int = 12

    # Tumors
    tumor_count_min: int = 0
    tumor_count_max: int = 5
    tumor_size_bins_mm: list = field(default_factory=lambda: [[5, 10], [10, 20], [20, 40], [40, 60]])
    tumor_probs: list = field(default_factory=lambda: [0.20, 0.35, 0.30, 0.15])
    tumor_contrast_min: float = 4.0
    tumor_contrast_max: float = 8.0
    min_edge_dist_px: int = 4
    min_center_dist_px: int = 6
    tumor_modes: list = field(default_factory=lambda: ["spiculated", "ellipsoid", "superellipsoid", "noise_threshold"])

    # Spiculated params
    spiculated_roughness: float = 0.35
    spiculated_spiciness: float = 3.0

    # Superellipsoid
    superellipse_p_min: float = 2.2
    superellipse_p_max: float = 3.0
    superellipse_elong_min: float = 0.6
    superellipse_elong_max: float = 1.2

    # Noise threshold
    noise_thresh_corr: float = 1.2
    noise_thresh_bias: float = 0.2

    # Perfusion
    perfusion_probs: dict = field(default_factory=lambda: {
        "Whole Liver": 0.05, "Tumor Only": 0.25, "Left Only": 0.35, "Right Only": 0.35
    })
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


# ─────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────

class Geometry3D:

    @staticmethod
    def get_grid(shape):
        z = np.linspace(-1, 1, shape[0])
        y = np.linspace(-1, 1, shape[1])
        x = np.linspace(-1, 1, shape[2])
        return np.meshgrid(z, y, x, indexing='ij')

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
    tumor_radii_mm: list          # list of floats
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
        np.savez_compressed(
            output_dir / f"case_{self.case_id:04d}.npz",
            activity=self.activity,
            mu_map=self.mu_map,
            liver_mask=self.liver_mask,
            left_mask=self.left_mask,
            right_mask=self.right_mask,
        )
        meta = {
            "case_id": self.case_id,
            "seed": self.seed,
            "perfusion_mode": self.perfusion_mode,
            "total_counts_actual": float(self.total_counts_actual),
            "liver_volume_ml": float(self.liver_volume_ml),
            "left_ratio": float(self.left_ratio),
            "n_tumors": self.n_tumors,
            "tumor_radii_mm": [float(r) for r in self.tumor_radii_mm],
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

    def __init__(self, config: PhantomConfig):
        self.cfg = config

    def generate_one(self, case_id: int, seed: Optional[int] = None) -> PhantomResult:
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

        body = Geometry3D.create_ellipsoid(shape, (0, 0, 0), (0.90, 0.65, 0.85))

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

        # ── 2. Lobe splitting (Cantlie plane) — bisection method ──
        tilt = rng.uniform(*cfg.cantlie_tilt_range)
        lo, hi = cfg.cantlie_offset_range[0], cfg.cantlie_offset_range[1]
        liver_vol = liver.sum()

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

        # Lungs (positioned in upper thorax, above liver)
        lung_r = Geometry3D.create_ellipsoid(shape, (0.55, 0.05, -0.28), (0.28, 0.20, 0.20))
        lung_l = Geometry3D.create_ellipsoid(shape, (0.55, 0.05, 0.28), (0.28, 0.20, 0.20))
        mu[lung_r | lung_l] = cfg.mu_lung

        # Spine
        Z, Y, X = Geometry3D.get_grid(shape)
        spine_mask = ((X - 0) ** 2 + (Y + 0.55) ** 2) <= 0.08 ** 2
        mu[spine_mask] = cfg.mu_spine

        # Liver
        mu[liver] = cfg.mu_liver

        # Fat layer (outer body shell)
        outer_body = Geometry3D.create_ellipsoid(shape, (0, 0, 0), (0.92, 0.67, 0.87))
        fat_layer = outer_body & ~body
        mu[fat_layer] = cfg.mu_fat

        # Noise
        noise = rng.random(shape).astype(np.float32)
        noise = gaussian_filter(noise, sigma=cfg.mu_noise_sigma).astype(np.float32)
        noise = (noise - noise.mean()) * cfg.mu_noise_amp
        mu = np.clip(mu + noise, 0, None)

        # ── 4. Tumors ──
        n_tumors = rng.integers(cfg.tumor_count_min, cfg.tumor_count_max + 1)
        tumor_masks = []
        tumor_radii_mm = []
        tumor_modes_used = []
        tumor_centers = []

        liver_indices = np.argwhere(liver)
        if len(liver_indices) == 0:
            n_tumors = 0

        for _ in range(n_tumors):
            # Sample size
            bin_idx = rng.choice(len(cfg.tumor_size_bins_mm), p=cfg.tumor_probs)
            r_min_mm, r_max_mm = cfg.tumor_size_bins_mm[bin_idx]
            radius_mm = rng.uniform(r_min_mm / 2, r_max_mm / 2)
            radius_vox = radius_mm / cfg.voxel_size_mm

            # Sample mode
            mode = rng.choice(cfg.tumor_modes)

            # Sample position (inside liver, away from edges)
            placed = False
            for attempt in range(50):
                idx = liver_indices[rng.integers(len(liver_indices))]
                cz, cy, cx = int(idx[0]), int(idx[1]), int(idx[2])

                # Edge distance check
                edge_ok = (cz >= cfg.min_edge_dist_px and cz < shape[0] - cfg.min_edge_dist_px and
                           cy >= cfg.min_edge_dist_px and cy < shape[1] - cfg.min_edge_dist_px and
                           cx >= cfg.min_edge_dist_px and cx < shape[2] - cfg.min_edge_dist_px)
                if not edge_ok:
                    continue

                # Center distance check
                center_ok = all(
                    np.sqrt(sum((c - cz) ** 2 + (d - cy) ** 2 + (e - cx) ** 2
                                for c, d, e in [tc]))
                    >= cfg.min_center_dist_px
                    for tc in tumor_centers
                ) if tumor_centers else True

                if not center_ok:
                    continue

                placed = True
                tumor_centers.append((cz, cy, cx))
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
            elif mode == "superellipsoid":
                p = rng.uniform(cfg.superellipse_p_min, cfg.superellipse_p_max)
                elong = rng.uniform(cfg.superellipse_elong_min, cfg.superellipse_elong_max)
                tmask = Geometry3D.create_superellipsoid(shape, (cz, cy, cx), radius_vox, p=p, elong=elong)
            elif mode == "noise_threshold":
                tmask = Geometry3D.create_noise_threshold(
                    shape, (cz, cy, cx), radius_vox,
                    corr=cfg.noise_thresh_corr, bias=cfg.noise_thresh_bias, rng=rng
                )
            else:  # ellipsoid
                elong = rng.uniform(0.7, 1.3)
                tmask = Geometry3D.create_superellipsoid(shape, (cz, cy, cx), radius_vox, p=2.0, elong=elong)

            tmask = tmask & liver
            if tmask.sum() == 0:
                continue

            tumor_masks.append(tmask)
            tumor_radii_mm.append(float(radius_mm * 2))  # store diameter
            tumor_modes_used.append(mode)

        # ── 5. Activity map ──
        perf_keys = list(cfg.perfusion_probs.keys())
        perf_vals = list(cfg.perfusion_probs.values())
        perfusion_mode = rng.choice(perf_keys, p=perf_vals)

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

        # Gradient
        if cfg.gradient_gain > 0 and liver_vol > 0:
            Z_grid, _, _ = Geometry3D.get_grid(shape)
            grad = (Z_grid + 1) / 2 * cfg.gradient_gain
            activity += (grad * liver).astype(np.float32)

        # Tumors
        contrast = rng.uniform(cfg.tumor_contrast_min, cfg.tumor_contrast_max)
        for tmask in tumor_masks:
            base_val = activity[tmask].mean() if activity[tmask].sum() > 0 else 1.0
            activity[tmask] = base_val * contrast

        # PSF is handled by SIMIND internally (collimator/detector model).
        # Do NOT blur here — SIMIND source input must be the clean activity map.

        # Normalize to total counts + Poisson noise
        if activity.sum() > 0:
            activity = activity / activity.sum() * cfg.total_counts
            activity = rng.poisson(np.maximum(activity, 0)).astype(np.float32)

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
            tumor_radii_mm=tumor_radii_mm,
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
