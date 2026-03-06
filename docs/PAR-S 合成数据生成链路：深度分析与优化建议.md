# PAR-S 合成数据生成链路：深度分析与优化建议

> **报告范围**：本报告基于对 PAR-S 仓库代码（`DataCreation_SYN.ipynb`、`DataPreparation.ipynb`、`CORRECTED_RUN_SYN.bat`）、reference 文件（`GATE生成投影流程+末尾宏文件测试.md`、`czt_ge.smc`、`DOC2109131-NMCT-870-CZT-PDS.pdf`、`simind_manual.pdf`）以及您的 DVF-Generator 论文的综合分析，结合相关文献，提供针对肝脏体模构建、仿真软件选型和参数配置的优化建议。

---

## 一、当前链路的整体架构与问题诊断

您当前的合成数据生成链路分为三个阶段：**解析几何体模生成**（`DataCreation_SYN.ipynb`）、**格式转换**（`DataPreparation.ipynb`）和**蒙特卡洛仿真**（SIMIND 或 GATE）。这一链路的核心思路是正确的，但在各个环节均存在可以显著提升的空间。

---

## 二、肝脏体模构建：如何更准确？

### 2.1 当前方法的局限性

您目前的方法（`DataCreation_SYN.ipynb`）使用**双椭球并集 + 穹隆交集 + 胆囊窝差集**来构建肝脏形态，并通过 Cantlie 平面划分左右叶。这是一种合理的解析几何方法，但存在以下局限：

首先，真实人体肝脏形态远比双椭球复杂。文献研究表明，肝脏形态在人群中具有高度变异性，其形状受到性别、年龄、体型指数（BMI）、相邻器官压迫等多种因素影响 [1]。仅用椭球参数的随机扰动（`scale_jitter=0.10`，`rot_jitter_deg=5.0`）无法充分捕捉这种变异性。

其次，当前的 μ-map 构建过于简化。身体轮廓使用单一椭球，肺部使用固定位置的椭球，缺乏对肋骨、横膈膜等结构的建模，而这些结构对 SPECT 图像中的散射和衰减有显著影响。

### 2.2 优化方向一：增强解析几何体模的解剖真实性

在不引入 XCAT 的前提下，可以从以下几个维度提升解析体模的准确性：

**（a）肝脏形态参数化**

基于 CT 人群统计研究 [1]，肝脏的关键形态参数应当参考真实解剖数据进行随机采样，而非简单地对固定锚点施加均匀扰动。建议将以下参数的分布改为基于文献的统计分布：

| 参数 | 当前设置 | 建议改进 | 文献依据 |
|:---|:---|:---|:---|
| 右叶半轴（Z/Y/X） | 固定锚点 ± 10% | 均值±标准差，参考成人 CT 统计 | Liver shape analysis [1] |
| 左叶/右叶体积比 | `TARGET_LEFT_RATIO=0.35` | 范围扩展至 [0.25, 0.45]，正态分布 | 解剖学文献 |
| 肝脏整体位置 | `global_shift_range=0.05` | 增加 Z 轴（头脚方向）的更大偏移范围 | XCAT 人群研究 [2] |
| 肝脏旋转角度 | `rot_jitter_deg=5.0` | 扩展至 ±15°，并加入三轴旋转 | 解剖变异研究 |

**（b）引入更真实的肝脏边界特征**

真实肝脏的右叶下缘并非光滑椭球，而是具有一定的不规则性。可以在当前的高斯平滑（`SMOOTH_SIGMA=1.2`）之后，叠加一个低频的 3D 柏林噪声（Perlin noise）场来模拟表面不规则性，这在文献中已被证明可以提高合成体模的真实性 [3]。

**（c）改进 μ-map 的解剖分层**

当前 μ-map 仅包含身体/肺/肝/脊柱四层。建议增加以下结构，以提升仿真的物理准确性：

- **横膈膜**：位于肝脏上方，厚约 3-5 mm，μ 值约 0.15 cm⁻¹（与软组织相近）。
- **胆囊**：位于胆囊窝处，充满胆汁时 μ 值约 0.14 cm⁻¹，空腹时接近空气。
- **腹壁脂肪层**：μ 值约 0.09 cm⁻¹（低于水），在肥胖患者中对衰减校正有显著影响。

### 2.3 优化方向二：引入 XCAT 体模（长期推荐）

您的 DVF-Generator 论文已经使用了 **4D XCAT 数字体模**（50 个虚拟受试者，40 男 10 女）[2]，这是目前核医学仿真领域最广泛使用的数字体模，具有以下优势：

