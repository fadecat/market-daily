"""可转债价格中位数 + 平均收益率折线图。

移植自 jisilu_ggx/cb_index_chart.py:字体解析改用 common.fonts.apply_cjk;
generate_cb_index_chart 增加可选 records 参数,便于无网测试/预览。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
import pandas as pd

from ...common import fonts
from .history import build_merged_history

FIGURE_DPI = 180
FIGURE_SIZE = (14, 5.2)
AX_BOUNDS = {
    "chart": [0.06, 0.18, 0.88, 0.68],
    "footer": [0.04, 0.03, 0.92, 0.05],
}
PALETTE = {
    "background": "#ffffff",
    "price": "#9dc3e6",
    "ytm": "#ed7c2b",
    "text_primary": "#1f1f1f",
    "text_muted": "#8a8a8a",
    "grid": "#efefef",
    "spine": "#d0d0d0",
}
FONT_SIZES = {
    "title": 15,
    "y_tick": 10,
    "x_tick": 9,
    "legend": 11,
    "footer": 9,
}
YEAR_TICK_MIN_GAP = pd.Timedelta(days=150)


def _records_to_frame(records: List[Dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("median_price", "avg_ytm"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df = df.dropna(subset=["date", "median_price", "avg_ytm"]).sort_values("date").reset_index(drop=True)
    return df


def _year_xticks(first_date: pd.Timestamp, last_date: pd.Timestamp) -> Tuple[List[pd.Timestamp], List[str]]:
    year_ticks = [
        pd.Timestamp(year=year, month=1, day=1)
        for year in range(first_date.year + 1, last_date.year + 1)
    ]
    year_ticks = [
        tick
        for tick in year_ticks
        if (tick - first_date) >= YEAR_TICK_MIN_GAP and (last_date - tick) >= YEAR_TICK_MIN_GAP
    ]
    values = [first_date, *year_ticks, last_date]
    labels = [
        first_date.strftime("%Y-%m-%d"),
        *(tick.strftime("%Y") for tick in year_ticks),
        last_date.strftime("%Y-%m-%d"),
    ]
    return values, labels


def _draw_chart(ax_price, ax_ytm, df: pd.DataFrame) -> None:
    dates = df["date"]
    price = df["median_price"].astype(float)
    ytm = df["avg_ytm"].astype(float)

    ax_price.plot(dates, price, color=PALETTE["price"], linewidth=1.2, label="价格中位数", zorder=3)
    ax_ytm.plot(dates, ytm, color=PALETTE["ytm"], linewidth=1.2, label="平均收益率", zorder=4)

    ax_price.set_zorder(ax_ytm.get_zorder() - 1)
    ax_price.patch.set_visible(False)

    price_min = float(price.min())
    price_max = float(price.max())
    price_pad = max((price_max - price_min) * 0.05, 1.0)
    ax_price.set_ylim(price_min - price_pad, price_max + price_pad)

    ytm_min = float(ytm.min())
    ytm_max = float(ytm.max())
    ytm_pad = max((ytm_max - ytm_min) * 0.05, 0.5)
    ax_ytm.set_ylim(ytm_min - ytm_pad, ytm_max + ytm_pad)

    ax_price.yaxis.grid(True, color=PALETTE["grid"], linewidth=0.8)
    ax_price.xaxis.grid(False)
    ax_ytm.grid(False)

    for ax in (ax_price, ax_ytm):
        ax.spines["top"].set_visible(False)
        ax.spines["left"].set_linewidth(0.8)
        ax.spines["right"].set_color(PALETTE["spine"])
        ax.spines["right"].set_linewidth(0.8)
        ax.spines["bottom"].set_color(PALETTE["spine"])
        ax.spines["bottom"].set_linewidth(0.8)
        ax.tick_params(axis="both", which="both", length=0, colors=PALETTE["text_muted"])

    ax_ytm.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{v:.2f}%"))
    for label in ax_price.get_yticklabels() + ax_ytm.get_yticklabels():
        label.set_fontsize(FONT_SIZES["y_tick"])
        label.set_color(PALETTE["text_muted"])

    first_date = pd.Timestamp(dates.iloc[0])
    last_date = pd.Timestamp(dates.iloc[-1])
    xtick_values, xtick_labels = _year_xticks(first_date, last_date)
    ax_price.set_xticks(xtick_values)
    ax_price.set_xticklabels(xtick_labels)
    ax_price.minorticks_off()
    for label in ax_price.get_xticklabels():
        label.set_fontsize(FONT_SIZES["x_tick"])
        label.set_color(PALETTE["text_muted"])


def _draw_title(fig: Figure) -> None:
    fig.text(
        0.5,
        0.93,
        "可转债价格中位数与平均收益率",
        ha="center",
        va="baseline",
        fontsize=FONT_SIZES["title"],
        fontweight="bold",
        color=PALETTE["text_primary"],
    )


def _draw_legend(fig: Figure) -> None:
    from matplotlib.lines import Line2D

    handles = [
        Line2D([0], [0], color=PALETTE["price"], linewidth=1.6, label="价格中位数"),
        Line2D([0], [0], color=PALETTE["ytm"], linewidth=1.6, label="平均收益率"),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.005),
        ncol=2,
        frameon=False,
        fontsize=FONT_SIZES["legend"],
        handlelength=2.2,
        columnspacing=2.0,
        labelcolor=PALETTE["text_primary"],
    )


def _draw_footer(ax, df: pd.DataFrame) -> None:
    ax.set_axis_off()
    start = df["date"].iloc[0].strftime("%Y-%m-%d")
    end = df["date"].iloc[-1].strftime("%Y-%m-%d")
    text = f"数据源：集思录可转债等权指数 · 覆盖区间 {start} ~ {end} · 共 {len(df)} 个交易日"
    ax.text(
        0.00,
        0.10,
        text,
        transform=ax.transAxes,
        fontsize=FONT_SIZES["footer"],
        color=PALETTE["text_muted"],
        ha="left",
        va="bottom",
    )


def build_figure(df: pd.DataFrame) -> Figure:
    fonts.apply_cjk(plt)

    fig = plt.figure(figsize=FIGURE_SIZE, dpi=FIGURE_DPI, facecolor=PALETTE["background"])
    ax_price = fig.add_axes(AX_BOUNDS["chart"])
    ax_ytm = ax_price.twinx()
    footer_ax = fig.add_axes(AX_BOUNDS["footer"])

    _draw_chart(ax_price, ax_ytm, df)
    _draw_title(fig)
    _draw_legend(fig)
    _draw_footer(footer_ax, df)
    return fig


def generate_cb_index_chart(
    output_path: Path, records: Optional[List[Dict]] = None
) -> Optional[Path]:
    """生成指数图 PNG。records 为 None 时抓取+合并(触网);否则用传入记录(测试/预览)。"""
    if records is None:
        merged, _ = build_merged_history()
    else:
        merged = records
    df = _records_to_frame(merged)
    if len(df) < 30:
        return None

    fig = build_figure(df)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=FIGURE_DPI, facecolor=PALETTE["background"], edgecolor="none")
    plt.close(fig)
    return output_path
