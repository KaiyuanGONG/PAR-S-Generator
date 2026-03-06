# PAR-S Generator：从 XCAT 数据到训练数据库的完整技术说明

> **文档版本**：v1.0 | **对应代码版本**：PAR-S-Generator（GitHub: KaiyuanGONG/PAR-S-Generator）  
> **适用范围**：本文档覆盖从 XCAT 原始数据出发，经参数校准、SYN 体模批量生成、格式转换、SIMIND 仿真，直至最终训练数据库的完整技术链路，所有步骤均与 PAR-S Generator 应用的实际代码逻辑严格对应。

---

## 总览：完整数据生成链路

```
┌──────────────────────────────────────────────────────────────────┐
│  阶段 0（一次性）：XCAT 参数校准                                      │
│  XCAT 50 受试者 → 提取肝脏掩码 → XCATSplitter 分割 →               │
│  PCA 椭球拟合 → 统计分析 → 更新 PhantomConfig                        │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  阶段 1：SYN 体模批量生成（PAR-S Generator 主功能）                    │
│  PhantomConfig → PhantomGenerator → case_XXXX.npz × N           │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  阶段 2：格式转换                                                   │
│  case_XXXX.npz → InterfileWriter → *_act_1.h33/.i33              │
│                                  → *_atn_1.h33/.i33              │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  阶段 3：SIMIND 蒙特卡洛仿真                                         │
│  Interfile 对 + czt_ge.smc → simind.exe → SPECT 投影数据           │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│  阶段 4：训练数据库整理                                               │
│  SPECT 投影 + npz（ground truth）→ dataset_registry.json          │
└──────────────────────────────────────────────────────────────────┘
```

---

## 阶段 0：XCAT 参数校准（一次性操作）

### 0.1 XCAT 数据要求

**需要哪些文件？**

每个 XCAT 受试者需要两类文件：

| 文件类型 | 命名规律 | 说明 |
|:---|:---|:---|
| 活度图 | `case_XXX_act_1.bin` | 帧 1（呼气末参考帧），float32，little-endian |
| 衰减图 | `case_XXX_atn_1.bin` | 帧 1（呼气末参考帧），float32，little-endian |

**关键约束：只使用帧 1（`_1.bin`），不使用其他帧。** 原因是 PAR-S 的训练目标是静态 SPECT 图像，统计分析应反映人群间的解剖变异，而非个体内的呼吸运动变异。使用多帧会引入呼吸导致的形态变化（主要是 Z 轴 1–2 cm 位移），人为增大标准差，污染参数估计。

**数据格式读取方式**（与 `DataCreation_XCAT.ipynb` 完全一致）：

```python
import numpy as np

def load_xcat_bin(filepath, shape=(128, 128, 128)):
    """读取 XCAT 二进制文件，返回 (Z, Y, X) 顺序的 float32 数组"""
    data = np.fromfile(filepath, dtype=np.dtype('<f4'))  # little-endian float32
    return data.reshape(shape)                            # Z, Y, X 顺序
```

**您的 50 个受试者数据来源**：来自 Duke 大学临床 CT 数据库，35 名男性（BMI 19.2–36.1）和 23 名女性（BMI 18.2–36.7），每个受试者均经过手动分割 + NURBS 拟合，是基于真实患者 CT 的高质量解剖模型 [^1]。

---

### 0.2 肝脏掩码提取

**当前方法（`DataCreation_XCAT.ipynb` 中的实现）**：

```python
liver_mask = act_data > 50.0  # 固定阈值
```

**问题**：固定阈值 50.0 对不同受试者的活度值范围不具有鲁棒性。如果某个受试者的活度图整体偏低或偏高，该阈值可能导致掩码过小或过大。

**改进建议（自适应 Otsu 阈值）**：

```python
from skimage.filters import threshold_otsu

def extract_liver_mask(act_data, fallback_threshold=50.0):
    """自适应阈值提取肝脏掩码"""
    nonzero = act_data[act_data > 0]
    if len(nonzero) < 100:
        return act_data > fallback_threshold
    threshold = threshold_otsu(nonzero)
    return act_data > threshold
```

---

### 0.3 左右叶分割：XCATSplitter

