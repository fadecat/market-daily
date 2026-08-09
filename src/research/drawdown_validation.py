from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .drawdown_events import (
    DEFAULT_ARCHIVE_ROOT,
    _context_map,
    load_index_price_series,
)


DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "value_growth_drawdown_events.json"
)
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "walk_forward_similarity_test.json"
)
DEFAULT_HORIZONS = (21, 63, 126, 252)
OVERLAP_RISK_THRESHOLD = 0.30


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _load_dataset(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _price_lookup(
    archive_root: Path | str,
    index_codes: Iterable[str],
) -> dict[str, list[tuple[str, float]]]:
    root = Path(archive_root)
    return {
        index_code: load_index_price_series(index_code, root)
        for index_code in sorted(set(index_codes))
    }


def _date_index(prices: list[tuple[str, float]]) -> dict[str, int]:
    return {trade_date: index for index, (trade_date, _) in enumerate(prices)}


def _close_on(prices: list[tuple[str, float]], trade_date: str) -> float:
    for current_date, close in prices:
        if current_date == trade_date:
            return float(close)
    raise KeyError(f"missing trade date: {trade_date}")


def prior_closed_events(
    events: Iterable[dict[str, Any]],
    target_event: dict[str, Any],
) -> list[dict[str, Any]]:
    target_start = str(target_event["start_date"])
    target_index = str(target_event["index_code"])
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_id") == target_event.get("event_id"):
            continue
        if str(event.get("index_code")) != target_index:
            continue
        recovery_date = event.get("recovery_date")
        if not event.get("recovered") or recovery_date is None:
            continue
        if str(recovery_date) < target_start:
            result.append(event)
    result.sort(key=lambda row: row["recovery_date"])
    return result


def build_sample_pollution_audit(
    dataset: dict[str, Any],
    *,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    threshold_ratio: float = OVERLAP_RISK_THRESHOLD,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in dataset.get("events", []):
        severity = str(event.get("severity", "unknown"))
        index_code = str(event.get("index_code"))
        grouped.setdefault((index_code, severity), []).append(event)

    prices_by_index = _price_lookup(
        archive_root,
        [index_code for index_code, _ in grouped],
    )
    result: list[dict[str, Any]] = []
    for (index_code, severity), rows in sorted(grouped.items()):
        recovery_days = [
            int(row["recovery_days"])
            for row in rows
            if row.get("recovery_days") is not None
        ]
        median_recovery_days = median(recovery_days) if recovery_days else None
        ordered = sorted(rows, key=lambda row: row["trough_date"])
        gap_flags: list[bool] = []
        positions = _date_index(prices_by_index[index_code])
        for left, right in zip(ordered, ordered[1:]):
            if median_recovery_days is None:
                continue
            gap = positions[str(right["trough_date"])] - positions[str(left["trough_date"])]
            gap_flags.append(gap / float(median_recovery_days) < 1.0)

        high_overlap_ratio = (
            round(sum(gap_flags) / len(gap_flags), 6) if gap_flags else 0.0
        )
        result.append(
            {
                "index": index_code,
                "severity": severity,
                "event_count": len(rows),
                "median_recovery_days": median_recovery_days,
                "high_overlap_ratio": high_overlap_ratio,
                "status": (
                    "存在明显样本污染风险"
                    if high_overlap_ratio > threshold_ratio
                    else "可以作为方向性统计"
                ),
            }
        )
    return result


def _forward_returns_from_start(
    prices: list[tuple[str, float]],
    start_date: str,
    *,
    horizons: tuple[int, ...],
) -> dict[str, float | None]:
    positions = _date_index(prices)
    if start_date not in positions:
        return {f"{horizon}d": None for horizon in horizons}
    start_index = positions[start_date]
    start_close = float(prices[start_index][1])
    result: dict[str, float | None] = {}
    for horizon in horizons:
        target_index = start_index + horizon
        if target_index >= len(prices):
            result[f"{horizon}d"] = None
            continue
        result[f"{horizon}d"] = float(prices[target_index][1]) / start_close - 1.0
    return result


def _recovered_to_peak_within_horizon(
    event: dict[str, Any],
    prices: list[tuple[str, float]],
    *,
    horizon: int = 252,
) -> bool | None:
    start_date = str(event["start_date"])
    positions = _date_index(prices)
    if start_date not in positions:
        return None
    start_index = positions[start_date]
    peak_close = float(event["peak_close"])
    end_index = min(len(prices), start_index + horizon + 1)
    window = [float(close) for _, close in prices[start_index + 1 : end_index]]
    if not window:
        return None
    return any(close >= peak_close for close in window)


def _enrich_events(
    dataset: dict[str, Any],
    *,
    archive_root: Path | str,
    horizons: tuple[int, ...],
) -> list[dict[str, Any]]:
    index_codes = [str(event["index_code"]) for event in dataset.get("events", [])]
    prices_by_index = _price_lookup(archive_root, index_codes)
    context_by_index = {
        index_code: _context_map(index_code, Path(archive_root))
        for index_code in sorted(set(index_codes))
    }
    enriched: list[dict[str, Any]] = []
    for event in dataset.get("events", []):
        row = dict(event)
        index_code = str(event["index_code"])
        prices = prices_by_index[index_code]
        row["start_close"] = float(
            row.get("start_close")
            if row.get("start_close") is not None
            else _close_on(prices, str(event["start_date"]))
        )
        row["start_context"] = row.get("start_context") or context_by_index[index_code].get(
            str(event["start_date"]),
            {},
        )
        row["start_forward_returns"] = row.get("start_forward_returns") or _forward_returns_from_start(
            prices,
            str(event["start_date"]),
            horizons=horizons,
        )
        if row.get("recovered_to_peak_within_252d") is None:
            row["recovered_to_peak_within_252d"] = _recovered_to_peak_within_horizon(
                row,
                prices,
                horizon=252,
            )
        enriched.append(row)
    return enriched


def _observation_features(
    event: dict[str, Any],
    *,
    positions: dict[str, int] | None = None,
) -> dict[str, float]:
    start_close = float(event["start_close"])
    peak_close = float(event["peak_close"])
    features = {
        "observed_drawdown_abs": abs(start_close / peak_close - 1.0),
    }
    if positions is not None:
        peak_date = str(event["peak_date"])
        start_date = str(event["start_date"])
        if peak_date in positions and start_date in positions:
            features["observed_drawdown_days"] = float(
                positions[start_date] - positions[peak_date]
            )
    peak_context = event.get("peak_context") or {}
    start_context = event.get("start_context") or {}
    peak_pe = _safe_float(peak_context.get("pe_ttm"))
    start_pe = _safe_float(start_context.get("pe_ttm"))
    peak_dividend = _safe_float(peak_context.get("dividend_yield"))
    start_dividend = _safe_float(start_context.get("dividend_yield"))
    peak_bond = _safe_float(peak_context.get("bond_10y"))
    start_bond = _safe_float(start_context.get("bond_10y"))
    if peak_pe is not None:
        features["peak_pe"] = peak_pe
    if start_pe is not None:
        features["start_pe"] = start_pe
    if peak_pe and start_pe is not None:
        features["pe_change_ratio"] = start_pe / peak_pe - 1.0
    if peak_dividend is not None:
        features["peak_dividend"] = peak_dividend
    if start_dividend is not None:
        features["start_dividend"] = start_dividend
    if peak_dividend and start_dividend is not None:
        features["dividend_change_ratio"] = start_dividend / peak_dividend - 1.0
    if peak_bond is not None:
        features["peak_bond"] = peak_bond
    if start_bond is not None:
        features["start_bond"] = start_bond
    if peak_bond is not None and start_bond is not None:
        features["bond_change"] = start_bond - peak_bond
    return features


def _feature_scales(
    pool: list[dict[str, Any]],
    positions_by_index: dict[str, dict[str, int]],
) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for event in pool:
        positions = positions_by_index.get(str(event["index_code"]))
        for key, value in _observation_features(event, positions=positions).items():
            values.setdefault(key, []).append(value)
    scales: dict[str, float] = {}
    for key, numbers in values.items():
        spread = max(numbers) - min(numbers) if len(numbers) > 1 else 0.0
        scales[key] = spread if spread > 0 else 1.0
    return scales


def _find_similar_prior_events(
    prior_pool: list[dict[str, Any]],
    target_event: dict[str, Any],
    *,
    positions_by_index: dict[str, dict[str, int]],
    top_n: int,
) -> list[dict[str, Any]]:
    if not prior_pool or top_n <= 0:
        return []
    target_positions = positions_by_index.get(str(target_event["index_code"]))
    target_features = _observation_features(target_event, positions=target_positions)
    scales = _feature_scales(prior_pool + [target_event], positions_by_index)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for event in prior_pool:
        candidate_positions = positions_by_index.get(str(event["index_code"]))
        candidate_features = _observation_features(event, positions=candidate_positions)
        shared = sorted(set(target_features) & set(candidate_features))
        if not shared:
            continue
        distance = sum(
            abs(target_features[key] - candidate_features[key]) / scales.get(key, 1.0)
            for key in shared
        ) / len(shared)
        ranked.append((distance, event))
    ranked.sort(key=lambda item: (item[0], item[1]["start_date"], item[1]["event_id"]))
    return [event for _, event in ranked[:top_n]]


def _median_prediction(
    events: list[dict[str, Any]],
    horizon_key: str,
) -> float | None:
    values = [
        _safe_float(event.get("start_forward_returns", {}).get(horizon_key))
        for event in events
    ]
    usable = [value for value in values if value is not None]
    return median(usable) if usable else None


def _median_recovery_rate(events: list[dict[str, Any]]) -> float | None:
    values = [
        event.get("recovered_to_peak_within_252d")
        for event in events
        if event.get("recovered_to_peak_within_252d") is not None
    ]
    if not values:
        return None
    return sum(1.0 for value in values if value) / len(values)


def evaluate_walkforward_information_increment(
    dataset: dict[str, Any],
    *,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    top_n: int = 3,
) -> dict[str, Any]:
    events = _enrich_events(dataset, archive_root=archive_root, horizons=horizons)
    index_codes = sorted({str(event["index_code"]) for event in events})
    prices_by_index = _price_lookup(archive_root, index_codes) if events else {}
    positions_by_index = {
        index_code: _date_index(prices)
        for index_code, prices in prices_by_index.items()
    }

    evaluations: list[dict[str, Any]] = []
    ordered = sorted(events, key=lambda row: (row["start_date"], row["event_id"]))
    for target in ordered:
        if not target.get("recovered"):
            continue
        prior_pool = prior_closed_events(ordered, target)
        matches = _find_similar_prior_events(
            prior_pool,
            target,
            positions_by_index=positions_by_index,
            top_n=top_n,
        )
        similar_prediction: dict[str, float | None] = {}
        random_prediction: dict[str, float | None] = {}
        actual_forward_returns: dict[str, float | None] = {}
        for horizon in horizons:
            horizon_key = f"{horizon}d"
            similar_prediction[horizon_key] = _median_prediction(matches, horizon_key)
            random_prediction[horizon_key] = _median_prediction(prior_pool, horizon_key)
            actual_forward_returns[horizon_key] = _safe_float(
                target.get("start_forward_returns", {}).get(horizon_key)
            )
        evaluations.append(
            {
                "event_id": target["event_id"],
                "index": target["index_code"],
                "start_date": target["start_date"],
                "candidate_pool_size": len(prior_pool),
                "matched_event_ids": [event["event_id"] for event in matches],
                "similar_prediction": similar_prediction,
                "random_prediction": random_prediction,
                "actual_forward_returns": actual_forward_returns,
                "actual_recovered_to_peak": target.get("recovered_to_peak_within_252d"),
                "similar_recovery_to_peak_rate": _median_recovery_rate(matches),
                "random_recovery_to_peak_rate": _median_recovery_rate(prior_pool),
            }
        )

    summaries: list[dict[str, Any]] = []
    for index_code in index_codes:
        rows = [
            row
            for row in evaluations
            if row["index"] == index_code and row["candidate_pool_size"] > top_n
        ]
        summary: dict[str, Any] = {
            "index": index_code,
            "sample_count": 0,
        }
        eligible_rows = rows
        for horizon in horizons:
            horizon_key = f"{horizon}d"
            horizon_rows = [
                row
                for row in rows
                if row["similar_prediction"].get(horizon_key) is not None
                and row["random_prediction"].get(horizon_key) is not None
                and row["actual_forward_returns"].get(horizon_key) is not None
            ]
            summary["sample_count"] = max(summary["sample_count"], len(horizon_rows))
            similar_values = [row["similar_prediction"][horizon_key] for row in horizon_rows]
            random_values = [row["random_prediction"][horizon_key] for row in horizon_rows]
            actual_values = [row["actual_forward_returns"][horizon_key] for row in horizon_rows]
            similar_errors = [
                abs(actual - predicted)
                for actual, predicted in zip(actual_values, similar_values)
            ]
            random_errors = [
                abs(actual - predicted)
                for actual, predicted in zip(actual_values, random_values)
            ]
            summary[f"similar_method_median_{horizon_key}_return"] = (
                median(similar_values) if similar_values else None
            )
            summary[f"random_median_{horizon_key}_return"] = (
                median(random_values) if random_values else None
            )
            summary[f"information_increment_{horizon_key}"] = (
                median(random_errors) - median(similar_errors)
                if similar_errors and random_errors
                else None
            )
            eligible_rows = horizon_rows
        recovery_rows = [
            row
            for row in eligible_rows
            if row["similar_recovery_to_peak_rate"] is not None
            and row["random_recovery_to_peak_rate"] is not None
            and row["actual_recovered_to_peak"] is not None
        ]
        similar_recovery = [
            row["similar_recovery_to_peak_rate"]
            for row in recovery_rows
        ]
        random_recovery = [
            row["random_recovery_to_peak_rate"]
            for row in recovery_rows
        ]
        actual_recovery = [
            1.0 if row["actual_recovered_to_peak"] else 0.0
            for row in recovery_rows
        ]
        similar_recovery_errors = [
            abs(actual - predicted)
            for actual, predicted in zip(actual_recovery, similar_recovery)
        ]
        random_recovery_errors = [
            abs(actual - predicted)
            for actual, predicted in zip(actual_recovery, random_recovery)
        ]
        summary["similar_recovery_to_peak_rate"] = (
            median(similar_recovery) if similar_recovery else None
        )
        summary["random_recovery_to_peak_rate"] = (
            median(random_recovery) if random_recovery else None
        )
        summary["recovery_information_increment"] = (
            median(random_recovery_errors) - median(similar_recovery_errors)
            if similar_recovery_errors and random_recovery_errors
            else None
        )
        summaries.append(summary)
    return {"summaries": summaries, "evaluations": evaluations}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    dataset = _load_dataset(args.dataset)
    payload = {
        "sample_pollution_audit": build_sample_pollution_audit(
            dataset,
            archive_root=args.archive_root,
        ),
        "walk_forward_similarity_test": evaluate_walkforward_information_increment(
            dataset,
            archive_root=args.archive_root,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"写入 drawdown validation: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
