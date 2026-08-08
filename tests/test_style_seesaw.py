from src.research.style_seesaw_930955_vs_399326 import (
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
