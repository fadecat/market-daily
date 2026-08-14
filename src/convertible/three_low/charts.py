"""可转债三低轮动 模拟盘邮件净值曲线图。

样式仿「投资账本」对比图:
- 策略净值(红) vs 集思录等权指数(蓝),均从首个共同日期归一、以涨跌幅展示
- 最大回撤区间用橙色带高亮,并标注回撤数值

移植自 cb_three_low_email_chart.py:字体解析改用 common.fonts.apply_cjk。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ...common import fonts

NAV_CHART_CID = "cb_three_low_nav_chart"

COLOR_STRATEGY = "#d93025"  # 策略 红
COLOR_BENCHMARK = "#2c7be5"  # 基准 蓝
COLOR_DRAWDOWN = "#f59e0b"  # 回撤区间 橙
FIG_SIZE = (12, 4.5)  # 邮件宽图:跟随正文表格宽度缩放
FIG_DPI = 140
MARKER_MAX_POINTS = 60
MONTH_TICK_MIN_DAYS = 370


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(str(value)[:10])


def _x_axis_padding(d_min: dt.date, d_max: dt.date) -> dt.timedelta:
    """按历史跨度留边界;数据积累期不再强制补成 20 天。"""
    return max(dt.timedelta(days=1), (d_max - d_min) * 0.03)


def _x_date_format(d_min: dt.date, d_max: dt.date) -> str:
    """短跨度显示月-日;超过约一年显示年-月,避免重复标签。"""
    return "%Y-%m" if (d_max - d_min).days >= MONTH_TICK_MIN_DAYS else "%m-%d"


def _marker_for_count(point_count: int) -> Optional[str]:
    """短历史保留数据点;长历史隐藏圆点,避免曲线糊成一条粗线。"""
    return "o" if point_count <= MARKER_MAX_POINTS else None


def generate_nav_chart(
    history: List[Dict[str, Any]],
    output_path: Path,
    *,
    title: str = "成立以来组合净值 vs 集思录等权指数",
    benchmark: Optional[List[Dict[str, Any]]] = None,
    drawdown: Optional[Dict[str, Any]] = None,
) -> Path:
    """生成净值对比图。

    history: 策略持仓历史(含 date/nav)
    benchmark: align_benchmark 的输出 [{"date","strategy_return","benchmark_return"}],
               为 None 时只画策略线(净值口径)
    drawdown: find_max_drawdown_window 的输出 {"peak_date","trough_date","max_drawdown"}
    """
    fonts.apply_cjk(plt)

    fig, ax = plt.subplots(figsize=FIG_SIZE)

    if benchmark:
        dates = [_parse_date(p["date"]) for p in benchmark]
        strategy_pct = [p["strategy_return"] * 100 for p in benchmark]
        benchmark_pct = [p["benchmark_return"] * 100 for p in benchmark]
        marker = _marker_for_count(len(dates))
        ax.plot(dates, strategy_pct, color=COLOR_STRATEGY, linewidth=2, marker=marker,
                markersize=3, label="三低轮动", zorder=3)
        ax.plot(dates, benchmark_pct, color=COLOR_BENCHMARK, linewidth=1.5, marker=marker,
                markersize=2.5, alpha=0.9, label="集思录等权", zorder=2)
        ax.axhline(0.0, color="#9aa0a6", linewidth=1, linestyle="--")
        ax.legend(loc="upper left", frameon=False, fontsize=10)
        ax.set_ylabel("区间涨跌幅 (%)")
    else:
        points = [e for e in history if e.get("nav") is not None]
        dates = [_parse_date(e["date"]) for e in points]
        navs = [float(e["nav"]) for e in points]
        if dates:
            marker = _marker_for_count(len(dates))
            ax.plot(dates, navs, color=COLOR_STRATEGY, linewidth=2, marker=marker, markersize=3)
            ax.fill_between(
                dates, 1.0, navs, where=[v >= 1.0 for v in navs],
                color=COLOR_STRATEGY, alpha=0.08, interpolate=True,
            )
            ax.fill_between(
                dates, navs, 1.0, where=[v < 1.0 for v in navs],
                color="#2E7D32", alpha=0.08, interpolate=True,
            )
            ax.axhline(1.0, color="#9aa0a6", linewidth=1, linestyle="--")
        ax.set_ylabel("组合净值")

    # 最大回撤区间高亮 + 标注
    if drawdown and drawdown.get("trough_date") and dates:
        peak_date = drawdown.get("peak_date")
        # peak_date 为 None 表示起点即最高点,用首个数据日代替
        band_start = _parse_date(peak_date) if peak_date else dates[0]
        band_end = _parse_date(drawdown["trough_date"])
        ax.axvspan(band_start, band_end, color=COLOR_DRAWDOWN, alpha=0.15, zorder=1)
        trough_x = band_end
        trough_idx = min(range(len(dates)), key=lambda i: abs((dates[i] - band_end).days))
        if benchmark:
            trough_y = strategy_pct[trough_idx]
        else:
            trough_y = navs[trough_idx] if dates else 0.0
        dd_pct = float(drawdown["max_drawdown"]) * 100
        ax.annotate(
            f"最大回撤 {dd_pct:.2f}%",
            xy=(trough_x, trough_y),
            xytext=(12, -22),
            textcoords="offset points",
            fontsize=9,
            color="#ffffff",
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.35", fc=COLOR_DRAWDOWN, ec="none", alpha=0.95),
            arrowprops=dict(arrowstyle="-", color=COLOR_DRAWDOWN, lw=1),
            zorder=4,
        )

    ax.set_title(title, fontsize=13)
    ax.grid(True, linestyle=":", alpha=0.4)
    if dates:
        d_min, d_max = min(dates), max(dates)
        pad = _x_axis_padding(d_min, d_max)
        ax.set_xlim(d_min - pad, d_max + pad)
        if len(set(dates)) < 10:  # 数据积累期提示
            ax.text(0.5, 0.03, f"数据积累中(已 {len(set(dates))} 个交易日)",
                    transform=ax.transAxes, ha="center", fontsize=9, color="#9aa0a6")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=8))
    date_format = _x_date_format(min(dates), max(dates)) if dates else "%m-%d"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(date_format))
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=FIG_DPI)
    plt.close(fig)
    return output_path