- 基于真实 CT 数据统计构建，解剖真实性远高于解析几何方法。
- 支持呼吸运动、心跳等动态过程的建模。
- 已内置多种组织的衰减系数，可直接生成 μ-map。
- 被大量文献验证，仿真结果具有良好的可重复性 [2] [4]。

**建议**：对于 PAR-S 的静态 SPECT 重建任务，可以直接复用 DVF-Generator 中已生成的 XCAT 体模数据（呼气末参考相位 φ=0 的帧），而无需重新生成解析几何体模。这样可以保证 PAR-S 和 DVF-Generator 两个项目使用完全一致的解剖基础，提升整个研究链路的内部一致性。

### 2.4 批量生成的参数调整建议

当前代码中，批量生成的随机性主要通过 `GLOBAL_SEED` 控制。为了生成足够多样化的训练数据，建议调整以下参数：

| 参数 | 当前值 | 建议值 | 理由 |
|:---|:---|:---|:---|
| `TUMOR_COUNT_RANGE` | (1, 5) | (0, 6) | 加入无肿瘤的正常肝脏案例，提升模型泛化性 |
| `TUMOR_BINS_MM` | [(10,20),(20,40),(40,60)] | 增加 (5,10) 小肿瘤区间 | 小肿瘤（<10mm）在临床中常见且检测困难 |
| `TUMOR_PROBS` | [0.30, 0.50, 0.20] | [0.40, 0.40, 0.20] | 增加小肿瘤比例 |
| `PERFUSION_PROBS` | Whole:5%, Tumor:25%, Left:35%, Right:35% | 保持，但增加双叶灌注减低的情况 | 模拟肝硬化等弥漫性病变 |
| `PSF_SIGMA_PX` | 2.5 | 2.0–3.5（随机采样） | 模拟不同扫描仪分辨率的变化 |
| `TOTAL_COUNTS` | 8×10⁴ | 5×10⁴ – 2×10⁵（对数均匀分布） | 模拟不同注射剂量和采集时间 |

---

## 三、仿真软件选型：SIMIND 还是 GATE？

这是您的核心问题之一。两款软件各有优势，选择取决于您的具体需求。

### 3.1 SIMIND

SIMIND 是由 Michael Ljungberg 教授（Lund 大学）开发的专用 SPECT 蒙特卡洛程序，已有超过 30 年的发展历史，是核医学仿真领域的标准工具之一 [5]。

**优势**：

- **专为 SPECT 设计**，配置简单，学习曲线平缓。`change` 程序提供菜单式参数配置，通过 `.smc` 文件即可完全定义仿真系统，无需编写复杂代码。
- **速度快**。由于 SIMIND 专注于 SPECT，其物理模型经过高度优化，仿真速度通常比通用蒙特卡洛程序（如 GATE）快 5–10 倍 [6]。
- **Windows 原生支持**。SIMIND 提供 64 位 Windows 可执行文件，可直接在 Windows 上运行，无需虚拟机。这与您开发 Windows 桌面应用的需求高度契合。
- **CZT 探测器支持**。Ljungberg 团队已专门为 GE Discovery Alcyone CZT SPECT 系统（与您的 GE NM/CT 870 CZT 同系列）开发了 CZT 电荷传输模型，并在 SIMIND 中实现，结果与实验测量吻合良好 [7]。
- **图像源（Image-Based Source）功能**。SIMIND 支持直接读取 Interfile 格式的 3D 活度图作为仿真源，与您当前的 `.h33`/`.i33` 工作流完全兼容。

**局限**：

- 仅支持 SPECT，不支持 PET 或 CT 仿真。
- 几何建模能力有限，复杂的探测器几何（如多针孔）配置较为繁琐。
- 不支持直接输出 DICOM 格式（需要后处理脚本转换）。

### 3.2 GATE（OpenGATE）

GATE 是基于 Geant4 的开源蒙特卡洛工具包，支持 PET、SPECT、CT、放疗剂量计算等多种应用 [8]。

**优势**：

- **通用性强**，可同时仿真 SPECT 和 CT，一套工具链即可生成配对的 SPECT/CT 数据。
- **物理模型完整**，基于 Geant4，物理过程建模最为精确，适合需要高精度物理验证的研究。
- **开源免费**，社区活跃，文档丰富，支持 Python 接口（OpenGate 10.x）。
- **支持 DICOM 输出**，可直接生成临床格式的仿真数据。

**局限**：

- **Windows 支持差**。GATE 主要在 Linux 环境下运行，Windows 用户通常需要通过 vGate 虚拟机（如您的 reference 文档所述）或 WSL2 使用，增加了部署复杂度。
- **仿真速度慢**。通用物理模型导致仿真时间显著长于 SIMIND，对于大规模批量生成训练数据（您需要数千个样本）而言，时间成本较高 [6]。
- **学习曲线陡峭**。宏文件语法复杂，CZT 探测器建模需要自行实现材料定义和数字化器配置（如您的 reference 文档中的详细步骤所示）。

