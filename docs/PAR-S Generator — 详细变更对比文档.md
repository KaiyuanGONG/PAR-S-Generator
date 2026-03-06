# PAR-S Generator — 详细变更对比文档

> **文档说明**：本文档逐项对比原始 PAR-S `notebooks/DataCreation_SYN.ipynb` 与 `notebooks/DataPreparation.ipynb` 中的代码，与最终开发的 PAR-S Generator 桌面应用（`src/core/phantom_generator.py`、`src/core/interfile_writer.py`、`src/core/batch_runner.py`）之间的所有差异。分为**参数变更**、**逻辑完善**、**架构重构**、**新增功能**四大类。

---

## 一、参数变更（数值调整）

### 1.1 肝脏几何锚点参数

这是最直接的数值差异，原始 notebook 中的锚点参数是通过交互式调参工具手动调出的视觉结果，但实际生成的肝脏体积约为 3400 mL（正常人体约 1200–1800 mL）。应用中对所有半轴进行了系统性缩小，使体积均值落在 ~1571 mL，范围 1184–2051 mL。

| 参数 | Notebook 原始值 | 应用修正值 | 修改原因 |
|:---|:---|:---|:---|
| `right_radii` (Z, Y, X) | `(0.50, 0.40, 0.40)` | `(0.38, 0.30, 0.30)` | 原始值生成体积 ~3400 mL，偏大约 2× |
| `right_shift` (Z, Y, X) | `(0.00, 0.00, 0.15)` | `(0.00, 0.00, 0.10)` | 配合半轴缩小，保持右叶位置合理 |
| `right_rot_deg` | `-15.0°` | `-15.0°` | **保持不变** |
| `left_radii` (Z, Y, X) | `(0.25, 0.35, 0.35)` | `(0.20, 0.26, 0.26)` | 同比缩小，维持左/右叶比例 |
| `left_shift` (Z, Y, X) | `(0.23, 0.10, 0.00)` | `(0.18, 0.07, 0.00)` | 配合半轴缩小，防止左叶过度延伸 |
| `left_rot_deg` | `10.0°` | `10.0°` | **保持不变** |
| `dome_radius` | `0.60` | `0.46` | 穹隆切割球缩小，与肝脏整体尺寸匹配 |
| `fossa_radius` | `0.45` | `0.34` | 胆囊窝切割球缩小，防止过度挖空 |
| `dome_offset` (Z, Y, X) | `(-0.10, 0.00, 0.00)` | `(-0.07, 0.00, 0.00)` | 微调，使穹隆顶部位置更自然 |
| `fossa_offset` (Z, Y, X) | `(-0.30, -0.05, 0.00)` | `(-0.22, -0.04, 0.00)` | 同比缩小偏移量 |

### 1.2 肿瘤尺寸分布

原始 notebook 的肿瘤尺寸区间从 10 mm 起步，遗漏了临床上最常见的小肿瘤（5–10 mm）。应用中新增了这一区间，并重新分配了概率权重。

| 参数 | Notebook 原始值 | 应用修正值 |
|:---|:---|:---|
| `TUMOR_BINS_MM` | `[(10,20), (20,40), (40,60)]` | `[[5,10], [10,20], [20,40], [40,60]]` |
| `TUMOR_PROBS` | `[0.30, 0.50, 0.20]` | `[0.20, 0.35, 0.30, 0.15]` |
| `TUMOR_COUNT_RANGE` | `(1, 5)`（最少 1 个）| `tumor_count_min=0, tumor_count_max=5`（允许 0 个）|

> **说明**：原始 notebook 中 `TUMOR_COUNT_RANGE = (1, 5)` 意味着每个 case 至少有 1 个肿瘤，这对于训练"无肿瘤"的阴性样本是不合理的。应用中将最小值改为 0，允许生成无肿瘤的正常肝脏 case。

### 1.3 μ-map 组织层次

