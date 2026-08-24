# PAR-S 论文证据总账：PAR-S Generator 软件与生成方案

> **Purpose:** manuscript evidence and writing ledger
>
> **Supersedes:** none
>
> **Numerical authority:** evaluator-generated result artifacts
>
> **Artifact identity authority:** manifests and SHA-256
>
> **Protocol authority:** frozen experiment contracts
>
> **This file:** synthesis, interpretation, status and manuscript mapping

| 字段 | 当前值 |
|---|---|
| 文档状态 | `ACTIVE_DRAFT`；可用于写作，但不是冻结结果文件 |
| 创建日期 | 2026-08-23（Europe/Paris） |
| 最近核对日期 | 2026-08-23 |
| 项目范围 | `PAR-S Generator`：合成体模、activity、μ-map、ACT/ATN、原生 Windows SIMIND、生成侧 QC、manifest 与打包 |
| 本地核对快照 | 分支 `codex/windows-hybrid-v2-v1`，commit `7ca676d2e54c6c9a738308d906c7c75b7b69a7e1` |
| 远端合并状态 | PR #1 已于 2026-08-23 合并；远端 `master` merge commit `dd62ba3f8d2c819bfca3c76090ef7dfe2a69c023` |
| 内容等价性 | PR head 与 merge commit 的 Git tree 均为 `bbb4a631e70646cb9d3a3c377d88967a9fe803f4` |
| 发布状态 | `NOT TAGGED / NOT RELEASED`；截至核对时无 `v1.0.0` tag、无 GitHub Release |
| 人工验收 | `PENDING`；用户决定稍后执行原生 Windows 可见桌面验收 |
| 外部算法项目 | `D:\PFE-U\PAR-S_2`，只读引用；本文件不替代其算法与 Formal550 结果总账 |

---

## 0. 文档契约：怎样使用这份总账

### 0.1 唯一人类可读入口，但不是新的数值权威

本文件是整篇论文在 **PAR-S Generator 这一侧**的唯一人类可读入口。它回答五类问题：

1. 做了什么、为什么这样做；
2. 当前实际运行的代码与协议是什么；
3. 哪些结论已经有原始证据，哪些仍在进行或尚未验证；
4. 每项证据在哪里、由什么 SHA-256 标识、能支持论文中的哪句话；
5. 哪些内容属于另一项目、历史路线或未来工作，不能混写成当前结果。

“事无巨细”在这里的含义是：把每项设计的意义、状态、出处、限制和论文用途写清楚。它**不**意味着把数百个病例的逐例数值、完整日志、二进制内容或 evaluator 输出复制进 Markdown。任何定量结果在写入论文前，仍须回到对应的 machine-readable artifact、manifest、结果表和 SHA-256。

### 0.2 三套互不替代的状态词

为避免把“计划”“代码存在”“测试通过”和“论文结论成立”混为一谈，本文件同时使用三套正交标签。

**内容性质（Kind）**

- `FACT`：可由代码、Git、manifest 或 artifact 直接核验的事实。
- `DECISION`：作者或项目明确做出的设计选择。
- `PLAN`：尚未完成的行动或拟开展分析。
- `HYPOTHESIS`：待由实验检验的假设。
- `RESULT`：由 evaluator 或已封存结果 artifact 产生的结果。
- `INTERPRETATION`：对事实或结果的解释，不等同于原始结果。
- `LIMITATION`：证据范围、实现边界或外推限制。

**生命周期（Lifecycle）**

- `DRAFT`、`FROZEN`、`PLANNED`、`PENDING`、`IN_PROGRESS`
- `PARTIAL`、`COMPLETE_UNVERIFIED`、`VERIFIED`
- `FAILED`、`BLOCKED`、`SUPERSEDED`、`EXCLUDED`

**证据等级（Evidence）**

- `USER_APPROVED`：用户明确批准的决策。
- `USER_REPORTED`：用户报告但尚未由 artifact 独立核验。
- `SESSION_OBSERVED`：本次审计在本机观察到。
- `REPO_VERIFIED`：由版本库代码、Git 对象或提交内容核验。
- `ARTIFACT_VERIFIED`：由具名结果文件、manifest 与 SHA-256 核验。
- `EXTERNAL_SOURCE_VERIFIED`：由外部项目的只读 artifact 核验。
- `UNVERIFIED`：尚无足够证据。

一个条目可以同时写成：`RESULT / VERIFIED / ARTIFACT_VERIFIED`。单独写“完成”不够，因为它没有说明完成的是实现、测试、数据生成、人工验收还是发布。

只有明确标为 `Kind / Lifecycle / Evidence` 的三段式字段使用上述受控词表。仪表板和研究问题表中的 `PASS`、`SEALED`、`SUPPORTED`、`CI PASS`、`AT COMMIT`、`COMMIT-IDENTIFIED`、`SHA-IDENTIFIED` 等是便于阅读的**结果判定、范围或身份限定词**，不是额外的 Lifecycle/Evidence 枚举；正式 evidence record 仍必须拆回三段式字段，并把这些信息放入 result、scope 或 identity 列。

### 0.3 权威优先级

发生冲突时按下列顺序处理，并在本文件登记冲突，而不是选一句看起来更顺眼的话：

1. evaluator 生成的结果、完成标记、sealed manifest、artifact inventory 和文件 SHA-256；
2. 冻结的实验合同、有效配置、release/tag 对应的源码；
3. 指定 commit 的代码和自动测试；
4. append-only 决策日志及人工签字；
5. 本总账的综合和解释；
6. 历史说明、旧 notebook、旧 README 或文件名中的暗示。

`protocol_status=gate_abc_complete_windows_v1` 是当前代码强制写入的协议标识，但**不能单凭这个字符串证明数值 Gate 已通过**。Gate 结果必须回指第 16 节的原始 artifact。

### 0.4 论文数字的准入规则

任何准备进入摘要、正文、图表或补充材料的数字，至少必须同时具备：

- 唯一 Evidence ID；
- 明确的研究对象、样本数、单位和统计口径；
- 可定位的原始 artifact；
- artifact SHA-256 或由 sealed manifest 间接绑定的 SHA-256；
- 生成该结果的代码/config 身份；
- 对应的限制和允许表述强度；
- 与图、表或正文小节的映射。

若缺一项，只能保留为 `DRAFT`、`USER_REPORTED` 或 `UNVERIFIED`，不得通过重复抄写变成“已证实”。

### 0.5 路径写法与可迁移性

- 当前仓库内的文件优先使用仓库相对路径，例如 `src/pipeline/runner.py`。
- 仅存在于本机的原始 run 使用“逻辑名称 + 当前操作者绝对路径 + SHA-256”。绝对路径是定位提示，不是 artifact identity。
- `PAR-S_2` 路径只作只读来源定位；本项目不得修改该目录，也不得建立运行时依赖。
- 当 artifact 被迁移到归档介质时，允许更新 locator，但不得更改原 SHA-256、Evidence ID 和原始状态。

### 0.6 更新纪律

本文件采用“主体可校正、证据事件追加”的方式维护：

- 事实写错可直接修正，但必须在第 24 节追加更正记录；
- 新实验先登记计划和准入条件，再登记结果；
- `FAILED`、`SUPERSEDED` 和偏离记录不得删除；
- 不以最新文件覆盖旧 SHA；新输出使用新 Evidence ID 或新版本；
- 算法项目的结果只记录交接身份和论文映射，不在此处复制其结果总账；
- 每次准备论文冻结稿时，重新核对 Git commit、config SHA、result SHA 和人工验收状态。

---

## 1. 整篇论文的跨项目结构

### 1.1 总体研究结构

整篇论文由两个相互衔接、但证据职责不同的项目构成：

| 论文层 | 科学问题 | 主要责任项目 | 本总账的责任 |
|---|---|---|---|
| **S/G：Software and Generation** | 怎样以可复现、可审计的方式生成解剖、病灶、activity、μ-map 和 SPECT 投影？ | **PAR-S Generator（当前项目）** | 完整记录方法、软件接口、配置、QC、Windows 执行和生成侧证据 |
| **D：Dataset Contract** | 怎样定义、封存、交接并接受训练/评估数据，避免身份漂移和泄漏？ | 两项目共有边界 | 本项目负责生成合同、病例身份、manifest、SHA 和生成侧 QC；`PAR-S_2` 负责返回验收、训练消费格式、split/leakage 和下游适用性 |
| **M：Model / Algorithm** | 算法如何重建、训练、比较基线并在封存评估上表现？ | **PAR-S_2** | 仅登记边界、输入身份和待引用的外部 Evidence ID；不在此宣称算法效果 |

因此，这篇论文不是把两个仓库机械拼接，而是一条明确的证据链：

```text
PAR-S_2 Gate C LimitedActivity source/config
        │  一次性、只读、SHA 标识的最小移植；无运行时依赖
        ▼
PAR-S Generator（论文逻辑模块；不是逐函数调用顺序）
anatomy → lesions → activity → μ-map/interface → ACT/ATN → SIMIND → generation QC
        │  sealed package + manifest + case identity + SHA-256
        ▼
PAR-S_2
return acceptance → reconstruction/training → sealed evaluation → algorithm results
```

这两条箭头都是**单向 provenance 边**：

1. `PAR-S_2 → Generator`：只传递 LimitedActivity v1 科学实现及冻结配置的身份；
2. `Generator → PAR-S_2`：只传递生成数据包、manifest、病例角色、配置和 artifact 身份。

它们不构成运行时循环依赖。Windows 软件线与 Linux 大批量 Formal550 线也不是同一个 runtime evidence channel；除非逐 artifact 的 manifest/SHA 明确证明，否则不得声称 Windows 和 Linux 输出逐字节等价。

### 1.2 当前项目在论文中的候选贡献

以下是可用于组织论文的**候选贡献**，不是未经限定的最终结论：

1. 一个在受支持的新建入口中只公开单一新生产 profile 的、原生 Windows、本地运行的可审计生成工作台；直接 config-path start API 的强制缺口见 `IMP-GAP-01`；
2. 将高级 Hybrid V2 patient/liver/torso 解剖、修正版 master 病灶和 LimitedActivity v1 组合为显式合同；
3. 从数组坐标、物理 μ-map、ACT/ATN 字节布局到 SIMIND 命令和投影方向的端到端可追溯链；
4. 对阳性与合成真阴性病例采用确定性的域隔离 seed、病例角色和 split 语义；
5. 通过 Gate A、LimitedActivity Delta、历史物理控制、原生 Windows 真实执行和 Formal550 封存 artifact 建立分层证据，而不是用单一“跑通”概括所有层级；
6. 通过 manifest、SHA-256、resume 漂移检查和行为保持重构证据，使软件工程状态可与论文主张逐条对应。

### 1.3 本项目明确不承担的内容

- 重建网络、训练流程、基线、消融、封存测试集结果和统计比较；
- 临床诊断性能、患者获益或临床部署验证；
- 把合成真阴性解释为健康人群；
- Linux/WSL 作为 Windows v1 软件模式或用户开关；
- 把生成体模称为个体患者的 digital twin；
- 把五个区域 proxy 称为真实 Couinaud 分段；
- 用历史 observation 管线代表当前 LimitedActivity 路线。

---

## 2. 当前状态仪表板

| 事项 | 状态 | 证据 | 论文影响 |
|---|---|---|---|
| 唯一科学/生产权威 profile | `FROZEN POLICY / ENFORCEMENT PARTIAL` | `windows_v1` + `hybrid_v2_limited_activity_v1` + `windows_native`；见 `IMP-GAP-01` | 方法只描述这一条路径；发布前补齐 API guard |
| Hybrid V2 Gate A 实现 | `COMPLETE / COMMIT-IDENTIFIED` | commit `921e2e7…`，archive tag 已存在 | 可描述实现已冻结 |
| Gate A 100 例 | `VERIFIED / ARTIFACT_VERIFIED` | 100/100 hard-QC；manifest SHA 见 `EV-GA-100` | 可报告限定范围内的解剖/放置结果 |
| LimitedActivity source/config | `FROZEN / SHA-IDENTIFIED` | source `43e0…`，config `04b4…` | 可精确写方法与来源 |
| Gate A Delta | `VERIFIED / EXTERNAL_SOURCE_VERIFIED` | 100 例不可变数组与 activity 合同 PASS | 支持 activity 替换未改变指定解剖数组 |
| Gate B Delta | `VERIFIED, SCOPED` | 13/13 jobs，NN=10、真阴性、FOV、replay PASS | 不支持三种 territory 均有真实 SIMIND 分层验证 |
| Gate C 源数据 | `SEALED PASS / EXTERNAL_SOURCE_VERIFIED` | 500 positive + 50 true-negative | 支持 Formal550 生成源数据已封存，不自动支持算法结果完成 |
| Windows 自动门槛 | `CI PASS` | merge commit 的 `windows-native` job 成功 | 支持自动化软件门槛通过 |
| Windows 真实 SIMIND | `VERIFIED AT 6f684ce` | 1 positive + 1 true-negative，NN=10，worker=1 | 支持少量本地原生 Windows 全流程可执行 |
| 重构等价性 | `42/42 PASS WITH RES VOLATILITY` | 数值 artifact 相同；`.res` 仅稳定行相同 | 不得写成 `.res` 全文件逐字节相同 |
| PR #1 | `MERGED` | merge commit `dd62ba3…` | 可写“已合并” |
| 人工 Windows 验收 | `PENDING` | 用户决定稍后执行 | 不得写“完整人工发布验收已完成” |
| `v1.0.0` 发布 | `NOT TAGGED / NOT RELEASED` | 远端无 tag、无 Release | 不得写“v1.0.0 已发布” |
| 算法结果 | `EXTERNAL / SEPARATE LEDGER` | `PAR-S_2` | 等外部总账提供最终 claim 和结果 ID |

当前最准确的一句话是：

> Windows v1 候选实现已经合并并通过合并后自动 CI；一正一负真实 SIMIND 证据在合并前的指定 commit 上通过；可见原生 Windows 人工验收以及正式 `v1.0.0` tag/Release 仍待完成。

---

## 3. 研究问题与贡献逻辑

### 3.1 本项目研究问题