### 3.3 综合建议与推荐方案

| 维度 | SIMIND | GATE |
|:---|:---|:---|
| Windows 原生支持 | **优秀**（直接运行） | 差（需虚拟机/WSL2） |
| 仿真速度 | **快**（5–10× 优势） | 慢 |
| SPECT 专用性 | **高** | 通用 |
| CZT 探测器建模 | **已验证** [7] | 需自行实现 |
| CT 仿真能力 | 无 | **支持** |
| 学习曲线 | **平缓** | 陡峭 |
| 大规模批量生成 | **适合** | 不适合 |

**结论**：对于您的 PAR-S 项目（Windows 桌面应用 + 大规模批量生成训练数据），**强烈推荐使用 SIMIND** 作为主要仿真工具。SIMIND 在 Windows 上的原生支持、快速的仿真速度以及已验证的 CZT 探测器模型，使其成为您当前需求的最优选择。

关于 CT 图像的生成，由于 SIMIND 不支持 CT 仿真，建议采用以下替代方案：**直接将 μ-map 转换为 CT HU 值**（如您的 GATE 流程文档中已有的 `atn_to_ct_dicom.py` 脚本所示），这种方法虽然不是真正的 CT 仿真，但对于训练需要配对 SPECT/CT 数据的神经网络而言已经足够，且与临床上使用 CT 进行衰减校正的实际流程一致。

---

## 四、仿真参数配置：GE NM/CT 870 CZT

根据您的数据手册（`DOC2109131-NMCT-870-CZT-PDS.pdf`）和 reference 文档，以下是针对 GE NM/CT 870 CZT 的完整仿真参数配置建议。

### 4.1 探测器参数

| 参数 | 数值 | 来源 |
|:---|:---|:---|
| 探测器材料 | CZT（Cd₀.₉Zn₀.₁Te） | 数据手册 |
| 晶体厚度 | 7.25 mm | 数据手册 |
| 像素尺寸（pixel pitch） | 2.46 mm × 2.46 mm | 数据手册 |
| 探测器矩阵 | 160 × 208 像素 | 数据手册 |
| FOV | 393.6 mm × 511.7 mm | 数据手册 |
| 能量范围 | 40–250 keV | 数据手册 |
| Tc-99m 能量分辨率（FWHM） | ≤ 6.3% @ 140 keV | 数据手册 |

### 4.2 准直器参数（WEHR，H3906CM）

| 参数 | 数值 | 来源 |
|:---|:---|:---|
| 孔形 | 方形（square） | 数据手册 |
| 孔径 | 2.26 mm | 数据手册 |
| 隔板厚度 | 0.20 mm | 数据手册 |
| 孔长（厚度） | 45 mm | 数据手册 |
| 系统分辨率 | FWHM ≈ 7.6 mm @ 100 mm | 数据手册 |
| Tc-99m 穿透率 | ≈ 0.55% @ 100 mm | 数据手册 |

### 4.3 采集几何参数

| 参数 | 数值 | 说明 |
|:---|:---|:---|
| 旋转轨道 | 圆形，半径 ~25 cm | 典型临床全身扫描 |
| 旋转范围 | 360° | 全角度采集 |
| 角度步长 | 6° | 60 个投影角度 |
| 投影矩阵 | 128 × 128 | 标准临床设置 |
| 投影像素大小 | ~3.07 mm | 与探测器像素对应 |

### 4.4 能量窗参数（Tc-99m）

| 窗口 | 范围 | 用途 |
|:---|:---|:---|
| 主窗（光电峰） | 126–154 keV（140 keV ± 10%） | 主要计数采集 |
| 散射窗（可选） | 114–126 keV | TEW 散射校正的低能窗 |

### 4.5 SIMIND `.smc` 文件关键参数对照

对照您现有的 `czt_ge.smc` 文件，以下是需要确认或调整的关键 Index 值：

| SIMIND Index | 含义 | 建议值 |
|:---|:---|:---|
| Index 1 | 光子能量 (keV) | 140.5（Tc-99m） |
| Index 15 | 源类型 | 负值（图像源模式） |
| Index 26 | 每投影光子历史数 | 5×10⁶（可通过 `/NN` 倍增） |
| Index 28 | 投影像素大小 (cm) | 0.307 |
| Index 32 | 非均匀体模方向 | 2（Z 方向） |
| Index 79 | 源图像矩阵大小（XY） | 128 |
| Index 82 | 源图像矩阵大小（Z） | 128 |
| Flag 5 | SPECT 模式 | True |
| Flag 1 | 屏幕输出 | False（批量运行时） |

