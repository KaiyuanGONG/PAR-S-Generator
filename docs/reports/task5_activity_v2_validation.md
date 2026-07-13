# PAR-S V2 Task 5 活度目标统计审计

- Profile: `population_tare_hcc_nopvi_v2`
- 病灶级目标数: **10,000**
- 总门禁: **PASS**

| 指标 | 目标/证据 | 观察 |
|---|---:|---:|
| TNR mean | 2.110 | 2.058 |
| TNR SD | 1.240 | 1.087 |
| 异质病灶比例 | 0.750 | 0.751 |
| 患者内相关 | 文献未报告 | 0.449（工程模型） |

## 尺寸相关坏死工程函数

| Dmax (mm) | 坏死概率 |
|---:|---:|
| 20 | 0.0585 |
| 40 | 0.1589 |
| 60 | 0.3646 |
| 80 | 0.6354 |
| 100 | 0.8411 |
| 120 | 0.9415 |
| 160 | 0.9933 |

TNR 与总体异质性是 HCC 病灶级文献边际；患者内相关结构、低频场和尺寸→坏死映射是显式 `engineering_prior`。注射区域为 `coverage_sampling`，不是疾病 prevalence。

`tumor_dominant_low_background`、`extreme_low_uptake` 与 territory mismatch 仅属于 population-weight-zero challenge，不进入上述主统计。

## 自动门禁

- [x] `tnr_support`
- [x] `tnr_mean`
- [x] `tnr_sd`
- [x] `heterogeneous_fraction`
- [x] `necrosis_probability_increases_with_size`
- [x] `tnr_evidence_is_hcc_lesion_level`
- [x] `injection_territory_is_not_population`
- [x] `within_patient_model_is_engineering`
