# PAR-S V2 Task 3 患者与肝脏目标采样统计报告

- Profile: `population_tare_hcc_nopvi_v2`
- Seed: `20260713`
- 无体素样本数: **10,000**
- 总门禁: **PASS**

## 关键分布

| 指标 | 观察值 | 目标/参考 |
|---|---:|---:|
| 男性比例 | 0.8331 | 0.8350 |
| 肝硬化比例 | 0.7871 | 0.8000 |
| 肝体积均值 (mL) | 1538.6 | 1533.0 |
| 肝体积 SD (mL) | 353.5 | 375.0 |
| 正常肝左叶比例中位数 | 0.3118 | 0.3100 |
| 身高–体重相关 | 0.5410 | > 0.45 |
| 体重–肝体积相关 | 0.4159 | > 0.30 |

## 肝硬化方向性

| 指标 | 正常 | 肝硬化 |
|---|---:|---:|
| S1–3/S4–8 proxy 均值 | 0.2808 | 0.5371 |
| 尾状叶比例均值 | 0.0149 | 0.0527 |
| 粗糙度目标均值 | 0.2560 | 0.2730 |

## 自动门禁

- [x] `male_fraction`
- [x] `cirrhosis_fraction`
- [x] `height_weight_correlation`
- [x] `weight_volume_correlation`
- [x] `volume_mean`
- [x] `volume_sd`
- [x] `normal_left_median`
- [x] `normal_left_variation`
- [x] `normal_left_support`
- [x] `cirrhotic_segment_direction`
- [x] `cirrhotic_caudate_direction`
- [x] `cirrhotic_roughness_direction`
- [x] `banned_upper_limit_not_used`

## 证据语义

年龄中位数和男性比例仅作为完整 TARE 队列的辅助边际；联合分布形状、肝体积条件模型、尾状叶出现率和连续表面场均保持 `engineering_prior`，不输出为 No-PVI prevalence。

`14×weight+979` 仅是已禁止的肝大上限式，本报告明确检查生成体积未使用该式。
