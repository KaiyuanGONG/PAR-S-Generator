# PAR-S Generator Windows v1.0.0 验收流程

本文是 `hybrid_v2_limited_activity_v1` 在原生 Windows 上的发布验收清单。所有勾选项都要保留日期、操作者、Git SHA、Windows 版本、Python/Node 版本和证据路径。服务器 550 例、Linux 与 WSL 不属于本版本门槛。

## 1. 前提与权威合同

- 系统：Windows 10/11，64 位；Python 3.11；Node.js 22.19 或更新版本。
- 唯一入口：`python main.py` 或 `start_windows.ps1`。`legacy_pyqt.py` 仅供查看历史界面。
- 唯一生产 profile：`hybrid_v2_limited_activity_v1`。
- 唯一后端：`runtime_backend=windows_native`。界面中不得出现 Linux/WSL/服务器选项。
- 解剖来源：Gate A commit `921e2e723804ed9ce1771d79c6a3cead9885c8fd`。
- Activity 来源：PAR-S_2 Gate C LimitedActivity v1，只读移植；软件运行时不得读取 PAR-S_2。
- 经验证的运行时 SHA-256：
  - `simind.exe`: `f984b8753f54b9f671f9fc1bcb2b45461e7cae8d027376b446dd1ed55a9a8319`
  - `ge870_czt.smc`: `4d10eab246a7a6690663230d2f33aeb3c32f67c598af36b56d1575f0e3551d10`

## 2. 干净克隆、安装、构建与启动

1. 在新的本地盘目录克隆目标发布 SHA，执行 `git status --short`，预期无输出。
2. 将合法授权的 `simind.exe` 放入任意本地目录；不要把二进制提交到仓库。
3. 在仓库根目录运行：

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\setup_windows.ps1
   .\start_windows.ps1
   ```

4. 预期浏览器自动打开 `http://127.0.0.1:<port>`，状态显示 `Service online`。用 `netstat` 确认只绑定 `127.0.0.1`，未绑定 `0.0.0.0` 或局域网地址。
5. 保持实例运行，再次执行 `start_windows.ps1`，预期显示已有实例并退出，不启动第二个服务。
6. 临时占用 8765 后启动，预期自动选择后续空闲 loopback 端口；关闭程序后端口和单实例锁都可再次使用。
7. 运行 `python legacy_pyqt.py` 只验证历史入口仍可显式打开；关闭后继续使用 Web v1。

记录：安装日志、前端 build 输出、实际 URL、进程退出码。

## 3. 原生文件与文件夹选择器

分别在 Simulation/Protocol 对应选择按钮中完成下表。每次取消选择前先记下当前值；取消后该值必须完全不变。选择器授权只持续到本次应用会话，重启后必须重新选择。

| 场景 | 操作 | 预期 |
| --- | --- | --- |
| 正确 EXE | 选择经验证的 `simind.exe` | 接受；显示文件 SHA 与 `validated_windows_v1` |
| 正确 SMC | 选择 `ge870_czt.smc` | 接受；显示文件 SHA 与 `validated_windows_v1` |
| runs 根目录 | 选择本地可写目录 | 接受并写入配置 |
| 实验导出目录 | 选择本地可写目录 | 接受并仅授权该目录 |
| 取消 | 在四类对话框中按 Cancel | 原配置不变，不出现空字符串 |
| 含空格 | 选择如 `D:\PAR S acceptance\runs` | 接受；命令与 manifest 保留正确路径 |
| 中文/重音 | 选择如 `D:\验收 données\runs` | 接受；可生成、读取、打包 |
| 错误扩展名 | EXE 选 `.txt`，SMC 选 `.exe` | 预检拒绝，不创建 run |
| 文件缺失 | 选择后移动/改名，再预检 | 明确报缺失并拒绝 |
| UNC | 输入或选择 `\\server\share\runs` | 预检拒绝 |
| 只读/不可访问 | 选择无写权限目录 | 预检拒绝且不留下探针文件 |
| 保留名称 | 使用 `CON`、`AUX`、`NUL` 等路径段 | 预检拒绝 |
| 尾随点/空格 | 使用带尾随点或空格的路径段 | 预检拒绝 |
| 长路径 | 解析后的绝对路径超过 240 字符 | 预检拒绝并显示长度原因 |
| 哈希不匹配 | 复制并修改一字节的 EXE 或 SMC | 显示 `unverified_runtime`；必须单独二次确认；不得显示 validated |

