# PAR-S V2 Task 4 肿瘤目标、栅格与完整放置报告

- Profile: `population_tare_hcc_nopvi_v2`
- 无体素目标样本: **10,000**
- 真实肝脏端到端病例: **3**
- 总门禁: **PASS**

## 文献分层边际

| 分层 | 类别 | 目标 | 观察 |
|---|---|---:|---:|
| count_bins | `1` | 0.3240 | 0.3239 |
| count_bins | `2-5` | 0.2240 | 0.2291 |
| count_bins | `>5` | 0.4520 | 0.4470 |
| dmax_bins | `10-<80_mm` | 0.6030 | 0.6017 |
| dmax_bins | `80-200_mm` | 0.3970 | 0.3983 |
| lobe_extents | `unilobar` | 0.5870 | 0.5851 |
| lobe_extents | `bilobar` | 0.4130 | 0.4149 |

数量层、Dmax 层和单/双叶层为 `literature_population`；层内具体数量、截断对数正态 Dmax、次级病灶比例、亚包膜概率和形态混合均为 `engineering_prior`，没有伪装成文献 prevalence。

## 必测直径栅格门禁

| 目标 (mm) | 实测 RECIST (mm) | 误差 (mm) | 容差 (mm) | primitive | 结果 |
|---:|---:|---:|---:|---:|---|
| 10 | 13.26 | 3.26 | 3.31 | 1 | PASS |
| 20 | 18.75 | 1.25 | 3.31 | 1 | PASS |
| 40 | 40.16 | 0.16 | 3.31 | 1 | PASS |
| 60 | 60.41 | 0.41 | 3.31 | 1 | PASS |
| 100 | 100.21 | 0.21 | 3.31 | 1 | PASS |
| 200 | 200.64 | 0.64 | 6.00 | 3 | PASS |
| 215 | 214.96 | 0.04 | 6.45 | 3 | PASS |

## 真实 Task 3 肝脏上的端到端门禁

- first-pass fraction: `1.000`
- attempt histogram: `{'1': 3}`
- rejection reasons: `{}`
- 该端到端集合是几何/重试链 smoke gate，不用 3 例 first-pass 比例推断 500 例生产通过率。

| 病例 | 肝形态 | 数量层 | Dmax 层 | 单/双叶 | 病灶数 | Dmax | 负荷 | 尝试 | 结果 |
|---|---|---|---|---|---:|---:|---:|---:|---|
| `task4_e2e_000` | cirrhotic | >5 | 10-<80_mm | bilobar | 8 | 67.1 | 0.075 | 1 | PASS |
| `task4_e2e_001` | cirrhotic | 1 | 10-<80_mm | unilobar | 1 | 60.2 | 0.025 | 1 | PASS |
| `task4_e2e_002` | cirrhotic | 1 | 80-200_mm | unilobar | 1 | 84.0 | 0.069 | 1 | PASS |

完整 containment 是对未裁切的完整 primitive union 逐体素检查；不同病灶实例在接受前检查零重叠。融合病灶允许自身 primitive 受控重叠，但输出单一 instance label。
