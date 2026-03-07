# smc完整配置单及流程

### 纯净版 `ge870_czt.smc` 配置清单

打开终端，输入 `change`，按以下参数设置：

#### 1. 主菜单 (Main Menu)

- **10 - Crystal material** -> 输入 **`czt`**
- **14 - Energy-resolution file** -> 输入 **`none`**

#### 2. 基础与能窗参数 (Index 1-28)

都设置结束后 在 `Enter Index Number >` 的提示符处，直接输入 **`\*standard`** 并回车！让程序重新计算内部几何关联。

- **01** (Photon energy) = **140**
- **Index 2** (Source half-width) = **28.285**
- **Index 3** (Source half-height) = **28.285**
- **Index 4** (Source half-length) = **28.285**
- **05** (Phantom half-length) = **28.285**
- ==**06** (Phantom half-length) = **28.285**==
- ==**07** (Phantom half-length) = **28.285**==
- **08** (Crystal half-length) = **25.585**
- **09** (Crystal thickness) = **0.725**
- **10** (Crystal half-width) = **19.68**
- ==**12** (Radius of rotation) = **30.0**== # 相机的旋转半径设在了 25 cm。这意味着**探测器完全嵌在病人的肉里面转！**
- **14** (Phantom type) = **-7**
- **15** (Source type) = **-7**
- **20** (Upper window threshold) = **154.0** (直接在这里设置主窗上限 )
- **21** (Lower window threshold) = **126.0** (直接在这里设置主窗下限 )
- **22** (Energy resolution) = **6.3**
- ==**23** (Intrinsic resolution) = **0**==
- **28** (Pixel size simulated image) = **0.442** 

#### 3. SPECT 与 体模参数 (Index 29-45)
- **29** (SPECT projections) = **60**
- **30** (SPECT Rotation) = **2**
- **31** (Pixel size in density) = **0.442**
- **34** (Number of density images) = **128**
- **41** (SPECT starting angle) = **180**
- ==**<u>*Index 42（Orbital rotation fraction）= 1*</u>**==

#### 4. GE WEHR CZT 准直器参数 (Index 46-60)

- **46** (Hole size X) = **0.226**
- **47** (Hole size Y) = **0.226**
- **48** (Distance between holes X) = **0.02** 
- **49** (Distance between holes Y) = **0.02**
- **52** (Collimator thickness) = **4.5** 
- ==**53 (Hole shape) = 1**==  **！！！改成解析准直器 设置为0 ！！！**
- **54** (Hole shape) = **4** 
#### 5. 矩阵与评分程序 (Index 76-84)
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
- ==**96** (Tau decay constant ) = **0.4**==
- ==**97 = 1**==
- **98** (Energy resolution model) = **-2** 
- ==**99 = 0.225**==
- ==**100 = 128 #旧的208输出有问题，直接全改成128**==
- ==**101 =128 # 同理 160**==

#### 7. 模拟开关配置 (Change simulation flags)
- ==**Flag 4** (Include the collimator) = **True**==
- **Flag 5** (Simulate SPECT) = **True**
- **Flag 11** (Interactions in phantom) = **True**
- **Flag 12** (Energy resolution) = **True**
- **Flag 14** (Write interfile) = **False** (生成 .mhd 格式头文件 )

👉 **在主菜单选择 `4`，保存为 `ge870_czt.smc`。**

自动检测 .smc 路径改为 simind/ge870_czt.smc

 每个病例的活度图/衰减图（命令行传入，运行时才指定）

  这类文件每个 case 都不同，所以不在 smc 里——通过命令行 /FS: 和 /FD: 在运行时传入：
  simind.exe ge870_czt "output\case_0001" /FS:"binary\case_0001_act" /FD:"binary\case_0001_atn"



手动运行：
simind ge870_czt.smc case_0000 /FD:case_0000 /FS:case_0000 /NN:50