**当前实现**（`DataCreation_XCAT.ipynb`，`XCATSplitter` 类）：

算法核心是**虚拟锚点 + Cantlie 平面扫描**，分三步执行：

**步骤一：计算虚拟锚点**

```python
(z0, z1), (y0, y1), (x0, x1) = get_bbox(liver_mask)
p_z = z0 + (z1 - z0) * 0.15   # Z: 底部向上 15%（模拟胆囊窝高度）
p_y = (y0 + y1) / 2.0          # Y: 前后几何中心
p_x = (x0 + x1) / 2.0          # X: 左右几何中心
```

**步骤二：角度扫描（−15° 到 +35°，共 11 步）**

```python
angles = np.linspace(-15, 35, 11)
for deg in angles:
    rad = np.radians(deg)
    nx = np.cos(rad)   # 法向量 X 分量
    ny = 0.0           # 法向量 Y 分量（固定为 0）
    nz = np.sin(rad)   # 法向量 Z 分量
    dist = nx * (X - p_x) + ny * (Y - p_y) + nz * (Z - p_z)
    mask_left  = liver_mask & (dist > 0)
    mask_right = liver_mask & (dist <= 0)
```

**步骤三：选择最接近目标比例（35%）的角度**

```python
best_angle = argmin |left_ratio(θ) - 0.35|
```

**解剖学依据**：该算法基于 **Cantlie 线**的解剖定义——连接胆囊窝（Fossa Vesicae Biliaris）和下腔静脉（IVC）的虚拟平面，是临床区分肝脏左右半肝的标准界限 [^2]。虚拟锚点位于肝脏底部 15% 处，近似胆囊窝的解剖位置；角度扫描范围 −15° 到 +35° 覆盖了 Cantlie 线在人群中的真实倾斜角度范围；目标比例 35% 符合文献报告的正常人群左叶体积比例 [^3]。

**已知局限性与改进建议**：

| 问题 | 当前状态 | 改进方案 |
|:---|:---|:---|
| 扫描步长粗糙（约 5°/步） | 最终比例可能偏离目标 ±3–5% | 粗扫后用二分法细化至 0.1° |
| 锚点 X 轴在正中（50%） | 真实胆囊窝偏右（约 55–60%） | 改为 `p_x = x0 + (x1-x0)*0.55` |
| 法向量 `ny=0`（2D 平面） | 忽略 Cantlie 线的前后倾斜 | 可选：增加 Y 方向旋转自由度 |

**改进版 XCATSplitter（建议替换）**：

```python
class XCATSplitterV2:
    def __init__(self, target_ratio=0.35):
        self.target_ratio = target_ratio

    def split(self, liver_mask):
        (z0, z1), (y0, y1), (x0, x1) = self._get_bbox(liver_mask)
        shape = liver_mask.shape
        
        # 改进 1：锚点 X 轴右移至 55%
        p_z = z0 + (z1 - z0) * 0.15
        p_y = (y0 + y1) / 2.0
        p_x = x0 + (x1 - x0) * 0.55   # ← 从 50% 改为 55%
        
        Z, Y, X = np.meshgrid(
            np.arange(shape[0]), np.arange(shape[1]), np.arange(shape[2]),
            indexing='ij'
        )
        total_vol = liver_mask.sum()
        if total_vol == 0:
            return liver_mask, np.zeros_like(liver_mask), {}
        
        # 改进 2：粗扫描（11 步）
        angles = np.linspace(-15, 35, 11)
        best_angle, best_diff = 0.0, float('inf')
        for deg in angles:
            rad = np.radians(deg)
            dist = np.cos(rad) * (X - p_x) + np.sin(rad) * (Z - p_z)
            r = (liver_mask & (dist > 0)).sum() / total_vol
            diff = abs(r - self.target_ratio)
            if diff < best_diff:
                best_diff, best_angle = diff, deg
        
        # 改进 3：二分法细化（12 次迭代，精度 ≈ 0.06°）
        lo, hi = best_angle - 5.0, best_angle + 5.0
        for _ in range(12):
            mid = (lo + hi) / 2.0
            rad = np.radians(mid)
            dist = np.cos(rad) * (X - p_x) + np.sin(rad) * (Z - p_z)
            r = (liver_mask & (dist > 0)).sum() / total_vol
            if r < self.target_ratio:
                hi = mid
            else:
                lo = mid
        
        final_angle = (lo + hi) / 2.0
        rad = np.radians(final_angle)
        dist = np.cos(rad) * (X - p_x) + np.sin(rad) * (Z - p_z)
        left_mask  = liver_mask & (dist > 0)
        right_mask = liver_mask & (dist <= 0)
        final_ratio = left_mask.sum() / total_vol
        
        return left_mask, right_mask, {
            "pivot": (p_z, p_y, p_x),
            "best_angle": final_angle,
            "final_ratio": float(final_ratio)
        }

    def _get_bbox(self, mask):
        z_idx = np.any(mask, axis=(1, 2))
        y_idx = np.any(mask, axis=(0, 2))
        x_idx = np.any(mask, axis=(0, 1))
        return (
            (np.where(z_idx)[0][[0, -1]]),
            (np.where(y_idx)[0][[0, -1]]),
            (np.where(x_idx)[0][[0, -1]])
        )
```

