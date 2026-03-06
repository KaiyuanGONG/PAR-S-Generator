# smc完整配置单及流程

### 纯净版 `ge870_czt.smc` 配置清单

打开终端，输入 `change`，按以下参数设置：

#### 1. 主菜单 (Main Menu)

- **10 - Crystal material** -> 输入 **`czt`**
- **14 - Energy-resolution file** -> 输入 **`none`**

#### 2. 基础与能窗参数 (Index 1-28)

- **01** (Photon energy) = **140** * **05** (Phantom half-length) = **28.285** * **08** (Crystal half-length) = **25.585** * **09** (Crystal thickness) = **0.725** * **10** (Crystal half-width) = **19.68** * **12** (Radius of rotation) = **25.0** * **14** (Phantom type) = **-7** * **15** (Source type) = **-7** * **20** (Upper window threshold) = **154.0** (直接在这里设置主窗上限 )
- **21** (Lower window threshold) = **126.0** (直接在这里设置主窗下限 )
- **22** (Energy resolution) = **6.3** * **23** (Intrinsic resolution) = **0.246** * **28** (Pixel size simulated image) = **0.442** #### 3. SPECT 与 体模参数 (Index 29-45)
- **29** (SPECT projections) = **60**
- **30** (SPECT Rotation) = **2** * **31** (Pixel size in density) = **0.442**
- **34** (Number of density images) = **128**
- **41** (SPECT starting angle) = **180**

#### 4. GE WEHR CZT 准直器参数 (Index 46-60)

- **46** (Hole size X) = **0.226** * **47** (Hole size Y) = **0.226**
- **48** (Distance between holes X) = **0.02** * 
- **49** (Distance between holes Y) = **0.02**
- **52** (Collimator thickness) = **4.5** * **54** (Hole shape) = **4** #### 5. 矩阵与评分程序 (Index 76-84)
- **76 & 77** (Matrix size image) = **128**
- **78 & 79** (Matrix size density/source I) = **128**
- **81 & 82** (Matrix size density/source J) = **128**
- **84** (Scoring routine) = **0** (关闭多能窗程序，改回默认标准程序 )

#### 6. CZT 独有硬件参数 (Index 91-101)

- **91** (Voltage) = **600**
- **92** (Mobility electrons) = **5.0**
- **93** (Mobility holes) = **0.4**
- **94** (Contact pad size) = **0.16**
- **95** (Anode pitch) = **0.246**
- **98** (Energy resolution model) = **-2** #### 7. 模拟开关配置 (Change simulation flags)
- **Flag 5** (Simulate SPECT) = **True**
- **Flag 11** (Interactions in phantom) = **True** * **Flag 12** (Energy resolution) = **True** * **Flag 14** (Write interfile) = **False** (生成 .mhd 格式头文件 )

👉 **在主菜单选择 `4`，保存为 `ge870_czt.smc`。**

自动检测 .smc 路径改为 simind/ge870_czt.smc