---

## 五、其他可选仿真软件

除 SIMIND 和 GATE 外，以下软件也值得了解：

**SIMSET**（University of Washington）：另一款专用 SPECT/PET 蒙特卡洛程序，功能与 SIMIND 类似，但社区活跃度和文档完整性不如 SIMIND，不推荐作为主要工具。

**MCNP/MCNPX**：通用粒子输运程序，物理精度高，但学习曲线极为陡峭，且需要许可证，不适合快速迭代的研究场景 [6]。

**TOPAS**（TOol for PArticle Simulation）：基于 Geant4 的高级用户界面，比 GATE 更易用，但主要面向放疗剂量计算，SPECT 仿真支持有限。

**stir-simset / STIR**：STIR（Software for Tomographic Image Reconstruction）是开源的 SPECT/PET 重建框架，可与 SIMIND 输出无缝对接，适合在仿真之后进行重建验证。

---

## 六、推荐的优化后完整链路

综合以上分析，建议将您的合成数据生成链路优化为以下流程：

```
[阶段 1] 体模生成
  ├── 方案 A（短期）：增强版解析几何体模
  │   DataCreation_SYN.ipynb（按本报告第二节建议修改参数）
  │   输出：act.npz + atn.npz（128×128×128，4.2 mm 体素）
  │
  └── 方案 B（长期）：复用 DVF-Generator 的 XCAT 体模
      直接使用 XCAT 呼气末参考帧
      输出：act.npz + atn.npz

[阶段 2] 格式转换
  DataPreparation.ipynb（现有代码，无需大改）
  输出：case_XXX_act_1.h33/.i33 + case_XXX_atn_1.h33/.i33

[阶段 3] SPECT 仿真（SIMIND，Windows 原生）
  simind.exe + czt_ge.smc
  批量执行脚本（.bat 或 Python 调用 subprocess）
  输出：SPECT 投影数据（.bxx 格式）

[阶段 4] CT 生成（Python 脚本）
  μ-map → HU 值转换 → CT DICOM 序列
  atn_to_ct_dicom.py（参考 GATE 流程文档中的脚本）
  输出：CT DICOM 序列

[阶段 5] 后处理与数据集打包
  SPECT 投影 → Interfile/DICOM 格式
  配对 (SPECT 投影, CT, 活度真值) → 训练数据集
```

---

## 七、桌面应用开发建议

基于以上分析，您计划开发的 Windows 桌面应用应当将上述五个阶段整合为一个统一的图形化工作流管理工具。建议采用以下技术方案：

- **前端框架**：Electron + React 或 PyQt6/PySide6（后者与 Python 科学计算栈集成更好）。
- **核心逻辑**：Python（NumPy、SciPy、PyDICOM），直接复用现有 notebook 中的代码。
- **仿真调用**：通过 Python `subprocess` 模块调用 `simind.exe`，实时捕获输出并显示进度。
- **可视化**：使用 Matplotlib 或 Plotly 提供 3D 体模预览和 2D 投影切片查看。

---

## 参考文献

[1] Liver shape analysis using statistical parametric maps at population scale. *BMC Medical Imaging*, 2023. https://link.springer.com/article/10.1186/s12880-023-01149-5

[2] W.P. Segars et al. Population of anatomically variable 4D XCAT adult phantoms for imaging research and optimization. *Medical Physics*, 40(4), 2013.

[3] J. Leube et al. Analysis of a deep learning-based method for generation of SPECT projections based on a large Monte Carlo simulated dataset. *EJNMMI Physics*, 2022. https://link.springer.com/article/10.1186/s40658-022-00476-w

[4] H. Pieters et al. Validation of a Monte Carlo simulated cardiac phantom for planar and SPECT studies. *Physica Medica*, 2023. https://www.sciencedirect.com/science/article/pii/S1120179723000947

[5] M. Ljungberg. The SIMIND Monte-Carlo Program, Version 8.0. Lund University, 2025. (本地文件：`simind_manual.pdf`)

[6] A. Taheri et al. Monte Carlo simulation of a SPECT system: GATE, MCNPX or SIMIND? (a comparative study). *Journal of Instrumentation*, 12(12), 2017. https://iopscience.iop.org/article/10.1088/1748-0221/12/12/P12022

[7] M. Ljungberg et al. Monte Carlo simulations of the GE Discovery Alcyone CZT SPECT systems. *IEEE NSS/MIC*, 2014. https://ieeexplore.ieee.org/document/7430823

[8] D. Sarrut et al. The OpenGATE ecosystem for Monte Carlo simulation in medical physics. *Physics in Medicine & Biology*, 2022. https://pmc.ncbi.nlm.nih.gov/articles/PMC11149651/
