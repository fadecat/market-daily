"""Extract reproducible drawdown events from archived index prices.

The first research phase deliberately keeps this module independent from the
daily email pipeline. It describes price events and their observable context;
it does not produce investment signals.

Events begin when a series falls beyond the configured drawdown threshold from
its current peak. Events end when the same down-leg has clearly repaired:
either the old peak is fully recovered, the price remains near that peak long
enough, or the trough rebounds enough without printing a new low.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Sequence


PricePoint = tuple[str, float]
DEFAULT_ARCHIVE_ROOT = Path(__file__).resolve().parents[2] / "data" / "archive"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "value_growth_drawdown_events.json"
)
INDEX_UNIVERSE = {
    "930955": "红利低波100",
    "931052": "中证价值100",
    "980081": "国证价值100",
    "399326": "深证成长40",
    "000300": "沪深300",
    "399303": "国证2000",
}
REBOUND_THRESHOLD = 0.10
REBOUND_STABILITY_DAYS = 20
NEAR_PEAK_GAP = 0.03
NEAR_PEAK_STABILITY_DAYS = 10


def _normalise_prices(prices: Iterable[Sequence[Any]]) -> list[PricePoint]:
    normalised: list[PricePoint] = []
    for point in prices:
        if len(point) != 2:
            raise ValueError("price points must contain exactly date and close")
        raw_date, raw_close = point
        try:
            parsed_date = date.fromisoformat(str(raw_date))
            close = float(raw_close)
        except (TypeError, ValueError) as exc:
            raise ValueError("price points must contain ISO dates and numbers") from exc
        if not isfinite(close) or close <= 0:
            raise ValueError("close prices must be finite and positive")
        normalised.append((parsed_date.isoformat(), close))

    if not normalised:
        return []
    dates = [item[0] for item in normalised]
    if dates != sorted(dates):
        raise ValueError("price points must be sorted by date")
    if len(set(dates)) != len(dates):
        raise ValueError("price points must not contain duplicate dates")
    return normalised


def _severity(max_drawdown: float) -> str:
    return "major" if max_drawdown <= -0.10 else "important"


def _finish_event(
    event: dict[str, Any],
    *,
    recovery_date: str | None,
    recovery_index: int | None,
    recovery_rule: str | None = None,
    full_peak_recovered: bool = False,
) -> dict[str, Any]:
    trough_index = int(event.pop("_trough_index"))
    peak_index = int(event.pop("_peak_index"))
    max_drawdown = float(event["trough_close"] / event["peak_close"] - 1.0)
    event["max_drawdown"] = max_drawdown
    event["drawdown_days"] = trough_index - peak_index
    event["recovery_date"] = recovery_date
    event["recovered"] = recovery_date is not None
    event["recovery_days"] = (
        recovery_index - trough_index if recovery_index is not None else None
    )
    event["recovery_rule"] = recovery_rule
    event["full_peak_recovered"] = full_peak_recovered
    event["severity"] = _severity(max_drawdown)
    return event


def _repair_rule(
    active: dict[str, Any],
    *,
    close: float,
    index: int,
) -> tuple[str, bool] | None:
    trough_close = float(active["trough_close"])
    days_since_trough = index - int(active["_trough_index"])
    if (
        trough_close > 0
        and close / trough_close - 1.0 >= REBOUND_THRESHOLD
        and days_since_trough >= REBOUND_STABILITY_DAYS - 1
    ):
        return ("rebound_stability", False)
    if int(active["_near_peak_streak"]) >= NEAR_PEAK_STABILITY_DAYS:
        return ("near_peak_stability", False)
    return None


def extract_drawdown_events(
    prices: Iterable[Sequence[Any]], *, threshold: float = 0.05
) -> list[dict[str, Any]]:
    """Extract one event for each drawdown excursion below ``threshold``.

    A new event starts on the first close at or below the threshold from the
    running high. Intermediate rebounds do not split the event. The event ends
    when the same down-leg is materially repaired by one of three rules:
    full peak recovery, near-peak stability, or rebound stability. An event
    still open at the last observation is retained with ``recovered=False``.
    """

    if not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1")
    points = _normalise_prices(prices)
    if not points:
        return []

    events: list[dict[str, Any]] = []
    peak_index = 0
    peak_date, peak_close = points[0]
    active: dict[str, Any] | None = None

    for index, (trade_date, close) in enumerate(points):
        if active is not None:
            if close < active["trough_close"]:
                active["trough_date"] = trade_date
                active["trough_close"] = close
                active["_trough_index"] = index
                active["_near_peak_streak"] = 0
            elif close / float(active["peak_close"]) >= 1.0 - NEAR_PEAK_GAP:
                active["_near_peak_streak"] = int(active["_near_peak_streak"]) + 1
            else:
                active["_near_peak_streak"] = 0
            if close >= active["peak_close"]:
                events.append(
                    _finish_event(
                        active,
                        recovery_date=trade_date,
                        recovery_index=index,
                        recovery_rule="full_peak_recovery",
                        full_peak_recovered=True,
                    )
                )
                active = None
                peak_index = index
                peak_date = trade_date
                peak_close = close
                continue
            repair = _repair_rule(active, close=close, index=index)
            if repair is not None:
                recovery_rule, full_peak_recovered = repair
                events.append(
                    _finish_event(
                        active,
                        recovery_date=trade_date,
                        recovery_index=index,
                        recovery_rule=recovery_rule,
                        full_peak_recovered=full_peak_recovered,
                    )
                )
                active = None
                peak_index = index
                peak_date = trade_date
                peak_close = close
            continue

        if close > peak_close:
            peak_index = index
            peak_date = trade_date
            peak_close = close

        drawdown = close / peak_close - 1.0
        if drawdown <= -threshold:
            active = {
                "_peak_index": peak_index,
                "_trough_index": index,
                "peak_date": peak_date,
                "peak_close": peak_close,
                "start_date": trade_date,
                "trough_date": trade_date,
                "trough_close": close,
                "threshold": threshold,
                "_near_peak_streak": 0,
            }

    if active is not None:
        events.append(
            _finish_event(
                active,
                recovery_date=None,
                recovery_index=None,
                recovery_rule=None,
                full_peak_recovered=False,
            )
        )
    return events


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"archive records must be a list: {path}")
    return [record for record in records if isinstance(record, dict)]


def load_index_price_series(
    index_code: str, archive_root: Path | str = DEFAULT_ARCHIVE_ROOT
) -> list[PricePoint]:
    """Load and normalize one index EOD archive."""

    path = Path(archive_root) / "index_eod" / f"{index_code}.json"
    points: list[PricePoint] = []
    for record in _load_records(path):
        if record.get("trdDt") is None or record.get("pxClose") is None:
            continue
        points.append((str(record["trdDt"]), record["pxClose"]))
    return _normalise_prices(points)


def _date_value_map(
    archive_root: Path, dataset: str, filename: str, date_key: str
) -> dict[str, dict[str, Any]]:
    path = archive_root / dataset / filename
    result: dict[str, dict[str, Any]] = {}
    for record in _load_records(path):
        raw_date = record.get(date_key)
        if raw_date is not None:
            result[str(raw_date)] = record
    return result


def _context_map(index_code: str, archive_root: Path) -> dict[str, dict[str, Any]]:
    valuation = _date_value_map(
        archive_root,
        "index_valuation_percentile",
        f"{index_code}.json",
        "trdDt",
    )
    dividend = _date_value_map(
        archive_root,
        "index_dividend_ratio",
        f"{index_code}.json",
        "trdDt",
    )
    bond = _date_value_map(
        archive_root,
        "bond_10y",
        "china_10y.json",
        "日期",
    )
    all_dates = set(valuation) | set(dividend) | set(bond)
    result: dict[str, dict[str, Any]] = {}
    for trade_date in all_dates:
        context: dict[str, Any] = {}
        valuation_row = valuation.get(trade_date, {})
        dividend_row = dividend.get(trade_date, {})
        bond_row = bond.get(trade_date, {})
        for source_key, output_key in (
            ("pETtm", "pe_ttm"),
            ("pETtm10Y", "pe_percentile_10y"),
            ("pBLf", "pb_lf"),
            ("pBLf10Y", "pb_percentile_10y"),
        ):
            if valuation_row.get(source_key) is not None:
                context[output_key] = float(valuation_row[source_key])
        if dividend_row.get("dividendYield") is not None:
            context["dividend_yield"] = float(dividend_row["dividendYield"])
        if bond_row.get("中国国债收益率10年") is not None:
            context["bond_10y"] = float(bond_row["中国国债收益率10年"])
        if context:
            result[trade_date] = context
    return result


def _forward_returns(
    prices: list[PricePoint], trough_date: str, horizons: tuple[int, ...] = (21, 63, 126, 252)
) -> dict[str, float | None]:
    dates = [item[0] for item in prices]
    try:
        trough_index = dates.index(trough_date)
    except ValueError:
        return {f"{horizon}d": None for horizon in horizons}
    base = prices[trough_index][1]
    result: dict[str, float | None] = {}
    for horizon in horizons:
        target_index = trough_index + horizon
        result[f"{horizon}d"] = (
            prices[target_index][1] / base - 1.0
            if target_index < len(prices)
            else None
        )
    return result


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _event_features(event: dict[str, Any]) -> dict[str, float]:
    peak_context = event.get("peak_context") or {}
    trough_context = event.get("trough_context") or {}
    peak_pe = _safe_float(peak_context.get("pe_ttm"))
    trough_pe = _safe_float(trough_context.get("pe_ttm"))
    peak_dividend = _safe_float(peak_context.get("dividend_yield"))
    trough_dividend = _safe_float(trough_context.get("dividend_yield"))
    peak_bond = _safe_float(peak_context.get("bond_10y"))
    trough_bond = _safe_float(trough_context.get("bond_10y"))

    features = {
        "drawdown_abs": abs(float(event["max_drawdown"])),
        "drawdown_days": float(event["drawdown_days"]),
    }
    if trough_pe is not None:
        features["trough_pe"] = trough_pe
    if peak_pe is not None and trough_pe is not None and peak_pe > 0:
        features["pe_change_ratio"] = trough_pe / peak_pe - 1.0
    if trough_dividend is not None:
        features["trough_dividend"] = trough_dividend
    if peak_dividend is not None and trough_dividend is not None and peak_dividend > 0:
        features["dividend_change_ratio"] = trough_dividend / peak_dividend - 1.0
    if trough_bond is not None:
        features["trough_bond"] = trough_bond
    if peak_bond is not None and trough_bond is not None:
        features["bond_change"] = trough_bond - peak_bond
    return features


def _feature_scales(events: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for event in events:
        for key, value in _event_features(event).items():
            values.setdefault(key, []).append(value)
    scales: dict[str, float] = {}
    for key, numbers in values.items():
        if len(numbers) < 2:
            scales[key] = 1.0
            continue
        spread = max(numbers) - min(numbers)
        scales[key] = spread if spread > 0 else 1.0
    return scales


def _shared_feature_keys(
    left: dict[str, Any], right: dict[str, Any]
) -> list[str]:
    return sorted(set(_event_features(left)) & set(_event_features(right)))


def find_similar_events(
    events: Iterable[dict[str, Any]],
    target_event: dict[str, Any],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Return historical neighbors for ``target_event`` within the same index.

    Similarity is constrained to the same index first, then prefers the same
    severity bucket. Distance combines drawdown size, drawdown duration, and
    any shared valuation/dividend/bond features that both events expose.
    """

    if top_n <= 0:
        return []
    all_events = [event for event in events if event.get("event_id") != target_event.get("event_id")]
    same_index = [
        event
        for event in all_events
        if event.get("index_code") == target_event.get("index_code")
    ]
    if not same_index:
        return []
    same_severity = [
        event
        for event in same_index
        if event.get("severity") == target_event.get("severity")
    ]
    pool = same_severity or same_index
    target_feature_count = len(_event_features(target_event))
    if target_feature_count > 2:
        shared_counts = [
            len(_shared_feature_keys(target_event, candidate)) for candidate in pool
        ]
        max_shared = max(shared_counts, default=0)
        if max_shared > 2:
            pool = [
                candidate
                for candidate in pool
                if len(_shared_feature_keys(target_event, candidate)) == max_shared
            ]
    scales = _feature_scales(pool + [target_event])
    target_features = _event_features(target_event)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for candidate in pool:
        candidate_features = _event_features(candidate)
        shared = _shared_feature_keys(target_event, candidate)
        if len(shared) < 2:
            continue
        distance = sum(
            abs(target_features[key] - candidate_features[key]) / scales.get(key, 1.0)
            for key in shared
        ) / len(shared)
        ranked.append((distance, candidate))
    ranked.sort(key=lambda item: (item[0], item[1].get("trough_date", "")))
    return [candidate for _, candidate in ranked[:top_n]]


