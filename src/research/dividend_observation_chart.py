from __future__ import annotations

import argparse
import json
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd

from ..valuation import estimate_ledger
from .dividend_observation_config import (
    DEFAULT_CONFIG_PATH,
    load_dividend_observation_window_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE_ROOT = REPO_ROOT / "data" / "archive"
DEFAULT_DATASET_PATH = REPO_ROOT / "data" / "research" / "value_growth_drawdown_events.json"
DEFAULT_EVENT_STATE_MODEL_PATH = REPO_ROOT / "data" / "research" / "event_state_model.json"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "research" / "dividend_observation_930955.json"
DEFAULT_STYLE_ROTATION_PAYLOAD_PATH = REPO_ROOT / "data" / "research" / "style_rotation_preview.json"
DEFAULT_ESTIMATE_ROOT = REPO_ROOT / "data" / "research" / "index_valuation_estimates"

INDEX_CODE = "930955"
INDEX_NAME = "红利低波100"
STYLE_LEFT_CODE = "399376"
STYLE_RIGHT_CODE = "399373"


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    return round(float(value), digits) if value is not None else None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_trade_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _load_json(path)
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"archive records must be a list: {path}")
    return [record for record in records if isinstance(record, dict)]


def _load_index_price_series(
    index_code: str,
    archive_root: Path,
) -> list[tuple[str, float]]:
    path = archive_root / "index_eod" / f"{index_code}.json"
    points: list[tuple[str, float]] = []
    for row in _load_records(path):
        trade_date = _normalize_trade_date(row.get("trdDt"))
        close = _safe_float(row.get("pxClose"))
        if trade_date is None or close is None or close <= 0:
            continue
        points.append((str(trade_date), float(close)))
    points.sort(key=lambda item: item[0])
    return points