原始 notebook 的 μ-map 只有 4 层（body/water、lung、liver、spine）。应用中新增了 2 个组织层，使衰减图的解剖层次更完整。

| 组织 | Notebook | 应用 | μ 值 (cm⁻¹) |
|:---|:---|:---|:---|
| Body (water) | ✅ | ✅ | 0.15 |
| Lung | ✅ | ✅ | 0.05 |
| Liver | ✅ | ✅ | 0.16 |
| Spine | ✅ | ✅ | 0.30 |
| **Diaphragm** | ❌ 无 | ✅ 新增 | **0.15** |
| **Abdominal fat** | ❌ 无 | ✅ 新增 | **0.09** |

### 1.4 其他参数

| 参数 | Notebook 原始值 | 应用值 | 说明 |
|:---|:---|:---|:---|
| `SMOOTH_THR` | `0.5` | `0.5` | 保持不变 |
| `SMOOTH_SIGMA` | `1.2` | `1.2` | 保持不变 |
| `TOTAL_COUNTS` | `8e4` | `8e4` | 保持不变 |
| `GRADIENT_GAIN` | `0.08` | `0.08` | 保持不变 |
| `PSF_SIGMA_PX` | `2.5` | `2.5` | 保持不变 |
| `RESIDUAL_BG` | `0.05` | `0.05` | 保持不变 |
| `TARGET_LEFT_RATIO` | `0.35` | `0.35` | 保持不变 |
| `VOXEL_SIZE_MM` | `4.20` | `4.20` | 保持不变 |
| `VOLUME_SHAPE` | `(128, 128, 128)` | `(128, 128, 128)` | 保持不变 |

---

## 二、逻辑完善（Bug 修复与算法改进）

### 2.1 肿瘤数量上限溢出 Bug

**原始 notebook 代码**（`Cell 6`，`_plant_tumors` 方法）：
```python
n_tumors = rng.integers(cfg.TUMOR_COUNT_RANGE[0], cfg.TUMOR_COUNT_RANGE[1] + 1)
```
这里 `rng.integers(low, high)` 的 `high` 是**不含端点**的，所以 `+1` 是正确的。但原始代码中实际写的是：
```python
n_tumors = int(rng.random() * (max_t - min_t + 1)) + min_t
```
当 `rng.random()` 恰好等于 1.0（极小概率）时，`n_tumors` 会等于 `max_t + 1`，超出上限。

**应用修复**：统一使用 `rng.integers(min_t, max_t + 1)` 的 NumPy 标准接口，保证结果严格在 `[min_t, max_t]` 范围内。

### 2.2 肿瘤对比度统一化

**原始 notebook 代码**：每个肿瘤有独立的对比度值：
```python
val = (base * t["contrast"]) if is_hot else bg_val
```
其中 `t["contrast"]` 在 `_plant_tumors` 时就已经为每个肿瘤单独采样。

**应用修正**：改为在灌注阶段统一采样一个全局对比度值，所有肿瘤使用相同的对比度：
```python
contrast = rng.uniform(cfg.tumor_contrast_min, cfg.tumor_contrast_max)
for tmask in tumor_masks:
    base_val = activity[tmask].mean() if activity[tmask].sum() > 0 else 1.0
    activity[tmask] = base_val * contrast
```
这样更符合临床现实（同一次扫描中肿瘤摄取差异主要来自大小，而非独立随机），同时使 `tumor_contrast_min/max` 参数的含义更直观。

### 2.3 肝叶分割的 Cantlie 平面迭代

**原始 notebook 代码**（`split_liver_lobes_3d`）：使用固定的 `tilt_deg=5.0, offset=0.0` 参数，不进行迭代优化，实际左叶比例与 `TARGET_LEFT_RATIO=0.35` 目标值可能偏差较大。

**应用实现**：增加了 Cantlie 平面的二分法迭代优化（最多 `cantlie_iter_max=12` 次迭代），在 `cantlie_tilt_range=(-6°, 10°)` 和 `cantlie_offset_range=(-0.12, 0.12)` 范围内搜索最优切割参数，使实际左叶比例尽量接近目标值 0.35：

