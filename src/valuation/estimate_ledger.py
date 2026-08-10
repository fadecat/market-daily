"""Produce auditable index valuation estimates from the existing archives."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd

from ..common import storage
from . import fetch


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE_ROOT = storage.ARCHIVE_DIR
DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "data" / "research" / "index_valuation_estimates"


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


def _load_ledger_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"estimate ledger must be a JSON object: {path}")
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise ValueError(f"estimate ledger records must be a list: {path}")
    return payload


def _validate_index_code(index_code: str) -> str:
    code = str(index_code).strip()
    if re.fullmatch(r"[0-9]{6}", code) is None:
        raise ValueError("index_code must contain exactly six ASCII digits")
    return code


def _validate_existing_ledger_index_code(payload: dict[str, Any], index_code: str) -> None:
    existing_code = str(payload.get("index_code") or "").strip()
    if existing_code and existing_code != index_code:
        raise ValueError(
            f"existing ledger index_code {existing_code!r} does not match requested "
            f"index_code {index_code!r}"
        )


def _merge_records(
    existing: list[Any], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Overlay generated records while keeping future fields on an existing day."""
    merged: dict[str, dict[str, Any]] = {}
    without_date: list[dict[str, Any]] = []
    for record in existing:
        if not isinstance(record, dict):
            continue
        estimate_date = str(record.get("estimate_date") or "").strip()
        if estimate_date:
            merged[estimate_date] = record
        else:
            without_date.append(record)
    for record in incoming:
        estimate_date = str(record.get("estimate_date") or "").strip()
        if not estimate_date:
            continue
        merged[estimate_date] = {**merged.get(estimate_date, {}), **record}
    return without_date + [merged[date] for date in sorted(merged)]


def _logical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Exclude volatile write metadata when deciding whether a write is needed."""
    return {key: value for key, value in payload.items() if key != "updated_at"}


def _updated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_and_write(
    output_path: Path, index_code: str, incoming: list[dict[str, Any]]
) -> bool:
    existing = _load_ledger_payload(output_path)
    _validate_existing_ledger_index_code(existing, index_code)
    payload = dict(existing)
    payload["schema_version"] = 1
    payload["index_code"] = index_code
    payload["records"] = _merge_records(existing.get("records", []), incoming)
    payload["updated_at"] = _updated_at()

    if existing and _logical_payload(payload) == _logical_payload(existing):
        return False

    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return True


def refresh_estimate_ledger(
    index_code: str,
    *,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    bond_history_fetcher: Callable[..., Any] = fetch.fetch_cn_10y_bond_history_with_archive_fallback,
) -> bool:
    """Build and persist one index's estimates, upserting by ``estimate_date``."""
    code = _validate_index_code(index_code)
    archive_path = Path(archive_root)
    output_path = Path(output_root) / f"{code}.json"
    _validate_existing_ledger_index_code(_load_ledger_payload(output_path), code)
    fetched = bond_history_fetcher(archive_root=archive_path)
    bond_history = fetched[0] if isinstance(fetched, tuple) else fetched
    incoming = build_estimate_records(
        code, archive_root=archive_path, bond_history=bond_history
    )
    return _upsert_and_write(Path(output_root) / f"{code}.json", code, incoming)


def main(argv: Sequence[str] | None = None) -> int:
    """Generate estimate ledgers for the explicitly selected index codes."""
    parser = argparse.ArgumentParser(description="生成指数估算账本")
    parser.add_argument("--index-code", action="append", required=True)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    for code in dict.fromkeys(args.index_code):
        refresh_estimate_ledger(
            code,
            archive_root=args.archive_root,
            output_root=args.output_root,
            bond_history_fetcher=fetch.fetch_cn_10y_bond_history_with_archive_fallback,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