---

### 0.4 PCA 椭球参数提取

对每个受试者的左叶和右叶掩码，使用主成分分析（PCA）反向拟合椭球参数：

```python
from sklearn.decomposition import PCA

def fit_ellipsoid_pca(mask, voxel_size_mm=4.20):
    """
    对二值掩码用 PCA 拟合椭球，返回归一化坐标系中的参数。
    
    返回:
        center_norm: 归一化坐标中的中心 (Z, Y, X)，范围 [-1, 1]
        radii_norm:  归一化坐标中的三轴半径 (rZ, rY, rX)
        rotation_deg: 主轴在 X-Z 平面内的旋转角度（度）
        volume_ml: 实际体积（mL）
    """
    shape = np.array(mask.shape)
    coords = np.argwhere(mask).astype(float)  # (N, 3)，顺序 Z, Y, X
    
    if len(coords) < 10:
        return None
    
    # 体积
    vox_vol_ml = (voxel_size_mm / 10) ** 3
    volume_ml = len(coords) * vox_vol_ml
    
    # 中心（归一化坐标，范围 [-1, 1]）
    center_vox = coords.mean(axis=0)
    center_norm = (center_vox / (shape / 2)) - 1.0
    
    # PCA 拟合主轴
    pca = PCA(n_components=3)
    pca.fit(coords - center_vox)
    
    # 半轴长度（归一化坐标）
    # 椭球半轴 ≈ 2σ（覆盖约 95% 的体素）
    std_devs = np.sqrt(pca.explained_variance_)
    radii_vox = std_devs * 2.0
    radii_norm = radii_vox / (shape / 2)
    
    # 主轴旋转角度（X-Z 平面，绕 Y 轴）
    # PCA 第一主轴（最大方差方向）
    principal_axis = pca.components_[0]  # (dZ, dY, dX)
    rotation_deg = np.degrees(np.arctan2(principal_axis[2], principal_axis[0]))
    
    return {
        "center_norm": center_norm.tolist(),   # [cZ, cY, cX]
        "radii_norm": radii_norm.tolist(),      # [rZ, rY, rX]（按方差从大到小排列）
        "rotation_deg": float(rotation_deg),
        "volume_ml": float(volume_ml)
    }
```

---

### 0.5 统计分析与参数更新

对 50 个受试者的提取结果进行统计，并映射到 `PhantomConfig` 的对应参数：