def _date_map(
    archive_root: Path,
    folder: str,
    filename: str,
    date_key: str,
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for row in _load_records(archive_root / folder / filename):
        trade_date = _normalize_trade_date(row.get(date_key))
        if trade_date is None:
            continue
        mapping[str(trade_date)] = row
    return mapping


def _estimate_by_date(index_code: str, estimate_root: Path) -> dict[str, dict[str, Any]]:
    path = estimate_root / f"{index_code}.json"
    if not path.exists():
        return {}
    try:
        payload = _load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or str(payload.get("index_code")) != str(index_code):
        return {}
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return {}
    estimates: dict[str, dict[str, Any]] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("status") != "estimated":
            continue
        estimate_date = _normalize_trade_date(row.get("estimate_date"))
        if estimate_date is not None and isinstance(row.get("estimates"), dict):
            estimates[estimate_date] = row
    return estimates


def _ensure_latest_estimate(
    *,
    archive_root: Path,
    estimate_root: Path,
    dates: list[str],
    bond: dict[str, dict[str, Any]],
) -> None:
    if not dates or dates[-1] in _estimate_by_date(INDEX_CODE, estimate_root):
        return
    bond_history = pd.DataFrame(
        [
            {"date": trade_date, "yield_pct": _safe_float(row.get("中国国债收益率10年"))}
            for trade_date, row in bond.items()
        ]
    )
    if bond_history.empty or bond_history["yield_pct"].isna().all():
        return
    try:
        estimate_ledger.refresh_estimate_ledger(
            INDEX_CODE,
            archive_root=archive_root,
            output_root=estimate_root,
            bond_history=bond_history,
        )
    except (OSError, ValueError, TypeError):
        return


def _rolling_peak_drawdown(
    closes: list[float],
    *,
    window_days: int,
) -> list[float | None]:
    result: list[float | None] = []
    for index, close in enumerate(closes):
        start = max(0, index - window_days + 1)
        window = closes[start : index + 1]
        peak = max(window) if window else None
        result.append(_round_or_none(close / peak - 1.0 if peak else None))
    return result


def _percentile_series(
    values: list[float | None],
    *,
    window_days: int,
) -> list[float | None]:
    result: list[float | None] = []
    for index, value in enumerate(values):
        if value is None:
            result.append(None)
            continue
        start = max(0, index - window_days + 1)
        window = [item for item in values[start : index + 1] if item is not None]
        if not window:
            result.append(None)
            continue
        rank = sum(1 for item in window if item <= value)
        result.append(round(rank / len(window) * 100.0, 4))
    return result


def _default_style_rotation_fetcher() -> dict[str, Any]:
    from ..valuation.style_rotation import collect_style_rotation_preview_payload

    payload = collect_style_rotation_preview_payload()
    if not isinstance(payload, dict):
        raise ValueError("style rotation payload must be a mapping")
    return payload


def _load_or_fetch_style_rotation_payload(
    style_rotation_payload_path: Path,
    style_rotation_fetcher: Callable[[], dict[str, Any]] | None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    if style_rotation_payload_path.exists() and not force_refresh:
        payload = _load_json(style_rotation_payload_path)
        if not isinstance(payload, dict):
            raise ValueError("style rotation payload must be a mapping")
        return payload

    if style_rotation_fetcher is None:
        raise FileNotFoundError(f"style rotation payload not found: {style_rotation_payload_path}")

    payload = style_rotation_fetcher()
    if not isinstance(payload, dict):
        raise ValueError("style rotation payload must be a mapping")
    style_rotation_payload_path.parent.mkdir(parents=True, exist_ok=True)
    style_rotation_payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _style_rotation_spread_values_from_payload(
    dates: list[str],
    payload: dict[str, Any],
) -> list[float | None]:
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, dict):
        return [None for _ in dates]

    raw_dates = series.get("dates")
    raw_spread = series.get("spread")
    if not isinstance(raw_dates, list) or not isinstance(raw_spread, list):
        return [None for _ in dates]

    spread_by_date: dict[str, float | None] = {}
    for trade_date, spread in zip(raw_dates, raw_spread):
        spread_by_date[str(trade_date)] = _safe_float(spread)
    return [spread_by_date.get(trade_date) for trade_date in dates]


def _event_state_series(
    *,
    dates: list[str],
    dataset_path: Path,
    event_state_model_path: Path,
    index_code: str,
) -> list[str | None]:
    dataset = _load_json(dataset_path) if dataset_path.exists() else {}
    event_rows = {
        str(event.get("event_id")): event
        for event in dataset.get("events", [])
        if str(event.get("index_code")) == index_code and event.get("event_id") is not None
    }
    payload = _load_json(event_state_model_path) if event_state_model_path.exists() else {}
    states = [None for _ in dates]
    date_to_index = {trade_date: idx for idx, trade_date in enumerate(dates)}
    for row in payload.get("event_state_model", []):
        if str(row.get("index")) != index_code:
            continue
        event_id = str(row.get("event_id"))
        event = event_rows.get(event_id)
        if not event:
            continue
        recovery_date = str(event.get("recovery_date") or "")
        confirm_date = str(row.get("state_confirm_date") or "")
        state_name = row.get("new_state")
        if not recovery_date or not confirm_date or state_name is None:
            continue
        start = date_to_index.get(recovery_date)
        end = date_to_index.get(confirm_date)
        if start is None or end is None:
            continue
        for idx in range(start, end + 1):
            states[idx] = str(state_name)
    return states


def build_dividend_observation_payload(
    *,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    estimate_root: Path | str = DEFAULT_ESTIMATE_ROOT,
    dataset_path: Path | str = DEFAULT_DATASET_PATH,
    event_state_model_path: Path | str = DEFAULT_EVENT_STATE_MODEL_PATH,
    style_rotation_payload_path: Path | str = DEFAULT_STYLE_ROTATION_PAYLOAD_PATH,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    style_rotation_fetcher: Callable[[], dict[str, Any]] | None = None,
    force_refresh_style_rotation_payload: bool = False,
    analysis_window_years: int | None = None,
    display_window_years: int | None = None,
    drawdown_window_days: int | None = None,
    valuation_window_days: int | None = None,
    spread_window_days: int | None = None,
    style_window_days: int | None = None,
) -> dict[str, Any]:
    root = Path(archive_root)
    estimates_root = Path(estimate_root)
    dataset_file = Path(dataset_path)
    event_state_file = Path(event_state_model_path)
    style_rotation_file = Path(style_rotation_payload_path)
    window_config = load_dividend_observation_window_config(config_path)

    analysis_window_years = analysis_window_years or window_config["analysis_window_years"]
    display_window_years = display_window_years or window_config["display_window_years"]
    drawdown_window_days = drawdown_window_days or window_config["drawdown_days"]
    valuation_window_days = valuation_window_days or window_config["valuation_days"]
    spread_window_days = spread_window_days or window_config["spread_days"]
    style_window_days = style_window_days or window_config["style_days"]

    price_series = _load_index_price_series(INDEX_CODE, root)
    dates = [trade_date for trade_date, _ in price_series]
    closes = [float(close) for _, close in price_series]

    valuation = _date_map(root, "index_valuation_percentile", f"{INDEX_CODE}.json", "trdDt")
    dividend = _date_map(root, "index_dividend_ratio", f"{INDEX_CODE}.json", "trdDt")
    bond = _date_map(root, "bond_10y", "china_10y.json", "日期")
    _ensure_latest_estimate(
        archive_root=root,
        estimate_root=estimates_root,
        dates=dates,
        bond=bond,
    )
    estimate_by_date = _estimate_by_date(INDEX_CODE, estimates_root)

    pe_values: list[float | None] = []
    pb_values: list[float | None] = []
    dividend_spread_values: list[float | None] = []
    earnings_spread_values: list[float | None] = []
    estimate_used: list[dict[str, float] | None] = []
    for trade_date in dates:
        pe = _safe_float((valuation.get(trade_date) or {}).get("pETtm"))
        pb = _safe_float((valuation.get(trade_date) or {}).get("pBLf"))
        dividend_yield = _safe_float((dividend.get(trade_date) or {}).get("dividendYield"))
        bond_10y = _safe_float((bond.get(trade_date) or {}).get("中国国债收益率10年"))
        dividend_spread = dividend_yield - bond_10y if dividend_yield is not None and bond_10y is not None else None
        earnings_spread = 100.0 / pe - bond_10y if pe is not None and pe > 0 and bond_10y is not None else None

        estimate_values = (estimate_by_date.get(trade_date) or {}).get("estimates")
        estimated = {
            key: _safe_float(estimate_values.get(key))
            for key in (
                "pe_ttm",
                "pb_lf",
                "dividend_yield_spread",
                "earnings_yield_spread",
            )
        } if isinstance(estimate_values, dict) else {}
        official_complete = all(value is not None for value in (pe, pb, dividend_spread, earnings_spread))
        estimate_complete = bool(estimated) and all(value is not None for value in estimated.values())
        if not official_complete and estimate_complete:
            pe = estimated["pe_ttm"]
            pb = estimated["pb_lf"]
            dividend_spread = estimated["dividend_yield_spread"]
            earnings_spread = estimated["earnings_yield_spread"]
            estimate_used.append({key: float(value) for key, value in estimated.items()})
        else:
            estimate_used.append(None)

        pe_values.append(pe)
        pb_values.append(pb)
        dividend_spread_values.append(dividend_spread)
        earnings_spread_values.append(earnings_spread)

    event_state = _event_state_series(
        dates=dates,
        dataset_path=dataset_file,
        event_state_model_path=event_state_file,
        index_code=INDEX_CODE,
    )
    style_rotation_percentile: list[float | None]
    try:
        style_rotation_payload = _load_or_fetch_style_rotation_payload(
            style_rotation_file,
            style_rotation_fetcher,
            force_refresh=force_refresh_style_rotation_payload,
        )
        style_rotation_spread_values = _style_rotation_spread_values_from_payload(
            dates,
            style_rotation_payload,
        )
        style_rotation_percentile = _percentile_series(
            style_rotation_spread_values,
            window_days=style_window_days,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 风格挤压本地 JSON 获取失败,改为无数据: {exc}")
        style_rotation_percentile = [None for _ in dates]

    series = {
        "dates": dates,
        "index_close": [_round_or_none(close) for close in closes],
        "drawdown_peak": _rolling_peak_drawdown(closes, window_days=drawdown_window_days),
        "pe_ttm_percentile": _percentile_series(pe_values, window_days=valuation_window_days),
        "pb_lf_percentile": _percentile_series(pb_values, window_days=valuation_window_days),
        "dividend_yield_spread_percentile": _percentile_series(
            dividend_spread_values,
            window_days=spread_window_days,
        ),
        "earnings_yield_spread_percentile": _percentile_series(
            earnings_spread_values,
            window_days=spread_window_days,
        ),
        "style_rotation_spread_percentile": style_rotation_percentile,
        "event_state": event_state,
    }

    latest_index = len(dates) - 1 if dates else None
    latest = {
        "date": dates[latest_index] if latest_index is not None else None,
        "index_close": series["index_close"][latest_index] if latest_index is not None else None,
        "drawdown_peak": series["drawdown_peak"][latest_index] if latest_index is not None else None,
        "pe_ttm_percentile": series["pe_ttm_percentile"][latest_index] if latest_index is not None else None,
        "pb_lf_percentile": series["pb_lf_percentile"][latest_index] if latest_index is not None else None,
        "dividend_yield_spread_percentile": (
            series["dividend_yield_spread_percentile"][latest_index] if latest_index is not None else None
        ),
        "earnings_yield_spread_percentile": (
            series["earnings_yield_spread_percentile"][latest_index] if latest_index is not None else None
        ),
        "style_rotation_spread_percentile": (
            series["style_rotation_spread_percentile"][latest_index] if latest_index is not None else None
        ),
        "event_state": series["event_state"][latest_index] if latest_index is not None else None,
    }

    meta: dict[str, Any] = {
            "index_code": INDEX_CODE,
            "index_name": INDEX_NAME,
            "analysis_window_years": analysis_window_years,
            "display_window_years": display_window_years,
            "window": {
                "drawdown_days": drawdown_window_days,
                "valuation_days": valuation_window_days,
                "spread_days": spread_window_days,
                "style_days": style_window_days,
            },
    }
    latest_estimate = estimate_used[latest_index] if latest_index is not None else None
    if latest_estimate is not None:
        meta["latest_estimate"] = {"date": dates[latest_index], **latest_estimate}

    return {
        "meta": meta,
        "series": series,
        "latest": latest,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate local dividend observation payload for 930955.")
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--event-state-model", type=Path, default=DEFAULT_EVENT_STATE_MODEL_PATH)
    parser.add_argument("--style-rotation-payload", type=Path, default=DEFAULT_STYLE_ROTATION_PAYLOAD_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--analysis-window-years", type=int)
    parser.add_argument("--display-window-years", type=int)
    parser.add_argument("--drawdown-window-days", type=int)
    parser.add_argument("--valuation-window-days", type=int)
    parser.add_argument("--spread-window-days", type=int)
    parser.add_argument("--style-window-days", type=int)
    args = parser.parse_args(argv)

    payload = build_dividend_observation_payload(
        archive_root=args.archive_root,
        dataset_path=args.dataset,
        event_state_model_path=args.event_state_model,
        style_rotation_payload_path=args.style_rotation_payload,
        config_path=args.config,
        style_rotation_fetcher=_default_style_rotation_fetcher,
        analysis_window_years=args.analysis_window_years,
        display_window_years=args.display_window_years,
        drawdown_window_days=args.drawdown_window_days,
        valuation_window_days=args.valuation_window_days,
        spread_window_days=args.spread_window_days,
        style_window_days=args.style_window_days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"写入 dividend observation payload: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