| ID | 研究问题 | 需要的证据 | 当前状态 |
|---|---|---|---|
| `RQ-SG-1` | 如何把多来源的解剖、病灶和 activity 方法约束成唯一、不可静默降级的新生产协议？ | profile/schema、严格验证、历史边界、manifest | `SUPPORTED AS POLICY/NEW-CREATION; PARTIAL UNIVERSAL ENFORCEMENT`；见 `IMP-GAP-01` |
| `RQ-SG-2` | 如何保证 anatomy、activity、μ-map、ACT/ATN 与 SIMIND 坐标/单位/字节合同一致？ | 代码合同、round-trip、物理控制、projection QC | `SUPPORTED, SCOPED` |
| `RQ-SG-3` | 如何生成有明确角色、可重放且不会与阳性混淆的合成真阴性病例？ | role/split/seed、零病灶、activity 和真实 run | `SUPPORTED` |
| `RQ-SG-4` | 原生 Windows 是否能承担少量本地的完整生成与 SIMIND 流程？ | 自动测试、真实一正一负、人工 GUI 验收 | `PARTIAL`；GUI 人工签字待完成 |
| `RQ-SG-5` | 行为保持重构能否不改变科学 artifact？ | 冻结输入、前后 run、逐 artifact 对比 | `SUPPORTED WITH .res CAVEAT` |
| `RQ-D-1` | 生成侧怎样向算法侧提供不可歧义的 Formal550 数据身份？ | sealed manifests、case plan、inventory、SHA | `SUPPORTED AT HANDOFF`；下游消费由外部总账负责 |

### 3.2 论证顺序

正文建议按“合同 → 生成 → 物理接口 → 证据 → 数据交接”展开：

1. 先说明为什么旧路线不能继续混入生产；
2. 再冻结唯一 profile、参数空间和随机性合同；
3. 描述 anatomy、lesion、activity 与 μ-map 的科学构造；
4. 描述 ACT/ATN 和 SIMIND 的坐标、字节及命令合同；
5. 用分层 QC 与 artifact 身份说明如何验证，而不是把软件测试当数值验证；
6. 最后说明 Formal550 如何封存并交给算法项目。

这样可避免论文叙事误变成“先写软件界面，再补科学方法”，也避免把算法性能倒推为生成方法已经验证。

---

## 4. 论文主张登记表

下表中的“建议表述”是当前证据允许的最大强度。最终稿若缩短语句，不得删除限定条件。

| Claim ID | 建议表述 | 状态 / 证据 | 适合位置 |
|---|---|---|---|
| `CL-SG-ARCH-01` | 科学协议与受支持的新建入口只认可 `windows_v1 / hybrid_v2_limited_activity_v1 / windows_native`；在 `IMP-GAP-01` 修复并测试前，不能声称所有直接 API 执行路径都技术性禁止 legacy config。 | `DECISION+FACT / PARTIAL / REPO_VERIFIED` | Methods—software architecture；release 前需升级 |
| `CL-SG-ANAT-01` | 当前解剖由 habitus-conditioned Hybrid V2 patient/liver/torso 生成器构造，并通过连通性、形状、体积与空间硬 QC。 | `FACT / VERIFIED`；`EV-GA-100` | Methods—anatomy |
| `CL-SG-REGION-01` | 五个肝内区域是无血管树的放置 proxy，而非解剖学 Couinaud 分割。 | `FACT / REPO_VERIFIED` | Methods + Limitations |
| `CL-SG-LESION-01` | 阳性病例生成 1–5 个、有效直径 10–60 mm 的非重叠肝内病灶；不可行配置明确失败，不静默缩小到其他尺寸带。 | `FACT / VERIFIED`；代码测试 + Gate A artifact | Methods—lesions |
| `CL-SG-ACT-01` | LimitedActivity v1 在可行 whole/right/left territory 中生成带轴向梯度的残余背景，并把逐病灶局部 ring TNR 约束在用户目标的 2% 内。 | `FACT / VERIFIED, CONTRACT-SCOPED`；Delta A/B | Methods—activity |
| `CL-SG-NEG-01` | 真阴性是来自同一合成解剖总体、强制零病灶的独立测试 control；它不是“健康临床阴性”。 | `FACT / VERIFIED` | Methods—cohort semantics |
| `CL-SG-MU-01` | SIMIND 使用组织标签构造的 140-keV 附近物理 μ-map；退化 CT-like μ 输入只保留 provenance，不进入当前 SIMIND。 | `FACT / REPO_VERIFIED` | Methods—attenuation |
| `CL-SG-IO-01` | ACT/ATN 采用 little-endian float32、C-order ZYX；ATN 为 `mu_map × 0.442`，并执行写后读回、长度和 SHA 检查。 | `FACT / VERIFIED`；代码、测试、Windows artifact | Methods—simulation interface |
| `CL-SG-PHYS-01` | 历史物理控制支持当前采用的投影翻转、Type-7 衰减解释、FOV 和 RR/NN 选择，但这些控制不等同于当前 LimitedActivity 全队列的临床验证。 | `RESULT / VERIFIED, HISTORICALLY SCOPED` | Validation—physics controls |
| `CL-SG-WIN-01` | 在指定合并前 commit 和验证过的 EXE/SMC 上，一例阳性和一例真阴性以 NN=10、worker=1 完成了原生 Windows 全流程。 | `RESULT / VERIFIED AT COMMIT 6f684ce` | Validation—Windows execution |
| `CL-SG-REF-01` | 行为保持重构的 42 项比较全部通过；NPZ、ACT、ATN 和投影数据逐字节一致，`.res` 去除预声明 volatile 行后一致。 | `RESULT / VERIFIED`；`EV-WIN-EQUIV` | Software validation |
| `CL-SG-CI-01` | PR #1 的合并树通过 Windows 自动 CI，包括 Python、Ruff、前端、浏览器、prepare 和 mock 门槛。 | `FACT / VERIFIED / REMOTE CI` | Software validation / supplement |
| `CL-D-550-01` | 外部 Gate C artifact 标识 550 例生成源数据已 sealed，其中 500 例阳性、50 例合成真阴性。 | `RESULT / EXTERNAL_SOURCE_VERIFIED` | Dataset / handoff |
| `CL-SG-REL-01` | 软件已经合并为 Windows v1 候选，但截至 2026-08-23 尚未完成可见 GUI 人工验收，也未创建 `v1.0.0` release。 | `FACT / VERIFIED` | Availability / limitations |

### 4.1 禁止或必须改写的表述

| 禁止表述 | 原因 | 安全表述 |
|---|---|---|
| “patient digital twin” | 体模不是个体患者的影像配准复制 | “population-informed synthetic anatomical phantom” |
| “representative of the clinical population” | 部分分布含工程先验，尚无完整外部代表性验证 | “sampled from the specified synthetic population contract” |
| “true Couinaud segmentation” | 当前为无血管树 proxy | “five Couinaud-inspired placement proxies” 或中文等价表述 |
| “healthy negative controls” | 真阴性可仍为 cirrhotic synthetic anatomy | “lesion-free synthetic true-negative controls” |
| “clinically validated / diagnostically equivalent” | 无临床诊断性能验证 | “passed the specified engineering and physics QC” |
| “absolute camera sensitivity was validated” | point/line 控制只支持 scoped engineering check | 报告具体 control 数值和范围，不外推绝对临床 cps/MBq |
| “Windows and Linux are equivalent” | 两条 runtime evidence lane 未做完整逐 artifact 等价验证 | 分别报告 Windows 本地证据与 Linux Formal550 证据 |
| “all three territories were validated by real SIMIND” | Delta B 的 10 个不同阳性均抽到 `whole_liver` | “territory contracts were tested; real Delta B SIMIND was unstratified and sampled whole-liver cases” |
| “`.res` files were byte-identical after refactoring” | 时间和性能行发生预期变化 | “stable scientific `.res` content matched after excluding declared volatile lines” |
| “v1.0.0 was released” | 无 tag/Release，人工验收待完成 | “merged Windows v1 integration candidate” |
| “algorithm performance improved” | 属于 `PAR-S_2` 的 evaluator 权威 | 引用算法总账中的冻结 Claim/Evidence ID |

---

## 5. 方法谱系与唯一科学权威

### 5.1 三套体模方法的生产地位

| 路线 | 解剖 | 病灶/activity | 当前地位 |
|---|---|---|---|
| legacy master | 简化体型与肝脏几何 | master 历史方案 | `EXCLUDED FROM NEW PRODUCTION`；仅历史参考 |
| Task12/Task13 full V2 | V2 anatomy | 更复杂但未充分验证的 V2 tumor/activity、旧 NN=1、旧 SMC | `EXCLUDED FROM NEW PRODUCTION`；保留历史证据 |
| hybrid V2 master + LimitedActivity v1 | 高级 V2 patient/liver/torso | corrected-master lesions + Gate C LimitedActivity v1 | **唯一活跃新生产权威** |

按冻结协议，旧草稿与旧 run 应只读查看，不得静默迁移、续跑或用于创建新生产任务。该政策已由 Windows v1 新建接口与 CLI 强制，但直接读取任意 config path 的 `/api/run/start` 尚缺同等 schema/profile guard，见 `IMP-GAP-01`；因此“所有路径均已禁止”当前仍是未满足的发布主张。Linux Gate B 归档分支冻结了当时的选择、Linux runtime 与打包合同；该分支自身的 preflight 记录 `real_simind_invocations=0`，不能被文件名误读为“真实 Gate B 已完成”。后续真正授权 Gate C 的证据来自 `PAR-S_2` 的 Gate A Delta / Gate B Delta artifact。

### 5.2 当前不可歧义的协议身份

| 字段 | 值 | 来源 |
|---|---|---|
| `schema_version` | `windows_v1` | `src/core/windows_v1.py` |
| `generation_profile` | `hybrid_v2_limited_activity_v1` | `src/core/windows_v1.py` |
| `runtime_backend` | `windows_native` | `src/core/windows_v1.py` |
| `protocol_status` | `gate_abc_complete_windows_v1` | `src/core/windows_v1.py` |
| Hybrid V2 Gate A commit | `921e2e723804ed9ce1771d79c6a3cead9885c8fd` | code provenance |
| LimitedActivity upstream source SHA-256 | `43e0b4de9231710d2956c1446c7afb373b2e4c0b49d57322c4b5d54765c3bfdb` | read-only `PAR-S_2` source |
| Gate C config SHA-256 | `04b40614ac8274cf7d474dc73eb360ea341ad65fa1c35634f3b8b18d7aa32fd7` | read-only `PAR-S_2` config |
| Windows 验证 EXE SHA-256 | `f984b8753f54b9f671f9fc1bcb2b45461e7cae8d027376b446dd1ed55a9a8319` | frozen runtime contract |
| Windows 验证 SMC SHA-256 | `4d10eab246a7a6690663230d2f33aeb3c32f67c598af36b56d1575f0e3551d10` | frozen runtime contract |

### 5.3 当前仓库内关键配置身份

以下 SHA 是本次核对快照的文件身份，写论文冻结稿时须重新核对：

| 文件 | SHA-256 |
|---|---|
| `configs/population_tare_hcc_nopvi_v2.json` | `3a0e9af3cafdd99e3ee8b6fe8752c0d307194bc5ea75e94c5decdf2807ad6248` |
| `configs/evidence_registry_v2.json` | `504b5b5db004f738244782e538de637fc24ca6c7fc6883068e08d69b589c2002` |
| `configs/gate_a_v2_master_100.json` | `cabad26b0e567acfe547e28f69abca54a6c1ef5a6e0211357904b72f67465f84` |
| `src/core/windows_v1.py` | `05b2bf15cdab6946bc67e1c91288a0ddd001da7f0c0e7b592a70ee062775362e` |
| `src/core/anatomy_v2.py` | `29f4dcd15a83c02da57db1d582d2e1d4261aed499320d77537d2a2ca8c436719` |
| `src/core/hybrid_v2_adapter.py` | `7f4b15ea301ddc6ffb53c70398d938d538319e4df32af6af9f7c393da02f892b` |
| `src/core/phantom_generator.py` | `41fcff4e2a6cb580abe07994634735d17c02482e57f97722c3a9d7b930af69db` |
| `src/core/limited_activity.py` | `855c0353e761e4fb3a815209fd3303993fe2fa259311a787094f148e23b85e93` |
| `src/pipeline/runner.py` | `93ed63af9fe7bd8a2bab9ac1a070760f5a6bd1d70c430d9cf077a4086fdd19dc` |
| `src/pipeline/simind.py` | `e0b4409066a3bbec976a7386472fe3c10d6d44bf8a88bdf466dffbec340fb556` |

这些文件 SHA 用于审计，不取代 Git commit。论文方法的最终源码身份应优先写 release commit/tag；当前尚无 `v1.0.0` release，因此只能写合并候选身份。

### 5.4 冻结决策登记表

本表记录“为什么现在是这条路线”。它不替代代码，但可直接服务于 Methods rationale、Discussion 和审稿回复。

| Decision ID | 决策 | 理由 | 状态 / 可更改条件 |
|---|---|---|---|
| `DEC-PROFILE-01` | 新生产只允许 `hybrid_v2_limited_activity_v1` | 防止三套几何/activity 在同一数据集中静默混用 | `FROZEN POLICY`；API 全路径 enforcement 见 `IMP-GAP-01` |
| `DEC-ANAT-01` | anatomy 采用 Gate A Hybrid V2 | 相比 legacy 提供更丰富的 patient/liver/torso 形态和硬 QC | `FROZEN` |
| `DEC-LESION-01` | 病灶保留 corrected-master 几何 | full V2 tumor/activity 未充分验证；当前病灶合同更明确 | `FROZEN` |
| `DEC-ACT-01` | activity 使用 PAR-S_2 Gate C LimitedActivity v1 的最小只读移植 | 统一当前 Formal550 activity 合同，同时避免运行时跨仓库依赖 | `FROZEN BY SOURCE+CONFIG SHA` |
| `DEC-BACKEND-01` | v1 只提供原生 Windows backend | 少量本地使用需要 Windows；Linux 大批量是另一执行证据线 | `FROZEN FOR v1`；WSL/Linux 留待新版本 |
| `DEC-NEG-01` | 真阴性为同一 anatomy population 的 lesion-free independent control | 保持生成机制可比，同时避免与阳性训练样本混淆 | `FROZEN` |
| `DEC-IO-01` | ACT/ATN 固定 `<f4`、C-order ZYX，ATN=`mu×0.442` | 消除平台、轴序和单位歧义 | `FROZEN` |
| `DEC-SIMIND-01` | 验证 runtime 由 EXE/SMC SHA 定义，不按文件名定义 | 文件名不能证明二进制身份 | `FROZEN` |
| `DEC-NN-01` | 默认 NN=10，正式人工验收 worker=1 | 历史 RR/NN control 与可复现验收的折中 | `FROZEN FOR ACCEPTANCE` |
| `DEC-MOCK-01` | prepare/mock 永不充当物理仿真结果 | 软件状态机证据和 Monte Carlo 数值证据必须分离 | `FROZEN` |
| `DEC-RESUME-01` | config/input/runtime/artifact 漂移时拒绝恢复 | 防止旧 run 被新参数静默污染 | `FROZEN` |
| `DEC-REFACTOR-01` | 功能通过后才做行为保持简化，不改公式/seed/token/旧历史路径 | 改善可维护性但不改变科学身份 | `COMPLETED, VERIFIED WITH CAVEAT` |

