"""商品极值板块:核心扫描逻辑(akshare 抓取 + 分位数计算)。

移植自 commodity-monitor-days 的 core.py,基本照搬。akshare 延迟到 fetch_history
内导入,避免无 akshare 环境(如 CI 测试)导入本模块即失败。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .config import MonitorConfig, SymbolConfig


@dataclass
class SymbolResult:
    symbol: SymbolConfig
    latest_date: date | None = None
    latest_price: float | None = None
    window_percentiles: dict[str, float | None] | None = None
    high_windows: list[str] | None = None
    low_windows: list[str] | None = None
    stale_days: int | None = None
    error: str | None = None


def percentile_of_latest(series: pd.Series) -> float:
    """Return percentile rank of latest value within the full series."""
    if series.empty:
        raise ValueError("Cannot compute percentile from empty series")
    latest = series.iloc[-1]
    return float((series <= latest).mean() * 100.0)


def compute_window_percentiles(
    close_series: pd.Series, windows: dict[str, int], min_points: int = 20
) -> dict[str, float | None]:
    percentiles: dict[str, float | None] = {}
    for label, days in windows.items():
        if len(close_series) < days:
            percentiles[label] = None
            continue
        window = close_series.tail(days)
        if len(window) < min_points:
            percentiles[label] = None
            continue
        percentiles[label] = round(percentile_of_latest(window), 2)
    return percentiles


def _normalize_ohlc_df(df: pd.DataFrame) -> pd.DataFrame:
    columns = {str(col).strip().lower(): col for col in df.columns}
    date_col = columns.get("date") or columns.get("日期")
    close_col = columns.get("close") or columns.get("收盘")
    if date_col is None or close_col is None:
        raise ValueError(f"Required columns not found, got: {list(df.columns)}")

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df[date_col], errors="coerce"),
            "close": pd.to_numeric(df[close_col], errors="coerce"),
        }
    )
    out = out.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date")
    out = out.reset_index(drop=True)
    if out.empty:
        raise ValueError("Normalized dataframe is empty")
    return out


def fetch_history(symbol: SymbolConfig) -> pd.DataFrame:
    import akshare as ak  # 延迟导入,无 akshare 环境下本模块仍可导入

    market = symbol.market
    if market == "foreign":
        raw = ak.futures_foreign_hist(symbol=symbol.code)
        return _normalize_ohlc_df(raw)
    if market in {"domestic", "china", "cn"}:
        raw = ak.futures_zh_daily_sina(symbol=symbol.code)
        return _normalize_ohlc_df(raw)
    raise ValueError(f"Unsupported market '{symbol.market}' for {symbol.code}")


def evaluate_symbol(symbol: SymbolConfig, cfg: MonitorConfig) -> SymbolResult:
    result = SymbolResult(symbol=symbol, high_windows=[], low_windows=[], window_percentiles={})
    try:
        history = fetch_history(symbol)
        latest_row = history.iloc[-1]
        latest_date = latest_row["date"].date()
        latest_price = float(latest_row["close"])
        if latest_price <= 0:
            raise ValueError(f"Non-positive latest price: {latest_price}")

        percentiles = compute_window_percentiles(history["close"], cfg.windows)
        high_windows = [
            label
            for label, value in percentiles.items()
            if value is not None and value >= cfg.thresholds.high_percentile
        ]
        low_windows = [
            label
            for label, value in percentiles.items()
            if value is not None and value <= cfg.thresholds.low_percentile
        ]

        result.latest_date = latest_date
        result.latest_price = latest_price
        result.window_percentiles = percentiles
        result.high_windows = high_windows
        result.low_windows = low_windows
        result.stale_days = (date.today() - latest_date).days
        return result
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
        return result


def run_scan(cfg: MonitorConfig, max_symbols: int | None = None) -> list[SymbolResult]:
    enabled_symbols = [item for item in cfg.symbols if item.enabled]
    if max_symbols is not None:
        enabled_symbols = enabled_symbols[:max_symbols]

    results: list[SymbolResult] = []
    for idx, symbol in enumerate(enabled_symbols):
        if idx > 0:
            pause = random.uniform(cfg.delay.min_seconds, cfg.delay.max_seconds)
            time.sleep(pause)
        results.append(evaluate_symbol(symbol, cfg))
    return results
