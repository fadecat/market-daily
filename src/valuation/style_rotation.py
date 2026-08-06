"""风格轮动区块:数据(指数收益率差值)+ 图表 + 邮件片段。

移植自 ``style_rotation_preview.py``(数据)、``prototype_style_rotation_chart.py``(图表)、
``monitor_drawdown`` 的 ``_render_style_rotation_email_section`` / ``_build_style_rotation_summary``
(片段)。主邮件用指数路径(国证小盘成长 vs 国证大盘价值);ETF 变体
(``collect_etf_style_rotation_preview_payload``,依赖 analyze_etf_com_cn_period_returns)未用于
日报,不迁。

- 取数:``fetch_index_history`` -> ``fetch.fetch_index_data``(tickflow + akshare 多源)。
- 图表:``generate_style_rotation_chart``,字体改用 ``common.fonts.apply_cjk``。
- ``build_section(work_dir)``:取数 + 作图 + 渲染片段,返回 ``{html, inline_images, as_of_date}``
  或 None(次要区块,失败跳过不中断整封邮件)。
"""
from __future__ import annotations

from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd

from ..common.fonts import apply_cjk
from .fetch import fetch_index_data, now_in_beijing

# ---------- 配置 ----------

FIXED_LEFT_SYMBOL = "399376"
FIXED_LEFT_NAME = "国证小盘成长"
FIXED_RIGHT_SYMBOL = "399373"
FIXED_RIGHT_NAME = "国证大盘价值"
DEFAULT_RETURN_WINDOW_DAYS = 250
DEFAULT_DISPLAY_WINDOW_DAYS = 252 * 5
STYLE_ROTATION_TICKFLOW_DAILY_COUNT = 5000

STYLE_ROTATION_CHART_CID = "style_rotation_chart"

# 图表样式
FIGURE_SIZE = (13.5, 6.2)
FIGURE_DPI = 180
POSITIVE_FILL = "#f6c1bb"
NEGATIVE_FILL = "#c9e8d0"
SPREAD_LINE = "#111111"
SPREAD_LINE_WIDTH = 1.6 / 3
ZERO_LINE = "#7a7a7a"
LATEST_X_AXIS_LABEL_COLOR = "#111111"

# 邮件片段配色(与 valuation/render.py 共享)
_EMAIL_TEXT_PRIMARY = "#1d1d1f"
_EMAIL_LABEL_COLOR = "#86868b"
_EMAIL_MUTED_COLOR = "#6e6e73"
_EMAIL_BORDER_CARD_SPLIT = "#f0f0f0"


# ---------- 数据 ----------