---

## 6. 当前真实管线与状态机

### 6.1 用户可见入口

```text
main.py
  └─ native Windows launcher (127.0.0.1 only)
       └─ FastAPI + prebuilt React Web workbench

CLI
  └─ strict WindowsV1 new-run config parser

Web / CLI
  └─ PipelineRunner
```

- `python main.py` 调用 `src/windows_launcher.py`，只绑定 `127.0.0.1` 并打开本地浏览器；
- FastAPI 的 `CreateRun → _pipeline_config_from_request()` 新建路径在 `webui/server/app.py` 中执行严格 Windows v1 配置转换；
- 同文件的 config-path `/api/run/start` 当前直接调用 `PipelineConfig.from_dict()`，尚未拒绝非 `windows_v1` schema；它是已登记的发布缺口，不是受支持的新生产入口的替代权威；
- CLI 入口为 `src/cli.py`；
- 旧 PyQt UI 只通过 `legacy_pyqt.py` 显式进入，不是新生产入口；
- 浏览器只是本地工作台界面，计算和文件仍在本机；
- Linux/WSL 没有 v1 入口、开关或状态。

### 6.2 实际执行顺序

当前代码的真实顺序是：

```text
Web/FastAPI/CLI
→ PipelineRunner.generate
→ Hybrid V2 patient/liver geometry + region proxies
→ torso/tissues
→ physical mu_true + degraded mu_input（attenuation construction 在此完成）
→ temporary legacy perfusion/base activity
→ corrected-master lesion placement + transient contrast/activity measurements
→ LimitedActivity v1 replaces/discards the transient activity fields
→ save phantom NPZ + metadata
→ phantom_qc
→ ACT/ATN export + readback
→ SIMIND plan
→ native Windows SIMIND
→ projection QC
→ figures/package
→ finalize
```

`generate` 阶段的调用顺序与论文按概念解释方法时的顺序不必相同；论文可以先讲 lesions/activity、再讲 μ-map，但不得把概念章节顺序称为代码执行顺序。

注意：部分高层说明曾把 ACT/ATN 放在 phantom QC 之前；**代码实际先执行保存后 phantom QC，再导出 ACT/ATN**。本总账以 `PipelineRunner` 状态机为准，并将该文档顺序差异登记为 `DOC-CONFLICT-01`。

### 6.3 阶段合同

| 阶段 | 主要输入 | 主要动作 | 必须产生/检查的内容 |
|---|---|---|---|
| `generate` | effective config、case plan、seed domains | anatomy、lesions、activity、μ-map | NPZ、metadata、生成 provenance |
| `phantom_qc` | 保存后的 NPZ/metadata | 数组、mask、几何、角色和一般 provenance 检查 | QC PASS 后方可继续 |
| `export` | activity、mu_map、voxel size | 写 ACT/ATN 并读回 | dtype/order/shape/bytes/SHA |
| `simind_plan` | SMC、runtime、case identity | 构造安全命令与 RR/NN | 完整命令、pre-run hashes |
| `expectation` | ACT/ATN、SIMIND | prepare/mock/execute 相应动作 | execute 才是物理仿真；mock 明确为非物理 |
| `projection_qc` | `.a00`、`.res`、`.mhd` | 方向统一、尺寸、finite/nonnegative、token 回读 | projection metrics 和 SHA |
| `package` | 所有已通过 artifact | inventory、figures、manifest | 不覆盖已有结果；身份闭合 |
| terminal `finalize` action | 全部七个 ledger stage PASS | 写顶层 `finalized` 与 `package_sha256` | 它不是 `stages.finalize`；prepare 不允许 finalize |

### 6.4 三种执行模式

| 模式 | 是否启动真实 SIMIND | 用途 | 可否作为物理结果 |
|---|---:|---|---:|
| `prepare` | 否 | 生成体模、ACT/ATN、命令计划与预检 | 否 |
| `mock` | 否 | 测试完整状态机、恢复、打包和 UI | 否；必须标记 nonphysics |
| `execute` | 是 | 原生 Windows SIMIND | 只有 runtime/结果/QC 均满足时可以 |

在受支持的 Web/CLI 交互边界，真实执行超过 10 例时显示成本估算并要求二次确认；prepare/mock 不触发该限制。未验证 runtime 也需要独立二次确认，manifest 标记 `unverified_runtime`，不得显示 `validated_windows_v1`。直接 Python `PipelineRunner` 是执行组件，不代表已经发生这两项人机 consent；调用方必须承担等价治理。

---

## 7. 病例、数据对象与坐标合同

### 7.1 病例角色与队列

| 队列模式 | 阳性数量 | 真阴性数量 | 角色规则 |
|---|---:|---:|---|
| `positive_only` | >0 | 0 | 每例病灶数从用户闭区间离散均匀抽样 |
| `true_negative_only` | 0 | >0 | 病灶数强制为 0 |
| `mixed` | 分别输入 | 分别输入 | 角色在 case plan 中固定，不由结果反推 |

本地生成数量为正整数，没有产品级硬上限；软件在运行前估计空间和任务量。这个“无产品级上限”不是性能承诺，也不表示任意数量已实测。

### 7.2 真阴性的准确语义

`true_negative` 在本项目中指：

- 与阳性来自同一合成 anatomy population contract；
- 强制 `tumor_masks` 为空，形状为 `(0, Z, Y, X)`；
- 仍生成一个可行 territory 和总计数为 80,000 的 activity；
- 默认标记 `independent_test_control`；
- 不进入阳性病例的训练/验证 split。

它不意味着健康、非肝硬化、临床检查阴性或病理学阴性。

### 7.3 数组与世界坐标

- 数组顺序：`ZYX`；
- 世界坐标：`SAR`，其中 Z=superior、Y=anterior、X=patient right；
- 网格：`128 × 128 × 128`；
- 各向同性 voxel：`4.42 mm`；
- affine 以体积中心对齐；
- projection 原始数组读取后统一应用 `raw[:, ::-1, :]`。

NPZ 主数组：

| 数组 | 形状/类型 | 含义 |
|---|---|---|
| `activity` | `(Z,Y,X)` float | LimitedActivity v1，归一化总计数 80,000 |
| `mu_map` | `(Z,Y,X)` float | SIMIND 使用的 physical `mu_true_140kev` |
| `liver_mask` | `(Z,Y,X)` bool/int mask | 全肝 |
| `left_mask` | `(Z,Y,X)` mask | 左侧 proxy partition |
| `right_mask` | `(Z,Y,X)` mask | 右侧 proxy partition |
| `tumor_masks` | `(N,Z,Y,X)` | 每个病灶独立 mask；真阴性 N=0 |

### 7.4 Split 合同

- 阳性默认 split seed 为 42，比例为 0.8/0.1/0.1；
- 真阴性固定到测试侧，角色为 `independent_test_control`；
- split 身份应在 case plan/manifest 中固定，不能在训练代码里重新随机解释；
- 下游实际消费、泄漏检查和算法 evaluator 的 split 权威属于 `PAR-S_2`。

---

## 8. Hybrid V2 anatomy 方法

### 8.1 生成逻辑

`HybridV2Adapter` 先从域隔离 seed bundle 采样 patient 与 liver target，再最多尝试 16 个派生 liver shape seed。每个 attempt 的 `fit_liver_geometry()` 构建并筛选 liver shape；shape gates 通过后，在返回同一个 `LiverGeometryV2` 前构建五个区域 proxy。adapter 接受该 geometry 后，才为病例构建一次 torso/tissues 和衰减模型。失败的 liver candidate 不会被“保留最大连通分量”静默修补为成功结果。

肝脏不是简单椭球，而是由不对称 wedge/CSG 结构表达：右叶、左叶、穹隆、脏面凹陷、胆囊窝、肝门和可选尾状叶。体积匹配使用外层 calibration 与阈值二分；最终 rasterized volume 受 voxelization 影响，因此可与连续 target clip 略有差异。

每个 `fit_liver_geometry()` 使用最多 10 次外层 shape/fullness calibration；每次内层用 14 次 threshold bisection 匹配目标体积。Hybrid adapter 层最多再更换 16 个派生 liver-shape seed。这里的 10×14 是单个 shape attempt 内的确定性拟合上限，16 是病例层的候选重试上限，三者不能相乘后解释成固定执行次数。

### 8.2 当前实际使用的 population 参数

| 参数 | 当前合同 | source type / evidence ID | 注释/限制 |
|---|---|---|---|
| male fraction | 0.835 | `literature_population` / `karger-tare-hcc-2025-table1-full` | **full-cohort auxiliary**，不是 no-PVI subgroup demographics，也不代表任意临床中心 |
| age | 截断正态：中心 66、SD 10、范围 40–85 years | 中心：`karger-tare-hcc-2025-table1-full`；分布形状：`engineering-patient-joint-model-v2` | 66 是 full-cohort median anchor；正态形状、SD 与截断是工程合同 |
| height | male 174±7 cm；female 162±6 cm；范围 145–195 cm | `engineering_prior` / `engineering-patient-joint-model-v2` | sex-conditioned joint-model assumption |
| BMI | 中心 26.5、SD 4.2、范围 18.5–38；年龄斜率 0.035/year | `engineering_prior` / `engineering-patient-joint-model-v2` | 不是从当前 no-PVI cohort 拟合的经验分布 |
| weight | `weight_kg = BMI × (height_cm / 100)²` | implementation-derived / `engineering-patient-joint-model-v2` | BMI 单位按 `kg/m²` |
| cirrhosis probability | 0.80 | `engineering_prior` / `decision-cirrhosis-a-20260713` | 不是实测 TARE prevalence |
| liver volume reference | 1533±375 mL | `literature_population` / `perez-radiology-2022-liver-volume` | weight/height/age/residual conditioning 属于 `engineering-liver-geometry-v2`；不把 `14×weight+979` 当生成均值 |
| liver target clip | 775–2300 mL | `engineering_prior` / `engineering-liver-geometry-v2` | 连续 target；rasterized actual 可略越边 |
| extent center | `[174.5, 160, 200] mm` | literature anchor：`seppelt-ship-mri-2022-liver-diameters`；residual model：`engineering-liver-geometry-v2` | 由健康 sample joint mean 按 reference volume 缩放，再加体积与 log residual；不是独立轴分布 |
| bbox fill target | 0.255–0.320 | `engineering_prior` / `engineering-liver-geometry-v2` | 形状 QC/校准阈值，不是生理参考区间 |
| left fraction | base/reference sample：中心 0.31、SD 0.06、截断 `[0.15,0.45]`；cirrhotic transform 后 final target cap=0.55 | 中心/基础范围：`mise-2014-liver-segments`；sampling/transform：`engineering-liver-geometry-v2`，方向锚点 `hunt-cirrhosis-lsvr` | cirrhosis transform 是简化方向 proxy；最终 rasterized mask 还受体素量化/geometry fit 影响 |
| centroid mean | `[-45, 15, 35] mm`；SD `[10,8,12] mm` | `engineering_prior` / `engineering-liver-geometry-v2` | SAR 定义见第 7 节 |
| caudate probability | normal 0.75；cirrhotic 0.95 | `engineering_prior` / `engineering-liver-geometry-v2`；方向参考 `hunt-cirrhosis-lsvr`、`ozaki-bjr-2016-cirrhosis-morphometry` | 概率本身不是文献 prevalence |
| roughness target | normal 0.256；cirrhotic 0.273 | `engineering_prior` / `engineering-liver-geometry-v2`；形状动机 `thanaj-ukbb-2024-liver-shape` | 形态合同，不是临床影像评分 |
| surface field amplitude | normal 0.04；cirrhotic 0.18 | `engineering_prior` / `engineering-liver-geometry-v2` | 控制表面扰动；未由真实 mask 标定 |

实际 liver-volume 条件模型为：

```text
z_weight = (weight_kg - 79) / 17
z_height = (height_cm - 172) / 8
z_age    = (age_years - 66) / 10
z_volume = 0.45 z_weight + 0.10 z_height - 0.08 z_age + 0.88 ε
ε ~ Normal(0, 1)
V_ml = clip(1533 + 375 z_volume, 775, 2300)
```

extent 的 joint center 随体积按立方根缩放：

```text
E0_zyx = [174.5, 160.0, 200.0] × (V_ml / 1533)^(1/3)
E_zyx  = E0_zyx × exp(σ_zyx ⊙ clip(η_zyx, -2.5, 2.5))
σ_zyx  = [0.055, 0.050, 0.055],   η_i ~ Normal(0,1)
```

最多提出 64 个 residual proposal，接受首个使 `V×1000/prod(E)` 落入 bbox-fill `[0.255,0.320]` 的 proposal；若都不满足，保留无 residual 的 `E0`。因此 extent 三轴是 joint conditional model，不能当作三个互相独立的正态变量。

### 8.3 肝内区域 proxy

当前五个区域属于 `couinaud_proxy_without_vascular_tree`：

1. S1 caudate proxy；
2. S2/3 left lateral proxy；
3. S4 left medial proxy；
4. S5–8 right anterior proxy；
5. S5–8 right posterior proxy。

它们用于病灶位置、分布分析和可行性检查。因为没有显式门静脉/肝静脉树，也未针对真实 Couinaud 标注验证，论文必须使用 “proxy” 或“近似区域”，不能称为真实分段。

### 8.4 Torso 与组织

Torso 是由 habitus 条件化的确定性 patient envelope；内部包含 paired lung ellipsoids、posterior spine proxy 和 subcutaneous fat shell。它提供仿真所需的组织标签和衰减环境，不是器官级全身数字人模型。

首先定义 `h = height_cm/172`、`b = BMI−25`。body target dimensions 为：

```text
SI = clamp(3 × height_cm, 470, FOV_SI − 4 × 4.42) mm
LR = clamp(sex_LR_base × h^0.35 + 4b, 325, 500) mm
AP = clamp(sex_AP_base × h^0.20 + 6b, 215, 400) mm

sex_LR_base = 392 mm (male), 366 mm (female)
sex_AP_base = 268 mm (male), 252 mm (female)
fat_thickness = clamp[10 + 1.25(BMI−18), 8, 42] mm
```

body center 为 `(S,A,R)=(0, −4+0.10b, 0) mm`，envelope 是幂次为 `(7.0, 2.35, 2.35)` 的 cropped superellipsoid：

