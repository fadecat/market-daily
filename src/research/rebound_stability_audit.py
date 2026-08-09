from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .drawdown_events import DEFAULT_ARCHIVE_ROOT, load_index_price_series


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
    / "rebound_stability_audit.json"
)


def audit_rebound_stability_event(
    event: dict[str, Any],
    prices: Iterable[tuple[str, float]],
    *,
    horizons: tuple[int, ...] = (20, 60, 120),
) -> dict[str, Any]:
    recovery_date = str(event["recovery_date"])
    rows = list(prices)
    dates = [trade_date for trade_date, _ in rows]
    recovery_index = dates.index(recovery_date)
    recovery_close = float(rows[recovery_index][1])
    future_closes = [float(close) for _, close in rows[recovery_index + 1 :]]
    trough_close = float(event["trough_close"])

    result: dict[str, Any] = {
        "event_id": event["event_id"],
        "index": event["index_code"],
        "end_method": "rebound_stability",
    }
    for horizon in horizons:
        window = future_closes[:horizon]
        result[f"{horizon}d_failed"] = any(close < trough_close for close in window)

    if future_closes:
        result["post_event_max_drawdown"] = round(min(future_closes) / recovery_close - 1.0, 6)
    else:
        result["post_event_max_drawdown"] = None
    return result


def build_rebound_stability_audit(
    dataset: dict[str, Any],
    *,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    horizons: tuple[int, ...] = (20, 60, 120),
) -> list[dict[str, Any]]:
    root = Path(archive_root)
    price_cache: dict[str, list[tuple[str, float]]] = {}
    audits: list[dict[str, Any]] = []
    for event in dataset.get("events", []):
        if event.get("recovery_rule") != "rebound_stability":
            continue
        index_code = str(event["index_code"])
        if index_code not in price_cache:
            price_cache[index_code] = load_index_price_series(index_code, root)
        audits.append(
            audit_rebound_stability_event(
                event,
                price_cache[index_code],
                horizons=horizons,
            )
        )
    return audits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    payload = build_rebound_stability_audit(dataset, archive_root=args.archive_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"写入 rebound stability audit: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