def _relative_returns(
    prices_by_code: dict[str, list[PricePoint]],
    peak_date: str,
    trough_date: str,
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for index_code, prices in prices_by_code.items():
        values = dict(prices)
        if peak_date in values and trough_date in values:
            result[index_code] = values[trough_date] / values[peak_date] - 1.0
        else:
            result[index_code] = None
    return result


def build_event_dataset(
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    *,
    index_codes: Iterable[str] = tuple(INDEX_UNIVERSE),
    threshold: float = 0.05,
) -> dict[str, Any]:
    """Build a JSON-safe event dataset from local archives only."""

    root = Path(archive_root)
    selected_codes = tuple(index_codes)
    prices_by_code = {
        index_code: load_index_price_series(index_code, root)
        for index_code in selected_codes
    }
    events: list[dict[str, Any]] = []
    for index_code, prices in prices_by_code.items():
        context = _context_map(index_code, root)
        for event in extract_drawdown_events(prices, threshold=threshold):
            row = dict(event)
            row.update(
                {
                    "event_id": f"{index_code}:{event['peak_date']}:{event['trough_date']}",
                    "index_code": index_code,
                    "index_name": INDEX_UNIVERSE.get(index_code, index_code),
                    "forward_returns": _forward_returns(prices, event["trough_date"]),
                    "peak_context": context.get(event["peak_date"], {}),
                    "trough_context": context.get(event["trough_date"], {}),
                    "recovery_context": context.get(event["recovery_date"], {})
                    if event["recovery_date"]
                    else {},
                    "relative_returns_peak_to_trough": _relative_returns(
                        prices_by_code,
                        event["peak_date"],
                        event["trough_date"],
                    ),
                }
            )
            events.append(row)
    events.sort(key=lambda item: (item["trough_date"], item["index_code"]))
    return {
        "threshold": threshold,
        "indices": {
            index_code: INDEX_UNIVERSE.get(index_code, index_code)
            for index_code in selected_codes
        },
        "events": events,
    }


def build_event_lookup(
    dataset: dict[str, Any],
    *,
    index_code: str,
) -> list[dict[str, Any]]:
    return [
        event
        for event in dataset["events"]
        if event.get("index_code") == index_code
    ]


def current_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    open_events = [event for event in events if not event.get("recovered")]
    if open_events:
        return sorted(open_events, key=lambda event: event["trough_date"])[-1]
    return sorted(events, key=lambda event: event["trough_date"])[-1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--threshold", type=float, default=0.05)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dataset = build_event_dataset(args.archive_root, threshold=args.threshold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for event in dataset["events"]:
        key = f"{event['index_code']}:{event['severity']}"
        counts[key] = counts.get(key, 0) + 1
    print(f"写入 {len(dataset['events'])} 个回撤事件: {args.output}")
    for key in sorted(counts):
        print(f"{key}={counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