```text
|S/(SI/2)|^7 + |(A−A0)/(AP/2)|^2.35 + |R/(LR/2)|^2.35 ≤ 1
```

authoritative liver 外再加至少约 8 mm 的 deterministic soft-tissue collar，并做一次 closing/fill-holes，以保证 population-tail liver 仍位于 body 内。

Paired lungs 的 compact contract：

- common center `S = 72 + 0.30(height_cm−172) mm`、`A=18 mm`；左右 `R` offset 为 `0.215×LR`；
- radii：`S=clamp(0.255×SI,112,140)`、`A=clamp(0.255×AP,58,90)`、`R=clamp(0.165×LR,52,78) mm`；
- right lung 相对 common center 向 inferior 5 mm，S radius 乘 1.03；
- lung 先截到 body 并排除 liver，再仅清除离散 raster islands；这项 largest-component cleanup 只用于左右肺，不用于修补失败的 liver geometry。

Spine proxy 位于 `A=−0.31×AP`，AP radius=`clamp[15+0.20(BMI−25),13,21] mm`，LR radius=`clamp[19+0.10(height_cm−172),16,25] mm`，SI half-extent=`0.42×SI`。Fat 是 body 内距表面不超过上述 fat thickness、并排除 liver/lung/bone 后的 shell。

Torso hard-QC：

| Gate | 范围/要求 |
|---|---|
| affine/grid | 与 liver 的 SAR/ZYX 128³ grid 精确一致 |
| body components | 恰好 1 |
| lung components | 恰好 2；left centroid R<0<right centroid R |
| body extent ZYX | S 440–570、A 200–420、R 300–520 mm |
| body volume | 20,000–80,000 mL |
| total lung volume | 1,500–8,000 mL |
| fat fraction | body volume 的 0.02–0.50 |
| relative position | lung centroid 至少比 liver 高 55 mm；liver 至少比 spine 前 45 mm |
| tissue topology | liver/lung/bone/fat 均在 body 内且互斥 |
| lung cleanup | 每侧 discarded raster-island fraction ≤0.002 |

### 8.5 Anatomy 硬 QC

候选至少检查：

- 肝脏连通性；
- left/right lobe overlap 与 partition 合法性；
- waist、taper、caudate outer-shape 和 dome；
- 脏面保持开放、无内部空洞；
- cut scale 与逐 slice 连通性；
- torso/tissue 空间关系和互斥；
- target/actual volume、centroid、extent 和关键 shape metrics。

生成器最多进行 16 个 shape attempt。无法找到合法 anatomy 时明确失败；这类失败是 population/config 可行性信息，不应被隐藏。

---

## 9. Corrected-master 病灶几何

### 9.1 数量与尺寸

| 参数 | 合同 |
|---|---|
| 阳性病灶数 | 用户闭区间；边界必须在 1–5；区间内离散均匀抽样 |
| 真阴性病灶数 | 强制 0 |
| small | `[10,20) mm` |
| medium | `[20,40) mm` |
| large | `[40,60] mm` |
| 默认原始权重 | 0.45 / 0.40 / 0.15 |
| 权重规则 | 每项非负、总和 >0；保存原始与规范化值；负数/全零拒绝 |

半径在所选尺寸带内均匀采样；最终 voxel mask 再计算 equivalent-sphere diameter 并验证仍在同一带内。实现不会为了提高成功率，把不可行的大病灶静默重采样成较小尺寸带。

### 9.2 形态

- ellipsoid 概率 0.70；elongation 从 `[0.7, 1.3]` 均匀采样；
- spiculated 概率 0.30；roughness 0.35、spiciness 3；
- 当前为固定的合成形态策略，不主张复现全部 HCC 形态学谱系；
- 旧 full V2 中的 necrosis、复杂 heterogeneous tumor activity 等字段不属于当前活跃病灶合同。

### 9.3 放置与失败语义

- 病灶按尺寸从大到小放置；
- 每个规格最多 20 次 sampling attempt；
- 每个中心最多 250 次尝试；
- 整体布局最多 12 次；
- 默认表面 margin 为一个 voxel，即 4.42 mm；
- 病灶之间不允许 voxel overlap；
- 当前 `subcapsular_fraction=0.0`，不主动抽样 subcapsular population stratum；
- 全部病灶先在全肝内完成几何放置，activity territory 后续再按可行性选择。

当容量不足时，显式 fallback 可以放松表面 margin，但仍要求病灶完全位于肝内且互不重叠，并记录 `capacity_fallback_margin_relaxed`。该 fallback 是计算可行性处理，不是旧 full-V2 profile 中“20% subcapsular prevalence”的实现或证据。若仍不可行，则任务明确失败，而不是缩小病灶、减少病灶数或换成未记录的 territory。

---

## 10. LimitedActivity v1

### 10.1 为什么替换旧 activity

`HybridV2Adapter` 返回 anatomy 与 attenuation 后，`PhantomGenerator.generate_one()` 为兼容 corrected-master lesion placement 暂时构造 legacy perfusion/base activity，并计算旧 contrast target/measurement 字段。当前协议随后将这些内容标记为 `discarded_not_persisted`，再由 LimitedActivity v1 生成唯一持久化的 activity。论文不得把旧临时场与最终 ACT 混写。

### 10.2 Territory 选择

持久化候选及公开 config ID 为 `whole_liver`、`right_lobar`、`left_lobar`；UI 可显示“全肝/右叶/左叶”等人类可读标签，但提交的仍是这些 canonical ID。某 territory 只有在以下条件同时满足时才可行：

- territory 非空；
- 包含该病例全部病灶；
- 每个病灶都能构造合法 local ring。

默认 `auto_equal_feasible` 对所有可行 territory 赋原始相等概率，再归一化采样。用户也可锁定 whole/right/left；锁定 territory 不可行时明确失败，不能退回另一区域。

### 10.3 背景和局部 TNR

残余背景按下式构造：

```text
background(z) = 0.05 × [1 + 0.08 × axial_coordinate(z)]
axial_coordinate ∈ [-1, 1]
```

每个病灶的局部背景 ring 定义为：病灶外欧氏距离 1–3 voxel、位于所选 territory 内、并排除所有病灶体素。每个病灶的目标 TNR 使用独立 domain seed，从连续均匀分布 `Uniform[TNR_min, TNR_max]` 抽样：

```text
2 ≤ TNR_min ≤ TNR_max ≤ 8
```

浮点端点使用内部 epsilon 处理；最终持久化 activity 的实际 local ring TNR 与目标偏差不得超过 2%。总 activity 先以 float64 归一化到 80,000，再转换为 C-contiguous float32。所选 territory 外严格为 0。

### 10.4 真阴性 activity

真阴性没有病灶和 TNR，但仍按相同 territory 机制生成背景并归一化到 80,000。这样保持投影任务有效，同时把“无病灶”与“无 activity”明确区分。

### 10.5 验证边界

生成后内存对象立即执行完整 `verify_limited_activity`。但当前稍后从磁盘重新载入的 `phantom_qc()` 主要检查保存数组和一般 provenance，**不会独立重算完整的 ring/territory/TNR 合同**。因此：

- 生成时的 LimitedActivity verifier 是 activity 数值合同的直接权威；
- reload QC 是附加保护，不应被描述成第二个完全独立的 activity evaluator；
- 若论文需要“持久化后独立重算 TNR”的强主张，应另建只读 evaluator artifact，而不是修改本总账措辞。

---

## 11. μ-map、ACT/ATN 与能量语义

### 11.1 物理 μ-map

当前近 Tc-99m photopeak 的组织线性衰减系数合同为：

| 组织 | μ (`cm⁻¹`) |
|---|---:|
| outside | 0.000 |
| lung | 0.050 |
| fat | 0.146 |
| water | 0.150 |
| soft tissue | 0.150 |
| liver | 0.160 |
| bone | 0.300 |

写入优先级为 soft body → fat → lung → liver → bone，从而使后写的特异组织覆盖更一般的 body label。

系统还生成一个 CT-like degraded `mu_input`，其合同包括约 1.5 mm blur、15 HU noise、5 HU bias、40 mm correlation scale 和 `[-1000,2000] HU` clip。该输入目前未校准，不写入主 NPZ，也不传给 SIMIND；只保存 hash/metadata provenance。当前 SIMIND 只使用 `mu_true_140kev`。

### 11.2 能量命名限制

代码 key 使用 `mu_true_140kev`，phantom metadata 的 reference 记为 140.5 keV，而当前 SMC photon energy 为 140.0 keV。三者都位于 Tc-99m photopeak 附近，但在作者正式统一术语前，论文应写“near the Tc-99m photopeak”并分别报告配置值，不能声称三个值在数值上完全相同。

### 11.3 ACT/ATN 字节合同

| 项目 | 合同 |
|---|---|
| dtype | little-endian `<f4` |
| 内存/文件顺序 | C-order ZYX |
| ACT | LimitedActivity v1 `activity` |
| ATN | `mu_map × voxel_size_cm = mu_map × 0.442` |
| 128³ 文件大小 | 8,388,608 bytes |
| 写入保证 | atomic write、写后读回、dtype/shape/size/SHA-256 检查 |

ATN 中乘以 0.442 是从 `cm⁻¹` 转换为每 voxel 的线积分系数合同，不是重新拟合 μ 值。

---

## 12. 原生 Windows SIMIND 合同

### 12.1 Runtime 身份

验证过的 runtime 必须精确匹配：

- `simind.exe` SHA-256：`f984b8753f54b9f671f9fc1bcb2b45461e7cae8d027376b446dd1ed55a9a8319`
- `ge870_czt.smc` SHA-256：`4d10eab246a7a6690663230d2f33aeb3c32f67c598af36b56d1575f0e3551d10`

执行前后都重新计算 hash。hash 不匹配时，用户可在 Web/CLI 边界经过二次确认继续，但运行必须标为 `unverified_runtime`。这只表示用户接受了不匹配风险，不把 runtime 变成已验证版本。

### 12.2 命令合同

标准 token 顺序为：

```text
ge870_czt case_xxxx
/FS:case_xxxx
/FD:case_xxxx
/NN:<NN>
/IN:x21,100x/25:1704/100:160/101:208
/RR:<case-seq>
```

要求：

- `/25:1704`、`/100:160`、`/101:208` 和 `/IN:x21,100x` 固定验证；
- `/RR` 是终端 token；
- `/NN` 为 1–1,000,000 的整数，默认 10；
- 输出 basename 仅允许 `[A-Za-z0-9_]+`；
- 在隔离 staging 目录执行，再原子移动到目标；
- 不覆盖已有结果，也不把不同 run 的 artifact 混合。

### 12.3 当前 SMC/采集合约中与论文相关的值

| 项目 | 当前值 | 解释边界 |
|---|---:|---|
| photon energy | 140.0 keV | 与 μ-map 命名差异见第 11.2 节 |
| radius | 30 cm | acquisition geometry |
| attenuation types 14/15 | -7 / -7 | scoped Type-7 control 已做历史验证 |
| energy window | 126–154 keV | SMC 合同 |
| index 25 | 1704 | nominal exposure token |
| views | 60 | projection first dimension |
| density/source voxel | 0.442 cm | 与 4.42 mm phantom voxel 一致 |
| volume | 128³ | source/attenuation volume |
| runtime detector override | 160 × 208 | `/100` 与 `/101` |
| detector pitch | 0.246 cm | 对应 FOV 39.36 × 51.168 cm |
| cross-sections | `h2o/h2o` | frozen acquisition contract |

`/25:1704` 可追溯到 60 MBq × 28.4 s 的 nominal exposure；本地 10 次历史 acquisition 的 frame duration 中位数为 28.354 s、范围 27.809–28.439 s，但 dose 字段为零，因此不可据此宣称绝对临床 cps/MBq 校准。

### 12.4 投影读取与 QC

- 预期投影：float32，形状 `(60,128,128)`；
- canonical orientation：`raw[:, ::-1, :]`；
- 检查精确字节数、有限值、非负值；
- `.res` 检查结束标记及关键 token echo；
- `.mhd` 要求 `DimSize = 128 128 60`、`MET_FLOAT`；
- 保存 projection metrics 与 SHA-256。

SIMIND `.a00` 在当前合同中是可含非整数值的 weighted Monte Carlo expectation estimator，不是又独立抽样一次的临床 Poisson event-count realization。phantom activity 的 80,000 是 source-array normalization contract，也不等于输出投影的像素总和。论文若研究 count noise，必须说明噪声来自 NN/RR Monte Carlo histories、额外 Poisson sampling，还是算法侧另行构造；三者不能混称“80,000 projection counts”。

`.res` 包含时间和性能信息，重复执行时允许这些预声明 volatile 行变化。数值 projection 和稳定科学字段仍必须满足对应比较合同。

---

## 13. 公开配置、接口与 Windows 软件设计

### 13.1 Windows v1 新建入口的严格公开配置边界

Windows v1 新建配置增加固定 `schema_version` 与唯一 `generation_profile`。经 `WindowsV1Config`、CreateRun 或 CLI 新建时，未知字段、旧 profile 和越界值拒绝，不静默丢弃。该陈述不覆盖当前仍可直接读取 legacy config 的 `/api/run/start`，见 `IMP-GAP-01`。

| 类别 | 允许值/边界 | UI/错误行为 |
|---|---|---|
| 队列模式 | `positive_only` / `true_negative_only` / `mixed` | mixed 分别输入阳性和阴性数量 |
| 病灶数 | 1–5 闭区间 | 0 或 6 拒绝；真阴性由角色强制为 0 |
| 尺寸权重 | 非负且总和 >0 | 保存原值和规范化值；NaN/Inf/负值/全零拒绝 |
| TNR | 2–8，且 min≤max | NaN/Inf、反向和越界拒绝 |
| territory | `auto_equal_feasible` / `whole_liver` / `right_lobar` / `left_lobar` | 锁定不可行明确失败 |
| global seed | 0 到 `2^53−1` | 与 JavaScript 安全整数一致 |
| NN | 1–1,000,000 整数；默认 10 | 1=快速测试，10=推荐质量，>10 提示耗时 |
| workers | 1–32；默认 1 | 正式验收 worker=1 |
| 体模锁定项 | 128³、4.42 mm、80,000、0.05、0.08、physical μ 等 | 篡改拒绝 |

### 13.2 “冻结”需要精确限定

严格 wrapper 硬锁定公开 profile/phantom object、128³、μ 合同、threshold 100、detector 160/208、`h2o/h2o`、NN/worker 边界和 observation 关闭。

但某些底层 `PipelineConfig` 字段并非全部写死，包括 projection shape、split seed/fractions、protocol label、相互一致的 activity/exposure/index25、额外 SIMIND override。有效 config SHA 会记录这些值，resume 也会检查漂移。因此论文的可复现身份必须绑定：

