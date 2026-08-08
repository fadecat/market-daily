from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable, Sequence

from .drawdown_events import (
    DEFAULT_ARCHIVE_ROOT,
    build_event_dataset,
    build_event_lookup,
    current_event,
    find_similar_events,
    load_index_price_series,
)


PAIR_ID = "930955_vs_399326"
LEFT_CODE = "930955"
LEFT_NAME = "红利低波100"
RIGHT_CODE = "399326"
RIGHT_NAME = "深证成长40"
SAMPLE_START = "2017-12-08"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "style_seesaw_930955_vs_399326.json"
)


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


def build_current_event_card(
    event: dict,
    *,
    drawdown_percentile: float,
    latest_context: dict,
) -> dict:
    peak_context = event.get("peak_context") or {}
    trough_context = event.get("trough_context") or {}
    return {
        "event_id": event["event_id"],
        "peak_date": event["peak_date"],
        "trough_date": event["trough_date"],
        "recovery_date": event.get("recovery_date"),
        "recovered": event["recovered"],
        "recovery_rule": event.get("recovery_rule"),
        "max_drawdown": event["max_drawdown"],
        "drawdown_days": event["drawdown_days"],
        "drawdown_percentile": drawdown_percentile,
        "peak_pe": peak_context.get("pe_ttm"),
        "trough_pe": trough_context.get("pe_ttm"),
        "latest_pe": latest_context.get("pe_ttm"),
        "peak_dividend_yield": peak_context.get("dividend_yield"),
        "trough_dividend_yield": trough_context.get("dividend_yield"),
        "latest_dividend_yield": latest_context.get("dividend_yield"),
        "peak_bond_10y": peak_context.get("bond_10y"),
        "trough_bond_10y": trough_context.get("bond_10y"),
        "latest_bond_10y": latest_context.get("bond_10y"),
    }


def _empty_distribution() -> dict[str, int | float | None]:
    return {"sample_size": 0, "p25": None, "p50": None, "p75": None}