```python
import numpy as np
import json
from pathlib import Path

def compute_stats_and_update_config(results_list, output_path="xcat_stats.json"):
    """
    results_list: 每个元素是 {'right': {...}, 'left': {...}} 的字典
    """
    right_centers = np.array([r['right']['center_norm'] for r in results_list])
    right_radii   = np.array([r['right']['radii_norm']  for r in results_list])
    right_rots    = np.array([r['right']['rotation_deg'] for r in results_list])
    left_centers  = np.array([r['left']['center_norm']  for r in results_list])
    left_radii    = np.array([r['left']['radii_norm']   for r in results_list])
    left_rots     = np.array([r['left']['rotation_deg'] for r in results_list])
    liver_vols    = np.array([r['right']['volume_ml'] + r['left']['volume_ml']
                              for r in results_list])
    
    stats = {
        "n_subjects": len(results_list),
        "liver_volume_ml": {
            "mean": float(np.mean(liver_vols)),
            "std":  float(np.std(liver_vols)),
            "p5":   float(np.percentile(liver_vols, 5)),
            "p95":  float(np.percentile(liver_vols, 95))
        },
        "right_lobe": {
            "center_mean": right_centers.mean(axis=0).tolist(),
            "center_std":  right_centers.std(axis=0).tolist(),
            "radii_mean":  right_radii.mean(axis=0).tolist(),
            "radii_std":   right_radii.std(axis=0).tolist(),
            "rot_mean":    float(np.mean(right_rots)),
            "rot_std":     float(np.std(right_rots))
        },
        "left_lobe": {
            "center_mean": left_centers.mean(axis=0).tolist(),
            "center_std":  left_centers.std(axis=0).tolist(),
            "radii_mean":  left_radii.mean(axis=0).tolist(),
            "radii_std":   left_radii.std(axis=0).tolist(),
            "rot_mean":    float(np.mean(left_rots)),
            "rot_std":     float(np.std(left_rots))
        }
    }
    
    with open(output_path, "w") as f:
        json.dump(stats, f, indent=2)
    
    # 映射到 PhantomConfig 参数
    # 规则：均值 → 锚点，2σ → 抖动范围（覆盖 ~95% 人群变异）
    config_update = {
        # 右叶
        "right_radii": stats["right_lobe"]["radii_mean"],
        "right_shift": [
            stats["right_lobe"]["center_mean"][i] - stats["right_lobe"]["center_mean"][i]
            for i in range(3)
        ],  # 相对于 liver_base_center 的偏移
        "right_rot_deg": stats["right_lobe"]["rot_mean"],
        # 左叶
        "left_radii": stats["left_lobe"]["radii_mean"],
        "left_rot_deg": stats["left_lobe"]["rot_mean"],
        # 全局抖动（取三轴 std 的均值 × 2）
        "scale_jitter": float(np.mean(stats["right_lobe"]["radii_std"]) /
                              np.mean(stats["right_lobe"]["radii_mean"])),
        "rot_jitter_deg": float(stats["right_lobe"]["rot_std"] * 2),
        "global_shift_range": float(np.mean(stats["right_lobe"]["center_std"]) * 2)
    }
    
    return stats, config_update
```

**参数映射关系总结**：

| `PhantomConfig` 参数 | 映射来源 | 说明 |
|:---|:---|:---|
| `right_radii` | 右叶 PCA 半轴均值 | 右叶椭球三轴半径锚点 |
| `left_radii` | 左叶 PCA 半轴均值 | 左叶椭球三轴半径锚点 |
| `right_rot_deg` | 右叶主轴旋转角均值 | 右叶在 X-Z 平面的旋转锚点 |
| `left_rot_deg` | 左叶主轴旋转角均值 | 左叶在 X-Z 平面的旋转锚点 |
| `scale_jitter` | 右叶半轴 std / mean | 相对尺度抖动范围（均匀分布） |
| `rot_jitter_deg` | 右叶旋转角 std × 2 | 旋转角抖动范围（±值） |
| `global_shift_range` | 中心坐标 std × 2 | 全局位置抖动范围（±值） |

---

## 阶段 1：SYN 体模批量生成

### 1.1 核心数据结构

**`PhantomConfig`（`src/core/phantom_generator.py`）**

`PhantomConfig` 是一个 Python `@dataclass`，支持 JSON 序列化/反序列化，可通过 `config.save(path)` 和 `PhantomConfig.load(path)` 持久化。以下是所有参数的完整说明：

**体积与体素参数**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `volume_shape` | `(128, 128, 128)` | 体素矩阵尺寸，顺序为 (Z, Y, X) |
| `voxel_size_mm` | `4.20` | 体素边长（mm），与 SIMIND 的 `SYSTEM PIXEL SIZE: 0.42 cm` 一致 |

**μ-map 衰减系数（单位：cm⁻¹，对应 140 keV Tc-99m）**