```text
release/source commit + effective config SHA + profile/registry SHA + runtime SHA + manifest SHA
```

不能只写一个 profile 名就称为完整冻结。profile/registry hash 会写入并在 resume 中比较，但新 run 创建时并非把所有文件都与一个硬编码 expected SHA 比较；release commit 与 manifest 仍是必要身份。

### 13.3 原生 Windows 文件选择

选择器覆盖 SIMIND `.exe`、SMC `.smc`、runs 输出根目录和实验导出目录。选择文件时授权其父目录，选择目录时只授权该目录本身；两者都仅在当前应用进程会话有效。

预检规则：

- 只允许本地 drive；拒绝 UNC；
- 拒绝不可访问或只读目标；
- 拒绝错误扩展名、Windows 保留名、尾随点或空格；
- resolved path 超过 240 字符时拒绝；
- 空格、中文和重音字符必须正常工作；
- Cancel 不得改变当前配置。

实现使用短生命周期 PyQt helper 调起原生 dialog，因为在 FastAPI worker 内直接持有 Qt dialog 曾导致挂起。Unicode、Cancel 状态和错误 API 路径已有自动测试；**真实可见 Windows Station 上的人工 picker 验收仍为 `PENDING`**。

### 13.4 启动、单实例与本地性

- `setup_windows.ps1`：锁定 Python 3.11 环境、`npm ci` 和前端构建；
- `start_windows.ps1`：日常一键启动；
- 发布形态为源代码 + 预构建 Web 资源，不制作 EXE/安装器；
- 服务只监听 loopback，处理端口冲突、单实例和退出清理；
- 不上传病例数据，不依赖云端服务；
- runtime 许可文件和本地设置不进入 Git。

### 13.5 恢复与防漂移

resume 重新核对：

- effective config fingerprint；
- profile/源码/config provenance；
- 输入和运行时 hash；
- 已存在 artifact 的 size/hash；
- 阶段状态和病例身份。

发现漂移立即停止，不覆盖旧结果。需要改变科学参数时应创建新 run，而不是篡改已有目录继续执行。

---

## 14. Seed、重放与病例身份

### 14.1 域隔离

主要随机域为：`patient`、`liver`、`tumor`、`activity`、`mu`、`simind`。除 SIMIND 外，case seed 派生使用：

```text
SHA256("pars-syn-v2|<global_seed>|<case_id>|<namespace>")
→ first 8 bytes
→ mod (2^63 − 1) + 1
```

LimitedActivity 的内部派生使用：

```text
SHA256("pars-hybrid-v2-limited-activity-v1|<activity_seed>|<domain>|<index>")
```

这样修改一个随机域的消费顺序不会自动改变其他域。manifest 保存各派生 seed，便于重放和定位差异。

### 14.2 顶层 `seed` 的兼容性陷阱

病例顶层 `seed` 仍是 legacy-compatible 的 `global_seed + numeric case id`。它不是当前科学随机性的完整权威；真正控制 anatomy/activity 等内容的是 metadata `v2.seeds` 中的 hash-derived case/child streams。论文和调试报告不得只抄顶层 seed 就声称可完整重放。

### 14.3 SIMIND RR

SIMIND RR 采用 1–10007 范围内的确定性 affine permutation，并为正式阳性与真阴性保留不相交 slot。RR、NN、完整命令和 runtime SHA 均进入 manifest。RR 负责 Monte Carlo stream 身份，不替代 anatomy/activity seed。

---

## 15. QC、provenance 与可复现打包

### 15.1 四层验证不能互相替代

| 层级 | 回答的问题 | 典型证据 |
|---|---|---|
| 代码/合同 | 设计是否被实现和拒绝非法输入？ | source、schema、unit/integration tests |
| 自动软件门槛 | 应用状态机、API、前端和 mock 是否工作？ | CI logs、test reports |
| 数值/物理 artifact | 生成数据和仿真结果是否满足指定数值合同？ | evaluator JSON、manifest、projection metrics、SHA |
| 人工验收 | 操作者在真实桌面环境能否完成关键交互？ | 签字 checklist、截图/日志、异常路径记录 |

CI 通过不能替代数值 artifact；两例真实 SIMIND 不能替代 550 例算法评估；自动 picker 测试也不能替代可见桌面人工验收。

### 15.2 Manifest 最低内容

当前 manifest/包应保存：

- schema/profile/backend/protocol status；
- source commit 与关键源码/config SHA；
- 原始和解析后参数、effective config fingerprint；
- case role、split role、全局与派生 seed；
- anatomy/lesion/activity/μ-map provenance；
- ACT/ATN size 和 SHA；
- Windows 信息、SIMIND/SMC pre/post SHA 与验证状态；
- 完整命令、RR、NN；
- phantom/projection QC；
- artifact inventory、figures、package/finalize 状态。

### 15.3 推荐最小 manuscript handoff record

每个进入论文分析的数据包至少向 `PAR-S_2` 交付：

```yaml
dataset_id: <stable logical id>
generator_source_commit: <sha>
generation_profile: hybrid_v2_limited_activity_v1
effective_config_sha256: <sha256>
case_plan_sha256: <sha256>
dataset_manifest_sha256: <sha256>
artifact_inventory_sha256: <sha256>
runtime_backend: windows_native
simind_sha256: <sha256>
smc_sha256: <sha256>
case_count:
  positive: <n>
  true_negative: <n>
roles_and_splits: <artifact locator>
generation_qc: <artifact locator + sha256>
downstream_ack: <external project evidence id>
```

上述 schema 是当前 Generator run/manifest 的交付记录，因此 `runtime_backend` 永远是 `windows_native`。导入外部 Formal550 handoff 时，应在接收侧另设 `external_execution_backend` 及外部 runtime SHA 字段保存 Linux 身份；不得把 Linux 值回填到 Windows v1 schema，也不得把它解释为本软件已有 backend 开关。

---

## 16. 原始证据登记表

### 16.1 当前直接证据

#### `EV-GA-100` — Hybrid V2 Gate A anatomy-only 100 例

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / VERIFIED / ARTIFACT_VERIFIED` |
| 逻辑目录 | `gate-a-v2-master-100-20260819` |
| 当前操作者 locator | `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-hybrid-v2-master-gate-a\runs\gate-a-v2-master-100-20260819` |
| code identity | Gate A commit `921e2e723804ed9ce1771d79c6a3cead9885c8fd` |
| `dataset_manifest.json` | SHA-256 `629cfffead80328a85610953172467f227782b2bde10405216274c0c77bd1c70` |
| `gate_a_report.json` | SHA-256 `e1b11528ceb996282da4a2500f4bc8a6de02f2812ae76aa4eb35f8467916bd14` |
| `gate_a_failures.json` | SHA-256 `0f9d583f983aeccd76758ec38fa0e5ce426f2aa14c6702af591446a60ae556ee` |
| `cases.jsonl` | SHA-256 `675ee6929074183e9d53311e54aeb77f0627e68ea81a1a1eb328c33099a51008` |
| effective config | SHA-256 `290ce4acfb9cd5bdd4ba22fb2bac935c7ea9b336f8452f74befd8b09e83f5793` |
| 结果范围 | 100/100 hard-QC；20 项 Gate checks PASS；5 例 bitwise replay PASS |
| 重要保管限制 | 完整 run 被 `.gitignore` 忽略，不包含在 archive tag 中；当前是本地 hash-identified artifact，需单独归档 |

这里的 `anatomy-only` 是 Gate 范围名：它表示不执行 ACT/ATN 与 SIMIND，而不是“病例里只有肝脏”。该 run 仍包含 corrected-master lesion masks、μ-map，以及当时生成路径的临时/旧 activity 内容。当前只使用其中 anatomy、mask、lesion geometry 与 μ-map 相关 artifact 作为权威；旧 activity 不能替代后来冻结的 LimitedActivity v1 证据。

因此，该证据可支持 Hybrid V2 anatomy/lesion 几何的限定结论，不包含 LimitedActivity 或 SIMIND 结果。

#### `EV-ACT-DELTA-A` — LimitedActivity Gate A Delta

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / VERIFIED / EXTERNAL_SOURCE_VERIFIED` |
| locator | `D:\PFE-U\PAR-S_2\outputs\gate_b_delta_cpu_20260821_v1_final\pars_gate_b_delta_cpu_20260821_v1\evidence\GATE_A_DELTA_ACK.json` |
| source SHA | `43e0b4de9231710d2956c1446c7afb373b2e4c0b49d57322c4b5d54765c3bfdb` |
| config SHA | `04b40614ac8274cf7d474dc73eb360ea341ad65fa1c35634f3b8b18d7aa32fd7` |
| immutable-array joint SHA | `c8c115d6cbd520ef5aae392bad9906ac83a74b0f1edc4077fcf239c827c58d3e` |
| ACK SHA | `a45e47525bb26e66be50d5c08d3fc02e3d929340576764826c04f1428474a5a0` |
| 结果 | 100 cases；science/immutable/replay 均 PASS |
| 允许结论 | activity 替换没有改变指定的 `mu_map`、liver/left/right masks 和 tumor masks |

#### `EV-ACT-DELTA-B` — LimitedActivity Gate B Delta

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / VERIFIED / EXTERNAL_SOURCE_VERIFIED` |
| ACK locator | `D:\PFE-U\PAR-S_2\outputs\server_returns\gate_b_delta_delta_20260821_v1\verified_spect\GATE_B_DELTA_LOCAL_ACK.json` |
| ACK SHA | `0d6a33d88357fff976114fb2c8d3c531fc2d9475045f00b43a182e52343673a8` |
| science QC SHA | `231544996f2828f15ceb3d6318589a7a624f18a4e3d345c8dfc79f6a028a0428` |
| return archive SHA | `e0fa97e979aea9231723718bfffeaed41e3b94013a99bd5ac3398d6181e45887` |
| package manifest SHA | `769bc333cc456266a28c4f318994e2e7de7d2788540ec9d64c0229fe1e9c7d57` |
| 结果 | 13/13 jobs；10 个不同阳性、`case_0012` 两次额外重复、1 个真阴性；NN=10 guard 等 PASS |
| Gate decision | `authorizes_gate_c_v2=true` |
| 限制 | 10 个不同阳性均为 `whole_liver`；验证范围是 `GATE_A_FULL_GATE_B_DELTA_UNSTRATIFIED` |

#### `EV-DATA-GATE-C-550` — Formal550 生成源数据

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / VERIFIED / EXTERNAL_SOURCE_VERIFIED` |
| status locator | `D:\PFE-U\PAR-S_2\outputs\hybrid_v2_formal550_20260821_v2\DATASET_READY.json` |
| `DATASET_READY.json` SHA | `d28cb981d30a653971fd1928cd39743e2e6d03ba25b69b57ddfd53c554d0d2ce` |
| case plan SHA | `3bf3476a4bfabdcb6af9fdaaac79f97ba0e2b897d7cf6c532e8786265ce903d8` |
| artifact inventory SHA | `2a1f450a8224cef9e7e8b5d4e2a8a3c24ba36b198cb617e1f47e8c3f8757cffb` |
| dataset manifest SHA | `0346de2873eaf6fe874d423e542c066b1968c3aa2c7836073aa1be8b120dc53b` |
| case count | 500 positive + 50 true-negative = 550 |
| state | `status=PASS`, `sealed=true`, `eligible_for_packaging=true` |

CPU/server package locator：

`D:\PFE-U\PAR-S_2\outputs\hybrid_v2_formal550_cpu_20260821_v2_fix2\pars_hybrid_v2_formal550_cpu_20260821_v2\PACKAGE_READY.json`

其 package manifest SHA 为 `ef745d8b0947fea4b6bff1056bd0c72bcbdbcc6357898f98967b73bed42414c2`。该证据证明源数据与执行包已经 sealed；服务器 SIMIND 返回和算法 evaluator 的最终状态必须由外部总账另行证明。

#### `EV-WIN-FAILED-OBS-20260822` — 旧 empirical observation 路径的失败证据

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / SUPERSEDED / ARTIFACT_VERIFIED`；原 run verdict=`FAILED` |
| run | `runs/windows-v1-pre-refactor-real-20260822` |
| `run.json` SHA | `33adf15f6feaddb3074a7f26280e03894e3ceb2a52e037ff185b0ae54b27876d` |
| design | 1 positive + 1 true-negative；NN=10；worker=1；validated runtime；旧 `create_poisson_observation=true` |
| passed before failure | generate、phantom QC、export、SIMIND expectation、projection QC |
| failed stage | offline empirical-count Poisson observation；2/2 cases failed angular-CV gate |
| case 1 | angular CV `0.3006001095`，低于 empirical range `[0.3336022670, 0.6201742993]`；`qc/case_0001_observation_qc.json` SHA `8351e4f2e8e61ac15b9dc1159f1ee2686deb1bc1ed57d153bbb32ad794becfc5` |
| case 2 | angular CV `0.2947000328`，低于同一 empirical range；`qc/case_0002_observation_qc.json` SHA `8ee6b737c498b4923c86aac5d7a5570eb924dd8cc2b37dad15ec8924a3e7a008` |
| terminal state | `finalized=false` |

这次失败没有被包装成成功结果。它表明当时的 offline empirical observation 路径不能满足预定 angular-CV gate；随后 Windows v1 权威明确改为 expectation-only、`create_poisson_observation=false`，并由 `EV-WIN-PRE/POST` 重新执行当前路径。该历史失败不能用于当前数值结果，也不得因“后来通过”而删除。

#### `EV-WIN-PRE` — 重构前原生 Windows 真实执行

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / VERIFIED / ARTIFACT_VERIFIED` |
| summary | `docs/evidence/windows_v1_pre_refactor_real_20260823.json` |
| summary SHA | `2511b020e2627309cde14d2d2e0d7c5ce125c6939bb19243a6340919bd2966c7` |
| source commit | `3ac54662aa220abb030f19548b39dd9c23ab66a6` |
| run | `windows-v1-pre-refactor-real-v2-20260823` |
| design | 1 positive + 1 true-negative；global seed 42；NN=10；worker=1 |
| manifest SHA | `9c67cef0a13388f587d91043c92b92e9bf8aaa0b5516c48c6fef8c5e6d8983f1` |

