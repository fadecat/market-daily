"""市场估值板块图表:估值分位(PE 走势)图 + 汇率图。

移植自 ``prototype_valuation_percentile_chart.py`` 与 ``prototype_fx_chart.py``。渲染
(PALETTE / AX_BOUNDS / _draw_*)保持原样;改动:

- 字体:原 ``pick_available_font_family`` + rcParams 改用 ``common.fonts.apply_cjk``。
- 取数:接收预取数据,避免重复请求。
  - 估值图只需 PE 历史时序(index_code/name 来自已取好的估值 item,不再重复拉 metrics)。
    ``pe_history`` 可由 run.py 预取(``fetch.fetch_index_pe_history_with_archive_fallback``)
    传入;为 None 时本函数自取(带归档回退)。
  - 估值图可叠股债收益差右轴:``bond_history`` 传入 10Y 国债历史
    (``fetch.fetch_cn_10y_bond_history_with_archive_fallback``,date/yield_pct)即画,
    缺省或样本不足则不画,不影响 PE 主图。
  - 汇率图 ``fx_history`` 可预取(``fetch.fetch_fx_history_with_archive_fallback``)传入;
    为 None 时自取。
- ``md.*`` -> ``fetch.*``;``now_in_beijing`` 自 ``fetch`` 导入。
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.ticker import FormatStrFormatter
import numpy as np
import pandas as pd

from ..common.fonts import apply_cjk
from .fetch import (
    fetch_fx_history_with_archive_fallback,
    fetch_index_pe_history_with_archive_fallback,
    now_in_beijing,
    resolve_target_index_code,
)

# ---------- 共享样式 ----------

FIGURE_DPI = 180
FIGURE_SIZE = (14, 5.2)
AX_BOUNDS = {
    "chart": [0.07, 0.16, 0.90, 0.70],
    "footer": [0.04, 0.03, 0.92, 0.07],
}
PALETTE = {
    "background": "#ffffff",
    "orange": "#ed7c2b",
    "line": "#ed7c2b",
    "spread": "#3a6ea5",
    "text_primary": "#1f1f1f",
    "text_muted": "#8a8a8a",
    "grid": "#f0f0f0",
    "spine": "#d0d0d0",
    "pct_low": "#2f9e4f",
    "pct_mid": "#9aa0a6",
    "pct_high": "#d94f3a",
}
FONT_SIZES = {
    "legend": 12,
    "main_title": 14,
    "y_tick": 10,
    "x_tick": 11,
    "latest_label": 11,
    "footer": 9,
}


# ---------- 估值分位(PE 走势)图 ----------


def _build_history_frame(
    item: Dict, pe_history: Optional[pd.DataFrame] = None
) -> Tuple[pd.DataFrame, Optional[str]]:
    index_code = str(item.get("index_code") or item.get("code") or "").strip()
    valuation_url = str(
        item.get("index_valuation_percentile_source")
        or item.get("index_valuation_percentile_url")
        or ""
    ).strip()

    if pe_history is None:
        pe_df, _meta = fetch_index_pe_history_with_archive_fallback(index_code, url=valuation_url)
    else:
        pe_df = pe_history

    pe_df = pe_df.dropna(subset=["date", "pe"]).copy()
    pe_df["date"] = pd.to_datetime(pe_df["date"], errors="coerce")
    pe_df["pe"] = pd.to_numeric(pe_df["pe"], errors="coerce")
    pe_df = pe_df.dropna(subset=["date", "pe"])
    pe_df = pe_df[pe_df["pe"] > 0].sort_values("date").reset_index(drop=True)
    if len(pe_df) < 20:
        return pd.DataFrame(columns=["date", "pe"]), None

    latest_date = pd.Timestamp(pe_df["date"].iloc[-1])
    cutoff = latest_date - pd.DateOffset(years=5)
    history_5y = pe_df[pe_df["date"] >= cutoff].copy().reset_index(drop=True)
    if len(history_5y) >= 20:
        return history_5y, None
    return pe_df, "使用全历史窗口(5Y 数据不足)"


def _build_spread_frame(
    history: pd.DataFrame, bond_history: Optional[pd.DataFrame]
) -> Optional[pd.DataFrame]:
    """由展示窗口内的 PE 历史 + 10Y 国债历史算股债收益差序列(1/PE − 10Y国债)。

    口径与 ``metrics.compute_equity_bond_spread_percentiles`` 一致。国债历史缺失 /
    样本不足 20 返回 None(图上不画利差)。
    """
    if bond_history is None or bond_history.empty:
        return None
    bond_df = bond_history.copy()
    bond_df["date"] = pd.to_datetime(bond_df["date"], errors="coerce")
    bond_df["yield_pct"] = pd.to_numeric(bond_df["yield_pct"], errors="coerce")
    bond_df = bond_df.dropna(subset=["date", "yield_pct"])
    merged = pd.merge(history[["date", "pe"]], bond_df[["date", "yield_pct"]], on="date", how="inner").dropna()
    if len(merged) < 20:
        return None
    merged["spread"] = (1.0 / merged["pe"]) * 100.0 - merged["yield_pct"]
    return merged[["date", "spread"]].sort_values("date").reset_index(drop=True)


def _prepare_valuation_data(
    item: Dict,
    pe_history: Optional[pd.DataFrame] = None,
    bond_history: Optional[pd.DataFrame] = None,
) -> Optional[Dict]:
    index_code = str(
        item.get("index_code") or resolve_target_index_code(item) or item.get("code") or ""
    ).strip()
    if not index_code:
        return None
    chart_item = dict(item)
    chart_item["index_code"] = index_code
    if not chart_item.get("index_name"):
        chart_item["index_name"] = str(item.get("name") or index_code).strip()

    history, footnote = _build_history_frame(chart_item, pe_history=pe_history)
    if len(history) < 20:
        return None

    q30, q50, q70 = [float(value) for value in history["pe"].quantile([0.3, 0.5, 0.7]).tolist()]
    return {
        "item": chart_item,
        "history": history,
        "spread_history": _build_spread_frame(history, bond_history),
        "q30": q30,
        "q50": q50,
        "q70": q70,
        "footnote": footnote,
    }


def _draw_valuation_main(ax, data: Dict) -> None:
    history = data["history"]
    dates = pd.to_datetime(history["date"])
    pes = history["pe"].astype(float)

    ax.text(
        0.00, 1.08, "PE走势", transform=ax.transAxes,
        fontsize=FONT_SIZES["main_title"], fontweight="bold",
        color=PALETTE["text_primary"], ha="left", va="baseline",
    )
    ax.text(
        0.00, 1.00, f"30分位值{data['q30']:.2f}", transform=ax.transAxes,
        fontsize=FONT_SIZES["legend"], color=PALETTE["pct_low"], ha="left", va="baseline",
    )
    ax.text(
        0.16, 1.00, f"中位值{data['q50']:.2f}", transform=ax.transAxes,
        fontsize=FONT_SIZES["legend"], color=PALETTE["pct_mid"], ha="left", va="baseline",
    )
    ax.text(
        0.31, 1.00, f"70分位值{data['q70']:.2f}", transform=ax.transAxes,
        fontsize=FONT_SIZES["legend"], color=PALETTE["pct_high"], ha="left", va="baseline",
    )

    ax.plot(
        dates, pes, color=PALETTE["orange"], linewidth=1.2,
        solid_joinstyle="round", solid_capstyle="round",
    )
    for q, color in [
        (data["q30"], PALETTE["pct_low"]),
        (data["q50"], PALETTE["pct_mid"]),
        (data["q70"], PALETTE["pct_high"]),
    ]:
        ax.axhline(q, color=color, linestyle=(0, (5, 4)), linewidth=1.0, alpha=0.95, zorder=1)

    estimate_meta = data["item"].get("estimate_meta")
    estimated = isinstance(estimate_meta, dict) and estimate_meta.get("status") == "estimated"
    latest_label = f"{pes.iloc[-1]:.2f}" + ("（预估）" if estimated else "")
    ax.annotate(
        latest_label, xy=(dates.iloc[-1], pes.iloc[-1]), xytext=(6, 6),
        textcoords="offset points", color=PALETTE["orange"],
        fontsize=FONT_SIZES["latest_label"], fontweight="bold",
    )

    ax.yaxis.grid(True, color=PALETTE["grid"], linewidth=0.8, alpha=1.0)
    ax.xaxis.grid(False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(PALETTE["spine"])
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_color(PALETTE["spine"])
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", which="both", length=0, colors=PALETTE["text_muted"])

    pe_min = float(pes.min())
    pe_max = float(pes.max())
    if pe_min == pe_max:
        pe_min -= 1
        pe_max += 1
    ax.set_ylim(pe_min * 0.985, pe_max * 1.015)
    ax.set_yticks(np.linspace(pe_min, pe_max, 5))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    for label in ax.get_yticklabels():
        label.set_fontsize(FONT_SIZES["y_tick"])
        label.set_color(PALETTE["text_muted"])

    first_date = pd.Timestamp(dates.iloc[0])
    last_date = pd.Timestamp(dates.iloc[-1])
    min_gap = pd.Timedelta(days=45)
    year_ticks = [
        pd.Timestamp(year=year, month=1, day=1)
        for year in range(first_date.year + 1, last_date.year + 1)
    ]
    year_ticks = [
        tick for tick in year_ticks
        if (tick - first_date) >= min_gap and (last_date - tick) >= min_gap
    ]
    xtick_values = [first_date, *year_ticks, last_date]
    xtick_labels = [
        first_date.strftime("%Y-%m-%d"),
        *(tick.strftime("%Y") for tick in year_ticks),
        last_date.strftime("%Y-%m-%d"),
    ]
    ax.set_xticks(xtick_values)
    ax.set_xticklabels(xtick_labels)
    ax.minorticks_off()
    for label in ax.get_xticklabels():
        label.set_fontsize(FONT_SIZES["x_tick"])
        label.set_color(PALETTE["text_muted"])


def _draw_spread_overlay(ax, data: Dict) -> None:
    """在 PE 分位图上叠股债收益差(右轴蓝线)。无利差序列时静默跳过。"""
    spread_history = data.get("spread_history")
    if spread_history is None or len(spread_history) < 20:
        return
    dates = pd.to_datetime(spread_history["date"])
    spreads = spread_history["spread"].astype(float)
    latest_spread = float(spreads.iloc[-1])

    ax.text(
        0.47, 1.00, f"股债利差{latest_spread:.2f}%", transform=ax.transAxes,
        fontsize=FONT_SIZES["legend"], color=PALETTE["spread"], ha="left", va="baseline",
    )

    ax2 = ax.twinx()
    ax2.plot(
        dates, spreads, color=PALETTE["spread"], linewidth=1.0, alpha=0.9,
        solid_joinstyle="round", solid_capstyle="round",
    )
    ax2.annotate(
        f"{latest_spread:.2f}%", xy=(dates.iloc[-1], latest_spread), xytext=(6, -14),
        textcoords="offset points", color=PALETTE["spread"],
        fontsize=FONT_SIZES["latest_label"], fontweight="bold",
    )

    spread_min = float(spreads.min())
    spread_max = float(spreads.max())
    if spread_min == spread_max:
        spread_min -= 1
        spread_max += 1
    ax2.set_ylim(spread_min * 0.9, spread_max * 1.1)
    ax2.set_yticks(np.linspace(spread_min, spread_max, 5))
    ax2.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    ax2.grid(False)
    ax2.spines["top"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax2.spines["bottom"].set_visible(False)
    ax2.spines["right"].set_color(PALETTE["spread"])
    ax2.spines["right"].set_linewidth(0.8)
    ax2.tick_params(axis="y", which="both", length=0, colors=PALETTE["spread"])
    for label in ax2.get_yticklabels():
        label.set_fontsize(FONT_SIZES["y_tick"])


def _draw_valuation_footer(ax, data: Dict) -> None:
    ax.set_axis_off()
    text = f"数据源:易方达估值中心 + 指数详情接口 · 生成时间 {now_in_beijing().strftime('%Y-%m-%d %H:%M')}"
    if data["footnote"]:
        text = f"{text} · {data['footnote']}"
    ax.text(
        0.00, 0.20, text, transform=ax.transAxes,
        fontsize=FONT_SIZES["footer"], color=PALETTE["text_muted"],
        ha="left", va="bottom",
    )


def generate_valuation_percentile_chart(
    item: Dict,
    output_dir: Path,
    *,
    pe_history: Optional[pd.DataFrame] = None,
    bond_history: Optional[pd.DataFrame] = None,
) -> Optional[Path]:
    """生成 PE 走势分位图(有国债历史时叠股债收益差右轴)。``item`` 为已取好的估值项
    (含 index_code/name);``pe_history`` 可预取传入,为 None 则自取(带归档回退);
    ``bond_history`` 为 10Y 国债历史(``date``/``yield_pct``),缺省则不画利差。
    数据不足返回 None。
    """
    data = _prepare_valuation_data(item, pe_history=pe_history, bond_history=bond_history)
    if data is None:
        return None

    apply_cjk(plt)
    fig = plt.figure(figsize=FIGURE_SIZE, dpi=FIGURE_DPI, facecolor=PALETTE["background"])
    # 右轴要放利差刻度,主图比 AX_BOUNDS 略窄
    chart_ax = fig.add_axes([0.07, 0.16, 0.85, 0.70])
    footer_ax = fig.add_axes(AX_BOUNDS["footer"])
    _draw_valuation_main(chart_ax, data)
    _draw_spread_overlay(chart_ax, data)
    _draw_valuation_footer(footer_ax, data)

    output_dir.mkdir(parents=True, exist_ok=True)
    index_code = str(data["item"].get("index_code") or data["item"].get("code") or "").strip()
    output_path = output_dir / f"valuation_percentile_{index_code}.png"
    fig.savefig(output_path, dpi=FIGURE_DPI, facecolor=PALETTE["background"], edgecolor="none")
    plt.close(fig)
    return output_path


# ---------- 汇率图 ----------


def _prepare_fx_data(
    fx_history: Optional[pd.DataFrame] = None,
    *,
    days: int = 3650,
    symbol: str = "USDCNH",
) -> Optional[Dict]:
    hist_df = fx_history if fx_history is not None else fetch_fx_history_with_archive_fallback(symbol=symbol)
    if hist_df.empty:
        return None

    end_date = hist_df["日期"].max()
    start_date = end_date - timedelta(days=days)
    hist_recent = hist_df[hist_df["日期"] >= start_date].copy().reset_index(drop=True)
    if hist_recent.empty:
        return None

    latest_hist = float(hist_recent.iloc[-1]["市场价"])
    return {
        "hist_df": hist_recent,
        "hist_symbol": symbol,
        "start_date": pd.Timestamp(start_date),
        "end_date": pd.Timestamp(end_date),
        "latest_hist": latest_hist,
        "hist_name": str(hist_recent.iloc[-1].get("名称") or symbol),
    }


def generate_fx_chart(
    output_dir: Path,
    *,
    fx_history: Optional[pd.DataFrame] = None,
    days: int = 3650,
    symbol: str = "USDCNH",
    slug: str = "fx_usd_cny_vs_mid_10y",
) -> Optional[Path]:
    """生成汇率市场价走势图。``fx_history`` 可预取传入,为 None 则自取(三级回退)。无数据返回 None。"""
    data = _prepare_fx_data(fx_history, days=days, symbol=symbol)
    if data is None:
        return None

    apply_cjk(plt)
    fig = plt.figure(figsize=FIGURE_SIZE, dpi=FIGURE_DPI)
    fig.patch.set_facecolor(PALETTE["background"])

    chart_ax = fig.add_axes(AX_BOUNDS["chart"])
    footer_ax = fig.add_axes(AX_BOUNDS["footer"])

    chart_ax.set_facecolor(PALETTE["background"])
    chart_ax.grid(True, axis="y", color=PALETTE["grid"], linewidth=0.8, alpha=1.0)
    chart_ax.grid(False, axis="x")
    chart_ax.spines["top"].set_visible(False)
    chart_ax.spines["right"].set_visible(False)
    chart_ax.spines["left"].set_color(PALETTE["spine"])
    chart_ax.spines["left"].set_linewidth(0.8)
    chart_ax.spines["bottom"].set_color(PALETTE["spine"])
    chart_ax.spines["bottom"].set_linewidth(0.8)
    chart_ax.tick_params(axis="both", which="both", length=0, colors=PALETTE["text_muted"])

    hist_df = data["hist_df"]
    hist_symbol = data["hist_symbol"]
    dates = pd.to_datetime(hist_df["日期"])
    prices = hist_df["市场价"].astype(float)

    chart_ax.text(
        0.00, 1.08, f"{hist_symbol} 市场价走势", transform=chart_ax.transAxes,
        fontsize=FONT_SIZES["main_title"], fontweight="bold",
        color=PALETTE["text_primary"], ha="left", va="baseline",
    )
    chart_ax.plot(
        dates, prices, color=PALETTE["line"], linewidth=1.2,
        solid_joinstyle="round", solid_capstyle="round",
    )
    chart_ax.annotate(
        f"{prices.iloc[-1]:.4f}", xy=(dates.iloc[-1], prices.iloc[-1]), xytext=(6, 6),
        textcoords="offset points", color=PALETTE["line"],
        fontsize=FONT_SIZES["latest_label"], fontweight="bold",
    )

    price_min = float(prices.min())
    price_max = float(prices.max())
    if price_min == price_max:
        price_min -= 0.01
        price_max += 0.01
    pad = max((price_max - price_min) * 0.05, 0.01)
    chart_ax.set_ylim(price_min - pad, price_max + pad)
    chart_ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    for label in chart_ax.get_yticklabels():
        label.set_fontsize(FONT_SIZES["y_tick"])
        label.set_color(PALETTE["text_muted"])

    first_date = pd.Timestamp(dates.iloc[0])
    last_date = pd.Timestamp(dates.iloc[-1])
    min_gap = pd.Timedelta(days=45)
    year_ticks = [
        pd.Timestamp(year=year, month=1, day=1)
        for year in range(first_date.year + 1, last_date.year + 1)
    ]
    year_ticks = [
        tick for tick in year_ticks
        if (tick - first_date) >= min_gap and (last_date - tick) >= min_gap
    ]
    xtick_values = [first_date, *year_ticks, last_date]
    xtick_labels = [
        first_date.strftime("%Y-%m-%d"),
        *(tick.strftime("%Y") for tick in year_ticks),
        last_date.strftime("%Y-%m-%d"),
    ]
    chart_ax.set_xticks(xtick_values)
    chart_ax.set_xticklabels(xtick_labels)
    chart_ax.minorticks_off()
    for label in chart_ax.get_xticklabels():
        label.set_fontsize(FONT_SIZES["x_tick"])
        label.set_color(PALETTE["text_muted"])

    footer_ax.axis("off")
    footer_ax.text(
        0.0, 0.50,
        f"区间: {data['start_date'].date()} ~ {data['end_date'].date()}    "
        f"最新值: {data['latest_hist']:.4f}    数据来源: AKShare · forex_hist_em",
        ha="left", va="center",
        fontsize=FONT_SIZES["footer"], color=PALETTE["text_muted"],
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slug}.png"
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path
