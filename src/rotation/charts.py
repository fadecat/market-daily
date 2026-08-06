"""ETF 20 日轮动 组合净值曲线图。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ..common import fonts

NAV_CHART_CID = "etf_rotation_20d_nav_chart"


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(str(value)[:10])


def generate_nav_chart(
    history: List[Dict[str, Any]],
    output_path: Path,
    *,
    title: str = "20 日涨幅 ETF 轮动 组合净值",
) -> Path:
    fonts.apply_cjk(plt)

    points = [e for e in history if e.get("nav") is not None]
    dates = [_parse_date(e["date"]) for e in points]
    navs = [float(e["nav"]) for e in points]

    fig, ax = plt.subplots(figsize=(9, 4))
    if dates:
        ax.plot(dates, navs, color="#2c7be5", linewidth=2, marker="o", markersize=3)
        ax.fill_between(
            dates,
            1.0,
            navs,
            where=[v >= 1.0 for v in navs],
            color="#d93025",
            alpha=0.08,
            interpolate=True,
        )
        ax.fill_between(
            dates,
            navs,
            1.0,
            where=[v < 1.0 for v in navs],
            color="#2E7D32",
            alpha=0.08,
            interpolate=True,
        )
        ax.axhline(1.0, color="#9aa0a6", linewidth=1, linestyle="--")
    ax.set_title(title, fontsize=13)
    ax.set_ylabel("组合净值")
    ax.grid(True, linestyle=":", alpha=0.4)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=130)
    plt.close(fig)
    return output_path
