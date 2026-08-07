"""商品极值板块:邮件 HTML 渲染。

移植自 commodity-monitor-days 的 reporting.py,精简:只保留邮件表格渲染
(build_email_html),去掉 v1/v2 markdown 报告、多周期共振判定与企业微信渲染。
渲染辅助改用本仓库 common.email 的 render_markdown/render_table,样式与其余板块统一。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..common.email import render_markdown, render_table
from .config import MonitorConfig
from .core import SymbolResult


@dataclass(frozen=True)
class ReportSummary:
    scanned: int
    success: int
    failed: int
    high_alerts: int
    low_alerts: int
    alert_symbols: int
    stale_symbols: int


def _fmt_price(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 10000:
        return f"{value:.0f}"
    if abs_value >= 1000:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if abs_value >= 100:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if abs_value >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.6f}".rstrip("0").rstrip(".")


# 板块映射(按品种代码根归类)
ENERGY_CHEM = {
    "CL", "NG", "OIL", "SC", "FU", "BU", "LU", "PG",
    "TA", "MA", "EG", "SA", "UR", "L", "PP", "V",
}
METALS = {
    "GC", "SI", "HG", "AHD", "CAD", "NID", "PBD", "SND", "ZSD",
    "AU", "AG", "CU", "AL", "ZN", "NI", "SN", "PB", "AO", "LC",
}
BLACKS = {"ZC", "JM", "J", "I", "RB", "HC", "SS", "SF"}
AGRI = {
    "C", "S", "W", "BO", "SM", "CT", "FCPO", "RSS",
    "M", "Y", "P", "RM", "OI", "A", "SR", "CF", "AP", "PK",
}


def _normalize_code_root(code: str) -> str:
    return re.sub(r"\d+$", "", code.upper())


def _section_name(code_root: str) -> str:
    if code_root in ENERGY_CHEM:
        return "能源与化工"
    if code_root in METALS:
        return "有色贵金属"
    if code_root in BLACKS:
        return "黑色建材"
    if code_root in AGRI:
        return "农产品"
    return "其他"


def _section_name_for_symbol(result: SymbolResult) -> str:
    code = result.symbol.code.upper()
    # "SM" 同时存在于 CBOT 豆粕(外盘)与 CZCE 锰硅(内盘)
    if code == "SM":
        return "农产品"
    if code == "SM0":
        return "黑色建材"
    return _section_name(_normalize_code_root(code))


def _is_stale(result: SymbolResult, stale_days_threshold: int) -> bool:
    return (
        result.error is None
        and result.stale_days is not None
        and result.stale_days > stale_days_threshold
    )


_EMAIL_WINDOW_LABEL_CN = {
    "d21": "21日",
    "d63": "63日",
    "y1": "1年",
    "y3": "3年",
    "y5": "5年",
    "y10": "10年",
}


def _email_window_label(label: str) -> str:
    return _EMAIL_WINDOW_LABEL_CN.get(label, label)


def _email_trend_cell(result: SymbolResult) -> tuple[str, str]:
    highs = result.high_windows or []
    lows = result.low_windows or []
    highs_cn = [_email_window_label(w) for w in highs]
    lows_cn = [_email_window_label(w) for w in lows]
    if highs and not lows:
        return "🔴", f"高位[{', '.join(highs_cn)}]"
    if lows and not highs:
        return "🟢", f"低位[{', '.join(lows_cn)}]"
    if highs and lows:
        return "🔴", f"高位[{', '.join(highs_cn)}] / 低位[{', '.join(lows_cn)}]"
    return "⚪", "无告警"


def _email_pct_cell(value: float | None, hit_direction: str | None) -> str:
    if value is None:
        return '<span style="color:#888">NA</span>'
    text = f"{value:.0f}%"
    if hit_direction == "high":
        return f'<b><span style="color:#D93026">{text}</span></b>'
    if hit_direction == "low":
        return f'<b><span style="color:#1AAD19">{text}</span></b>'
    return text


def _email_row_spec(result: SymbolResult, window_order: list[str]) -> dict:
    highs = set(result.high_windows or [])
    lows = set(result.low_windows or [])
    emoji, trend = _email_trend_cell(result)
    pct = result.window_percentiles or {}
    price_text = _fmt_price(result.latest_price) if result.latest_price is not None else "--"
    cells = [emoji, str(result.symbol.name), str(result.symbol.code), price_text, trend]
    for label in window_order:
        direction = "high" if label in highs else "low" if label in lows else None
        cells.append(_email_pct_cell(pct.get(label), direction))
    return {"cells": cells}


def build_email_html(
    results: list[SymbolResult],
    cfg: MonitorConfig,
    stale_days_threshold: int = 5,
) -> tuple[list[str], ReportSummary]:
    """渲染商品极值邮件正文片段列表(供 compose_sections 拼装)。"""
    today_cn = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    window_order = list(cfg.windows.keys())

    success_items = [r for r in results if r.error is None]
    failed_items = [r for r in results if r.error is not None]
    stale_items = [r for r in success_items if _is_stale(r, stale_days_threshold)]
    alert_raw = [r for r in success_items if (r.high_windows or r.low_windows)]
    alert_items = [r for r in alert_raw if not _is_stale(r, stale_days_threshold)]

    grouped: dict[str, list[SymbolResult]] = {
        "能源与化工": [],
        "黑色建材": [],
        "有色贵金属": [],
        "农产品": [],
        "其他": [],
    }
    for item in alert_items:
        grouped[_section_name_for_symbol(item)].append(item)

    high_alerts = sum(1 for r in alert_items if r.high_windows)
    low_alerts = sum(1 for r in alert_items if r.low_windows)
    summary = ReportSummary(
        scanned=len(results),
        success=len(success_items),
        failed=len(failed_items),
        high_alerts=high_alerts,
        low_alerts=low_alerts,
        alert_symbols=len(alert_items),
        stale_symbols=len(stale_items),
    )

    headers = ["状态", "品种", "代码", "最新价", "告警"] + [
        _email_window_label(w) for w in window_order
    ]

    html_parts: list[str] = [
        render_markdown(
            f"**商品极值监控日报** ({today_cn})\n"
            f"> 监控规则: 高位区 >= {cfg.thresholds.high_percentile:.0f}%, "
            f"低位区 <= {cfg.thresholds.low_percentile:.0f}%"
        )
    ]

    section_order = [
        ("能源与化工", "🛢"),
        ("黑色建材", "🏗"),
        ("有色贵金属", "👑"),
        ("农产品", "🌾"),
        ("其他", "🧩"),
    ]
    has_any = False
    for sec, icon in section_order:
        items = grouped[sec]
        if not items:
            continue
        has_any = True
        html_parts.append(render_markdown(f"{icon} **【{sec}】**"))
        html_parts.append(render_table(
            headers,
            [_email_row_spec(item, window_order) for item in items],
        ))

    if not has_any:
        html_parts.append(render_markdown("✅ 本次无有效告警。"))

    html_parts.append(render_markdown(
        '<font color="comment">'
        f"系统信息: 扫描完成({summary.scanned}个品种), "
        f"有效告警{summary.alert_symbols}个, 高位{summary.high_alerts}个, "
        f"低位{summary.low_alerts}个, 剔除过期数据{summary.stale_symbols}个, "
        f"抓取失败{summary.failed}个。"
        "</font>"
    ))

    return html_parts, summary
