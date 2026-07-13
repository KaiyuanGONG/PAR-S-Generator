# PAR-S V2 Task 3 体素几何门禁

- 选择角色: `coverage_qa_not_population_prevalence`
- 网格: `[128, 128, 128]` @ `4.42 mm`
- 病例数: **14**
- 总门禁: **PASS**

| 角色 | 形态 | 尾状叶 | 体积误差 | 最大三径误差 | 几何左叶误差 | 腰比 | 渐薄比 | 尾状叶外显 | fossa | 结果 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `centre-normal-caudate-off` | normal | False | -1.7825% | 2.40 mm | -0.0614 | 0.8975 | 0.3546 | 0.0000 | 0.0571 | PASS |
| `centre-normal-caudate-on` | normal | True | -1.7851% | 3.93 mm | -0.0729 | 0.9251 | 0.3421 | 0.0154 | 0.0522 | PASS |
| `centre-cirrhotic-caudate-off` | cirrhotic | False | -0.0038% | 1.74 mm | +0.0165 | 0.8676 | 0.2555 | 0.0000 | 0.0250 | PASS |
| `centre-cirrhotic-caudate-on` | cirrhotic | True | -0.0164% | 3.82 mm | +0.0239 | 0.9869 | 0.2534 | 0.0423 | 0.0332 | PASS |
| `joint-size-p10` | normal | True | -2.6118% | 1.94 mm | -0.1013 | 0.9572 | 0.3227 | 0.0104 | 0.0580 | PASS |
| `joint-size-p90` | cirrhotic | True | -0.0015% | 1.25 mm | +0.0372 | 0.9745 | 0.2410 | 0.0515 | 0.0350 | PASS |
| `left-p05` | normal | False | -1.5209% | 2.76 mm | -0.0443 | 0.9989 | 0.3633 | 0.0000 | 0.0848 | PASS |
| `left-p95` | cirrhotic | False | 0.0052% | 2.98 mm | -0.0251 | 0.7959 | 0.2508 | 0.0000 | 0.0121 | PASS |
| `shape-u-p05` | normal | True | -1.5330% | 3.68 mm | -0.0202 | 0.9540 | 0.3328 | 0.0085 | 0.0651 | PASS |
| `shape-u-p95` | cirrhotic | True | 0.0065% | 2.04 mm | +0.0172 | 1.0000 | 0.3243 | 0.0211 | 0.0482 | PASS |
| `shape-v-p05` | normal | False | -1.7846% | 1.85 mm | -0.0939 | 0.9555 | 0.3598 | 0.0000 | 0.0627 | PASS |
| `shape-v-p95` | cirrhotic | False | -0.0077% | 3.45 mm | +0.0481 | 0.8749 | 0.2447 | 0.0000 | 0.0217 | PASS |
| `stress-cirrhotic-caudate-upper` | cirrhotic | True | 0.0194% | 1.32 mm | +0.0182 | 0.9414 | 0.2023 | 0.0519 | 0.0126 | PASS |
| `stress-cirrhotic-left-upper` | cirrhotic | True | -0.0228% | 1.56 mm | -0.0448 | 0.9962 | 0.2713 | 0.0395 | 0.0322 | PASS |

## 聚合方向性

- 正常肝粗糙度均值: `0.2437`
- 肝硬化粗糙度均值: `0.2523`
- 独立覆盖集差值（仅描述）: `+0.0086`
- 受控配对粗糙度差值（门禁）: `+0.0139`

上述形态阈值是用于拒绝明显不自然构造的工程 QA 门禁，待真实肝脏 mask 校准；并非文献直接给出的生理解剖阈值。

本报告验证固定目标的直接拟合（`shape_seed=None`），不把生产重试当作通过条件。pilot 前仍须用固定 `liver_seed` 运行真实生产小样本，并报告 first-pass rate、attempt histogram 与各失败门禁频次。