| 参数 | 默认值 | 解剖对应 | 文献参考值 |
|:---|:---|:---|:---|
| `mu_water` | `0.15` | 软组织背景 | 0.150–0.155 cm⁻¹ |
| `mu_liver` | `0.16` | 肝脏实质 | 0.155–0.165 cm⁻¹ |
| `mu_lung` | `0.05` | 肺组织 | 0.040–0.060 cm⁻¹ |

**肝脏几何锚点参数（归一化坐标，范围 [-1, 1]）**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `liver_base_center` | `(-0.2, 0.1, 0.2)` | 肝脏整体中心锚点 (Z, Y, X)，Z 负值 = 下方 |
| `right_radii` | `(0.38, 0.30, 0.30)` | 右叶椭球三轴半径 (rZ, rY, rX) |
| `right_shift` | `(0.0, 0.0, 0.10)` | 右叶中心相对于 base_center 的偏移 |
| `right_rot_deg` | `-15.0` | 右叶在 X-Z 平面内的旋转角度（度） |
| `left_radii` | `(0.20, 0.26, 0.26)` | 左叶椭球三轴半径 (rZ, rY, rX) |
| `left_shift` | `(0.18, 0.07, 0.00)` | 左叶中心相对于 base_center 的偏移 |
| `left_rot_deg` | `10.0` | 左叶在 X-Z 平面内的旋转角度（度） |
| `dome_radius` | `0.46` | 肝脏穹隆（上界约束椭球）半径 |
| `fossa_radius` | `0.34` | 胆囊窝（下界切除椭球）半径 |

**随机抖动参数**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `global_shift_range` | `0.05` | 全局位置均匀抖动范围（±值） |
| `scale_jitter` | `0.10` | 各轴半径的相对抖动（±10%） |
| `rot_jitter_deg` | `5.0` | 旋转角抖动范围（±5°） |
| `detail_jitter` | `0.05` | 穹隆和胆囊窝半径的抖动范围 |

**Cantlie 平面分割参数**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `target_left_ratio` | `0.35` | 目标左叶体积比例 |
| `cantlie_tilt_range` | `(-6.0, 10.0)` | Cantlie 平面倾斜角随机范围（度） |
| `cantlie_offset_range` | `(-0.12, 0.12)` | Cantlie 平面 X 轴偏移随机范围 |
| `cantlie_iter_max` | `12` | 二分法迭代次数（精度约 0.01°） |

**肿瘤参数**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `tumor_count_min` | `0` | 最少肿瘤数量 |
| `tumor_count_max` | `5` | 最多肿瘤数量 |
| `tumor_size_bins_mm` | `[[5,10],[10,20],[20,40],[40,60]]` | 肿瘤直径区间（mm） |
| `tumor_probs` | `[0.20, 0.35, 0.30, 0.15]` | 各尺寸区间的采样概率 |
| `tumor_contrast_min` | `4.0` | 肿瘤相对于肝脏的最小活度对比度 |
| `tumor_contrast_max` | `8.0` | 肿瘤相对于肝脏的最大活度对比度 |
| `tumor_modes` | `["spiculated", "ellipsoid", "superellipsoid", "noise_threshold"]` | 肿瘤形态类型（等概率选取） |

**灌注模式参数**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `perfusion_probs` | `{"Whole Liver": 0.05, "Tumor Only": 0.25, "Left Only": 0.35, "Right Only": 0.35}` | 各灌注模式的采样概率 |
| `residual_bg` | `0.05` | 非灌注区域的残余活度（相对值） |
| `gradient_gain` | `0.08` | 活度梯度强度（模拟肝脏上下活度差异） |

**物理模拟参数**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `psf_sigma_px` | `2.5` | PSF 高斯模糊的 σ（体素），模拟 SPECT 空间分辨率 |
| `total_counts` | `8e4` | Poisson 噪声归一化的总计数（×1，即 80,000 counts） |

**批量生成参数**

| 参数 | 默认值 | 说明 |
|:---|:---|:---|
| `n_cases` | `10` | 批量生成的案例数量 |
| `global_seed` | `42` | 全局随机种子 |
| `use_global_seed` | `True` | True：每个 case 的 seed = global_seed + case_id（可复现）；False：随机 seed |
| `output_dir` | `"output/syn3d"` | 输出目录路径 |

---

### 1.2 体模生成算法流程

