from __future__ import annotations

from statistics import mean, pstdev
from typing import Iterable


def build_relative_ratio_series(
    left_prices: Iterable[tuple[str, float]],
    right_prices: Iterable[tuple[str, float]],
) -> list[tuple[str, float]]:
    left_map = dict(left_prices)
    right_map = dict(right_prices)
    common_dates = sorted(set(left_map) & set(right_map))
    return [
        (trade_date, left_map[trade_date] / right_map[trade_date])
        for trade_date in common_dates
        if right_map[trade_date] > 0
    ]


def _percentile_rank(values: list[float], current: float) -> float:
    if not values:
        return 0.0
    rank = sum(1 for value in values if value <= current)
    return round(100.0 * rank / len(values), 4)


def _window_zscore(values: list[float], current: float, window: int) -> float | None:
    sample = values[-window:] if len(values) >= window else values
    if len(sample) < 2:
        return None
    sigma = pstdev(sample)
    if sigma == 0:
        return 0.0
    return round((current - mean(sample)) / sigma, 6)


def build_ratio_snapshot(
    ratio_series: list[tuple[str, float]],
    *,
    as_of_date: str,
) -> dict[str, float | None]:
    filtered = [(date, value) for date, value in ratio_series if date <= as_of_date]
    if not filtered:
        raise ValueError("ratio series is empty at as_of_date")
    current = filtered[-1][1]
    history = [value for _, value in filtered]
    return {
        "value": current,
        "percentile": _percentile_rank(history, current),
        "zscore_60d": _window_zscore(history, current, 60),
        "zscore_120d": _window_zscore(history, current, 120),
    }
