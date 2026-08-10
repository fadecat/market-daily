"""Produce auditable index valuation estimates from the existing archives."""
from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import Any

import pandas as pd


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"archive records must be a list: {path}")
    return [record for record in records if isinstance(record, dict)]


def _date_string(value: Any) -> str | None:
    date_value = pd.to_datetime(value, errors="coerce")
    if pd.isna(date_value):
        return None
    return date_value.strftime("%Y-%m-%d")


def _load_price_map(index_code: str, archive_root: Path) -> dict[str, float]:
    prices: dict[str, float] = {}
    path = archive_root / "index_eod" / f"{index_code}.json"
    for record in _load_records(path):
        trade_date = _date_string(record.get("trdDt"))
        close = _safe_float(record.get("pxClose"))
        if trade_date and close is not None and close > 0:
            prices[trade_date] = close
    return prices


def _load_valuation_map(index_code: str, archive_root: Path) -> dict[str, dict[str, float]]:
    valuations: dict[str, dict[str, float]] = {}
    path = archive_root / "index_valuation_percentile" / f"{index_code}.json"
    for record in _load_records(path):
        trade_date = _date_string(record.get("trdDt"))
        pe_ttm = _safe_float(record.get("pETtm"))
        pb_lf = _safe_float(record.get("pBLf"))
        if trade_date and pe_ttm is not None and pe_ttm > 0 and pb_lf is not None and pb_lf > 0:
            valuations[trade_date] = {"pe_ttm": pe_ttm, "pb_lf": pb_lf}
    return valuations


def _load_dividend_map(index_code: str, archive_root: Path) -> dict[str, float]:
    dividends: dict[str, float] = {}
    path = archive_root / "index_dividend_ratio" / f"{index_code}.json"
    for record in _load_records(path):
        trade_date = _date_string(record.get("trdDt"))
        dividend_yield = _safe_float(record.get("dividendYield"))
        if trade_date and dividend_yield is not None:
            dividends[trade_date] = dividend_yield
    return dividends


def _bond_map(bond_history: pd.DataFrame) -> dict[str, float]:
    if not isinstance(bond_history, pd.DataFrame):
        raise TypeError("bond_history must be a pandas DataFrame")
    if not {"date", "yield_pct"}.issubset(bond_history.columns):
        return {}

    bonds: dict[str, float] = {}
    for record in bond_history[["date", "yield_pct"]].to_dict(orient="records"):
        trade_date = _date_string(record["date"])
        yield_pct = _safe_float(record["yield_pct"])
        if trade_date and yield_pct is not None:
            bonds[trade_date] = yield_pct
    return bonds


def _latest_base(
    values: dict[str, Any], estimate_date: str, prices: dict[str, float]
) -> tuple[str, float, Any] | None:
    base_dates = [
        trade_date
        for trade_date in values
        if trade_date <= estimate_date and trade_date in prices
    ]
    if not base_dates:
        return None
    base_date = max(base_dates)
    return base_date, prices[base_date], values[base_date]


def _estimate_row(
    estimate_date: str,
    estimate_close: float,
    valuation_base: tuple[str, float, dict[str, float]],
    dividend_base: tuple[str, float, float],
    bond_yield: float,
) -> dict[str, Any]:
    valuation_date, valuation_close, valuation = valuation_base
    dividend_date, dividend_close, dividend_yield = dividend_base
    valuation_factor = estimate_close / valuation_close
    dividend_factor = estimate_close / dividend_close
    estimated_pe_ttm = valuation["pe_ttm"] * valuation_factor
    estimated_pb_lf = valuation["pb_lf"] * valuation_factor
    estimated_dividend_yield_raw = dividend_yield / dividend_factor
    pe_ttm = round(estimated_pe_ttm, 6)
    pb_lf = round(estimated_pb_lf, 6)
    estimated_dividend_yield = round(estimated_dividend_yield_raw, 6)

    return {
        "estimate_date": estimate_date,
        "status": "estimated",
        "inputs": {
            "estimate_close": estimate_close,
            "valuation_price_factor": round(valuation_factor, 6),
            "dividend_price_factor": round(dividend_factor, 6),
            "valuation_base": {
                "date": valuation_date,
                "close": valuation_close,
                "pe_ttm": valuation["pe_ttm"],
                "pb_lf": valuation["pb_lf"],
            },
            "dividend_base": {
                "date": dividend_date,
                "close": dividend_close,
                "dividend_yield": dividend_yield,
            },
            "bond_10y": {"date": estimate_date, "yield_pct": bond_yield},
        },
        "estimates": {
            "pe_ttm": pe_ttm,
            "pb_lf": pb_lf,
            "dividend_yield": estimated_dividend_yield,
            "dividend_yield_spread": round(estimated_dividend_yield_raw - bond_yield, 6),
            "earnings_yield_spread": round(100.0 / estimated_pe_ttm - bond_yield, 6),
        },
    }


def build_estimate_records(
    index_code: str, *, archive_root: Path | str, bond_history: pd.DataFrame
) -> list[dict[str, Any]]:
    """Build one estimate for each price day lacking complete official inputs.

    The valuation and dividend sources can lag by different dates, so each keeps
    its own source date and close.  Bond values must be real values for the
    estimate date; no prior-day bond row is carried forward.
    """
    root = Path(archive_root)
    prices = _load_price_map(index_code, root)
    valuations = _load_valuation_map(index_code, root)
    dividends = _load_dividend_map(index_code, root)
    bonds = _bond_map(bond_history)
    results: list[dict[str, Any]] = []

    for estimate_date, estimate_close in sorted(prices.items()):
        if estimate_date in valuations and estimate_date in dividends:
            continue
        valuation_base = _latest_base(valuations, estimate_date, prices)
        dividend_base = _latest_base(dividends, estimate_date, prices)
        bond_yield = bonds.get(estimate_date)
        if valuation_base and dividend_base and bond_yield is not None:
            results.append(
                _estimate_row(
                    estimate_date,
                    estimate_close,
                    valuation_base,
                    dividend_base,
                    bond_yield,
                )
            )
    return results