对哈希不匹配场景，只在隔离目录以 prepare/mock 验证确认流程。除非该未知运行时已经独立评审，不执行真实 SIMIND。

## 4. 参数边界与队列组合

每次修改后运行预检；合法值应能锁定计划，非法值应在创建任务前拒绝。不得静默截断、规范化非法范围或忽略未知字段。

### 4.1 队列与角色

1. `positive_only`：阳性 1 例，阴性 0；manifest 角色为 `positive`。
2. `true_negative_only`：阳性 0，阴性 1；强制病灶数 0，角色为 `true_negative`，默认用途为 `independent_test_control`。
3. `mixed`：阳性 1、阴性 1；总数为 2，病例角色与病例工件一一对应。
4. 三种模式分别输入矛盾数量，例如 positive-only 同时输入阴性，预期拒绝。
5. 本地数量输入 0、负数、小数、NaN/Inf，预期拒绝；输入大于 10 的 execute 队列，预期出现成本估算与独立二次确认。prepare/mock 不触发此确认。

### 4.2 病灶、尺寸、TNR 与区域

| 项目 | 接受测试 | 拒绝测试 |
| --- | --- | --- |
| 病灶数 | min/max 为 1、5；1–5 闭区间 | 0、6、min>max、非整数 |
| 尺寸分箱 | 10、20、40、60 mm 的所属规则与 `[10,20)`、`[20,40)`、`[40,60]` 一致 | 小于 10、大于 60 |
| 权重 | 0.45/0.40/0.15；1/1/1；允许某项为 0 且总和>0 | 负数、全零、NaN、Inf、不是三项 |
| 权重记录 | manifest 同时保存原值和归一化值 | 用户宣称的归一化值与原值不一致时拒绝 |
| TNR | 2、8、2–8、等值范围 | <2、>8、min>max、NaN、Inf |
| TNR 数值 | 每一病灶实测局部 TNR 相对目标误差 ≤2% | 超过 2% 时 QC 失败 |
| 区域 | auto、whole、right、left 的可行样本 | 锁定区域无法容纳时明确失败，不回退到其他区域 |

### 4.3 Seed、NN、并行与锁定参数

- Seed 接受 0 与 `9007199254740991`；拒绝负数、超过上限、小数。检查 anatomy、lesion、activity、split、SIMIND RR 等派生 seed 域隔离并写入 manifest。
- NN 接受 1、10、1,000,000；拒绝 0、1,000,001、小数。NN=1 显示“快速测试”，NN=10 显示“推荐质量”，NN>10 显示耗时警告。
- 并行接受 1、32；拒绝 0、33、小数。正式真实验收固定 worker=1。
- 尝试通过 API/保存文件修改 128³、4.42 mm、80,000 counts、residual_bg=0.05、gradient_gain=0.08、物理 μ-map、固定形态策略、采集/FOV 合约，均应被拒绝。
- 在 JSON 中增加未知字段、旧 profile、旧 schema 或 Linux/WSL backend，预期 HTTP 422 或 CLI 明确失败。
- 把旧草稿键 `pars.workspace.v3` 单独写入浏览器存储，重启后不得自动变成可运行 Windows v1 草稿。

## 5. Prepare 完整流程

1. 新建 `positive_only` 1 例，NN=10、worker=1、mode=prepare。
2. 生成 Phantom 预览，检查 axial/coronal/sagittal、3D/MIP、liver/tumor overlay、μ-map 与测量表。
3. 运行 preflight，锁定后创建任务。
4. 确认未启动 `simind.exe` 子进程。
5. 打开 plan/job JSON，逐项确认 `/25:1704`、`/100:160`、`/101:208`、`/IN:x21,100x`、`/RR`、`/NN:10` 以及安全相对输出名。
6. 确认 ACT/ATN 都为 C-order ZYX、小端 `<f4`，各 8,388,608 字节；ATN 数值等于 `mu_map × 0.442`；写后读回哈希与 manifest 一致。
7. Prepare 状态应为 `prepared`/`skipped`，不能 Finalize 为完整数据集。

