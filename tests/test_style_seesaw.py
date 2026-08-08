from src.research.style_seesaw_930955_vs_399326 import (
    build_current_event_card,
    build_ratio_snapshot,
    build_relative_ratio_series,
)


def test_build_relative_ratio_series_aligns_common_dates_only():
    left = [("2026-08-05", 100.0), ("2026-08-06", 102.0), ("2026-08-07", 101.0)]
    right = [("2026-08-04", 200.0), ("2026-08-06", 204.0), ("2026-08-07", 202.0)]

    assert build_relative_ratio_series(left, right) == [
        ("2026-08-06", 0.5),
        ("2026-08-07", 0.5),
    ]


def test_build_ratio_snapshot_returns_percentile_and_zscores():
    ratios = [
        ("2026-08-01", 0.40),
        ("2026-08-04", 0.45),
        ("2026-08-05", 0.48),
        ("2026-08-06", 0.52),
        ("2026-08-07", 0.60),
    ]

    snapshot = build_ratio_snapshot(ratios, as_of_date="2026-08-07")

    assert snapshot["value"] == 0.60
    assert snapshot["percentile"] == 100.0
    assert "zscore_60d" in snapshot
    assert "zscore_120d" in snapshot


def test_build_current_event_card_extracts_required_fields():
    event = {
        "event_id": "930955:2025-11-13:2026-06-30",
        "peak_date": "2025-11-13",
        "trough_date": "2026-06-30",
        "recovery_date": "2026-07-30",
        "recovered": True,
        "recovery_rule": "rebound_stability",
        "max_drawdown": -0.1542,
        "drawdown_days": 160,
        "peak_context": {"pe_ttm": 9.94, "dividend_yield": 4.10, "bond_10y": 1.81},
        "trough_context": {"pe_ttm": 8.22, "dividend_yield": 4.60, "bond_10y": 1.73},
        "recovery_context": {"pe_ttm": 9.10, "dividend_yield": 4.30, "bond_10y": 1.74},
    }

    card = build_current_event_card(
        event,
        drawdown_percentile=88.0,
        latest_context={"pe_ttm": 9.10, "dividend_yield": 4.30, "bond_10y": 1.74},
    )

    assert card["event_id"] == "930955:2025-11-13:2026-06-30"
    assert card["drawdown_percentile"] == 88.0
    assert card["peak_pe"] == 9.94
    assert card["trough_pe"] == 8.22
    assert card["latest_pe"] == 9.10