`PhantomGenerator.generate_one(case_id)` 按以下顺序执行：

**步骤 1：构建肝脏掩码**

```
右叶椭球 ∪ 左叶椭球
    ∩ 身体轮廓椭球（防止肝脏超出体表）
    ∩ 穹隆椭球（限制上界，模拟膈肌）
    − 胆囊窝椭球（切除下界凹陷）
    → 高斯平滑（σ=1.2 px）→ 阈值二值化（0.5）
```

**步骤 2：Cantlie 平面分割左右叶**

在随机倾斜角（`cantlie_tilt_range`）和随机偏移（`cantlie_offset_range`）的初始值基础上，通过最多 12 次迭代调整偏移量，使左叶比例收敛到目标值 35%（`target_left_ratio`）。

**步骤 3：构建 μ-map**

```
全局填充 mu_water（软组织）
→ 肺部（两个椭球）填充 mu_lung
→ 脊柱（圆柱）填充 mu_bone（0.48 cm⁻¹）
→ 肝脏掩码区域填充 mu_liver
```

**步骤 4：植入肿瘤**

对每个肿瘤：从肝脏体素中随机选取中心位置（需满足边缘距离 ≥ 4 px、肿瘤间距 ≥ 6 px），从 `tumor_size_bins_mm` 中采样直径，从 `tumor_modes` 中等概率选取形态类型，生成对应的三维掩码。

**步骤 5：生成活度图**

根据 `perfusion_probs` 采样灌注模式，按模式分配基础活度，叠加 Z 方向梯度（`gradient_gain`），为每个肿瘤设置对比度（`tumor_contrast_min`–`tumor_contrast_max`），最后施加 PSF 高斯模糊和 Poisson 噪声。

**步骤 6：计算元数据并保存**

体积计算公式（与 Web 端完全一致）：

```python
vox_vol_ml = (voxel_size_mm / 10) ** 3   # 4.2 mm → 0.0741 mL/voxel
liver_volume_ml = liver_mask.sum() * vox_vol_ml
```

---

### 1.3 输出文件格式

每个案例输出两个文件：

**`case_XXXX.npz`（NumPy 压缩格式）**

| 键名 | 形状 | 数据类型 | 说明 |
|:---|:---|:---|:---|
| `activity` | `(128, 128, 128)` | float32 | 活度图（含 PSF 模糊和 Poisson 噪声） |
| `mu_map` | `(128, 128, 128)` | float32 | 衰减图（cm⁻¹） |
| `liver_mask` | `(128, 128, 128)` | bool | 完整肝脏掩码 |
| `left_mask` | `(128, 128, 128)` | bool | 左叶掩码 |
| `right_mask` | `(128, 128, 128)` | bool | 右叶掩码 |

**`case_XXXX_meta.json`（元数据）**

包含 14 个字段：`case_id`、`seed`、`perfusion_mode`、`total_counts_actual`、`liver_volume_ml`、`left_ratio`、`n_tumors`、`tumor_radii_mm`（直径列表）、`tumor_modes`、`voxel_size_mm`、`volume_shape`、`generation_time_s`。

---

## 阶段 2：格式转换（npz → Interfile）

### 2.1 转换逻辑

`InterfileWriter`（`src/core/interfile_writer.py`）将 `.npz` 文件转换为 SIMIND 可识别的 Interfile 格式，每个案例生成 4 个文件：

| 输出文件 | 说明 |
|:---|:---|
| `case_XXXX_act_1.h33` | 活度图 Interfile 头文件 |
| `case_XXXX_act_1.i33` | 活度图二进制数据（float32） |
| `case_XXXX_atn_1.h33` | 衰减图 Interfile 头文件 |
| `case_XXXX_atn_1.i33` | 衰减图二进制数据（float32） |

**关键技术细节：轴顺序转置**

NumPy 数组使用 `(Z, Y, X)` 顺序，而 Interfile 标准要求 `(X, Y, Z)` 顺序（即 Fortran 列主序）。代码中已处理此转置：

```python
arr = data.astype(np.float32)
# NumPy (Z, Y, X) → Interfile (X, Y, Z)
arr = np.asfortranarray(arr.transpose(2, 1, 0))
arr.tofile(str(data_path))
```