#### `EV-WIN-POST` — 重构后原生 Windows 真实执行

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / VERIFIED / ARTIFACT_VERIFIED` |
| summary | `docs/evidence/windows_v1_post_refactor_real_20260823.json` |
| summary SHA | `5fcd91e4979eae6ddbb44f74c5c04387c57b127c4cffafce1847cab5f5e6e216` |
| source commit | `6f684ce3cf54b04b6d724564938e9727a8b4d665` |
| run | `windows-v1-post-refactor-real-20260823` |
| design | 同角色、seed、NN、worker 和 validated runtime |
| manifest SHA | `ed0b4c94bc233ac47c438038811dd15e032eb35bc5aefbbc121c79faada17a2d` |
| state | generation/export/QC/SIMIND/projection/package/finalize PASS |

真实 run 目录目前是本地未跟踪 artifact；Git 只保存 machine-readable evidence summary。正式归档前不得删除。

#### `EV-WIN-EQUIV` — 行为保持重构等价性

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `RESULT / VERIFIED / ARTIFACT_VERIFIED` |
| summary | `docs/evidence/windows_v1_refactor_equivalence_20260823.json` |
| summary SHA | `cb582c3200e4bfcdc1995be4db2b8718f82791ae49396c16ff1924b25d319082` |
| result | 42/42 checks PASS |
| byte-identical | NPZ、ACT、ATN、`.a00` |
| semantically identical | role、split、seed、RR、command、QC |
| `.res` | 时间、elapsed 与 `DetectorHits/CPUsec` 可变；排除预声明 volatile 行后一致 |

#### `EV-CI-MERGE` — 合并后 Windows 自动 CI

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `FACT / VERIFIED / EXTERNAL_SOURCE_VERIFIED` |
| merge commit | `dd62ba3f8d2c819bfca3c76090ef7dfe2a69c023` |
| workflow/job | `windows-native` |
| run/job ID | `32642767283 / 97202292683` |
| result | success，完成于 2026-08-23T13:47:57Z |
| coverage | Python tests + Ruff；frontend lint/unit/build；E2E/a11y/visual；prepare positive；mock true-negative |
| limitation | 使用零字节占位 `simind.exe`；没有执行真实 SIMIND，也不包含可见 GUI 人工签字 |

#### `EV-WIN-MANUAL` — 原生 Windows 人工验收

| 字段 | 值 |
|---|---|
| Kind / Lifecycle / Evidence | `PLAN / PENDING / USER_APPROVED` |
| protocol | `docs/WINDOWS_V1_ACCEPTANCE.md` |
| 当前状态 | 用户明确决定稍后执行，有问题再反馈 |
| 尚需覆盖 | clean clone/install/start；真实 picker；Cancel；Unicode/空格；错误扩展、UNC、只读、长路径；真实 EXE/SMC；corruption/resume；操作者签字 |
| claim gate | 完成前不得把候选称为“人工验收完成的正式 v1.0.0 release” |

### 16.2 历史但仍可引用的物理控制

`docs/DECISION_GATES.md`、`docs/evidence/stage2_validation_summary_2026-08-18.json`、`docs/evidence/stage3_pilot_summary_2026-08-18.json` 以及相应旧 run 属于早期 master/activity/observation 路线。它们不是当前 Hybrid V2 + LimitedActivity 的直接数值验证，但可以支持未改变的 SIMIND acquisition/物理接口决策，前提是正文明确标注历史范围。

这些聚合数字的 machine-readable identity 为：

- `docs/evidence/stage2_validation_summary_2026-08-18.json`：SHA-256 `76049f7fe8c25ad45665e4a1faee71c6cd069d8e287fef7f6ca3440efdea1cbc`；
- `docs/evidence/stage3_pilot_summary_2026-08-18.json`：SHA-256 `8e5b746028daff1abfe6ea636b90466469fcb9a06ec73e90d9a143f50069f025`。

表中 Stage 2 定量项以第一份 summary 及其指向的原始控制 artifact 为权威；Stage 3 项以第二份 summary 和具名 run manifest 为权威。若迁移后无法取得 summary 所指向的原始 artifact，论文应把相关数字降级为历史聚合证据，而不是视为重新独立验证。

| Evidence ID | 控制 | 结果摘要 | 允许用途 |
|---|---|---|---|
| `EV-HIST-ORIENT` | orientation | `raw[:,::-1,:]` 误差 1.019 px，对照 27.429 px；ratio 26.93；detector-row residual 0.010 | 支持 canonical projection flip |
| `EV-HIST-TYPE7` | Type-7 attenuation | 目标 μ=0.15 时 readback median 0.1499779；primary/air 推断 μ=0.1495518 | 支持 scoped attenuation interpretation |
| `EV-HIST-FOV` | detector FOV | `/100=160`、`/101=208`、pitch 0.246 cm → 39.36×51.168 cm | 支持 FOV contract |
| `EV-HIST-POINTLINE` | point/line | 300 mm、零衰减 FWHM 17.68 mm vs predicted 17.50 mm；center error 0.753 px | engineering control，不作临床性能外推 |
| `EV-HIST-RRNN` | RR/NN | NN 1/5/10 的 integrated CV 0.0763/0.0352/0.0163 | 支持 NN=10 推荐质量和 weighted expectation 解释 |
| `EV-HIST-STAGE3-100` | old master phantom | 100/100 QC、312 lesions、0 outside/overlap | 仅历史方法对照 |
| `EV-HIST-STAGE3-10` | old SIMIND pilot | 10/10 projection 和 10/10 historical observation | 仅 acquisition/旧 observation 范围 |

旧 Stage3 10 例 manifest SHA 为 `0ae80dc6bcea9d0f6780e9cee7d87472ebcff6a9a4f66318a10b81c6d4f63d61`。旧 Stage3 100 例 manifest SHA 为 `92b1676365c0a8a53ae46e17ddc513b99c52cb757db39ba63faaf7c5678b48d9`。不得把这些数字合并进当前 LimitedActivity 的病例统计。

### 16.3 实验登记与硬门槛

| Experiment ID | 设计/对象 | 预先规定的主要门槛 | 当前结论 |
|---|---|---|---|
| `EXP-GA-100` | Hybrid V2 anatomy-only，100 例 | 所有 hard-QC；无肝外/重叠病灶；指定 replay 一致 | `PASS`；`EV-GA-100` |
| `EXP-DELTA-A` | LimitedActivity 替换，100 例 | source/config 身份一致；指定 anatomy/mask/μ 数组不变；science/replay PASS | `PASS`；`EV-ACT-DELTA-A` |
| `EXP-DELTA-B` | NN=10 server delta，10 distinct positive + repeats + 1 negative | structural/science/FOV/replay/negative/NN guard 全 PASS | `PASS, UNSTRATIFIED`；`EV-ACT-DELTA-B` |
| `EXP-GATE-C-550` | 500 positive + 50 true-negative source cases | complete inventory、case plan、manifest；sealed/eligible | `PASS AT SOURCE-DATA LEVEL`；`EV-DATA-GATE-C-550` |
| `EXP-WIN-REAL-PREPOST` | 同一一正一负，重构前/后，NN=10、worker=1 | 两次完整状态机 PASS；runtime verified | `PASS AT IDENTIFIED COMMITS` |
| `EXP-REFACTOR-EQUIV` | 42 项 frozen comparison | 科学 arrays/ACT/ATN/projection byte equality；命令/roles/QC 等价；`.res` volatile 规则 | `PASS WITH DECLARED .res VOLATILITY` |
| `EXP-CI-MERGE` | merge tree Windows CI | 所有 workflow steps 自行退出 0 | `PASS`；不含真实 SIMIND/可见人工 UI |
| `EXP-WIN-MANUAL` | 真实桌面操作者 checklist | `docs/WINDOWS_V1_ACCEPTANCE.md` 全项签字 | `PENDING` |

任何新增实验必须先在此处写明对象、主要门槛、失败处理和 artifact 命名，再运行；否则只能作为 exploratory evidence，不能事后升级为预先规定的 confirmatory gate。

---

## 17. 可报告结果总表

### 17.1 Hybrid V2 Gate A 100 例

以下数字来自 `EV-GA-100`，可用于当前 anatomy/lesion 方法的描述性结果：

| 指标 | 结果 |
|---|---:|
| cases hard-QC | 100/100 |
| lesions | 329 |
| outside-liver lesion voxels | 0 |
| overlapping lesion voxels | 0 |
| morphology labels | 85 cirrhotic / 15 normal |
| caudate enabled | 94/100 |
| actual liver volume | min 752.634, median 1541.061, max 2299.783 mL |
| left fraction | min 0.232577, median 0.394269, max 0.526993 |
| rejected shape attempts | mean 0.19，max 3 |
| accepted attempt index | mean 1.19，max 4 |
| bitwise replay subset | cases 1/25/50/75/100 all PASS |

解释限制：连续 volume target clip 是 775–2300 mL，rasterized actual minimum 为 752.634 mL；这属于体素化后的测量差异，不应通过删去 minimum 来掩盖，也不能直接解释为真实人群范围。left fraction 的 `[0.15,0.45]` 是 cirrhosis transform 前的 base sample；final target 可到 0.55，因此 observed actual maximum `0.526993` 不与基础范围矛盾。

### 17.2 LimitedActivity Delta 与 Formal550

| 指标 | 结果 | 权威 |
|---|---:|---|
| Delta A cases | 100 | `EV-ACT-DELTA-A` |
| immutable arrays after activity replacement | PASS | `EV-ACT-DELTA-A` |
| Delta B jobs | 13/13 | `EV-ACT-DELTA-B` |
| distinct positive cases | 10 | `EV-ACT-DELTA-B` |
| repeated runs of `case_0012` | total 3 | `EV-ACT-DELTA-B` |
| true-negative jobs | 1 | `EV-ACT-DELTA-B` |
| Formal550 source cases | 500 positive + 50 true-negative | `EV-DATA-GATE-C-550` |
| Formal550 source status | sealed PASS | `EV-DATA-GATE-C-550` |

Formal550 的逐例分布、服务器返回、算法消费和最终统计不在本节复制；应由 `PAR-S_2` evaluator artifacts 提供。

### 17.3 Windows 真实两例

| case | role | top-level seed | RR | NN | projection sum（重构前/后稳定） |
|---|---|---:|---:|---:|---:|
| case 1 | positive | 43 | 4310 | 10 | 1,199,272.8877 |
| case 2 | true-negative | 44 | 6412 | 10 | 925,644.408 |

这两个数仅用于确认同一冻结输入在重构前后产生稳定 projection，不用于估计总体分布、检出性能或阳性/阴性差异。

### 17.4 自动测试事实

候选验收文档记录过：Python 280/280、Ruff PASS、frontend unit 19/19、E2E 6/6、a11y 6/6、visual 61/61、额外 a11y stress 60/60，以及 prepare/mock verify exit 0。之后测试集合又有增加，因此正文若需要精确测试数量，应以某个 CI run 的 machine-readable log 为准，不把旧候选计数写成当前永恒数字。

当前更强的状态事实是：`EV-CI-MERGE` 在与 PR head 内容相同的 merge tree 上整体成功。

---

## 18. 统计与结果生成规则

### 18.1 本项目可做的分析

本项目适合报告：

- anatomy/lesion/activity 参数的实现分布与 hard-QC pass/failure；
- 目标与实际肝体积、left fraction、病灶数量/尺寸/形态、territory、fallback 和 TNR 偏差；
- seed uniqueness/replay；
- ACT/ATN round-trip 与 projection integrity；
- SIMIND runtime、RR/NN、FOV 和 physics-control 指标；
- 运行成功率、阶段耗时、artifact 数量与 storage；
- Windows 软件自动测试、人工任务完成率和错误路径。

### 18.2 不在本项目计算的分析

- reconstruction quality、模型损失、PSNR/SSIM/CRC 等算法指标；
- baseline/ablation 的显著性或置信区间；
- sealed test set 性能；
- subgroup diagnostic performance；
- 临床统计推断。

这些必须由 `PAR-S_2` 的 evaluator 和其总账提供。两个项目合稿时，以稳定 `dataset_id + case_id + manifest SHA` 连接，而不是手工复制 Excel 数字。

### 18.3 统计报告原则

- 连续量默认同时报告样本数、中心和离散程度；分布偏斜时优先 median/IQR，并保留 min/max 作为 QC 范围；
- 分类量报告 numerator/denominator，而不只写百分比；
- 失败、重试与排除必须有 denominator 归属；
- replay 和等价性要区分 bitwise、tolerance-based 和 semantic equality；
- planned analysis 不得先填占位结果；
- 图中任何 aggregate 都要能回到 evaluator 输出和脚本 commit；
- 多项目合并表必须标明每列的 owner 和 numerical authority。

### 18.4 文献、创新性与引用登记

本总账不在没有检索和核验的情况下虚构参考文献。下面登记的是论文写作必须完成的检索流；最终每条引用应保存 DOI/PMID/URL、题名、版本、访问日期、支持的具体句子和核验状态。

| Citation stream | 需要支撑的问题 | 优先来源 | 状态 |
|---|---|---|---|
| `CIT-SPECT-SIM-01` | Monte Carlo SPECT simulation、SIMIND 的方法学来源与适用范围 | `simind-manual-v8` 已核验本地 manual；仍需 SIMIND 原始论文 | `PARTIAL` |
| `CIT-CZT-01` | GE Discovery NM/CT 870 CZT 或相近 CZT SPECT 采集背景 | `ge-870-czt-pds-page10` 已核验厂商 PDF；peer-reviewed validation 待检索 | `PARTIAL` |
| `CIT-TC99M-MU-01` | Tc-99m photopeak 附近组织 attenuation coefficient 的依据 | `nist-xcom-140kev-v2` 已核验；water/fat 为直接 anchor，lung/liver/bone 仍是 declared approximations | `VERIFIED WITH SCOPE` |
| `CIT-PHANTOM-01` | population-based computational phantoms、肝脏形状模型与可复现 synthetic cohorts | 已核验 `perez-radiology-2022-liver-volume`、`seppelt-ship-mri-2022-liver-diameters`、`thanaj-ukbb-2024-liver-shape` 等 component anchors；综合 related-work 检索待做 | `PARTIAL` |
| `CIT-HCC-TARE-01` | HCC/TARE 背景、病灶尺度/activity 异质性与研究动机 | `karger-tare-hcc-2025-*` 与 `ilhan-jnm-2015-hcc-table3` 已核验；临床指南/系统综述待补 | `PARTIAL` |
| `CIT-COUINAUD-01` | Couinaud 分段的解剖学定义，及为何本文只称 proxy | `mise-2014-liver-segments`、`hunt-cirrhosis-lsvr` 已核验为 fraction/directional anchors；标准定义来源待补 | `PARTIAL` |
| `CIT-SYNTH-VALID-01` | synthetic medical data 的 fidelity、utility、bias 与外推风险 | 方法学综述/共识 | `PENDING` |
| `CIT-REPRO-01` | manifests、content hashes、deterministic seeds 和 provenance 的可复现研究原则 | FAIR/provenance/scientific software 规范 | `PENDING` |
| `CIT-MC-NOISE-01` | histories/NN 与 Monte Carlo variance 的关系 | Monte Carlo transport 原始/教科书级来源 | `PENDING` |
| `CIT-ALGORITHM-*` | reconstruction 方法、baselines 与评价指标 | `PAR-S_2` 文献登记 | `EXTERNAL` |

当前 `configs/evidence_registry_v2.json` 已登记并核验的 parameter/component anchors 如下；它们支持各自限定字段，不自动证明整个合成总体具有临床代表性：

| Evidence ID | Locator | 直接支持 | 必须保留的 scope |
|---|---|---|---|
| `karger-tare-hcc-2025-table3-nopvi` | `https://karger.com/lic/article/14/2/158/913513/` | no-PVI tumor count/Dmax/lobe bins（旧 full-V2 profile） | 当前 corrected-master 1–5/10–60 mm 不采用这些旧 bin |
| `karger-tare-hcc-2025-table1-full` | 同上，Table 1 | age median auxiliary、male fraction auxiliary | full cohort，不是 no-PVI subgroup demographics |
| `ilhan-jnm-2015-hcc-table3` | `https://doi.org/10.2967/jnumed.114.150565` | HCC lesion-level TBR/TNR 与 heterogeneity 背景 | 当前 LimitedActivity 使用受控 Uniform 2–8，不是复刻该经验分布 |
| `perez-radiology-2022-liver-volume` | `https://doi.org/10.1148/radiol.2021210531` | adult liver-volume/body-size association | `14×weight+979` 是 upper limit，不作生成均值 |
| `mise-2014-liver-segments` | `https://pmc.ncbi.nlm.nih.gov/articles/PMC4008162/` | normal left-liver fraction anchor | 不支持精确 Couinaud reconstruction |
| `hunt-cirrhosis-lsvr` | `https://pmc.ncbi.nlm.nih.gov/articles/PMC4870102/` | cirrhotic segment-volume directional change | 仅简化 region proxy 的方向锚点 |
| `seppelt-ship-mri-2022-liver-diameters` | `https://doi.org/10.1038/s41598-022-04825-8` | healthy liver joint extents/volume | 轴与体积 joint 使用，不把单轴当独立 predictor |
| `thanaj-ukbb-2024-liver-shape` | `https://doi.org/10.1186/s12880-023-01149-5` | population-scale 3D shape variation | 支持需要 shape variation，不校准当前 CSG 常数 |
| `ozaki-bjr-2016-cirrhosis-morphometry` | `https://doi.org/10.1259/bjr.20150896` | cirrhotic lobar shape direction | aetiology/stage 有差异，当前只取方向性 proxy |
| `rhodes-2021-gallbladder-fossa` | `https://doi.org/10.1371/journal.pone.0257848` | gallbladder-fossa scale/localisation | 用作小型开放脏面凹陷，不作大内部空腔 |
| `saha-2023-porta-hepatis` | `https://doi.org/10.5603/FM.a2022.0047` | porta-hepatis scale/location | constructive geometry anchor |
| `nist-xcom-140kev-v2` | `https://physics.nist.gov/PhysRefData/XrayMassCoef/tab4.html` | water/fat attenuation anchors near 140 keV | lung/liver/bone 仍为 declared approximations |
| `ge-870-czt-pds-page10` | `docs/DOC2109131-NMCT-870-CZT-PDS.pdf#page=10` | collimator catalog/hole geometry | 厂商技术资料，不替代 peer-reviewed system validation |
| `simind-manual-v8` | `docs/simind_manual.pdf` | source histories、index 25/26、flags、RR/NN、rotation/basis | 仍需论文正式 bibliographic citation |

