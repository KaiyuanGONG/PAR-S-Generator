# PAR-S V2 Task 3 体素几何门禁

- 选择角色: `coverage_qa_not_population_prevalence`
- 网格: `[128, 128, 128]` @ `4.42 mm`
- 病例数: **6**
- 总门禁: **PASS**

| Case | 形态 | 尾状叶 | 体积误差 | 最大三径误差 | 最大质心误差 | 粗糙度 | 结果 |
|---|---|---:|---:|---:|---:|---:|---|
| `task3_voxel_candidate_00012` | normal | False | 0.0037% | 1.30 mm | 0.05 mm | 0.2625 | PASS |
| `task3_voxel_candidate_00003` | normal | True | 0.0019% | 2.02 mm | 0.02 mm | 0.2564 | PASS |
| `task3_voxel_candidate_00005` | normal | True | -0.0036% | 1.52 mm | 0.09 mm | 0.2579 | PASS |
| `task3_voxel_candidate_00000` | cirrhotic | True | 0.0004% | 3.30 mm | 0.11 mm | 0.3088 | PASS |
| `task3_voxel_candidate_00001` | cirrhotic | True | -0.0081% | 5.32 mm | 0.52 mm | 0.2922 | PASS |
| `task3_voxel_candidate_00002` | cirrhotic | True | 0.0030% | 4.47 mm | 0.39 mm | 0.2734 | PASS |

## 聚合方向性

- 正常肝粗糙度均值: `0.2589`
- 肝硬化粗糙度均值: `0.2915`
