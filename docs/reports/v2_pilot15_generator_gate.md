# PAR-S V2 15 例冻结数据 Generator 门禁报告

- Generator gate：**PASS**
- Dataset：`PAR-S-TARE-HCC-NoPVI-SYN-v2-pilot15` / `2.0.0-pilot15`
- 冻结病例数：15
- Manifest SHA-256：`cdaa87ded094bed74927ab91bd7bdcf3067eda17d3fed3625d034771aeab3678`
- `/NN=1` 仅用于 deterministic smoke；不能据此声明临床计数标定完成。
- 200 mm 与 215 mm 为预期结构性拒绝边界，不伪装成可完整 containment 的主人群病例。

## 病例结果

| Case | Split | 肝形态 | 目标 Dmax (mm) | 实际 RECIST (mm) | 叶范围 | 注射区 | Mismatch | /RR | 投影权重和 |
|---|---|---|---:|---:|---|---|---|---:|---:|
| case_00000 | train | normal | 20.0 | 18.75 | unilobar | whole_liver | False | 7765 | 750112 |
| case_00001 | val | cirrhotic | 55.0 | 54.87 | unilobar | right_lobar | False | 5706 | 961086 |
| case_00002 | train | cirrhotic | 90.0, 24.0 | 89.80, 25.70 | bilobar | left_lobar | True | 3647 | 776086 |
| case_00003 | train | normal | 12.0 | 13.26 | unilobar | whole_liver | False | 1588 | 835700 |
| case_00004 | test | cirrhotic | 18.0 | 18.75 | unilobar | left_lobar | False | 9536 | 410137 |
| case_00005 | val | cirrhotic | 32.0 | 31.25 | unilobar | right_lobar | False | 7477 | 870115 |
| case_00006 | test | normal | 45.0, 28.0, 14.0 | 45.46, 28.87, 13.26 | bilobar | right_lobar | True | 5418 | 560913 |
| case_00007 | train | cirrhotic | 68.0 | 67.51 | unilobar | left_lobar | False | 3359 | 558611 |
| case_00008 | train | cirrhotic | 75.0, 38.0, 22.0, 16.0 | 75.73, 38.00, 22.97, 13.26 | bilobar | whole_liver | False | 1300 | 733192 |
| case_00009 | train | normal | 100.0 | 100.10 | unilobar | right_lobar | False | 9248 | 772236 |
| case_00010 | train | cirrhotic | 110.0 | 110.02 | unilobar | sector_proxy | True | 7189 | 514844 |
| case_00011 | test | cirrhotic | 70.0 | 69.82 | unilobar | sector_proxy | True | 5130 | 594990 |
| case_00012 | val | normal | 42.0 | 41.81 | unilobar | sector_proxy | True | 3071 | 1.33884e+06 |
| case_00013 | train | normal | 58.0, 26.0 | 57.71, 25.70 | unilobar | left_lobar | False | 1012 | 1.02566e+06 |
| case_00014 | train | cirrhotic | 62.0 | 61.50 | unilobar | whole_liver | False | 8960 | 603160 |

## 当前结论

Generator 端冻结字节、SIMIND 溯源、保留输入、payload、RECIST、完整 containment 和覆盖检查均已通过。
15-case visual/manual review and runtime-environment binding remediation are required