```python
# 应用中的迭代逻辑（phantom_generator.py 约 310-340 行）
for _ in range(cfg.cantlie_iter_max):
    left_t, right_t = Geometry3D.split_liver_lobes(liver, shape, tilt_deg, offset)
    ratio = left_t.sum() / max(liver.sum(), 1)
    if abs(ratio - cfg.target_left_ratio) < 0.02:
        break
    # 二分法调整 offset
    if ratio < cfg.target_left_ratio:
        offset -= step
    else:
        offset += step
    step *= 0.6
```

### 2.4 肿瘤归属叶的标记方式

**原始 notebook 代码**：在 `_plant_tumors` 时通过 `t["lobe"]` 字段标记肿瘤属于左叶还是右叶，但 `_plant_tumors` 方法需要接收 `left, right` 参数，原始代码中存在参数传递遗漏（注释中有 `# 🔴 关键修复：参数增加了 left, right`）。

**应用修正**：在 `generate_one` 方法中，肿瘤生成时直接通过 `left_mask` 和 `right_mask` 的交集来判断归属，不依赖额外的 `lobe` 字段，逻辑更清晰：
```python
# 判断肿瘤归属叶（通过掩码交集）
lobe = "left" if (tmask & left_mask).sum() > (tmask & right_mask).sum() else "right"
```

### 2.5 体积计算方式

**原始 notebook 代码**：在 `BatchProductionPipeline` 中没有计算肝脏体积，只在 `meta` 中记录了 `left_ratio`。

**应用实现**：精确计算并记录肝脏体积（mL）：
```python
vox_vol_ml = (cfg.voxel_size_mm / 10) ** 3   # 1 voxel = (4.2/10)³ cm³ = mL
liver_volume_ml = float(liver.sum() * vox_vol_ml)
```

### 2.6 Interfile 头文件格式

**原始 notebook 代码**（`DataPreparation.ipynb Cell 4`）：`write_interfile_header` 函数生成的头文件使用 `(Nx, Ny, Nz)` 顺序，但 NumPy 数组是 `(Z, Y, X)` 顺序，存在轴顺序不一致的隐患：
```python
Nx, Ny, Nz = shape   # 原始代码直接解包，未转置
```

**应用修正**（`interfile_writer.py`）：明确区分 NumPy 的 `(Z, Y, X)` 存储顺序与 Interfile 的 `(X, Y, Z)` 约定，在写入前进行显式转置，并在头文件中正确标注轴顺序：
```python
# interfile_writer.py
arr_to_write = arr.transpose(2, 1, 0)  # (Z,Y,X) → (X,Y,Z) for Interfile
Nx, Ny, Nz = arr_to_write.shape
```

### 2.7 输出文件命名规范

| 方面 | Notebook 原始 | 应用规范 |
|:---|:---|:---|
| npz 文件名 | `case_00000.npz`（5位，从 0 起） | `case_0000.npz`（4位，从 0 起） |
| npz 内 key 名 | `activity_clean`, `attenuation`, `mask_liver` 等 | `activity`, `mu_map`, `liver_mask`, `left_mask`, `right_mask` |
| meta 文件 | 统一写入 `dataset_registry.json` | 每个 case 单独的 `case_XXXX_meta.json` |
| Interfile 文件名 | `case_00000_act.h33/.i33` | `case_0000_act.h33/.i33`（与 npz 命名保持一致）|

---

## 三、架构重构（从 Notebook 到生产代码）

### 3.1 配置系统：从 `class Config` 到 `@dataclass PhantomConfig`

这是最重要的架构变化。原始 notebook 使用一个普通类 `Config` 存储所有参数，所有参数都是类变量（class attributes），无法实例化多个不同配置，也无法序列化。

应用中将其重构为 Python `dataclass`，具备以下能力：