## 6. Mock 状态机、暂停/恢复与打包

1. 新建 `mixed` 1 阳性 + 1 真阴性、mode=mock、NN=1。
2. 开始任务，在可用阶段执行 Pause；状态显示 Paused 且已有检查点保留。
3. Resume 后跑完 generation、phantom QC、export、mock expectation、projection QC、observation、package/finalize。
4. 检查真阴性 mask 为空、病灶数为 0、病例用途正确；阳性病例病灶数处于设置区间。
5. 检查 `run.json`、`cases.jsonl`、`splits.json`、`dataset_manifest.json`，并确认 mock 投影明确标记 `deterministic_mock_not_simind`，不得标记为 SIMIND 科学结果。
6. 重启软件，从列表打开该 run；只读查看与证据仍完整。

## 7. 真实原生 Windows SIMIND

发布验收必须使用上面两个精确哈希，固定 NN=10、worker=1。在同一 `mixed` run 中生成 1 个阳性和 1 个真阴性病例，执行前保存有效配置、所有输入哈希和完整命令。

1. 通过基础 execute 确认；因为只有 2 例，不应出现 >10 例确认；验证过的哈希不应出现 unverified 确认。
2. 确认实际启动的是选择的本地 `simind.exe`，且串行执行。
3. 执行结束后确认 runtime 前后哈希一致；若漂移，任务必须失败。
4. 对两例检查 `.res`、`.a00`/投影、shape `(60,128,128)`、统一显示变换 `raw[:, ::-1, :]`、projection QC 与完整命令 token。
5. 检查阳性与真阴性的病例角色、ACT/ATN、μ-map、mask、QC 和 manifest；Finalize 后验证 package SHA-256。
6. 将整个 run 复制到只读验收证据位置，并记录 Git SHA、运行时 SHA、配置 SHA 和 wall time。

## 8. 恢复漂移与不覆盖测试

对 prepare、mock 和真实 run 的副本分别执行：

1. 原样 Resume：已验证阶段不重算，最终哈希不变。
2. 修改配置的任一公开参数：配置指纹漂移，Resume 立即停止。
3. 修改/截断 ACT、ATN、projection、QC JSON 任一文件：输入或阶段哈希漂移，Resume 停止。
4. 替换 EXE 或 SMC：runtime 哈希漂移，Resume 停止。
5. 把已有输出名作为新 run 目标：不得静默覆盖；必须选择新 run ID 或显式 Resume。

记录错误消息和修改前后的哈希。测试后仅删除验收副本，不改原证据。

## 9. 自动验证入口

完整本地验证：

```powershell
.\scripts\verify_windows_v1.ps1
```

脚本依次校验 runtime 哈希、Python 全量测试、前端 lint/unit/build/E2E/a11y/visual、loopback 启动、prepare 和 mock；最后以 `Read-Host` 要求明确输入 `RUN SIMIND` 才执行两例真实 NN=10 验收。CI 或只做自动部分时使用：

```powershell
.\scripts\verify_windows_v1.ps1 -SkipRealSimind
```

任何一步非零退出即为失败。不得用“手工看起来正常”覆盖失败结果。

## 10. 重构等价性与发布签字

功能冻结后、代码简化前保存相同阳性/阴性输入、seed、配置、命令、runtime SHA 与全部输出 SHA。重构后重跑同一案例：

- anatomy、mask、activity、μ-map、ACT、ATN 逐字节一致；
- 同 RR/同运行时下 `.res` 与投影首先要求逐字节一致；不一致必须调查；
- QC、manifest、命令和验证状态一致。

最后附上全量测试日志、两例真实证据、等价性对照、合并 SHA 和拟发布 `v1.0.0` SHA。只有全部通过才允许合并和打标签。