与上述 literature/standard anchor 分开的 engineering evidence IDs 包括 `engineering-patient-joint-model-v2`、`engineering-liver-geometry-v2`、`engineering-ct-degradation-v2`、`decision-cirrhosis-a-20260713` 和当前 Windows v1 contracts。论文的参数表应同时列 source type，不能把 engineering prior 写成文献测得值。

每条最终 citation record 建议采用：

```yaml
citation_id: CIT-...
verified_status: VERIFIED | PENDING | REJECTED
bibliographic_identity: DOI/PMID/title/year
source_locator: URL or reference-manager key
supports_claims: [CL-...]
supported_sentence: "..."
scope_or_caveat: "..."
checked_on: YYYY-MM-DD
```

**创新性判断也必须有比较对象。** 当前只能把下列内容作为 novelty hypothesis，而非不经检索的“首次”：

- `NOV-H1`：将 Hybrid V2 anatomy、corrected-master lesions 与 LimitedActivity 合同统一为单一、严格拒绝旧 profile 的生成路径；
- `NOV-H2`：把 tissue μ、ACT/ATN 字节合同、SIMIND token、projection orientation、case roles 和 SHA provenance 作为同一可审计接口；
- `NOV-H3`：面向原生 Windows 少量本地使用，同时把大批量 Linux 运行作为独立证据线而不混用；
- `NOV-H4`：用分层 Gate 和行为保持 artifact equivalence 连接科学验证与软件重构。

只有在系统检索后确认相关工作差异，才能把 “novel/first” 写进摘要；否则使用 “we developed/implemented/evaluated”。

### 18.5 伦理、隐私、许可与数据治理

Generator 生成的 phantom、mask、activity、μ-map 和模拟 projection 本身是合成数据，不含直接患者标识；但这不自动消除所有研究治理义务。

| Governance ID | 对象 | 当前状态 | 论文/发布要求 |
|---|---|---|---|
| `GOV-SYN-01` | 合成体模与 Formal550 | `SYNTHETIC, PROVENANCE-BOUND` | 说明生成合同、来源先验与 bias；不声称匿名临床患者数据 |
| `GOV-LOCAL-ACQ-01` | 用于 28.4 s/view 技术锚点的本地 10 次 acquisition metadata | `PENDING AUTHOR/INSTITUTIONAL CONFIRMATION` | 核对 IRB/伦理豁免、consent、去标识、使用许可、保留期限和可公开程度 |
| `GOV-SIMIND-01` | SIMIND binary/SMC | `LICENSE-CONSTRAINED` | 代码 release 不分发无权分发的 binary；记录获取与版本/hash 方法 |
| `GOV-LITERATURE-01` | 文献派生 population/physics 参数 | `ATTRIBUTION REQUIRED` | 引用原始来源并区分文献锚点与工程先验 |
| `GOV-OUTPUT-01` | 本地 runs/日志/Windows 信息 | `REDACTION REVIEW PENDING` | 发布 manifest 前检查用户名、绝对路径、机器名和许可文件位置 |

在 `GOV-LOCAL-ACQ-01` 闭合前，本地 10 次 frame-duration 的中位数/范围只能作为内部技术锚点，不进入正式结果或公开 supplement。若无法确认合法使用与披露条件，则从论文中删除该组本地 aggregate，仅保留冻结 nominal protocol 及可公开来源。

---

## 19. 图、表与补充材料计划

| ID | 候选内容 | 数据/证据源 | Owner | 状态 |
|---|---|---|---|---|
| `FIG-1` | 整篇论文跨项目流程和两条 provenance 箭头 | 第 1 节、双方 frozen contract | 共同 | `DRAFT` |
| `FIG-2` | Windows v1 实际管线与阶段状态机 | `PipelineRunner`、第 6 节 | Generator | `READY FOR DRAWING` |
| `FIG-3` | Hybrid V2 anatomy 示例：torso、liver、五个 proxy | `EV-GA-100` 选定病例 | Generator | `PENDING CASE SELECTION` |
| `FIG-4` | lesion/activity/territory 与 1–3 voxel ring 示意 | 受控示例 + LimitedActivity contract | Generator | `PENDING` |
| `FIG-5` | μ-map → ACT/ATN → SIMIND → canonical projection | Windows verified case | Generator | `PENDING ARTIFACT EXPORT` |
| `FIG-6` | Gate A 100 例 anatomy/lesion 描述性分布 | `EV-GA-100` evaluator table | Generator | `PENDING EVALUATOR EXPORT` |
| `FIG-7` | 物理控制：orientation、FOV、Type-7、RR/NN | 历史 scoped artifacts | Generator | `PENDING SCOPE LABELS` |
| `FIG-8+` | reconstruction/algorithm 结果 | `PAR-S_2` ledger | Algorithm | `EXTERNAL` |
| `TAB-1` | 唯一活跃 protocol、锁定项与运行时身份 | 第 5、11、12、13 节 | Generator | `CONTENT READY` |
| `TAB-2` | synthetic population/anatomy 参数 | 第 8 节 + config | Generator | `CONTENT READY, VERIFY AT FREEZE` |
| `TAB-3` | lesions/activity 参数与边界 | 第 9、10 节 | Generator | `CONTENT READY` |
| `TAB-4` | 分层验证证据和允许结论 | 第 16、17 节 | Generator | `CONTENT READY` |
| `TAB-5+` | algorithm comparison/ablation | `PAR-S_2` evaluator | Algorithm | `EXTERNAL` |
| `SUPP-S1` | 完整 config schema 与拒绝边界 | code/config/test export | Generator | `PENDING EXPORT` |
| `SUPP-S2` | manifest schema、seed domains、artifact inventory | verified run | Generator | `PENDING REDACTION REVIEW` |
| `SUPP-S3` | Windows 人工验收 checklist | `EV-WIN-MANUAL` | Generator | `PENDING` |
| `SUPP-S4` | 历史路线与当前路线的谱系 | tags/commits/evidence table | Generator | `DRAFT` |

病例可视化必须先固定 selection rule，避免只选择最好看的案例。建议从预先声明的 replay subset 或由 seed 决定的病例中选取，并记录 case ID、artifact SHA、slice 和 window。

---

## 20. 论文组装映射

| 论文章节 | 本总账来源 | 可直接写的内容 | 仍需补充 |
|---|---|---|---|
| Abstract | 第 1、3、4、17 节 | 问题、生成框架、限定的验证层级 | 算法主结果与最终 release 状态 |
| Introduction | 第 1、3、4.1 节 | 可复现生成、证据边界和跨项目动机 | 文献检索与临床背景 |
| Methods—system | 第 5、6、13–15 节 | 唯一 profile、软件、状态机、配置、provenance | release commit/tag |
| Methods—phantom | 第 7–11 节 | anatomy、lesion、activity、μ-map、I/O | 公式排版和引用 |
| Methods—simulation | 第 11–12 节 | ACT/ATN、SMC、命令、projection QC | SIMIND 正式引用与版本说明 |
| Dataset | 第 7、15–17 节 | roles、split handoff、Formal550 identity | 下游接受/消费 artifact |
| Validation | 第 16–18 节 | Gate A/Delta、物理控制、Windows、CI | 可见人工验收；必要时 final-commit real rerun |
| Algorithm Methods/Results | 外部总账 | 仅通过 Evidence ID 引用 | 全部由 `PAR-S_2` 提供 |
| Discussion | 第 4.1、21 节 | scope、limitations、runtime lanes | 与算法 limitation 合并 |
| Data/Code Availability | 第 2、5、16、22 节 | 当前 merge 状态和 artifact identities | 正式 tag/Release、归档位置和访问策略 |
| Supplement | 第 19 节 | schema、evidence table、acceptance | 自动生成图表和签字记录 |

### 20.1 建议 Methods 最小骨架

1. Software scope and active production profile
2. Synthetic patient and Hybrid V2 liver anatomy
3. Corrected-master lesion generation
4. LimitedActivity v1 and true-negative semantics
5. Physical attenuation and ACT/ATN serialization
6. Native-Windows SIMIND orchestration
7. Seed isolation, provenance and resume protection
8. Generation-side QC and staged validation
9. Formal550 packaging and cross-project handoff

### 20.2 建议 Results 最小骨架

1. Gate A anatomy/lesion feasibility and replay
2. LimitedActivity Delta preservation and scoped SIMIND validation
3. Physics/interface controls
4. Native Windows end-to-end and refactor equivalence
5. Formal550 source dataset sealing
6. Algorithm results（外部项目提供）

---

## 21. 限制、偏离与冲突登记

| ID | 类型 | 事实 | 影响/处理 | 状态 |
|---|---|---|---|---|
| `LIM-01` | population validity | sex/age/BMI/cirrhosis 等含工程先验，未证明临床总体代表性 | 只称 specified synthetic population | `OPEN` |
| `LIM-02` | anatomy semantics | 五个区域无血管树 | 只称 placement proxies | `PERMANENT SCOPE` |
| `LIM-03` | negative semantics | true-negative 不等于 healthy | 正文和图例必须写 lesion-free synthetic control | `PERMANENT SCOPE` |
| `LIM-04` | activity re-QC | reload `phantom_qc` 不完整重算 LimitedActivity ring/TNR | 如需更强主张，增加独立只读 evaluator | `OPEN` |
| `LIM-05` | territory evidence | Delta B 阳性实跑均为 whole liver | 不声称 right/left 已有真实分层 SIMIND 证据 | `OPEN / TEST-COVERED ONLY` |
| `LIM-06` | runtime lanes | Windows 与 Linux 未做全量逐 artifact 等价 | 分开报告，不混用 runtime identity | `OPEN` |
| `LIM-07` | energy nomenclature | 140、140.5 和 SMC 140.0 表述不完全一致 | 暂写 near Tc-99m photopeak | `AUTHOR DECISION NEEDED` |
| `LIM-08` | hidden legacy fields | 底层对象保留兼容字段，如 legacy `mu_fat=0.09`、旧 radii/perfusion/PSF 等 | Methods 只引用活跃实现和有效 config，不从序列化字段猜公式 | `DOCUMENTED` |
| `LIM-09` | profile surplus | population profile 中 >5 tumor count、10–200 mm、necrosis、heterogeneous activity、injection territory 等不控制 Windows v1 | 不写入当前方法 | `DOCUMENTED` |
| `LIM-10` | freeze strength | 某些底层 PipelineConfig 字段由 config hash 约束而非全部硬编码 | 绑定 exact effective config + commit + manifest | `DOCUMENTED` |
| `LIM-11` | consent boundary | unverified-runtime 确认与 >10 例真实执行成本确认位于 Web/CLI 边界；直接 Python `PipelineRunner` 调用不代表用户 UI consent | 对外使用支持入口；自动调用方需提供等价授权治理；manifest 仍标 runtime | `DOCUMENTED` |
| `LIM-12` | local evidence custody | Gate A 完整 run 与 Windows raw real runs 未跟踪 | 删除前独立归档并验证 SHA | `PENDING ARCHIVE` |
| `DEV-01` | process deviation | PR #1 在可见 Windows 人工验收签字前已合并 | 合并视为 integration candidate；人工验收和 release 仍待完成 | `USER-ACKNOWLEDGED` |
| `DEV-02` | final commit coverage | 最后真实 SIMIND 在 `6f684ce`，不是 merge SHA `dd62ba3` | 精确报告 commit；不得称最终 merge commit 已重跑真实 SIMIND | `OPEN` |
| `DEV-03` | protocol marker | post-refactor real manifest 仍含旧 `stage3_protocol_promoted_pilot_pending`；后续代码改成当前 status 并拒绝旧 resume | 数值 artifact 可用于重构/运行证据，但不能证明当前 marker 在真实 run 中写出 | `DOCUMENTED` |
| `DEV-04` | failed historical path | `EV-WIN-FAILED-OBS-20260822` 的真实 expectation 已通过，但旧 offline empirical observation 2/2 angular-CV 失败且 run 未 finalize | 保留失败 artifact；当前路径关闭 observation；不得将失败 run 与通过的 v2 run 合并统计 | `FAILED+SUPERSEDED, PRESERVE` |
| `DOC-CONFLICT-01` | documentation | 某些图示写 ACT/ATN 在 phantom QC 前；实际 runner 相反 | 论文与本总账采用实际状态机 | `RESOLVED HERE` |
| `DOC-CONFLICT-02` | gate naming | 归档 Gate B Linux 分支名易被误读为真实 Gate B 完成 | 用 Delta B artifact 作为真实授权证据 | `RESOLVED HERE` |
| `DOC-CONFLICT-03` | test count | 验收文档的 280 项是候选时点，后来 collection 增加 | 论文引用具名 CI run，不写漂移中的裸计数 | `RESOLVED HERE` |
| `IMP-GAP-01` | production gate | `/api/run/start` 对 config path 调用 `PipelineConfig.from_dict()`；非 `windows_v1` schema 会落入 legacy 构造，`PipelineRunner` 仍可执行/恢复；finalize 路径也未形成统一 legacy read-only gate | 发布前在服务/runner 权威边界拒绝非 Windows v1 的新执行与恢复，保留单独只读查看；增加 API 回归测试 | `OPEN, RELEASE-BLOCKING FOR UNIVERSAL CLAIM` |

