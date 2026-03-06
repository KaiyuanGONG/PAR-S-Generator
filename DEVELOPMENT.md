# PAR-S Generator — 开发、运行与打包指南

## 1. 环境要求

| 依赖 | 最低版本 | 说明 |
|:---|:---|:---|
| Python | 3.10+ | 推荐 3.11 |
| pip | 23+ | 用于安装依赖 |
| Windows | 10 / 11 | 桌面应用运行环境 |
| SIMIND | 7.0+ | 需自行申请授权，放入 `simind/` 目录 |

---

## 2. 首次安装

```bash
# 克隆仓库
git clone https://github.com/KaiyuanGONG/PAR-S-Generator.git
cd PAR-S-Generator

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate          # Windows

# 安装依赖
pip install -r requirements.txt
```

---

## 3. 直接运行（开发阶段）

```bash
# 激活虚拟环境后
python main.py
```

> **注意**：首次运行需要约 10–30 秒加载 PyQt6 和 NumPy。

---

## 4. 修改代码后的工作流

```
修改代码 → 保存文件 → 重新运行 python main.py
```

无需编译步骤，Python 是解释型语言，直接运行即可看到效果。

**核心文件位置：**

| 文件 | 作用 |
|:---|:---|
| `src/core/phantom_generator.py` | 体模生成核心逻辑（PhantomConfig + PhantomGenerator） |
| `src/core/batch_runner.py` | 批量生成后台线程（BatchWorker） |
| `src/core/interfile_writer.py` | npz → Interfile 格式转换 |
| `src/ui/pages/phantom_page.py` | Phantom 页面 UI |
| `src/ui/pages/simulation_page.py` | Simulation 页面 UI |
| `src/ui/pages/results_page.py` | Results 页面 UI |
| `src/ui/pages/settings_page.py` | Settings 页面 UI |
| `src/ui/widgets/slice_viewer.py` | 三平面切片 + 3D 可视化组件 |
| `resources/styles/dark_theme.qss` | 全局样式表（深色科学风格） |
| `simind/czt_ge.smc` | GE NM/CT 870 CZT 默认 SIMIND 配置 |

---

## 5. 打包为 Windows exe

打包**必须在 Windows 上执行**（Linux/macOS 无法生成 Windows exe）。

### 5.1 安装 PyInstaller

```bash
pip install pyinstaller
```

### 5.2 执行打包

```bash
# 使用项目自带的 spec 文件（推荐）
pyinstaller build_windows.spec

# 或者一键命令（不使用 spec）
pyinstaller --onedir --windowed --name "PAR-S Generator" ^
    --add-data "resources;resources" ^
    --add-data "simind;simind" ^
    --hidden-import scipy.ndimage ^
    --hidden-import scikit_image ^
    main.py
```

### 5.3 打包输出

```
dist/
└── PAR-S Generator/
    ├── PAR-S Generator.exe   ← 主程序
    ├── simind/
    │   ├── simind.exe        ← 需要用户自行放置
    │   └── czt_ge.smc
    └── resources/
        └── styles/
            └── dark_theme.qss
```

> **关于 SIMIND**：SIMIND 由 Lund 大学免费提供，需在官网下载。
> 下载地址：https://www.msf.lu.se/en/research/simind-monte-carlo-program/downloads
> 下载后将 `simind.exe` 放入 `simind/` 目录即可。

### 5.4 打包常见问题

| 问题 | 解决方法 |
|:---|:---|
| `ModuleNotFoundError: scipy` | 在 spec 文件中添加 `--hidden-import scipy.ndimage` |
| `ModuleNotFoundError: skimage` | 添加 `--hidden-import skimage.measure` |
| 窗口一闪而过 | 去掉 `--windowed` 参数先用控制台模式调试 |
| 体积过大（>500 MB） | 使用 `--onedir` 而非 `--onefile`，前者启动更快 |

---

## 6. 依赖清单说明

```
PyQt6          — GUI 框架
numpy          — 数组计算（体模生成核心）
scipy          — 高斯滤波（gaussian_filter）
scikit-image   — 3D marching cubes（等值面可视化）
matplotlib     — 统计图表
pyqtgraph      — 实时交互可视化
```

---

## 7. 项目结构

```
PAR-S-Generator/
├── main.py                     ← 入口
├── requirements.txt
├── build_windows.spec          ← PyInstaller 配置
├── DEVELOPMENT.md              ← 本文件
├── README.md
├── LICENSE                     ← MIT
├── simind/
│   ├── simind.exe              ← 用户自行放置
│   └── czt_ge.smc              ← GE NM/CT 870 CZT 默认配置
├── src/
│   ├── core/
│   │   ├── phantom_generator.py
│   │   ├── batch_runner.py
│   │   └── interfile_writer.py
│   └── ui/
│       ├── main_window.py
│       ├── pages/
│       │   ├── phantom_page.py
│       │   ├── simulation_page.py
│       │   ├── results_page.py
│       │   └── settings_page.py
│       └── widgets/
│           ├── slice_viewer.py
│           └── param_widgets.py
├── resources/
│   └── styles/
│       └── dark_theme.qss
└── output/                     ← 生成数据（gitignored）
    ├── syn3d/                  ← .npz 体模文件
    ├── interfile/              ← .h33 + .i33 文件
    └── simind/                 ← SIMIND 输出
```

---

## 8. 开源贡献说明

本项目使用 MIT 许可证，欢迎 Pull Request。

```bash
# 提交代码
git add .
git commit -m "feat: describe your change"
git push origin main
```