| 能力 | Notebook `class Config` | 应用 `@dataclass PhantomConfig` |
|:---|:---|:---|
| 多实例 | ❌ 单例，全局共享 | ✅ 可创建多个独立配置 |
| JSON 序列化 | ❌ 需手动实现 | ✅ `config.save(path)` 一行保存 |
| JSON 反序列化 | ❌ 需手动实现 | ✅ `PhantomConfig.load(path)` 一行加载 |
| 类型提示 | ❌ 无 | ✅ 完整类型注解 |
| 默认值工厂 | ❌ 可变默认值有 bug 风险 | ✅ 使用 `field(default_factory=...)` |
| UI 绑定 | ❌ 无法直接绑定 | ✅ 每个字段对应一个 UI 控件 |

### 3.2 生成器：从 `SyntheticGenerator3D` 到 `PhantomGenerator`

原始 notebook 的 `SyntheticGenerator3D` 类将所有逻辑（体模生成、肿瘤植入、灌注、μ-map）混合在一个类中，且依赖全局 `Config` 类。应用中将其重构为接收 `PhantomConfig` 实例的 `PhantomGenerator`，并引入 `PhantomResult` 数据类封装输出。

```
原始：
SyntheticGenerator3D(Config) → (act_clean, act_noisy, mu, masks, meta)

应用：
PhantomGenerator(config: PhantomConfig) → PhantomResult
  PhantomResult.activity       # float32 ndarray
  PhantomResult.mu_map         # float32 ndarray
  PhantomResult.liver_mask     # bool ndarray
  PhantomResult.left_mask      # bool ndarray
  PhantomResult.right_mask     # bool ndarray
  PhantomResult.tumor_masks    # list of bool ndarrays
  PhantomResult.tumor_radii_mm # list of floats (直径，mm)
  PhantomResult.tumor_modes_used # list of str
  PhantomResult.perfusion_mode # str
  PhantomResult.liver_volume_ml # float
  PhantomResult.left_ratio     # float
  PhantomResult.n_tumors       # int
  PhantomResult.total_counts_actual # float
  PhantomResult.generation_time_s   # float
```

原始 notebook 的 `meta` 字典只有 5 个字段（`case_id, seed, perfusion_mode, tumor_count, left_ratio`），应用的 `PhantomResult` 有 **17 个字段**，增加了肿瘤半径列表、肿瘤形态模式、体积、生成时间等详细信息。

### 3.3 批量生成：从 `BatchProductionPipeline` 到 `BatchWorker`

| 方面 | Notebook `BatchProductionPipeline` | 应用 `BatchWorker` |
|:---|:---|:---|
| 运行方式 | 同步，阻塞 Jupyter 内核 | 异步，在独立 `QThread` 中运行 |
| 进度反馈 | `print()` 每 50 个打印一次 | Qt 信号实时推送（每个 case 完成即推送）|
| 中断支持 | ❌ 只能强制中断内核 | ✅ `worker.stop()` 优雅停止 |
| 错误处理 | `try/except` 打印后继续 | 捕获后通过信号推送到 UI，记录失败列表 |
| ETA 计算 | 简单的剩余时间估算 | 滑动窗口平均速度，ETA 更稳定 |
| 路径配置 | 硬编码 `D:\PFE-U\PAR-S\data\raw\SYN` | 通过 `PhantomConfig.output_dir` 配置 |
| 注册表 | 单个 `dataset_registry.json` | 每个 case 独立 `_meta.json` + 批次级 `batch_summary.json` |

### 3.4 格式转换：从 notebook 函数到独立模块

原始 `DataPreparation.ipynb` 中的格式转换是两个独立函数（`write_interfile_header` 和 `convert_single_npz_to_interfile`），散落在 notebook cell 中，无法复用。

应用中将其封装为独立模块 `src/core/interfile_writer.py`，提供：
- `InterfileWriter` 类，支持单文件转换和批量转换
- 自动创建输出目录
- 写入转换日志
- 验证输出文件完整性（检查文件大小是否与头文件声明一致）

