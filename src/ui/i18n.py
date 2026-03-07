"""
Bilingual support for PAR-S Generator  (English / Chinese).

Usage:
    from ui.i18n import tr, init_language

    # At app startup (before any window is created):
    init_language()

    # In widget constructors:
    self.setWindowTitle(tr("Settings"))

Language change requires a restart to take effect.
"""

from __future__ import annotations
from PyQt6.QtCore import QSettings

_LANG: str = "en"

# Key = English string.  Value = {"zh": "Chinese translation"}.
# Only strings that differ between languages need entries.
_STRINGS: dict[str, dict[str, str]] = {
    # ── Navigation ──────────────────────────────────────────────────
    "Phantom":    {"zh": "体模"},
    "Simulation": {"zh": "仿真"},
    "Results":    {"zh": "结果"},
    "Settings":   {"zh": "设置"},
    "WORKFLOW":   {"zh": "工作流"},

    # ── Phantom page ────────────────────────────────────────────────
    "Phantom Configuration":    {"zh": "体模配置"},
    "Preview":                  {"zh": "预览"},
    "No phantom generated yet": {"zh": "尚未生成体模"},
    "⬡  Preview Single Case":  {"zh": "⬡  预览单个案例"},
    "▶  Start Batch":          {"zh": "▶  开始批量生成"},
    "Generating...":            {"zh": "生成中..."},
    "Save Config":              {"zh": "保存配置"},
    "Load Config":              {"zh": "加载配置"},
    "VOLUME":                   {"zh": "体积"},
    "LIVER GEOMETRY":           {"zh": "肝脏几何"},
    "TUMORS":                   {"zh": "肿瘤"},
    "ACTIVITY":                 {"zh": "活度"},
    "BATCH GENERATION":         {"zh": "批量生成"},
    "Matrix (NxNxN)":           {"zh": "矩阵 (NxNxN)"},
    "Voxel size (mm)":          {"zh": "体素尺寸 (mm)"},
    "Scale jitter":             {"zh": "尺度抖动"},
    "Rotation jitter (°)":      {"zh": "旋转抖动 (°)"},
    "Global shift range":       {"zh": "全局偏移范围"},
    "Target left ratio":        {"zh": "目标左叶比例"},
    "Smoothing \u03c3 (px)":    {"zh": "平滑 \u03c3 (像素)"},
    "Min tumors":               {"zh": "最少肿瘤数"},
    "Max tumors":               {"zh": "最多肿瘤数"},
    "Contrast min (T/L)":       {"zh": "最低对比度 (T/L)"},
    "Contrast max (T/L)":       {"zh": "最高对比度 (T/L)"},
    "Total counts (\xd710\u2074)": {"zh": "总计数 (\xd710\u2074)"},
    "PSF \u03c3 (px)":          {"zh": "PSF \u03c3 (像素)"},
    "Residual BG":              {"zh": "残余本底"},
    "Number of cases":          {"zh": "案例数量"},
    "Global seed":              {"zh": "全局随机种子"},
    "Use fixed seed":           {"zh": "使用固定种子"},
    "Output directory":         {"zh": "输出目录"},
    "Browse...":                {"zh": "浏览..."},
    "Liver Vol.":               {"zh": "肝脏体积"},
    "Left Ratio":               {"zh": "左叶比例"},
    "Tumors":                   {"zh": "肿瘤数"},
    "Total Counts":             {"zh": "总计数"},
    "Gen. Time":                {"zh": "生成时间"},

    # ── Simulation page ─────────────────────────────────────────────
    "Simulation Pipeline":      {"zh": "仿真流程"},
    "STEP 1 \u2014 FORMAT CONVERSION (npz \u2192 .bin)":
                                {"zh": "步骤1 \u2014 格式转换 (npz \u2192 .bin)"},
    "STEP 2 \u2014 SIMIND CONFIGURATION":
                                {"zh": "步骤2 \u2014 SIMIND 配置"},
    "STEP 3 \u2014 GENERATE & RUN":
                                {"zh": "步骤3 \u2014 生成 & 运行"},
    "Console":                  {"zh": "控制台"},
    "SIMIND Preview":           {"zh": "SIMIND 预览"},
    "Convert All Cases":        {"zh": "转换所有案例"},
    "Generate .bat Script":     {"zh": "生成 .bat 脚本"},
    "\u25b6  Run SIMIND Now":   {"zh": "\u25b6  立即运行 SIMIND"},
    "\u25a0  Stop":             {"zh": "\u25a0  停止"},

    # ── Results page ─────────────────────────────────────────────────
    "Batch Generation & Results": {"zh": "批量生成与结果"},
    "Load Existing Summary":    {"zh": "加载已有摘要"},
    "\u25b6  Start Batch Generation": {"zh": "\u25b6  开始批量生成"},
    "Statistics Charts":        {"zh": "统计图表"},
    "Case Table":               {"zh": "案例表格"},
    "Log":                      {"zh": "日志"},
    "SIMIND Output":            {"zh": "SIMIND 输出"},

    # ── Settings page ────────────────────────────────────────────────
    "SIMIND CONFIGURATION":     {"zh": "SIMIND 配置"},
    "DEFAULT PATHS":            {"zh": "默认路径"},
    "PERFORMANCE":              {"zh": "性能"},
    "APPEARANCE":               {"zh": "外观"},
    "ABOUT":                    {"zh": "关于"},
    "Batch threads:":           {"zh": "批量线程数:"},
    "Auto-save config on batch start": {"zh": "批量开始时自动保存配置"},
    "Theme:":                   {"zh": "主题:"},
    "Dark":                     {"zh": "深色"},
    "Light":                    {"zh": "浅色"},
    "Language:":                {"zh": "语言:"},
    "Save Settings":            {"zh": "保存设置"},
    "Reset to Defaults":        {"zh": "恢复默认"},
    "Settings saved successfully.": {"zh": "设置已成功保存。"},
    "Settings reset to defaults.":  {"zh": "设置已恢复默认。"},
    "Language change will apply on next restart.":
                                {"zh": "语言更改将在下次重启后生效。"},
    "Browse":                   {"zh": "浏览"},
    "Saved":                    {"zh": "已保存"},
    "Reset":                    {"zh": "已重置"},
}


def init_language() -> None:
    """Load saved language preference.  Call once before creating any window."""
    global _LANG
    s = QSettings("PAR-S", "Generator")
    _LANG = str(s.value("appearance/language", "en"))


def set_language(lang: str) -> None:
    """Persist a new language choice ('en' or 'zh')."""
    global _LANG
    _LANG = lang
    QSettings("PAR-S", "Generator").setValue("appearance/language", lang)


def current_language() -> str:
    return _LANG


def tr(text: str) -> str:
    """Return translated text for the current language, or the original string."""
    if _LANG == "en":
        return text
    entry = _STRINGS.get(text)
    if entry is None:
        return text
    return entry.get(_LANG, text)
