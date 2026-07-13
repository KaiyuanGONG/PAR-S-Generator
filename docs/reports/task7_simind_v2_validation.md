# PAR-S V2 Task 7 SIMIND 语义与完成门禁审计

- 实现状态：**PASS_WITH_RUNTIME_BLOCKER**
- 固定协议名：`SPECT_60MBq_28p4s_v2`
- `/RR` 碰撞审计：550/550 唯一

## 固定语义

| 字段 | 语义/结果 |
|---|---|
| SMC Index 25 | 1704.0 MBq·s = 60 MBq × 28.4 s |
| SMC Index 26 | `ignored_for_voxel_source` |
| Flag 8 | `true`，随机数序列控制 |
| `base_histories` | 源图体素和，与 Index 25、`/NN` 分离 |
| `/NN` | 仅 Monte Carlo 统计倍率 |
| `/RR` | 每例稳定、并行无碰撞的随机种子 |

## 真实既有四件套兼容检查

旧数据中的一套真实 SIMIND 输出通过严格审计：60 views、3932160 bytes、finite、non-negative、MHD 配对与四文件 hash 全部有效。

## 本机运行时边界

- `simind.exe`：已找到并计算 hash
- `simind.ini`/完整 SMC runtime：未找到
- 真实单例 seed pilot：`blocked_missing_complete_official_runtime`

当前本机只存在孤立的 `simind.exe`，缺少可审计的官方 `simind.ini`/SMC_DIR。实现层的真实 subprocess 复现测试已用确定性测试替身通过，但不会伪造物理配置来冒充正式 SIMIND pilot。正式数据生成前必须补齐并冻结官方完整运行时。

## 自动门禁

- [x] `smc_flag8_random_sequence_true`
- [x] `smc_index25_is_1704_mbq_s`
- [x] `index26_explicitly_ignored_for_voxel_source`
- [x] `activity_time_product_is_60_x_28p4`
- [x] `base_histories_separate_from_index25`
- [x] `rr_seed_collision_free`
- [x] `protocol_name_is_frozen_28p4s`
- [x] `legacy_real_quartet_passes_strict_audit`