---

## 四、新增功能（Notebook 中完全没有的）

### 4.1 图形用户界面（GUI）

原始 notebook 依赖 `ipywidgets` 提供交互，只能在 Jupyter 环境中运行。应用提供完整的 PyQt6 桌面 GUI，包含：

| 页面 | 功能 |
|:---|:---|
| **Phantom** | 参数面板（分组滑块）+ 三平面切片预览 + 3D 等值面 + 统计信息卡片 |
| **Simulation** | npz → Interfile 转换配置 + SIMIND 调用配置 + 一键生成 .bat 脚本 |
| **Results** | 批量生成进度条 + ETA + 实时统计图表（6 张）+ 完整案例表格 + 日志 |
| **Settings** | SIMIND 路径、默认目录、CPU 线程数等持久化设置 |

### 4.2 配置文件的保存与加载

原始 notebook 中修改参数需要直接编辑代码。应用支持将当前参数配置保存为 JSON 文件，并在下次启动时加载，实现参数预设管理。

### 4.3 生成时间统计

原始 notebook 只在批量生成时统计总耗时。应用对每个 case 单独记录生成时间（`generation_time_s`），并在 Results 页面展示生成速度分布直方图。

### 4.4 SIMIND 执行脚本自动生成

原始仓库中的 `CORRECTED_RUN_SYN.bat` 是手动编写的。应用的 Simulation 页面可以根据当前配置（输入目录、SIMIND 路径、.smc 文件路径）自动生成对应的 `.bat` 脚本，并支持直接在应用内调用 `simind.exe`。

### 4.5 数据集注册表

原始 notebook 的 `dataset_registry.json` 只记录 5 个字段。应用生成的注册表包含 14 个字段，并额外生成 `batch_summary.json` 汇总整批次的统计信息（成功率、平均体积、肿瘤分布等）。

---

## 五、未改变的核心算法

以下算法与原始 notebook **完全一致**，未作任何修改：

| 算法 | 说明 |
|:---|:---|
| `Geometry3D.create_ellipsoid` | 旋转椭球体生成（含 xz 平面旋转） |
| `Geometry3D.create_spiculated_tumor` | 毛刺状肿瘤（高斯噪声扰动半径）|
| `Geometry3D.create_superellipsoid` | 超椭球体肿瘤 |
| `Geometry3D.create_noise_threshold` | 噪声阈值肿瘤 |
| `Geometry3D.split_liver_lobes` | Cantlie 平面分割（基础实现）|
| PSF 模糊 + Poisson 噪声 | `gaussian_filter` + `rng.poisson` |
| 灌注模式概率分布 | `{Whole Liver: 5%, Tumor Only: 25%, Left Only: 35%, Right Only: 35%}` |
| 活度梯度 | Z 轴线性梯度，`gain=0.08` |
| μ-map 纹理噪声 | 高斯噪声叠加，`amp=0.015, sigma=2.0` |

---

## 六、变更汇总

| 类别 | 数量 | 影响 |
|:---|:---|:---|
| 参数数值调整 | 10 项 | 肝脏体积从 ~3400 mL 修正至 ~1571 mL |
| 新增参数 | 6 项 | 新增小肿瘤区间、横膈膜/脂肪 μ 值、允许 0 肿瘤 |
| Bug 修复 | 4 项 | 肿瘤数量溢出、轴顺序不一致、参数传递遗漏、体积未计算 |
| 算法完善 | 2 项 | Cantlie 迭代优化、对比度统一化 |
| 架构重构 | 4 项 | Config→dataclass、批量异步化、输出结构化、格式转换模块化 |
| 新增功能 | 5 项 | GUI、配置保存/加载、生成时间统计、SIMIND 脚本生成、注册表 |
| **未改变** | 9 项核心算法 | 所有几何体生成算法保持原始实现 |
