# PAR-S Generator — 项目记忆

## 项目概述
Windows 桌面应用（PyQt6），用于生成肝脏 SPECT 合成数据集：
体模生成 → Interfile 转换 → SIMIND 蒙特卡洛仿真 → 训练数据集

## 关键文件结构
- `src/core/phantom_generator.py` — 核心体模生成（PhantomConfig, PhantomGenerator, Geometry3D）
- `src/core/interfile_writer.py` — npz→Interfile 转换 + SIMIND .bat 生成
- `src/core/batch_runner.py` — 批量生成 QThread 工作线程
- `src/ui/` — PyQt6 GUI（main_window, 4 pages, widgets）
- `simind/simind.exe` — SIMIND 可执行文件（需用户下载）
- `simind.smc` — SMCV2 格式 .smc 配置文件（项目根目录）
- `simind/czt_ge.smc` — 参数说明文档（非 SIMIND 可读格式）

## 已修复的 Bug（v2.0→v2.1）
1. `interfile_writer.py` — 完全重写：
   - 文件命名：`case_XXXX_act_av.bin` / `case_XXXX_atn_av.bin`（SIMIND XcatBinMap 要求 `_av` 后缀）
   - bat 脚本：pushd 到 binary 目录，copy smc 到当前目录，`/FS:case_XXXX /FD:case_XXXX`（纯 stem，无路径/无后缀）
   - 这样避免 SIMIND 把路径中的反斜杠误解为参数分隔符
2. `phantom_generator.py` — Cantlie 二分法方向修正（之前写反了），移除 PSF
3. `batch_runner.py` — start_id 从 0 改为 1（case_0001 开始）
4. `results_page.py` — 新增 SimindOutputViewer（.a00 投影+sinogram 可视化），配置刷新修复
5. `main_window.py` — 用 config_getter 替代缓存配置（Bug 1+2 根本修复）

## 待处理
- `mu_diaphragm` 参数存在但横膈膜几何体未实现（Issue-6）
- Settings 页线程数 UI 无效（Issue-9）
- Total counts 太小（8e4）导致 SIMIND 仿真无计数，建议用 1e7

## SIMIND 关键信息（已验证）
- 文件命名：`_act_av.bin` 和 `_atn_av.bin`（不可更改，SIMIND 强制要求）
- 调用：`simind ge870_czt "abs_out_stem" /FS:case_XXXX /FD:case_XXXX`（相对 stem）
- 必须 pushd 到 binary 目录运行，并将 smc copy 到该目录
- Index-14=-7, Index-15=-7 = XcatBinMap 格式（float32 C-order，Z,Y,X）
- Index-26 单位：百万次/投影（v4.5+），值=10 → 10M次/投影
- SIMIND 输出：`.a00`（主窗投影 60×128×128）、`.mhd`、`.res`、`.spe`
- 训练数据读取：`np.fromfile("case.a00", dtype=np.float32).reshape(60,128,128)`

## 已生成的文件
- `notebook/pipeline_overview.ipynb` — 完整流程可视化 notebook
- `docs/技术路线审查与纠错报告.md` — v2.0 问题清单 + 配置指南（含 ge870_czt.smc 参数表）

## 用户偏好
- 中文沟通
- 严格按照 SIMIND 手册操作
