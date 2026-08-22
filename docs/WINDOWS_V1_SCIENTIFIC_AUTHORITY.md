# Windows v1 科学权威与活跃管线

## 唯一新生产路径

PAR-S Generator v1.0.0 只有一个可创建或恢复新生产任务的 profile：

```text
Web / FastAPI / CLI
  → Hybrid V2 patient / torso / liver anatomy
  → corrected-master lesion geometry
  → LimitedActivity v1
  → physical μ-map
  → ACT / ATN Type-7 export
  → phantom QC
  → native Windows SIMIND
  → projection QC
  → package / finalize
```

公开标识固定为：

- `schema_version=windows_v1`
- `generation_profile=hybrid_v2_limited_activity_v1`
- `runtime_backend=windows_native`

`runtime_backend` 是 provenance 预留字段，不是 v1 的可选后端。Linux、WSL 与服务器批量执行不进入 v1 UI、状态机或发布门槛。

## 来源与边界

| 组成 | 权威来源 | v1 处理 |
| --- | --- | --- |
| V2 解剖 | Gate A commit `921e2e723804ed9ce1771d79c6a3cead9885c8fd` | 保留提交历史合并 |
| 病灶几何 | corrected master | 与 Hybrid V2 解剖组合 |
| Activity | PAR-S_2 Gate C LimitedActivity v1 | 最小只读移植；记录源码与配置 SHA；无运行时依赖 |
| μ-map | 当前物理材料映射 | 固定物理 μ-map |
| Windows SIMIND | 指定 EXE/SMC SHA | 前后重算；未知哈希单独确认并降级 provenance |

LimitedActivity 的上游源码 SHA-256 为 `43e0b4de9231710d2956c1446c7afb373b2e4c0b49d57322c4b5d54765c3bfdb`，Gate C 配置 SHA-256 为 `04b40614ac8274cf7d474dc73eb360ea341ad65fa1c35634f3b8b18d7aa32fd7`。`D:\PFE-U\PAR-S_2` 只作为审计来源，软件不得在运行时读取、修改或写入该仓库。

## 历史实现

- legacy/master 简化体型与肝脏：历史参考，不得创建新任务。
- Task12/Task13 full V2：V2 解剖与未充分验证的旧 V2 tumor/activity、旧 NN=1、旧 SMC；仅历史证据。
- 旧 PyQt：显式 `legacy_pyqt.py` 入口，只用于兼容查看。
- Gate B Linux 与服务器 Formal 550：保留证据和恢复标签，不合并为 Windows v1 的运行模式。

旧配置、旧浏览器草稿和旧运行可以只读查看，不能静默迁移或续跑为 Windows v1。任何恢复都必须重新验证配置指纹、输入/中间文件哈希和 runtime 哈希。

## 固定二进制与坐标合同

- volume shape：ZYX `(128,128,128)`；voxel size 4.42 mm。
- ACT/ATN：C-order ZYX、小端 `<f4`；单文件 8,388,608 字节。
- ACT：LimitedActivity v1 activity。
- ATN：`mu_map × 0.442`。
- projection：读取后统一应用 `raw[:, ::-1, :]`。
- 固定 SIMIND token：`/25:1704`、`/100:160`、`/101:208`、`/IN:x21,100x`、`/RR` 与 `/NN`。

经验证的 Windows runtime：

- `simind.exe` SHA-256 `f984b8753f54b9f671f9fc1bcb2b45461e7cae8d027376b446dd1ed55a9a8319`
- `ge870_czt.smc` SHA-256 `4d10eab246a7a6690663230d2f33aeb3c32f67c598af36b56d1575f0e3551d10`

哈希不匹配可以在独立二次确认后执行，但 manifest 必须为 `unverified_runtime`，不得宣称 `validated_windows_v1`。
