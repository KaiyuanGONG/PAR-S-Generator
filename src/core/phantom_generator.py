"""
Phantom Generator Core
======================
3D analytical liver phantom generation.
Ported and enhanced from PAR-S/notebooks/DataCreation_SYN.ipynb
"""

from __future__ import annotations
import json
import hashlib
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.ndimage import binary_erosion, distance_transform_edt, gaussian_filter

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
    mu_unit: str = "cm^-1"
    mu_reference_energy_kev: float = 140.5
    mu_contract_status: str = "verified_type7_mu_times_voxel_v10_current_h2o_protocol"

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

    # Anatomy integration.  ``legacy`` preserves the frozen master path;
    # ``v2_population`` activates the additive Gate A adapter.
    anatomy_model: str = "legacy"
    activity_model: str = "legacy"
    territory_policy: str = "auto_equal_feasible"
    v2_population_profile: str = "configs/population_tare_hcc_nopvi_v2.json"
    v2_evidence_registry: str = "configs/evidence_registry_v2.json"
    v2_max_liver_shape_attempts: int = 16

    # Lobe splitting
    target_left_ratio: float = 0.35
    cantlie_tilt_range: tuple = (-6.0, 10.0)
    cantlie_offset_range: tuple = (-0.05, 0.12)  # covers Cantlie plane at X~0.02-0.05
    cantlie_iter_max: int = 12
    cantlie_tolerance: float = 0.005
    cantlie_expand_step: float = 0.05
    cantlie_expand_limit: float = 1.0

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
    tumor_min_liver_margin_mm: float = 4.42
    tumor_overlap_gap_mm: float = 0.0
    subcapsular_fraction: float = 0.0
    subcapsular_max_depth_mm: float = 5.0
    allow_capacity_subcapsular_fallback: bool = True
    tumor_placement_attempts: int = 250
    tumor_spec_attempts: int = 20
    tumor_layout_attempts: int = 12
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
        # Express local coordinates in units of the requested semi-axes.  The
        # previous implementation normalized the entire ``r + 2`` bounding
        # box to [-1, 1], which silently inflated small lesions by up to 2
        # voxels per side and applied elongation twice.
        zz = (np.arange(z0, z1, dtype=np.float32) - float(cz)) / max(float(rz), 1e-6)
        yy = (np.arange(y0, y1, dtype=np.float32) - float(cy)) / max(float(ry), 1e-6)
        xx = (np.arange(x0, x1, dtype=np.float32) - float(cx)) / max(float(rx), 1e-6)
        Z, Y, X = np.meshgrid(zz, yy, xx, indexing='ij')
        body = (np.abs(X) ** p + np.abs(Y) ** p + np.abs(Z) ** p) <= 1.0
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
    tumor_diameters_mm: list      # measured equivalent-sphere diameters
    tumor_nominal_diameters_mm: list
    tumor_modes_used: list        # list of str
    tumor_metadata: list          # measured, auditable lesion records
    perfusion_mode: str
    total_counts_actual: float
    liver_volume_ml: float
    left_ratio: float
    cantlie_target_ratio: float
    cantlie_offset: float
    cantlie_tilt_deg: float
    cantlie_converged: bool
    cantlie_iterations: int
    cantlie_abs_error: float
    cantlie_search_evidence: dict
    n_tumors: int
    voxel_size_mm: float
    volume_shape: tuple
    mu_unit: str
    mu_reference_energy_kev: float
    mu_contract_status: str
    v2_metadata: dict | None
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
            "tumor_nominal_diameters_mm": [float(d) for d in self.tumor_nominal_diameters_mm],
            "tumor_modes": self.tumor_modes_used,
            "tumors": self.tumor_metadata,
            "cantlie": {
                "offset": float(self.cantlie_offset),
                "tilt_deg": float(self.cantlie_tilt_deg),
                "target_left_ratio": float(self.cantlie_target_ratio),
                "converged": bool(self.cantlie_converged),
                "iterations": int(self.cantlie_iterations),
                "abs_error": float(self.cantlie_abs_error),
                "search": self.cantlie_search_evidence,
            },
            "attenuation_contract": {
                "unit": self.mu_unit,
                "reference_energy_kev": float(self.mu_reference_energy_kev),
                "status": self.mu_contract_status,
            },
            "voxel_size_mm": self.voxel_size_mm,
            "volume_shape": list(self.volume_shape),
            "generation_time_s": self.generation_time_s,
        }
        if self.v2_metadata is not None:
            meta["v2"] = self.v2_metadata
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
        if config.anatomy_model not in {"legacy", "v2_population"}:
            raise ValueError("anatomy_model must be legacy or v2_population")
        if config.activity_model not in {"legacy", "limited_v1"}:
            raise ValueError("activity_model must be legacy or limited_v1")
        if config.activity_model == "limited_v1" and config.anatomy_model != "v2_population":
            raise ValueError("limited_v1 activity requires v2_population anatomy")
        self._hybrid_v2_adapter_instance = None

    def _hybrid_v2_adapter(self):
        if self._hybrid_v2_adapter_instance is None:
            from .hybrid_v2_adapter import HybridV2Adapter

            self._hybrid_v2_adapter_instance = HybridV2Adapter(
                profile_path=self.cfg.v2_population_profile,
                evidence_registry_path=self.cfg.v2_evidence_registry,
                volume_shape=tuple(int(value) for value in self.cfg.volume_shape),
                voxel_size_mm=float(self.cfg.voxel_size_mm),
                max_shape_attempts=int(self.cfg.v2_max_liver_shape_attempts),
            )
        return self._hybrid_v2_adapter_instance

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

    @staticmethod
    def _surface(mask: np.ndarray) -> np.ndarray:
        if not np.any(mask):
            return np.zeros_like(mask, dtype=bool)
        return mask & ~binary_erosion(mask, border_value=0)

    def _split_liver_to_target(self, liver: np.ndarray, tilt: float) -> tuple:
        """Find a Cantlie offset, expanding the bracket when necessary."""
        cfg = self.cfg
        liver_vol = int(liver.sum())

        def evaluate(offset: float):
            left, right = Geometry3D.split_liver_lobes(
                liver, cfg.volume_shape, tilt_deg=tilt, offset=offset
            )
            return float(left.sum() / liver_vol), left, right

        initial_lo, initial_hi = map(float, cfg.cantlie_offset_range)
        lo, hi = initial_lo, initial_hi
        ratio_lo, _, _ = evaluate(lo)
        ratio_hi, _, _ = evaluate(hi)
        expansions = 0
        while not (ratio_lo <= cfg.target_left_ratio <= ratio_hi):
            changed = False
            if ratio_lo > cfg.target_left_ratio and lo > -cfg.cantlie_expand_limit:
                lo = max(-cfg.cantlie_expand_limit, lo - cfg.cantlie_expand_step)
                ratio_lo, _, _ = evaluate(lo)
                changed = True
            if ratio_hi < cfg.target_left_ratio and hi < cfg.cantlie_expand_limit:
                hi = min(cfg.cantlie_expand_limit, hi + cfg.cantlie_expand_step)
                ratio_hi, _, _ = evaluate(hi)
                changed = True
            expansions += 1
            if not changed or expansions > 50:
                break

        expanded_lo, expanded_hi = lo, hi
        bracketed = bool(ratio_lo <= cfg.target_left_ratio <= ratio_hi)
        search_evidence = {
            "initial_offset_range": [float(initial_lo), float(initial_hi)],
            "expanded_offset_range": [float(expanded_lo), float(expanded_hi)],
            "expansions": int(expansions),
            "expanded_beyond_initial_range": bool(expansions > 0),
            "target_bracketed": bracketed,
            "hit_expansion_limit": bool(
                not bracketed
                and (np.isclose(lo, -cfg.cantlie_expand_limit) or np.isclose(hi, cfg.cantlie_expand_limit))
            ),
        }

        if not bracketed:
            candidates = [(abs(ratio_lo - cfg.target_left_ratio), lo),
                          (abs(ratio_hi - cfg.target_left_ratio), hi)]
            best_offset = min(candidates)[1]
            ratio, left, right = evaluate(best_offset)
            search_evidence["solution_on_search_boundary"] = True
            return left, right, best_offset, False, 0, abs(ratio - cfg.target_left_ratio), search_evidence

        iterations = 0
        best_offset = (lo + hi) / 2.0
        left = right = None
        ratio = 0.0
        for iterations in range(1, cfg.cantlie_iter_max + 1):
            best_offset = (lo + hi) / 2.0
            ratio, left, right = evaluate(best_offset)
            if abs(ratio - cfg.target_left_ratio) <= cfg.cantlie_tolerance:
                break
            if ratio < cfg.target_left_ratio:
                lo = best_offset
            else:
                hi = best_offset
        error = abs(ratio - cfg.target_left_ratio)
        search_evidence["solution_on_search_boundary"] = bool(
            np.isclose(best_offset, expanded_lo) or np.isclose(best_offset, expanded_hi)
        )
        return left, right, best_offset, error <= cfg.cantlie_tolerance, iterations, error, search_evidence

    def _generate_legacy_anatomy(self, rng: np.random.Generator) -> tuple:
        """Run the frozen master anatomy path without changing its RNG order."""
        cfg = self.cfg
        shape = cfg.volume_shape
        base_center = np.array(cfg.liver_base_center)
        global_shift = rng.uniform(-cfg.global_shift_range, cfg.global_shift_range, 3)
        center = base_center + global_shift

        right_radii = tuple(
            radius * rng.uniform(1 - cfg.scale_jitter, 1 + cfg.scale_jitter)
            for radius in cfg.right_radii
        )
        right_template = Geometry3D.create_ellipsoid(
            shape,
            tuple(center + np.array(cfg.right_shift)),
            right_radii,
            rotation_deg=cfg.right_rot_deg,
            rotation_plane="xz",
            rng=rng,
        )
        left_radii = tuple(
            radius * rng.uniform(1 - cfg.scale_jitter, 1 + cfg.scale_jitter)
            for radius in cfg.left_radii
        )
        left_template = Geometry3D.create_ellipsoid(
            shape,
            tuple(center + np.array(cfg.left_shift)),
            left_radii,
            rotation_deg=cfg.left_rot_deg,
            rotation_plane="xz",
            rng=rng,
        )
        body = Geometry3D.create_ellipsoid(shape, (0, 0, 0), (0.67, 0.39, 0.60))
        dome_radius = cfg.dome_radius + rng.uniform(-cfg.detail_jitter, cfg.detail_jitter)
        dome = Geometry3D.create_ellipsoid(
            shape,
            tuple(center + np.array(cfg.dome_offset)),
            (dome_radius,) * 3,
            rng=rng,
        )
        fossa_radius = cfg.fossa_radius + rng.uniform(-cfg.detail_jitter, cfg.detail_jitter)
        fossa = Geometry3D.create_ellipsoid(
            shape,
            tuple(center + np.array(cfg.fossa_offset)),
            (fossa_radius,) * 3,
            rng=rng,
        )
        liver = (right_template | left_template) & body & dome & ~fossa
        if cfg.smooth_sigma > 0:
            liver = gaussian_filter(liver.astype(float), sigma=cfg.smooth_sigma) > cfg.smooth_thr
        if not np.any(liver):
            raise RuntimeError("Generated liver mask is empty. Adjust geometry parameters and retry.")

        tilt = float(rng.uniform(*cfg.cantlie_tilt_range))
        (
            left_mask,
            right_mask,
            best_offset,
            converged,
            iterations,
            absolute_error,
            search_evidence,
        ) = self._split_liver_to_target(liver, tilt)

        mu_map = np.ones(shape, dtype=np.float32) * cfg.mu_water
        right_lung = Geometry3D.create_ellipsoid(
            shape, (0.38, 0.05, -0.22), (0.20, 0.14, 0.18)
        )
        left_lung = Geometry3D.create_ellipsoid(
            shape, (0.38, 0.05, 0.22), (0.20, 0.14, 0.18)
        )
        mu_map[right_lung | left_lung] = cfg.mu_lung
        _, y_grid, x_grid = Geometry3D.get_grid(shape)
        spine_mask = ((x_grid - 0) ** 2 + (y_grid + 0.30) ** 2) <= 0.06**2
        mu_map[spine_mask] = cfg.mu_spine
        mu_map[liver] = cfg.mu_liver
        outer_body = Geometry3D.create_ellipsoid(shape, (0, 0, 0), (0.69, 0.41, 0.62))
        mu_map[outer_body & ~body] = cfg.mu_fat
        noise = rng.random(shape).astype(np.float32)
        noise = gaussian_filter(noise, sigma=cfg.mu_noise_sigma).astype(np.float32)
        noise = (noise - noise.mean()) * cfg.mu_noise_amp
        mu_map = np.clip(mu_map + noise, 0, None)
        mu_map[~outer_body] = 0.0
        return (
            liver,
            left_mask,
            right_mask,
            mu_map.astype(np.float32, copy=False),
            best_offset,
            tilt,
            converged,
            iterations,
            absolute_error,
            search_evidence,
        )

    def generate_one(self, case_id: int, seed: Optional[int] = None, overrides: PreviewOverrides | None = None) -> PhantomResult:
        t0 = time.time()
        cfg = self.cfg

        if seed is None:
            if cfg.use_global_seed:
                v2_global_seed = int(cfg.global_seed)
                seed = cfg.global_seed + case_id
            else:
                seed = np.random.randint(0, 2**31)
                v2_global_seed = int(seed)
        else:
            v2_global_seed = int(seed)

        rng = np.random.default_rng(seed)
        shape = cfg.volume_shape
        v2_metadata = None
        v2_tumor_seed = None
        v2_activity_seed = None

        if cfg.anatomy_model == "v2_population":
            v2_case = self._hybrid_v2_adapter().generate(
                case_id=f"case_{case_id:04d}",
                global_seed=v2_global_seed,
            )
            liver = np.asarray(v2_case.geometry.mask, dtype=bool)
            labels = np.asarray(v2_case.geometry.region_labels)
            left_mask = np.isin(labels, (1, 2, 3))
            right_mask = np.isin(labels, (4, 5))
            mu = np.asarray(v2_case.mu_map, dtype=np.float32)
            liver_vol = int(liver.sum())
            actual_left_ratio = float(left_mask.sum() / liver_vol)
            target_left_ratio = float(v2_case.target.left_fraction)
            cantlie_abs_error = abs(actual_left_ratio - target_left_ratio)
            best_offset = 0.0
            tilt = 0.0
            cantlie_converged = bool(
                cantlie_abs_error <= (1.0 / max(liver_vol, 1)) + 1e-12
            )
            cantlie_iterations = 0
            cantlie_search_evidence = {
                "method": "v2_region_proxy_partition",
                "legacy_cantlie_not_used": True,
                "target_left_fraction": target_left_ratio,
                "actual_left_fraction": actual_left_ratio,
                "voxel_quantization_tolerance": 1.0 / max(liver_vol, 1),
                "expanded_beyond_initial_range": False,
                "target_bracketed": True,
            }
            if not cantlie_converged:
                raise RuntimeError(
                    "V2 left/right region adapter exceeded one-voxel target tolerance"
                )
            v2_metadata = v2_case.metadata
            v2_activity_seed = int(v2_case.seed_bundle.activity)
            rng = np.random.default_rng(v2_case.seed_bundle.activity)
            v2_tumor_seed = int(v2_case.seed_bundle.tumor)
        else:
            (
                liver,
                left_mask,
                right_mask,
                mu,
                best_offset,
                tilt,
                cantlie_converged,
                cantlie_iterations,
                cantlie_abs_error,
                cantlie_search_evidence,
            ) = self._generate_legacy_anatomy(rng)
            liver_vol = int(liver.sum())
            actual_left_ratio = float(left_mask.sum() / liver_vol)
            target_left_ratio = float(cfg.target_left_ratio)

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

        # V2 uses a separate child stream so patient/liver/activity acceptance
        # cannot bias the frozen master lesion sampler.
        if v2_tumor_seed is not None:
            rng = np.random.default_rng(v2_tumor_seed)

        # ── 5. Tumors ──
        # Geometry and perfusion are independent sampled factors.  Restricting
        # all lesions to a single active lobe makes the configured 1-5 lesion
        # and 40-60 mm strata geometrically infeasible and biases accepted
        # sizes.  Place within the full liver and record whether the accepted
        # mask lies in the high- or low-perfusion region.
        if perfusion_mode == "Left Only":
            active_placement_mask = left_mask
        elif perfusion_mode == "Right Only":
            active_placement_mask = right_mask
        else:
            active_placement_mask = liver
        placement_indices = np.argwhere(liver)

        n_tumors = self._resolve_tumor_count(rng, placement_indices, overrides)

        tumor_masks = []
        tumor_nominal_diameters_mm = []
        tumor_modes_used = []
        tumor_records = []
        liver_distance = distance_transform_edt(liver)

        sampled_bin_indices = [
            int(rng.choice(len(cfg.tumor_size_bins_mm), p=cfg.tumor_probs))
            for _ in range(n_tumors)
        ]
        sampled_subcapsular = [
            bool(rng.random() < cfg.subcapsular_fraction)
            for _ in range(n_tumors)
        ]
        # Place larger strata first so smaller lesions cannot consume the only
        # anatomically valid centre for a large lesion.  The sampled bins and
        # their probabilities are unchanged.
        placement_order = sorted(
            range(n_tumors),
            key=lambda index: (
                cfg.tumor_size_bins_mm[sampled_bin_indices[index]][0],
                index,
            ),
            reverse=True,
        )

        # A greedy layout can trap the final lesion even when the sampled set
        # is feasible.  Restart the *whole layout* while retaining the sampled
        # size strata.  This removes the former acceptance bias where a failed
        # large stratum was silently replaced by a newly sampled small one.
        placement_plans: list[tuple[str, list[bool]]] = [
            ("configured", sampled_subcapsular)
        ]
        if cfg.allow_capacity_subcapsular_fallback and n_tumors > 1:
            # If the eroded liver core cannot contain the entire multifocal
            # burden, progressively relax the surface-margin constraint for
            # the smallest remaining lesion(s).  The fallback is labelled in
            # metadata; masks must still be fully inside and non-overlapping.
            fallback_order = sorted(
                range(n_tumors),
                key=lambda index: (
                    cfg.tumor_size_bins_mm[sampled_bin_indices[index]][1],
                    index,
                ),
            )
            for fallback_count in range(1, n_tumors + 1):
                plan = list(sampled_subcapsular)
                for tumor_id in fallback_order[:fallback_count]:
                    plan[tumor_id] = True
                if plan != placement_plans[-1][1]:
                    placement_plans.append(("capacity_fallback", plan))

        failed_tumor_id = None
        accepted_plan_name = None
        for plan_name, plan_subcapsular in placement_plans:
            plan_complete = False
            for _layout_attempt in range(max(int(cfg.tumor_layout_attempts), 1)):
                trial_masks = []
                trial_records = []
                occupied = np.zeros(shape, dtype=bool)
                layout_complete = True

                for tumor_id in placement_order:
                    placed = False
                    chosen_mask = None
                    chosen_center = None
                    chosen_elong = 1.0
                    bin_idx = sampled_bin_indices[tumor_id]
                    r_min_mm, r_max_mm = cfg.tumor_size_bins_mm[bin_idx]

                    for _spec_attempt in range(cfg.tumor_spec_attempts):
                        radius_mm = rng.uniform(r_min_mm / 2, r_max_mm / 2)
                        radius_vox = radius_mm / cfg.voxel_size_mm
                        mode = self._resolve_tumor_mode(rng, overrides)
                        is_subcapsular = bool(plan_subcapsular[tumor_id])
                        is_capacity_fallback = (
                            is_subcapsular and not sampled_subcapsular[tumor_id]
                        )

                        for _center_attempt in range(cfg.tumor_placement_attempts):
                            idx = placement_indices[rng.integers(len(placement_indices))]
                            cz, cy, cx = int(idx[0]), int(idx[1]), int(idx[2])

                            # Retain the FOV guard as a cheap pre-filter, but
                            # liver-surface distance is the anatomical constraint.
                            margin = max(cfg.min_edge_dist_px, int(np.ceil(radius_vox)))
                            edge_ok = (cz >= margin and cz < shape[0] - margin and
                                       cy >= margin and cy < shape[1] - margin and
                                       cx >= margin and cx < shape[2] - margin)
                            if not edge_ok:
                                continue

                            center_depth_mm = max(
                                float(liver_distance[cz, cy, cx]) - 1.0, 0.0
                            ) * cfg.voxel_size_mm
                            if is_subcapsular and not is_capacity_fallback:
                                if center_depth_mm > radius_mm + cfg.subcapsular_max_depth_mm:
                                    continue
                            elif (not is_capacity_fallback and
                                  center_depth_mm < radius_mm + cfg.tumor_min_liver_margin_mm):
                                continue

                            if mode == "spiculated":
                                candidate = Geometry3D.create_spiculated_tumor(
                                    shape, (cz, cy, cx), radius_vox,
                                    roughness=cfg.spiculated_roughness,
                                    spiciness=cfg.spiculated_spiciness, rng=rng
                                )
                                elong = 1.0
                            else:
                                elong = rng.uniform(0.7, 1.3)
                                candidate = Geometry3D.create_superellipsoid(
                                    shape, (cz, cy, cx), radius_vox, p=2.0, elong=elong
                                )

                            if not np.any(candidate) or np.any(candidate & ~liver):
                                continue
                            candidate_volume_mm3 = float(candidate.sum()) * cfg.voxel_size_mm**3
                            candidate_diameter_mm = float(
                                (6.0 * candidate_volume_mm3 / np.pi) ** (1.0 / 3.0)
                            )
                            upper_ok = (
                                candidate_diameter_mm <= r_max_mm
                                if bin_idx == len(cfg.tumor_size_bins_mm) - 1
                                else candidate_diameter_mm < r_max_mm
                            )
                            if candidate_diameter_mm < r_min_mm or not upper_ok:
                                continue
                            if np.any(candidate & occupied):
                                continue
                            if cfg.tumor_overlap_gap_mm > 0 and np.any(occupied):
                                gap_vox = cfg.tumor_overlap_gap_mm / cfg.voxel_size_mm
                                distance_to_occupied = distance_transform_edt(~occupied)
                                if float(distance_to_occupied[candidate].min()) < gap_vox:
                                    continue

                            surface_margin_mm = max(
                                float(liver_distance[candidate].min()) - 1.0, 0.0
                            ) * cfg.voxel_size_mm
                            if (not is_subcapsular and not is_capacity_fallback and
                                    surface_margin_mm + 1e-6 < cfg.tumor_min_liver_margin_mm):
                                continue

                            placed = True
                            chosen_mask = candidate
                            chosen_center = (cz, cy, cx)
                            chosen_elong = float(elong)
                            break
                        if placed:
                            break

                    if not placed or chosen_mask is None or chosen_center is None:
                        failed_tumor_id = int(tumor_id)
                        layout_complete = False
                        break

                    tmask = chosen_mask
                    cz, cy, cx = chosen_center
                    trial_masks.append(tmask)
                    occupied |= tmask
                    trial_records.append(
                        {
                            "id": int(tumor_id),
                            "center_vox": [int(cz), int(cy), int(cx)],
                            "mode": mode,
                            "elongation": chosen_elong,
                            "nominal_diameter_mm": float(radius_mm * 2),
                            "sampled_size_bin_mm": [float(r_min_mm), float(r_max_mm)],
                            "placement_stratum": (
                                "capacity_fallback_margin_relaxed"
                                if is_capacity_fallback
                                else ("subcapsular" if is_subcapsular else "central")
                            ),
                            "perfusion_region": (
                                "high_perfusion"
                                if float(np.mean(active_placement_mask[tmask])) >= 0.5
                                else "low_perfusion"
                            ),
                        }
                    )

                if layout_complete:
                    tumor_masks = trial_masks
                    tumor_records = trial_records
                    accepted_plan_name = plan_name
                    plan_complete = True
                    break
            if plan_complete:
                break
        if accepted_plan_name is None:
            raise RuntimeError(
                f"Unable to place tumor set without clipping or overlap after "
                f"{cfg.tumor_layout_attempts} attempts per placement plan; failed tumor "
                f"{(failed_tumor_id or 0) + 1}/{n_tumors}, sampled size bins="
                f"{sampled_bin_indices}."
            )

        # Apply contrast only after an entire layout has succeeded so discarded
        # layout attempts cannot contaminate the saved activity map.
        for record, tmask in zip(tumor_records, tumor_masks):
            if overrides and overrides.exact_tumor_contrast is not None:
                contrast = overrides.exact_tumor_contrast
            else:
                contrast = rng.uniform(cfg.tumor_contrast_min, cfg.tumor_contrast_max)
            base_val = activity[tmask].mean() if activity[tmask].sum() > 0 else 1.0
            activity[tmask] = base_val * contrast
            record["target_contrast"] = float(contrast)
            tumor_nominal_diameters_mm.append(float(record["nominal_diameter_mm"]))
            tumor_modes_used.append(str(record["mode"]))

        # PSF is handled by SIMIND internally (collimator/detector model).
        # Do NOT blur here — SIMIND source input must be the clean activity map.

        # Normalize to total counts — no Poisson noise here.
        # SIMIND uses this as a probability density (source distribution);
        # photon-count statistics are handled internally by Monte Carlo sampling.
        if activity.sum() > 0:
            activity = (activity / activity.sum() * cfg.total_counts).astype(np.float32)

        total_counts_actual = float(activity.sum())

        # Derive all lesion measurements from the final masks/activity rather
        # than from sampled parameters.  These records are the auditable truth
        # used by QC and manifests.
        tumor_union = np.zeros(shape, dtype=bool)
        for tmask in tumor_masks:
            tumor_union |= tmask
        voxel_volume_ml = (cfg.voxel_size_mm / 10.0) ** 3
        liver_boundary = self._surface(liver)
        tumor_diameters_mm = []
        for record, tmask in zip(tumor_records, tumor_masks):
            volume_ml = float(tmask.sum() * voxel_volume_ml)
            effective_diameter_mm = float(2.0 * ((3.0 * volume_ml * 1000.0) / (4.0 * np.pi)) ** (1.0 / 3.0))
            surface = self._surface(tmask)
            contact_fraction = float((surface & liver_boundary).sum() / max(int(surface.sum()), 1))
            surface_margin_mm = max(float(liver_distance[tmask].min()) - 1.0, 0.0) * cfg.voxel_size_mm
            left_overlap = int((tmask & left_mask).sum())
            right_overlap = int((tmask & right_mask).sum())
            lobe = "left" if left_overlap >= right_overlap else "right"
            local_lobe = left_mask if lobe == "left" else right_mask
            local_bg = local_lobe & ~tumor_union
            global_bg = liver & ~tumor_union
            tumor_mean = float(activity[tmask].mean())
            local_mean = float(activity[local_bg].mean()) if np.any(local_bg) else 0.0
            global_mean = float(activity[global_bg].mean()) if np.any(global_bg) else 0.0
            record.update(
                {
                    "lobe": lobe,
                    "voxel_count": int(tmask.sum()),
                    "volume_ml": volume_ml,
                    "effective_diameter_mm": effective_diameter_mm,
                    "surface_margin_mm": float(surface_margin_mm),
                    "boundary_contact_fraction": contact_fraction,
                    "tnr_local": float(tumor_mean / local_mean) if local_mean > 0 else None,
                    "tnr_global": float(tumor_mean / global_mean) if global_mean > 0 else None,
                    "overlaps": [],
                }
            )
            tumor_diameters_mm.append(effective_diameter_mm)

        if cfg.activity_model == "limited_v1":
            if v2_activity_seed is None or v2_metadata is None:
                raise RuntimeError("limited_v1 activity requires V2 seed and anatomy metadata")
            from .limited_activity import build_limited_activity, derive_domain_seed
            from .windows_v1 import (
                GATE_A_GENERATOR_COMMIT,
                GATE_C_CONFIG_SHA256,
                GENERATION_PROFILE,
                LIMITED_ACTIVITY_UPSTREAM_SOURCE_SHA256,
            )

            stale_activity_fields = {
                "perfusion_region",
                "target_contrast",
                "tnr_local",
                "tnr_global",
            }
            clean_records = [
                {key: value for key, value in record.items() if key not in stale_activity_fields}
                for record in tumor_records
            ]
            target_tnrs = None
            if not (
                np.isclose(cfg.tumor_contrast_min, 2.0)
                and np.isclose(cfg.tumor_contrast_max, 8.0)
            ):
                target_tnrs = [
                    float(
                        np.random.default_rng(
                            derive_domain_seed(v2_activity_seed, "tnr", index)
                        ).uniform(cfg.tumor_contrast_min, cfg.tumor_contrast_max)
                    )
                    for index in range(len(tumor_masks))
                ]
            limited = build_limited_activity(
                liver_mask=liver,
                left_mask=left_mask,
                right_mask=right_mask,
                tumor_masks=[np.ascontiguousarray(mask, dtype=bool) for mask in tumor_masks],
                tumor_records=clean_records,
                activity_seed=v2_activity_seed,
                residual_bg=cfg.residual_bg,
                gradient_gain=cfg.gradient_gain,
                total_counts=cfg.total_counts,
                target_tnrs=target_tnrs,
                territory_policy=cfg.territory_policy,
            )
            activity = limited.activity
            perfusion_mode = limited.selected_territory
            tumor_records = limited.tumor_records
            total_counts_actual = float(np.sum(activity, dtype=np.float64))
            contract_sha256 = hashlib.sha256(
                json.dumps(
                    limited.contract,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            v2_metadata.setdefault("adapters", {})["activity"] = (
                "hybrid_v2_limited_activity_v1_sole_authority"
            )
            v2_metadata.setdefault("contracts", {})["generation_profile"] = GENERATION_PROFILE
            v2_metadata["limited_activity"] = {
                "schema_version": "pars_hybrid_v2_limited_activity_v1",
                "status": "PASS",
                "generation_profile": GENERATION_PROFILE,
                "gate_a_generator_commit": GATE_A_GENERATOR_COMMIT,
                "gate_c_config_sha256": GATE_C_CONFIG_SHA256,
                "upstream_source_sha256": LIMITED_ACTIVITY_UPSTREAM_SOURCE_SHA256,
                "adapter_source_sha256": limited.contract["adapter_source_sha256"],
                "selected_territory": limited.selected_territory,
                "contract": limited.contract,
                "contract_sha256": contract_sha256,
                "tumor_records": limited.tumor_records,
                "upstream_activity_and_perfusion": "discarded_not_persisted",
            }

        # ── 6. Metadata ──
        liver_volume_ml = float(liver.sum() * voxel_volume_ml)

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
            tumor_nominal_diameters_mm=tumor_nominal_diameters_mm,
            tumor_modes_used=tumor_modes_used,
            tumor_metadata=tumor_records,
            perfusion_mode=perfusion_mode,
            total_counts_actual=total_counts_actual,
            liver_volume_ml=liver_volume_ml,
            left_ratio=float(actual_left_ratio),
            cantlie_target_ratio=float(target_left_ratio),
            cantlie_offset=float(best_offset),
            cantlie_tilt_deg=float(tilt),
            cantlie_converged=bool(cantlie_converged),
            cantlie_iterations=int(cantlie_iterations),
            cantlie_abs_error=float(cantlie_abs_error),
            cantlie_search_evidence=cantlie_search_evidence,
            n_tumors=len(tumor_masks),
            voxel_size_mm=cfg.voxel_size_mm,
            volume_shape=tuple(shape),
            mu_unit=cfg.mu_unit,
            mu_reference_energy_kev=cfg.mu_reference_energy_kev,
            mu_contract_status=cfg.mu_contract_status,
            v2_metadata=v2_metadata,
            generation_time_s=time.time() - t0,
        )
        return result
