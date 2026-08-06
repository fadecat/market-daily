"""CJK 字体解析(供 matplotlib 图表统一使用)。

CI(Ubuntu)装 ``fonts-noto-cjk``,开发机(Windows)用 Microsoft YaHei/SimHei。
"""
from __future__ import annotations

from matplotlib import font_manager

PREFERRED_FONT_FAMILIES = [
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Microsoft YaHei",
    "SimHei",
    "WenQuanYi Zen Hei",
    "PingFang SC",
    "sans-serif",
]


def resolve_font() -> str:
    """返回系统里第一个可用的 CJK 字体族名,找不到则回退 sans-serif。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for family in PREFERRED_FONT_FAMILIES:
        if family in available:
            return family
    return "sans-serif"


def apply_cjk(plt) -> str:
    """设置 matplotlib rcParams 使用 CJK 字体,返回所选字体族名。"""
    font = resolve_font()
    plt.rcParams["font.family"] = font
    plt.rcParams["axes.unicode_minus"] = False
    return font
