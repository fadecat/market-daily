from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ..common import fonts

PRICE_CHART_CID = "dividend_observation_price_chart"
SPREAD_CHART_CID = "dividend_observation_spread_chart"
VALUATION_CHART_CID = "dividend_observation_valuation_chart"
STYLE_CHART_CID = "dividend_observation_style_chart"
PALETTE = {
    "background": "#ffffff",
    "primary": "#5470c6",
    "secondary": "#f28a2e",
    "accent": "#91cc75",
    "danger": "#91cc75",
    "style": "#d9485f",
    "grid": "#e8eef5",
    "spine": "#d7dee8",
    "text_muted": "#6b7685",
    "text_primary": "#2b2f33",
}
LINE_WIDTH = 1.4
FILL_ALPHA = 0.04
SERIES_LABELS = {
    "index_close": "指数点位",
    "drawdown_peak": "回撤",
    "dividend_yield_spread_percentile": "股息率差分位",
    "earnings_yield_spread_percentile": "盈利收益率差分位",
    "pe_ttm_percentile": "PE分位",
    "pb_lf_percentile": "PB分位",
    "style_rotation_spread_percentile": "风格挤压分位",
}


@dataclass
class ChartResult:
    cid: str
    image_path: str | None
    error: str | None = None


def _parse_dates(series_dates: list[str]) -> list[dt.date]:
    return [dt.date.fromisoformat(str(value)[:10]) for value in series_dates]


def _save_figure(fig: plt.Figure, output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=130)
    plt.close(fig)
    return str(output_path)


def _series_points(payload: dict[str, Any], *keys: str) -> tuple[list[dt.date], list[list[float]]]:
    series = payload.get("series") or {}
    raw_dates = list(series.get("dates") or [])
    if not raw_dates:
        return [], []
    raw_values = [list(series.get(key) or []) for key in keys]
    points: list[tuple[dt.date, list[float]]] = []
    for index, trade_date in enumerate(raw_dates):
        converted: list[float] = []
        valid = True
        for values in raw_values:
            if index >= len(values) or values[index] is None:
                valid = False
                break
            converted.append(float(values[index]))
        if valid:
            points.append((dt.date.fromisoformat(str(trade_date)[:10]), converted))
    if not points:
        return [], []
    dates = [item[0] for item in points]
    columns = [[item[1][column] for item in points] for column in range(len(keys))]
    return dates, columns


def _empty_result(cid: str) -> ChartResult:
    return ChartResult(cid=cid, image_path=None, error="该图暂无数据")


def _date_tick_indexes(dates: list[dt.date], *, tick_count: int = 6) -> list[int]:
    if not dates:
        return []
    if len(dates) <= tick_count:
        return list(range(len(dates)))
    last_index = len(dates) - 1
    indexes = {
        round(last_index * step / (tick_count - 1))
        for step in range(tick_count - 1)
    }
    indexes.add(last_index)
    return sorted(indexes)


def _apply_date_axis_style(ax: plt.Axes, dates: list[dt.date], *, right_pad_frac: float = 0.02) -> None:
    if not dates:
        return
    tick_indexes = _date_tick_indexes(dates)
    tick_dates = [dates[index] for index in tick_indexes]
    last_index = len(dates) - 1
    tick_labels = [
        dates[index].strftime("%Y-%m-%d") if index == last_index else dates[index].strftime("%Y-%m")
        for index in tick_indexes
    ]
    right_padding_days = max(6, round(max((dates[-1] - dates[0]).days, 1) * right_pad_frac))
    ax.set_xticks(tick_dates, labels=tick_labels)
    ax.set_xlim(dates[0], dates[-1] + dt.timedelta(days=right_padding_days))


def _annotate_endpoint(
    ax: plt.Axes,
    dates: list[dt.date],
    values: list[float],
    color: str,
    formatter,
    *,
    dy_points: float = 0.0,
) -> None:
    ax.annotate(
        formatter(values[-1]),
        xy=(dates[-1], values[-1]),
        xytext=(6, dy_points),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=12,
        fontweight="bold",
        color=color,
    )


def _percentile_label_offsets(first: float, second: float, *, threshold: float = 6.0) -> tuple[float, float]:
    if abs(first - second) >= threshold:
        return 0.0, 0.0
    return (9.0, -9.0) if first >= second else (-9.0, 9.0)


def _base_axis() -> tuple[plt.Figure, plt.Axes]:
    fonts.apply_cjk(plt)
    fig, ax = plt.subplots(figsize=(10, 3.6))
    fig.patch.set_facecolor(PALETTE["background"])
    ax.set_facecolor(PALETTE["background"])
    ax.grid(True, axis="y", color=PALETTE["grid"], linewidth=0.7, alpha=1.0)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(PALETTE["spine"])
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_color(PALETTE["spine"])
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", which="both", length=0, labelsize=12, colors=PALETTE["text_muted"])
    return fig, ax