### 21.1 软件“接口已经设计好了吗”的准确回答

大部分是：Windows v1 的公开 schema、唯一科学 profile、queue/role、病灶/activity 参数边界、runtime/hash、文件选择、prepare/mock/execute、resume 和 manifest 接口都已设计并实现，合并后自动 CI 已通过。受支持的新建路径是严格的。

但最终审计发现 `IMP-GAP-01`：直接 config-path `/api/run/start` 尚可把非 Windows v1 配置解析成 legacy `PipelineConfig` 并交给 runner。因此当前不能写“所有 Web/API 路径已经从技术上禁止 legacy 新执行/恢复”。这是发布前应修复并加回归测试的接口 gate，不改变本文件所定义的唯一科学权威。

但这不等于所有发布证据都完成。当前仍有四个明确尾项：

1. 可见原生 Windows 文件选择器与异常路径的人工验收；
2. 关闭 `IMP-GAP-01`，确保 legacy 只能只读查看；
3. 是否在最终 release commit 上再跑一次一正一负真实 NN=10 SIMIND；
4. 创建 `v1.0.0` tag/Release 并归档本地原始证据。

---

## 22. Git、发布和保管状态

### 22.1 已核验远端事实

- PR #1 head：`7ca676d2e54c6c9a738308d906c7c75b7b69a7e1`；
- PR #1 merged：2026-08-23T13:34:34Z；
- remote `master`：`dd62ba3f8d2c819bfca3c76090ef7dfe2a69c023`；
- merge-tree CI：success；
- 远端 archive tags：
  - `archive/hybrid-v2-gate-a-20260819` → `921e2e7…`
  - `archive/hybrid-v2-gate-b-linux-20260819` → `3f764c0…`
  - `archive/task12-formal550-v2.0.0` → `6f60d60…`
  - `archive/web-workbench-pre-hybrid-20260822` → `77722bf…`
- 截至核对时：无远端 `v1.0.0`，GitHub Release 数量为 0；
- `pyqt-v0.5-freeze` 仅本地可见，尚未核验远端 tag。

### 22.2 当前本地事实

本文件创建时，本地仍位于 PR head 分支 `codex/windows-hybrid-v2-v1`，本地 remote-tracking ref 尚未同步到远端 merge commit。合并 commit 与 PR head 内容树一致，所以方法审计适用于合并树；Git 状态叙述仍必须区分本地和远端。

以下未跟踪内容在本总账创建前已经存在，属于待保管/清理对象，不由本次文档工作创建：

- `_to_delete/`
- `runs/windows-v1-pre-refactor-real-20260822/`（`EV-WIN-FAILED-OBS-20260822`，有信息量的失败/被取代证据，不是普通垃圾）
- `runs/windows-v1-pre-refactor-real-v2-20260823/`
- `runs/windows-v1-post-refactor-real-20260823/`

按项目治理规则，在完成 closeout 报告并取得用户新的明确删除确认前，不得删除这些目录、历史 worktree 或分支。

### 22.3 Release claim gate

只有以下条件闭合后，本总账才可把 `CL-SG-REL-01` 更新为正式 release：

- `IMP-GAP-01` 已修复，并有非 Windows v1 config 无法启动/恢复/finalize 新生产的自动回归证据；
- 人工验收记录签字；
- release commit 明确；
- 必要时 final-commit real SIMIND rerun 或作者明确接受现有 scoped evidence；
- `v1.0.0` tag 指向精确 commit；
- GitHub/source release、预构建前端、checksums 与验收报告可定位；
- 原始 Gate A/Windows real artifacts 有独立、校验后的归档；
- 所有论文引用的 config/result SHA 已冻结。

---

## 23. 跨项目交接与合稿规则

### 23.1 Generator → Algorithm 的最小连接键

| 字段 | Owner | 用途 |
|---|---|---|
| `dataset_id` | Generator/共同冻结 | 区分 Formal550 版本 |
| `case_id` | Generator | 跨 phantom、projection、reconstruction 对齐 |
| `case_role` | Generator | positive / true-negative |
| `split_role` | 共同合同；算法侧核验 | 防止泄漏和错误消费 |
| generator commit/profile/config SHA | Generator | 生成方法身份 |
| case plan / manifest / inventory SHA | Generator | artifact 集合身份 |
| SIMIND/SMC/runtime backend | 执行方记录 | 仿真身份，不允许 Windows/Linux 模糊化 |
| return ACK | `PAR-S_2` | 算法项目确认收到并验证 |
| evaluator run ID/result SHA | `PAR-S_2` | 算法数字权威 |

### 23.2 合稿时的引用规则

- Generator 方法或生成侧数值：引用本总账 Claim ID + 本项目 artifact；
- Formal550 下游接受、重建、训练与结果：引用 `PAR-S_2` 总账的 Claim/Evidence ID；
- 共享数据表：每列标注 numerical owner；
- 同一数字不得在两个总账各自手工重算；
- 外部总账若仍为 `ACTIVE_DRAFT`，这里只能写“external draft reports…”，不能升级为冻结论文结论；
- 任何跨项目数字不一致时，先比较 manifest/SHA 和 denominator，再修改叙事。

### 23.3 当前外部总账定位

当前审计参考了 `PAR-S_2` 工作树中的同类文档结构：

`D:\PFE-U\PAR-S_2\.codex-worktrees\formal550-final-experiments\docs\paper\MANUSCRIPT_EVIDENCE_LEDGER.md`

其当前状态是工作树内 `ACTIVE_DRAFT`，只用作跨项目结构和外部证据 locator，不是本项目的运行时依赖，也不是本文件内容的自动权威。算法结果在正式合稿前应由该项目给出冻结版本、commit 和 ledger SHA。

---

## 24. 追加式更新日志与待作者决定事项

### 24.1 更新事件格式

新增证据时在本节追加，不覆盖旧事件：

```markdown
### YYYY-MM-DD — <event id>

- Actor:
- Kind / Lifecycle / Evidence:
- Change:
- Source commit/config:
- Artifact locator(s):
- SHA-256:
- Claims affected:
- Manuscript sections affected:
- Limitations/deviations:
- Next gate:
```

### 24.2 已登记事件

#### 2026-08-23 — `LEDGER-INIT-01`

- Actor：Codex（只读审计与文档综合）；
- Kind / Lifecycle / Evidence：`INTERPRETATION / DRAFT / REPO_VERIFIED`；各原始结果的 `ARTIFACT_VERIFIED` 等级由对应 Evidence record 单独表达；
- Change：创建本总账，核对当前管线、参数、Git/CI/release、Gate A/Delta/C、Windows real、历史物理控制和跨项目边界；
- Source snapshot：local PR head `7ca676d…`；remote merged tree `dd62ba3…`，两者 tree 相同；
- Artifact policy：未修改 `PAR-S_2`，未修改/删除原始 run；
- Claims affected：建立第 4 节初始 claim registry；
- Next gate：用户人工 Windows 验收、release 决策、外部算法 ledger 冻结。

#### 2026-08-23 — `DEC-MERGE-BEFORE-MANUAL-01`

- Actor：用户；
- Kind / Lifecycle / Evidence：`DECISION / VERIFIED / USER_APPROVED`；
- Change：PR #1 已合并；用户决定人工验收稍后执行，如有问题再处理；
- Consequence：当前称“merged integration candidate”，不称“manually accepted v1.0.0 release”；
- Claims affected：`CL-SG-REL-01`、`EV-WIN-MANUAL`。

### 24.3 待作者决定

| Decision ID | 问题 | 当前默认安全处理 | 何时必须决定 |
|---|---|---|---|
| `AD-01` | 论文标题和主要 framing 更偏软件平台、数据生成，还是算法方法？ | 采用 S/G → D → M 的统一证据链，不预设标题 | 写摘要前 |
| `AD-02` | 是否把 Windows v1 作为正式 release 名称写入论文？ | 暂写 merged integration candidate | tag/Release 后 |
| `AD-03` | 是否在最终 release commit 重跑一正一负 NN=10？ | 保留 `DEV-02`，精确报告旧 commit | release 冻结前 |
| `AD-04` | 140/140.5 keV 术语如何统一？ | 写 near Tc-99m photopeak，并分别报告数值 | Methods 冻结前 |
| `AD-05` | 是否增加 right/left locked territory 的真实 SIMIND 小型验证？ | 不声称已有真实分层证据 | 若正文强调 territory 物理差异则必须 |
| `AD-06` | 是否增加持久化后独立 LimitedActivity evaluator？ | 披露 reload QC 限制 | 若需强 activity 验证主张则必须 |
| `AD-07` | Gate A 和 Windows raw run 存放到哪里？ | 保留本地、禁止清理 | closeout/release 前 |
| `AD-08` | 哪些病例进入论文图？ | 先冻结 selection rule，不挑最好看的 | 制图前 |
| `AD-09` | 外部算法 ledger 的 frozen commit/SHA 是什么？ | 只写 external draft locator | 合稿前 |
| `AD-10` | 是否在论文保留本地 10 次 acquisition metadata 技术锚点；IRB/豁免、consent、去标识和披露许可由谁确认？ | `GOV-LOCAL-ACQ-01` 闭合前不进入正式结果；无法确认则删除该 aggregate | Results/Data Availability 冻结前 |

---

## 25. 写作前最终检查清单

### 25.1 Methods 冻结

- [ ] `IMP-GAP-01` 已修复，或正文明确披露 protocol authority 与 API enforcement 的差异；
- [ ] release/source commit 与 effective config SHA 已确定；
- [ ] profile/registry/runtime/SMC hashes 已重新核对；
- [ ] anatomy、lesion、activity、μ-map 参数来自活跃实现而非旧 profile surplus；
- [ ] `ZYX`、SAR、voxel、ACT/ATN 和 projection orientation 表述一致；
- [ ] true-negative 和 five-region proxy 的限制写入正文；
- [ ] Windows/Linux evidence lane 分开；
- [ ] 所有公式、单位和 endpoint 语义由实现复核。

### 25.2 Results 冻结

- [ ] 每个数字有 Evidence ID、denominator、artifact 和 SHA；
- [ ] `GOV-LOCAL-ACQ-01` 已由作者/机构闭合，或本地 10 次 acquisition aggregate 已从正式论文结果删除；
- [ ] Gate A、Delta、历史控制、Windows real 和 Formal550 没有混成同一实验；
- [ ] `.res` volatile 差异表述准确；
- [ ] 未用两例 Windows run 推断总体或算法性能；
- [ ] Formal550 服务器返回/算法状态从 `PAR-S_2` frozen ledger 导入；
- [ ] 图表由 evaluator 输出生成，而非手抄本文件。

### 25.3 Release 与可用性

- [ ] `EV-WIN-MANUAL` 已签字，或论文明确写 pending/not performed；
- [ ] `v1.0.0` tag/Release 状态重新核对；
- [ ] Code/Data Availability 与真实可访问位置一致；
- [ ] 数据治理、去标识、伦理/豁免和本地 metadata 披露范围已写入 Data Availability/Ethics；
- [ ] licensed SIMIND binary 的分发限制已说明；
- [ ] 本地未跟踪原始证据已归档并验证 SHA；
- [ ] 总账更新日志记录最终冻结事件；总账 SHA-256 由 Git blob/commit、外部 sidecar 或 handoff manifest 记录，避免在文件内部形成自引用悖论。

---

## 结语：当前可以诚实写到什么程度

当前已经足够开始撰写完整的 Generator Methods、软件架构、生成合同、参数边界、分层验证设计和大部分生成侧 Results。最强而诚实的中心叙述是：项目把高级合成 anatomy、受控 lesion/activity、物理 attenuation、明确的 SIMIND 字节/命令合同以及 artifact-level provenance 统一进一条原生 Windows 活跃管线，并以 Gate A、LimitedActivity Delta、scoped physics controls、Windows 两例真实执行和 Formal550 sealed source artifact 分层证明其可执行性和可追溯性。

当前还不能写成：所有 API 路径均已技术性禁止 legacy 新执行/恢复、完整人工 Windows 发布验收已完成、`v1.0.0` 已发布、Windows 与 Linux 全量等价、三种 territory 都经过真实 SIMIND 分层验证、或算法性能已经由本项目证明。随着接口 gate 修复、人工验收、正式 release 和 `PAR-S_2` evaluator 冻结，这些状态应通过新 Evidence ID 追加，而不是回头改写历史。