def _distribution(values: list[float]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    if not ordered:
        return _empty_distribution()

    def pick(q: float) -> float:
        index = round((len(ordered) - 1) * q)
        return ordered[index]

    return {
        "sample_size": len(ordered),
        "p25": pick(0.25),
        "p50": pick(0.50),
        "p75": pick(0.75),
    }


def _drawdown_percentile(events: list[dict[str, Any]], target: dict[str, Any]) -> float:
    return _percentile_rank(
        [abs(float(event["max_drawdown"])) for event in events],
        abs(float(target["max_drawdown"])),
    )


def _repair_distribution(events: list[dict[str, Any]]) -> dict[str, dict[str, int | float | None]]:
    result: dict[str, dict[str, int | float | None]] = {}
    for key in ("21d", "63d", "126d", "252d"):
        values = [
            float(event.get("forward_returns", {}).get(key))
            for event in events
            if event.get("forward_returns", {}).get(key) is not None
        ]
        result[key] = _distribution(values)
    return result


def _latest_context_for_event(event: dict[str, Any]) -> dict[str, Any]:
    recovery_context = event.get("recovery_context") or {}
    if recovery_context:
        return recovery_context
    trough_context = event.get("trough_context") or {}
    if trough_context:
        return trough_context
    return event.get("peak_context") or {}


def _style_state(
    *,
    ratio_percentile: float | None,
    left_card: dict[str, Any] | None,
    right_card: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence: list[str] = []
    signal = "balanced"
    if ratio_percentile is not None:
        if ratio_percentile >= 60:
            signal = "dividend_stronger"
            evidence.append("relative_ratio_high")
        elif ratio_percentile <= 40:
            signal = "growth_stronger"
            evidence.append("relative_ratio_low")
    if left_card and right_card:
        left_dd = abs(float(left_card["max_drawdown"]))
        right_dd = abs(float(right_card["max_drawdown"]))
        if left_dd > right_dd:
            evidence.append("dividend_drawdown_deeper")
        elif right_dd > left_dd:
            evidence.append("growth_drawdown_deeper")
    return {"signal": signal, "evidence": evidence}


def _match_summary(match: dict[str, Any]) -> dict[str, Any]:
    peak_context = match.get("peak_context") or {}
    trough_context = match.get("trough_context") or {}
    forward = match.get("forward_returns", {})
    return {
        "event_id": match["event_id"],
        "trough_date": match["trough_date"],
        "max_drawdown": match["max_drawdown"],
        "drawdown_days": match["drawdown_days"],
        "recovery_rule": match.get("recovery_rule"),
        "peak_pe": peak_context.get("pe_ttm"),
        "trough_pe": trough_context.get("pe_ttm"),
        "peak_dividend_yield": peak_context.get("dividend_yield"),
        "trough_dividend_yield": trough_context.get("dividend_yield"),
        "forward_21d": forward.get("21d"),
        "forward_63d": forward.get("63d"),
        "forward_126d": forward.get("126d"),
    }


def build_style_seesaw_payload(
    *,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    as_of_date: str = "2026-08-07",
    dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(archive_root)
    dataset = dataset or build_event_dataset(
        root,
        index_codes=(LEFT_CODE, RIGHT_CODE),
        threshold=0.05,
    )

    left_prices = load_index_price_series(LEFT_CODE, root) if (root / "index_eod").exists() else []
    right_prices = load_index_price_series(RIGHT_CODE, root) if (root / "index_eod").exists() else []
    ratio_series = build_relative_ratio_series(left_prices, right_prices) if left_prices and right_prices else []
    ratio_snapshot = (
        build_ratio_snapshot(ratio_series, as_of_date=as_of_date)
        if ratio_series
        else {"value": None, "percentile": None, "zscore_60d": None, "zscore_120d": None}
    )

    left_events = build_event_lookup(dataset, index_code=LEFT_CODE)
    right_events = build_event_lookup(dataset, index_code=RIGHT_CODE)
    left_current = current_event(left_events) if left_events else None
    right_current = current_event(right_events) if right_events else None
    left_card = (
        build_current_event_card(
            left_current,
            drawdown_percentile=_drawdown_percentile(left_events, left_current),
            latest_context=_latest_context_for_event(left_current),
        )
        if left_current
        else None
    )
    right_card = (
        build_current_event_card(
            right_current,
            drawdown_percentile=_drawdown_percentile(right_events, right_current),
            latest_context=_latest_context_for_event(right_current),
        )
        if right_current
        else None
    )

    left_matches = (
        [_match_summary(match) for match in find_similar_events(left_events, left_current, top_n=5)]
        if left_current
        else []
    )
    right_matches = (
        [_match_summary(match) for match in find_similar_events(right_events, right_current, top_n=5)]
        if right_current
        else []
    )

    return {
        "meta": {
            "pair_id": PAIR_ID,
            "as_of_date": as_of_date,
            "generated_at": None,
            "sample_start": SAMPLE_START,
            "left": {"index_code": LEFT_CODE, "index_name": LEFT_NAME},
            "right": {"index_code": RIGHT_CODE, "index_name": RIGHT_NAME},
            "event_rule_version": "style-seesaw-v1",
        },
        "current": {
            "relative_ratio": ratio_snapshot,
            "left_event": left_card,
            "right_event": right_card,
            "style_state": _style_state(
                ratio_percentile=ratio_snapshot["percentile"],
                left_card=left_card,
                right_card=right_card,
            ),
        },
        "distribution": {
            "relative_ratio_percentile": {
                "current": ratio_snapshot["percentile"],
                "sample_size": len(ratio_series),
            },
            "left_drawdown_percentile": {
                "current": None if left_card is None else left_card["drawdown_percentile"],
                "sample_size": len(left_events),
            },
            "right_drawdown_percentile": {
                "current": None if right_card is None else right_card["drawdown_percentile"],
                "sample_size": len(right_events),
            },
            "left_repair_distribution": _repair_distribution(left_events),
            "right_repair_distribution": _repair_distribution(right_events),
            "relative_repair_distribution": {
                key: _empty_distribution() for key in ("21d", "63d", "126d", "252d")
            },
        },
        "similar_events": {
            "left_event_matches": left_matches,
            "right_event_matches": right_matches,
        },
        "trace": {
            "left_event_id": None if left_current is None else left_current["event_id"],
            "right_event_id": None if right_current is None else right_current["event_id"],
            "left_match_pool_size": len(left_events),
            "right_match_pool_size": len(right_events),
            "relative_ratio_sample_size": len(ratio_series),
            "left_drawdown_sample_size": len(left_events),
            "right_drawdown_sample_size": len(right_events),
            "raw_metrics": {
                "relative_ratio_value": ratio_snapshot["value"],
                "relative_ratio_percentile": ratio_snapshot["percentile"],
            },
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--as-of-date", default="2026-08-07")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    payload = build_style_seesaw_payload(
        archive_root=args.archive_root,
        as_of_date=args.as_of_date,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"写入 {PAIR_ID}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