**Interfile 头文件关键字段**（与 SIMIND 要求对应）：

```
!matrix size [1] := 128        ← X 方向（列数）
!matrix size [2] := 128        ← Y 方向（行数）
!scaling factor (mm/pixel) [1] := 4.2000   ← 与 voxel_size_mm 一致
!scaling factor (mm/pixel) [2] := 4.2000
!number of slices := 128       ← Z 方向（层数）
```

### 2.2 批量转换

```python
from core.interfile_writer import batch_convert_npz_to_interfile
from pathlib import Path

batch_convert_npz_to_interfile(
    npz_dir=Path("output/syn3d"),
    output_dir=Path("output/interfile"),
    voxel_size_mm=4.20
)
```

---

## 阶段 3：SIMIND 蒙特卡洛仿真

### 3.1 SIMIND 配置文件（`czt_ge.smc`）

以下是 `reference/czt_ge.smc` 中所有关键参数的完整说明，对应 **GE NM/CT 870 CZT** 扫描仪：

**探测器材料与晶体**

| SIMIND 参数 | 值 | 说明 |
|:---|:---|:---|
| `LAYER-0` | `CZT` | 探测器材料：碲化镉锌 |
| `CRYSTAL MATERIAL` | `czt.cr3` | 交叉截面文件（需与 .smc 同目录） |
| `INDEX-9` | `0.5` | 晶体厚度（cm）= 5 mm |
| `INDEX-22` | `6.3` | 能量分辨率 FWHM（%），在 140 keV |
| `INDEX-23` | `0.05` | 固有空间分辨率 FWHM（cm）= 0.5 mm |

**WEHR 准直器**

| SIMIND 参数 | 值 | 说明 |
|:---|:---|:---|
| `INDEX-53` | `1` | 准直器模式（1 = 模拟穿透和散射） |
| `INDEX-54` | `4` | 孔形状（4 = 方形，匹配 WEHR） |
| `INDEX-55` | `0` | 准直器类型（0 = 平行孔） |
| `INDEX-56` | `0.226` | 孔开口（cm）= 2.26 mm |
| `INDEX-57` | `0.02` | 隔壁厚度（cm）= 0.2 mm |
| `INDEX-58` | `4.5` | 孔长度（cm）= 45 mm |

**像素与采集几何**

| SIMIND 参数 | 值 | 说明 |
|:---|:---|:---|
| `CRYSTAL PIXEL` | `0.246` | 固有像素（cm）= 2.46 mm |
| `SYSTEM PIXEL SIZE` | `0.42` | 系统像素（cm）= 4.2 mm，与 `voxel_size_mm` 一致 |
| `INDEX-12` | `25.0` | 旋转半径 ROR（cm） |
| `INDEX-41` | `0.0` | 起始角度（度） |
| `INDEX-42` | `0.0` | 轨道类型（0 = 圆形） |

**能量窗（Tc-99m，140 keV）**

| 窗类型 | 范围（keV） | 说明 |
|:---|:---|:---|
| 主能量窗 | 126–154 | ±10%，用于图像重建 |
| 散射窗（低） | 110–126 | TEW 散射估计 |
| 散射窗（高） | 154–170 | TEW 散射估计 |

**仿真控制参数**

| SIMIND 参数 | 值 | 说明 |
|:---|:---|:---|
| `FLAG-1` | `1` | 启用散射模拟 |
| `FLAG-2` | `1` | 启用衰减 |
| `FLAG-3` | `1` | 启用准直器穿透 |
| `FLAG-12` | `1` | 启用能量分辨率 |
| `INDEX-26` | `1000`（测试）/ `5,000,000`（生产） | 每投影光子历史数（千） |
| `INDEX-70` | `1` | 计算空白投影（用于衰减校正） |

### 3.2 批量执行脚本生成

`InterfileWriter.generate_simind_bat()` 自动生成 Windows `.bat` 脚本，调用格式为：

```batch
simind.exe czt_ge.smc /FI:"case_0001_act_1.h33" /FA:"case_0001_atn_1.h33" /FO:"output\case_0001" /NN:5000000
```