def normalize_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(
            {
                "date": pd.Series(dtype="datetime64[ns]"),
                "close": pd.Series(dtype="float64"),
            }
        )

    frame = df.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]

    date_column = "date" if "date" in frame.columns else "trade_date"
    if "close" not in frame.columns or date_column not in frame.columns:
        raise ValueError("price frame must contain date and close columns")

    frame = frame[[date_column, "close"]].copy()
    frame.columns = ["date", "close"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"])
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return frame


def calculate_style_rotation_preview(
    *,
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    return_window_days: int = 250,
    display_window_days: int = 252,
) -> Dict[str, Any]:
    if return_window_days <= 0:
        raise ValueError("return_window_days must be greater than 0")

    left = normalize_price_frame(left_df)
    right = normalize_price_frame(right_df)

    merged = pd.merge(left, right, on="date", how="inner", suffixes=("_left", "_right"))
    merged = merged.sort_values("date").reset_index(drop=True)
    if merged.empty:
        raise ValueError("对齐后的价格数据为空")

    merged["left_return"] = merged["close_left"].pct_change(return_window_days) * 100
    merged["right_return"] = merged["close_right"].pct_change(return_window_days) * 100
    merged["spread"] = merged["left_return"] - merged["right_return"]
    merged = merged.dropna(subset=["left_return", "right_return", "spread"])
    if merged.empty:
        raise ValueError("有效收益率差值为空")

    if display_window_days > 0:
        merged = merged.tail(display_window_days)

    merged = merged.reset_index(drop=True)

    return {
        "dates": merged["date"].dt.strftime("%Y-%m-%d").tolist(),
        "left_return": merged["left_return"].round(2).tolist(),
        "right_return": merged["right_return"].round(2).tolist(),
        "spread": merged["spread"].round(2).tolist(),
    }


def build_style_rotation_preview_payload(
    *,
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    left_symbol: str = FIXED_LEFT_SYMBOL,
    left_name: str = FIXED_LEFT_NAME,
    right_symbol: str = FIXED_RIGHT_SYMBOL,
    right_name: str = FIXED_RIGHT_NAME,
    return_window_days: int = DEFAULT_RETURN_WINDOW_DAYS,
    display_window_days: int = DEFAULT_DISPLAY_WINDOW_DAYS,
) -> Dict[str, Any]:
    preview = calculate_style_rotation_preview(
        left_df=left_df,
        right_df=right_df,
        return_window_days=return_window_days,
        display_window_days=display_window_days,
    )
    return {
        "meta": {
            "left_symbol": left_symbol,
            "left_name": left_name,
            "right_symbol": right_symbol,
            "right_name": right_name,
            "return_window_days": return_window_days,
            "display_window_days": display_window_days,
        },
        "series": preview,
    }


def fetch_index_history(symbol: str) -> pd.DataFrame:
    end_date = pd.Timestamp.today().normalize()
    start_date = end_date - pd.Timedelta(days=365 * 10)
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    frame = fetch_index_data(
        symbol,
        start_str,
        end_str,
        tickflow_daily_count=STYLE_ROTATION_TICKFLOW_DAILY_COUNT,
    )
    normalized = normalize_price_frame(frame)
    if normalized.empty:
        raise RuntimeError(f"指数历史数据规范化后为空: {symbol}")
    return normalized


def collect_style_rotation_preview_payload(
    *,
    return_window_days: int = DEFAULT_RETURN_WINDOW_DAYS,
    display_window_days: int = DEFAULT_DISPLAY_WINDOW_DAYS,
) -> Dict[str, Any]:
    left_df = fetch_index_history(FIXED_LEFT_SYMBOL)
    right_df = fetch_index_history(FIXED_RIGHT_SYMBOL)
    return build_style_rotation_preview_payload(
        left_df=left_df,
        right_df=right_df,
        left_symbol=FIXED_LEFT_SYMBOL,
        left_name=FIXED_LEFT_NAME,
        right_symbol=FIXED_RIGHT_SYMBOL,
        right_name=FIXED_RIGHT_NAME,
        return_window_days=return_window_days,
        display_window_days=display_window_days,
    )


def resolve_as_of_label(payload: Dict[str, Any]) -> str:
    dates = ((payload.get("series") or {}).get("dates") or [])
    if dates:
        return str(dates[-1])
    return now_in_beijing().strftime("%Y-%m-%d")


# ---------- 图表 ----------


def _is_supported_series_like(value: Any) -> bool:
    if isinstance(value, (str, bytes)):
        return False
    return hasattr(value, "__iter__") and hasattr(value, "__len__")


def _extract_series(payload: Dict[str, Any]) -> Tuple[pd.DatetimeIndex, pd.Series]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")

    series = payload.get("series")
    if series is None:
        series = {}
    if not isinstance(series, Mapping):
        raise ValueError("payload['series'] must be a mapping")

    raw_dates = series["dates"] if "dates" in series else []
    raw_spread = series["spread"] if "spread" in series else []

    if not _is_supported_series_like(raw_dates):
        raise ValueError("series['dates'] must be a sequence")
    if not _is_supported_series_like(raw_spread):
        raise ValueError("series['spread'] must be a sequence")
    if len(raw_dates) != len(raw_spread):
        raise ValueError("series['dates'] and series['spread'] length mismatch")

    dates = pd.to_datetime(list(raw_dates), errors="coerce")
    spread = pd.to_numeric(list(raw_spread), errors="coerce")
    if pd.isna(dates).any():
        raise ValueError("series['dates'] contains invalid date values")
    if pd.isna(spread).any():
        raise ValueError("series['spread'] contains invalid numeric values")

    frame = pd.DataFrame({"date": dates, "spread": spread})
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    if frame.empty:
        raise ValueError("style rotation payload has no valid spread data")
    return pd.DatetimeIndex(frame["date"]), frame["spread"]


def _build_chart_title(meta: Mapping[str, Any]) -> str:
    left_name = str(meta.get("left_name") or "左侧标的").strip()
    right_name = str(meta.get("right_name") or "右侧标的").strip()
    left_symbol = str(meta.get("left_symbol") or "").strip()
    right_symbol = str(meta.get("right_symbol") or "").strip()
    left_label = f"{left_name}({left_symbol})" if left_symbol else left_name
    right_label = f"{right_name}({right_symbol})" if right_symbol else right_name
    return f"风格轮动收益率差值({left_label} vs {right_label})"


def _build_footer_text(payload: Mapping[str, Any]) -> str:
    dates, spread = _extract_series(payload)
    latest_date_dt = dates[-1].to_pydatetime()
    latest_date = f"{latest_date_dt.year}年{latest_date_dt.month}月{latest_date_dt.day}日"
    latest_spread = float(spread.iloc[-1])
    return f"最新日期:{latest_date}    最新差值:{latest_spread:.2f}%"


def _build_latest_x_axis_label(payload: Mapping[str, Any]) -> str:
    dates, _ = _extract_series(payload)
    return dates[-1].strftime("%Y-%m-%d")


def _hide_matching_tick_label_objects(tick_labels: list, target_label: str) -> None:
    for tick_label in tick_labels:
        if tick_label.get_text() == target_label:
            tick_label.set_visible(False)


def generate_style_rotation_chart(payload: Dict[str, Any], output_dir: Path) -> Path:
    apply_cjk(plt)

    dates, spread = _extract_series(payload)
    meta = payload.get("meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, Mapping):
        raise ValueError("payload['meta'] must be a mapping")

    title = _build_chart_title(meta)
    footer_text = _build_footer_text(payload)
    latest_x_axis_label = _build_latest_x_axis_label(payload)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "style_rotation_preview.png"

    fig, ax = plt.subplots(figsize=FIGURE_SIZE, dpi=FIGURE_DPI)
    try:
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        x_values = dates.to_pydatetime()
        y_values = spread.astype(float).tolist()
        y_zero = [0.0] * len(y_values)

        ax.fill_between(
            x_values, y_values, y_zero,
            where=[value >= 0 for value in y_values], interpolate=True,
            color=POSITIVE_FILL, alpha=0.95,
        )
        ax.fill_between(
            x_values, y_values, y_zero,
            where=[value < 0 for value in y_values], interpolate=True,
            color=NEGATIVE_FILL, alpha=0.95,
        )
        ax.plot(
            x_values, y_values, color=SPREAD_LINE, linewidth=SPREAD_LINE_WIDTH,
            solid_capstyle="round", solid_joinstyle="round",
        )
        ax.axhline(0, color=ZERO_LINE, linestyle=(0, (4, 4)), linewidth=1.1)

        ax.set_title(title, fontsize=18, fontweight="bold", pad=16)

        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.2f}%"))
        fig.canvas.draw()
        _hide_matching_tick_label_objects(list(ax.get_xticklabels()), latest_x_axis_label)

        y_min = min(y_values)
        y_max = max(y_values)
        y_span = max(y_max - y_min, 1.0)
        y_margin = max(y_span * 0.18, 0.8)
        ax.set_ylim(y_min - y_margin, y_max + y_margin)

        ax.grid(axis="y", color="#E5E7EB", linewidth=0.8)
        ax.grid(axis="x", visible=False)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#D0D7E2")
        ax.spines["bottom"].set_color("#D0D7E2")
        ax.tick_params(axis="x", labelrotation=0, labelsize=9, colors="#4C5563")
        ax.tick_params(axis="y", labelsize=10, colors="#4C5563")
        ax.text(
            0.998, -0.02, latest_x_axis_label, transform=ax.transAxes,
            ha="right", va="top", fontsize=9, color=LATEST_X_AXIS_LABEL_COLOR,
        )

        fig.text(
            0.5, 0.02, footer_text, ha="center", va="bottom",
            fontsize=11, color="#4C5563",
        )

        plt.tight_layout(rect=(0.02, 0.06, 0.98, 0.96))
        fig.savefig(output_path, facecolor="white", bbox_inches="tight")
        return output_path
    finally:
        plt.close(fig)


# ---------- 片段 / 编排 ----------


def _get_latest_style_rotation_spread(payload: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    series = payload.get("series")
    if not isinstance(series, dict):
        return None
    spread_values = series.get("spread")
    if not isinstance(spread_values, list) or not spread_values:
        return None
    try:
        return float(spread_values[-1])
    except (TypeError, ValueError):
        return None


def _build_style_rotation_summary(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    return {
        "left_name": str(meta.get("left_name") or "左侧标的").strip(),
        "right_name": str(meta.get("right_name") or "右侧标的").strip(),
        "return_window_days": meta.get("return_window_days"),
        "display_window_days": meta.get("display_window_days"),
        "latest_spread": _get_latest_style_rotation_spread(payload),
    }


def _render_fragment(
    payload: Optional[Dict[str, Any]],
    as_of_label: Optional[str],
    chart_path: Optional[Path],
) -> str:
    summary = _build_style_rotation_summary(payload)
    if not summary:
        return ""

    latest_spread = summary.get("latest_spread")
    spread_text = f"{latest_spread:.2f}%" if latest_spread is not None else "-"
    return_window = summary.get("return_window_days")
    display_window = summary.get("display_window_days")
    chart_html = ""
    if chart_path:
        chart_html = (
            f'<div style="padding:14px 0 0 0">'
            f'<img src="cid:{STYLE_ROTATION_CHART_CID}" alt="风格轮动收益率差值图" '
            f'style="width:100%;max-width:100%;height:auto;display:block">'
            f'</div>'
        )

    return (
        f'<tr><td style="padding:24px 28px 0 28px">'
        f'<div style="border-top:1px solid {_EMAIL_BORDER_CARD_SPLIT};padding-top:24px">'
        f'<div style="font-size:18px;font-weight:700;color:{_EMAIL_TEXT_PRIMARY}">'
        f'风格轮动收益率差值</div>'
        f'<div style="font-size:12px;color:{_EMAIL_LABEL_COLOR};margin-top:4px">'
        f'数据截至 {escape(str(as_of_label or "-"))}</div>'
        f'<div style="font-size:14px;color:{_EMAIL_TEXT_PRIMARY};margin-top:10px">'
        f'{escape(str(summary["left_name"]))} vs {escape(str(summary["right_name"]))}</div>'
        f'<div style="font-size:12px;color:{_EMAIL_MUTED_COLOR};margin-top:6px">'
        f'展示窗口 {escape(str(display_window or "-"))} 天'
        f' &nbsp;|&nbsp; 计算窗口 {escape(str(return_window or "-"))} 天'
        f' &nbsp;|&nbsp; 当前差值 {escape(spread_text)}</div>'
        f'{chart_html}'
        f'</div></td></tr>'
    )


def build_section(work_dir: Path) -> Optional[Dict[str, Any]]:
    """取数 + 作图 + 渲染片段。失败返回 None(次要区块,跳过不中断整封邮件)。"""
    try:
        payload = collect_style_rotation_preview_payload()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 风格轮动数据获取失败,跳过该区块: {exc}")
        return None

    as_of_label = resolve_as_of_label(payload)

    try:
        chart_path = generate_style_rotation_chart(payload, work_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 风格轮动图表生成失败,片段不带图: {exc}")
        chart_path = None

    html = _render_fragment(payload, as_of_label, chart_path)
    if not html:
        return None

    inline_images = {STYLE_ROTATION_CHART_CID: str(chart_path)} if chart_path else {}
    return {"html": html, "inline_images": inline_images, "as_of_date": as_of_label}
