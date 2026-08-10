"""Apply an auditable valuation estimate to an email valuation item.

This module deliberately consumes existing estimate and archive data only.  It
does not fetch data and never mutates the item or DataFrames supplied by its
caller.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .metrics import compute_equity_bond_spread_percentiles


_VALUATION_WINDOWS: tuple[tuple[str, object], ...] = (
    ("3M", pd.DateOffset(months=3)),
    ("6M", pd.DateOffset(months=6)),
    ("1Y", pd.DateOffset(years=1)),
    ("2Y", pd.DateOffset(years=2)),
    ("3Y", pd.DateOffset(years=3)),
    ("5Y", pd.DateOffset(years=5)),
    ("10Y", pd.DateOffset(years=10)),
)


@dataclass(frozen=True)
class EstimateOverlay:
    """A replacement email item and the corresponding PE series for charts."""

    item: dict
    pe_history: pd.DataFrame


def _number(value: Any, *, positive: bool = False) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(result) or (positive and result <= 0):
        return None
    return result


def _date(value: Any) -> pd.Timestamp | None:
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except (TypeError, ValueError, OverflowError):
        return None
    if not isinstance(parsed, pd.Timestamp) or pd.isna(parsed):
        return None
    return parsed.normalize()


def _date_text(value: Any) -> str | None:
    parsed = _date(value)
    return None if parsed is None else parsed.strftime("%Y-%m-%d")


def _estimate_values(estimate: Any, price_date: str) -> tuple[float, float, float] | None:
    if not isinstance(estimate, dict) or _date_text(estimate.get("estimate_date")) != price_date:
        return None
    values = estimate.get("estimates")
    if not isinstance(values, dict):
        return None
    pe = _number(values.get("pe_ttm"), positive=True)
    pb = _number(values.get("pb_lf"), positive=True)
    dividend = _number(values.get("dividend_yield"), positive=True)
    dividend_spread = _number(values.get("dividend_yield_spread"))
    earnings_spread = _number(values.get("earnings_yield_spread"))
    if None in (pe, pb, dividend, dividend_spread, earnings_spread):
        return None
    return pe, pb, dividend


def _history_frame(
    history: Any, columns: dict[str, Iterable[str]]
) -> pd.DataFrame | None:
    if not isinstance(history, pd.DataFrame):
        return None
    resolved: dict[str, str] = {}
    for output, candidates in columns.items():
        source = next((name for name in candidates if name in history.columns), None)
        if source is None:
            return None
        resolved[output] = source
    frame = pd.DataFrame({name: history[source] for name, source in resolved.items()})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    for name in resolved:
        if name != "date":
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame.dropna().copy()


def _replace_target(frame: pd.DataFrame, target: pd.Timestamp, values: dict[str, float]) -> pd.DataFrame:
    result = frame[frame["date"] != target].copy()
    result = pd.concat([result, pd.DataFrame([{ "date": target, **values }])], ignore_index=True)
    return result.sort_values("date").reset_index(drop=True)


def _percentiles(frame: pd.DataFrame, value_column: str, current: float, target: pd.Timestamp) -> dict[str, float]:
    historical = frame[frame["date"] <= target]
    values = historical[value_column]
    result: dict[str, float] = {}
    for label, offset in _VALUATION_WINDOWS:
        window = historical[historical["date"] >= target - offset][value_column]
        result[label] = round(float((window < current).mean() * 100), 2) if not window.empty else 0.0
    year_to_date = historical[historical["date"] >= pd.Timestamp(year=target.year, month=1, day=1)][value_column]
    result["今年以来"] = round(float((year_to_date < current).mean() * 100), 2) if not year_to_date.empty else 0.0
    result["成立以来"] = round(float((values < current).mean() * 100), 2) if not values.empty else 0.0
    return result


def _dividend_values(frame: pd.DataFrame, current: float, target: pd.Timestamp) -> tuple[dict[str, float], float]:
    historical = frame[frame["date"] <= target]
    percentiles: dict[str, float] = {}
    for label, years in (("1Y", 1), ("3Y", 3), ("5Y", 5), ("10Y", 10)):
        window = historical[historical["date"] >= target - pd.DateOffset(years=years)]["dividend_yield"]
        if len(window) >= 20:
            percentiles[label] = round(float((window <= current).mean() * 100), 2)
    average_window = historical[historical["date"] >= target - pd.DateOffset(years=5)]["dividend_yield"]
    return percentiles, round(float(average_window.mean()), 4)


def apply_estimate(
    item: dict,
    *,
    estimate: dict,
    price_date: str,
    valuation_history: pd.DataFrame,
    dividend_history: pd.DataFrame,
    bond_history: pd.DataFrame,
) -> EstimateOverlay | None:
    """Return an item whose valuation fields are all recomputed for ``price_date``."""
    target = _date(price_date)
    price_date_text = _date_text(price_date)
    values = _estimate_values(estimate, price_date_text or "")
    if target is None or price_date_text is None or values is None or not isinstance(item, dict):
        return None
    pe, pb, dividend = values
    valuations = _history_frame(
        valuation_history, {"date": ("date",), "pe": ("pe_ttm", "pe", "pETtm"), "pb": ("pb_lf", "pb", "pBLf")}
    )
    dividends = _history_frame(
        dividend_history, {"date": ("date",), "dividend_yield": ("dividend_yield", "yield", "dividendYield")}
    )
    bonds = _history_frame(bond_history, {"date": ("date",), "yield_pct": ("yield_pct",)})
    if valuations is None or dividends is None or bonds is None:
        return None
    valuations = valuations[(valuations["date"] <= target) & (valuations["pe"] > 0) & (valuations["pb"] > 0)]
    dividends = dividends[(dividends["date"] <= target) & (dividends["dividend_yield"] > 0)]
    bonds = bonds[(bonds["date"] <= target) & bonds["yield_pct"].map(lambda value: _number(value) is not None)]
    if valuations.empty or dividends.empty:
        return None
    same_day_bond = bonds.loc[bonds["date"] == target, "yield_pct"]
    if same_day_bond.empty:
        return None
    bond_yield = _number(same_day_bond.iloc[-1])
    if bond_yield is None:
        return None

    valuations = _replace_target(valuations, target, {"pe": pe, "pb": pb})
    dividends = _replace_target(dividends, target, {"dividend_yield": dividend})
    bonds = _replace_target(bonds, target, {"yield_pct": bond_yield})
    pe_history = valuations[["date", "pe"]].copy()
    spread = compute_equity_bond_spread_percentiles(pe_history, bonds[["date", "yield_pct"]])
    dividend_percentiles, dividend_average = _dividend_values(dividends, dividend, target)

    replacement = deepcopy(item)
    metric_values = replacement.get("index_valuation_metrics")
    metrics = deepcopy(metric_values) if isinstance(metric_values, dict) else {}
    metrics["PE(TTM)"] = {"current": pe, "percentiles": _percentiles(valuations, "pe", pe, target)}
    metrics["PB(LF)"] = {"current": pb, "percentiles": _percentiles(valuations, "pb", pb, target)}
    replacement.update(
        {
            "index_valuation_metrics": metrics,
            "index_valuation_date": price_date_text,
            "index_dividend_yield": dividend,
            "index_dividend_yield_date": price_date_text,
            "index_dividend_yield_percentiles": dividend_percentiles,
            "index_dividend_yield_average_5y": dividend_average,
            "estimate_meta": {"date": price_date_text, "status": "estimated"},
            "equity_bond_ratio": round(100.0 / pe - bond_yield, 4),
            "equity_bond_spread": spread,
        }
    )
    return EstimateOverlay(item=replacement, pe_history=pe_history)


def _safe_records(path: Path) -> list[dict[str, Any]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return None
    return [row for row in records if isinstance(row, dict)]


def _valid_index_code(item: dict) -> str | None:
    code = str(item.get("index_code") or item.get("code") or "").strip()
    return code if len(code) == 6 and code.isascii() and code.isdigit() else None


def _archive_history(records: list[dict[str, Any]], values: dict[str, str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        row = {"date": record.get("trdDt", record.get("date"))}
        row.update({output: record.get(source) for output, source in values.items()})
        rows.append(row)
    return pd.DataFrame(rows, columns=["date", *values])


def apply_from_archives(
    item: dict,
    *,
    estimate: dict,
    price_date: str,
    archive_root: Path | str,
    bond_history: pd.DataFrame,
) -> EstimateOverlay | None:
    """Load local valuation, dividend and EOD archives before applying an estimate."""
    if not isinstance(item, dict):
        return None
    code = _valid_index_code(item)
    target = _date_text(price_date)
    if code is None or target is None:
        return None
    root = Path(archive_root)
    valuation_rows = _safe_records(root / "index_valuation_percentile" / f"{code}.json")
    dividend_rows = _safe_records(root / "index_dividend_ratio" / f"{code}.json")
    eod_rows = _safe_records(root / "index_eod" / f"{code}.json")
    if valuation_rows is None or dividend_rows is None or eod_rows is None:
        return None
    has_target_close = any(
        _date_text(row.get("trdDt", row.get("date"))) == target
        and (_number(row.get("pxClose", row.get("close")), positive=True) is not None)
        for row in eod_rows
    )
    if not has_target_close:
        return None
    return apply_estimate(
        item,
        estimate=estimate,
        price_date=target,
        valuation_history=_archive_history(valuation_rows, {"pe_ttm": "pETtm", "pb_lf": "pBLf"}),
        dividend_history=_archive_history(dividend_rows, {"dividend_yield": "dividendYield"}),
        bond_history=bond_history,
    )


def latest_price_date(index_code: str, archive_root: Path | str) -> str | None:
    """Return the latest archived EOD date carrying a finite positive close."""
    code = str(index_code).strip()
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    records = _safe_records(Path(archive_root) / "index_eod" / f"{code}.json")
    if records is None:
        return None
    dates = [
        parsed for record in records
        if _number(record.get("pxClose", record.get("close")), positive=True) is not None
        for parsed in [_date_text(record.get("trdDt", record.get("date")))]
        if parsed is not None
    ]
    return max(dates) if dates else None