其中：
- `/FI`：活度图 Interfile 头文件路径
- `/FA`：衰减图 Interfile 头文件路径
- `/FO`：输出文件路径前缀
- `/NN`：每投影光子历史数（生产环境建议 5,000,000）

### 3.3 SIMIND 输出文件

SIMIND 对每个案例输出以下文件：

| 文件 | 说明 |
|:---|:---|
| `case_XXXX.h00` | 投影数据 Interfile 头文件 |
| `case_XXXX.a00` | 主窗投影数据（Sinogram） |
| `case_XXXX.b00` | 低散射窗投影数据（TEW） |
| `case_XXXX.c00` | 高散射窗投影数据（TEW） |
| `case_XXXX.d00` | 空白投影（用于衰减校正） |

---

## 阶段 4：训练数据库整理

### 4.1 数据集注册表

每次批量生成完成后，`BatchWorker` 自动在输出目录生成 `batch_summary.json`，包含以下统计字段：

| 字段 | 说明 |
|:---|:---|
| `total` / `completed` / `failed` | 总数、成功数、失败数 |
| `liver_vol_mean_ml` / `_std_ml` / `_min_ml` / `_max_ml` | 肝脏体积统计 |
| `left_ratio_mean` / `_std` | 左叶比例统计 |
| `avg_tumors` / `total_tumors` | 肿瘤数量统计 |
| `tumor_diam_mean_mm` / `_std_mm` | 肿瘤直径统计 |
| `perfusion_modes` | 各灌注模式的案例数量分布 |
| `avg_gen_time_s` / `elapsed_s` | 生成时间统计 |

### 4.2 训练数据对应关系

| 网络输入 | 来源文件 | 说明 |
|:---|:---|:---|
| SPECT 投影（含噪声） | `case_XXXX.a00`（SIMIND 输出） | 模拟的真实 SPECT 采集数据 |
| 衰减校正参考 | `case_XXXX.d00`（SIMIND 输出） | 空白投影，用于 Chang 校正 |
| Ground truth 活度图 | `case_XXXX.npz` → `activity` | 无噪声、无衰减的理想活度分布 |
| Ground truth μ-map | `case_XXXX.npz` → `mu_map` | 理想衰减系数图 |
| 肿瘤标注 | `case_XXXX_meta.json` | 肿瘤位置、尺寸、类型 |

---

## 附录：关键参数一致性核查表

以下表格确认 PAR-S Generator 各模块之间的参数一致性：

| 参数 | `PhantomConfig` | `czt_ge.smc` | Interfile 头文件 | 说明 |
|:---|:---|:---|:---|:---|
| 体素大小 | `voxel_size_mm = 4.20` | `SYSTEM PIXEL SIZE: 0.42 cm` | `scaling factor: 4.2000 mm` | 三处完全一致 ✓ |
| 矩阵大小 | `volume_shape = (128,128,128)` | — | `matrix size [1/2] = 128` | 一致 ✓ |
| 光子能量 | — | `PRIMARY ENERGY WINDOW: 126–154 keV` | — | 对应 Tc-99m 140 keV ✓ |
| 衰减系数 | `mu_liver = 0.16 cm⁻¹` | `FLAG-2: 1`（启用衰减） | — | 140 keV 下肝脏 μ 值合理 ✓ |

---

## 参考文献

[^1]: Segars WP, Bond J, Frush J, et al. Population of anatomically variable 4D XCAT adult phantoms for imaging research and optimization. *Medical Physics*. 2013;40(4):043701. https://pmc.ncbi.nlm.nih.gov/articles/PMC3612121/

[^2]: Abdel-Misih SR, Bloomston M. Liver anatomy. *Surgical Clinics of North America*. 2010;90(4):643-653. https://pubmed.ncbi.nlm.nih.gov/20637938/

[^3]: Urata K, Kawasaki S, Matsunami H, et al. Calculation of child and adult standard liver volume for liver transplantation. *Hepatology*. 1995;21(5):1317-1321. https://pubmed.ncbi.nlm.nih.gov/7737637/

[^4]: Segars WP, Sturgeon G, Mendonca S, et al. 4D XCAT phantom for multimodality imaging research. *Medical Physics*. 2010;37(9):4902-4915. https://aapm.onlinelibrary.wiley.com/doi/abs/10.1118/1.3480985
