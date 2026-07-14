# PAR-S V2 Task 12：首批 3 例 pilot 报告

- Generator gate：**PASS**
- Dataset：`PAR-S-TARE-HCC-NoPVI-SYN-v2-pilot3` / `2.0.0-pilot3`
- 冻结病例数：3
- Manifest SHA-256：`900b3ec8dc71b388e8d5aa79a752677e755043b705b057ab57e9f2e1bc512dbd`
- `/NN=1` 仅用于 deterministic smoke；不能据此声明临床计数标定完成。
- 200 mm 与 215 mm 为预期结构性拒绝边界，不伪装成可完整 containment 的主人群病例。

## 病例结果

| Case | Split | 肝形态 | 目标 Dmax (mm) | 实际 RECIST (mm) | 叶范围 | 注射区 | Mismatch | /RR | 投影权重和 |
|---|---|---|---:|---:|---|---|---|---:|---:|
| case_00000 | train | normal | 20.0 | 18.75 | unilobar | whole_liver | False | 7765 | 750112 |
| case_00001 | test | cirrhotic | 55.0 | 54.87 | unilobar | right_lobar | False | 5706 | 961086 |
| case_00002 | val | cirrhotic | 90.0, 24.0 | 89.80, 25.70 | bilobar | left_lobar | True | 3647 | 776081 |

## 当前结论

Generator 端生成、SIMIND、原子 case writer 与 dataset freeze 均已通过；
PAR-S_2 也已通过冻结 manifest 的 train/val/test 实际加载门禁。多切面、投影
sinogram 与每视角权重的只读复核见
[`v2_pilot3_overview.png`](v2_pilot3_overview.png)，其 SHA-256 为
`908b577f84531ce4d2f422864008abf5350c4f168e03b8ba39dc1aae1c5d4489`；未见病灶截断、
mask 错位或非有限投影。

Task 11 原冻结变换在 3 例合并搜索中仍排名第一，但 NN=1 smoke 的唯一性门禁未通过：
score margin `0.002931 < 0.005`、bootstrap top-1 `0.34 < 0.95`、逐病例一致率
`1/3 < 1.0`。这不是一个统一的 loader 方向错误；替代最优解在病例间分别为不同的
reverse/roll 组合。复核进一步发现 Task 11 阈值的实际基线是 `80,000 × /NN=5`
（约 400,000 histories/projection），而本数据集按设计仅为 `/NN=1`。

因此当前状态为：**3 例数据冻结与 loader PASS，但扩大到 15 例仍为 NO-GO**。
下一门禁是在不覆盖正式四件套的独立目录中，对同一冻结 phantom/source/density
运行 `/NN=5` companion 并原样重跑 480 候选阈值；若仍不唯一，再运行同历史水平、
强非对称、零衰减的 current-runtime 坐标 fixture。禁止降低阈值或挑选病例绕过。

完整非 UI 回归还发现并修复了 main/negative `/RR` 跨前缀碰撞：正式 500+50
现共享一个仿射置换并占用不重叠槽位，同时保持本批三例已冻结 `/RR` 不变。