def _safe_render_price_chart(payload: dict[str, Any], output_path: Path) -> ChartResult:
    dates, columns = _series_points(payload, "index_close", "drawdown_peak")
    if not dates:
        return _empty_result(PRICE_CHART_CID)
    prices, drawdowns = columns
    fig, ax = _base_axis()
    _apply_date_axis_style(ax, dates, right_pad_frac=0.09)
    price_line = ax.plot(
        dates,
        prices,
        color=PALETTE["primary"],
        linewidth=LINE_WIDTH,
        label=SERIES_LABELS["index_close"],
    )[0]
    _annotate_endpoint(ax, dates, prices, PALETTE["primary"], lambda v: f"{v:,.0f}")
    ax.set_ylabel("")
    ax2 = ax.twinx()
    drawdown_line = ax2.plot(
        dates,
        drawdowns,
        color=PALETTE["danger"],
        linewidth=LINE_WIDTH,
        label=SERIES_LABELS["drawdown_peak"],
    )[0]
    ax2.fill_between(dates, drawdowns, 0.0, color=PALETTE["danger"], alpha=FILL_ALPHA)
    ax2.set_ylabel("")
    ax2.yaxis.set_major_formatter(lambda value, _pos: f"{value * 100:.0f}%")
    ax2.spines["top"].set_visible(False)
    ax2.spines["left"].set_visible(False)
    ax2.spines["right"].set_color(PALETTE["spine"])
    ax2.spines["right"].set_linewidth(0.8)
    ax2.tick_params(axis="y", which="both", length=0, labelsize=12, colors=PALETTE["text_muted"])
    _annotate_endpoint(ax2, dates, drawdowns, PALETTE["danger"], lambda v: f"{v * 100:.1f}%")
    ax.legend(
        [price_line, drawdown_line],
        [SERIES_LABELS["index_close"], SERIES_LABELS["drawdown_peak"]],
        loc="upper left",
        bbox_to_anchor=(0.0, 1.02),
        ncol=2,
        frameon=False,
        fontsize=13,
        handlelength=1.4,
        handletextpad=0.5,
        columnspacing=1.0,
    )
    fig.autofmt_xdate(rotation=28, ha="right")
    return ChartResult(
        cid=PRICE_CHART_CID,
        image_path=_save_figure(fig, output_path),
    )


def _safe_render_two_line_chart(
    payload: dict[str, Any],
    output_path: Path,
    left_key: str,
    right_key: str,
    title: str,
    cid: str,
) -> ChartResult:
    dates, columns = _series_points(payload, left_key, right_key)
    if not dates:
        return _empty_result(cid)
    left_values, right_values = columns
    fig, ax = _base_axis()
    _apply_date_axis_style(ax, dates, right_pad_frac=0.09)
    left_line = ax.plot(
        dates,
        left_values,
        color=PALETTE["primary"],
        linewidth=LINE_WIDTH,
        label=SERIES_LABELS.get(left_key, left_key),
    )[0]
    right_line = ax.plot(
        dates,
        right_values,
        color=PALETTE["secondary"],
        linewidth=LINE_WIDTH,
        label=SERIES_LABELS.get(right_key, right_key),
    )[0]
    left_dy, right_dy = _percentile_label_offsets(left_values[-1], right_values[-1])
    _annotate_endpoint(ax, dates, left_values, PALETTE["primary"], lambda v: f"{v:.1f}%", dy_points=left_dy)
    _annotate_endpoint(ax, dates, right_values, PALETTE["secondary"], lambda v: f"{v:.1f}%", dy_points=right_dy)
    ax.set_ylabel("")
    ax.set_ylim(0, 100)
    legend = ax.legend(
        [left_line, right_line],
        [SERIES_LABELS.get(left_key, left_key), SERIES_LABELS.get(right_key, right_key)],
        loc="upper left",
        bbox_to_anchor=(0.0, 1.02),
        ncol=2,
        frameon=False,
        fontsize=13,
        handlelength=1.4,
        handletextpad=0.5,
        columnspacing=1.0,
    )
    if legend is not None:
        for text in legend.get_texts():
            text.set_color(PALETTE["text_muted"])
    fig.autofmt_xdate(rotation=28, ha="right")
    return ChartResult(cid=cid, image_path=_save_figure(fig, output_path))


def _safe_render_single_line_chart(
    payload: dict[str, Any],
    output_path: Path,
    series_key: str,
    title: str,
    cid: str,
) -> ChartResult:
    dates, columns = _series_points(payload, series_key)
    if not dates:
        return _empty_result(cid)
    values = columns[0]
    fig, ax = _base_axis()
    _apply_date_axis_style(ax, dates, right_pad_frac=0.09)
    line = ax.plot(
        dates,
        values,
        color=PALETTE["style"],
        linewidth=LINE_WIDTH,
        label=SERIES_LABELS.get(series_key, series_key),
    )[0]
    _annotate_endpoint(ax, dates, values, PALETTE["style"], lambda v: f"{v:.1f}%")
    ax.set_ylabel("")
    ax.set_ylim(0, 100)
    legend = ax.legend(
        [line],
        [SERIES_LABELS.get(series_key, series_key)],
        loc="upper left",
        bbox_to_anchor=(0.0, 1.02),
        ncol=1,
        frameon=False,
        fontsize=13,
        handlelength=1.4,
        handletextpad=0.5,
        columnspacing=1.0,
    )
    if legend is not None:
        for text in legend.get_texts():
            text.set_color(PALETTE["text_muted"])
    fig.autofmt_xdate(rotation=28, ha="right")
    return ChartResult(cid=cid, image_path=_save_figure(fig, output_path))


def generate_chart_bundle(payload: dict[str, Any], work_dir: Path) -> dict[str, ChartResult]:
    work_dir.mkdir(parents=True, exist_ok=True)
    return {
        "price": _safe_render_price_chart(payload, work_dir / "price.png"),
        "spread": _safe_render_two_line_chart(
            payload,
            work_dir / "spread.png",
            "dividend_yield_spread_percentile",
            "earnings_yield_spread_percentile",
            "利率相对吸引力",
            SPREAD_CHART_CID,
        ),
        "valuation": _safe_render_two_line_chart(
            payload,
            work_dir / "valuation.png",
            "pe_ttm_percentile",
            "pb_lf_percentile",
            "绝对定价",
            VALUATION_CHART_CID,
        ),
        "style": _safe_render_single_line_chart(
            payload,
            work_dir / "style.png",
            "style_rotation_spread_percentile",
            "风格挤压",
            STYLE_CHART_CID,
        ),
    }
