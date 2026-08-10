from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd

from src.valuation.estimate_overlay import (
    EstimateOverlay,
    apply_estimate,
    apply_from_archives,
    latest_price_date,
)


TARGET_DATE = "2026-08-10"


def _history(days: int = 41) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2026-07-01", periods=days, freq="D")
    valuation = pd.DataFrame(
        {
            "date": dates,
            "pe_ttm": [10.0 + index / 10 for index in range(days)],
            "pb_lf": [1.0 + index / 100 for index in range(days)],
        }
    )
    dividends = pd.DataFrame(
        {"date": dates, "dividend_yield": [3.0 + index / 100 for index in range(days)]}
    )
    bonds = pd.DataFrame({"date": dates, "yield_pct": [2.0] * days})
    return valuation, dividends, bonds


def _item() -> dict:
    return {
        "index_code": "931052",
        "index_valuation_metrics": {
            "PE(TTM)": {"current": 99.0, "percentiles": {"5Y": 1.0}},
            "PB(LF)": {"current": 9.0, "percentiles": {"5Y": 1.0}},
        },
        "index_dividend_yield": 0.1,
        "index_dividend_yield_percentiles": {"5Y": 1.0},
        "index_dividend_yield_average_5y": 0.1,
        "equity_bond_ratio": -99.0,
        "equity_bond_spread": {"current": -99.0},
        "untouched": {"value": True},
    }


def _estimate(date: str = TARGET_DATE) -> dict:
    return {
        "estimate_date": date,
        "estimates": {
            "pe_ttm": 12.0,
            "pb_lf": 1.2,
            "dividend_yield": 3.5,
            "dividend_yield_spread": 1.5,
            "earnings_yield_spread": 6.333333,
        },
    }


def test_apply_estimate_replaces_all_current_derived_values_and_appends_target_day():
    valuation, dividends, bonds = _history()
    item = _item()

    result = apply_estimate(
        item,
        estimate=_estimate(),
        price_date=TARGET_DATE,
        valuation_history=valuation,
        dividend_history=dividends,
        bond_history=bonds,
    )

    assert isinstance(result, EstimateOverlay)
    assert result.item["index_valuation_metrics"]["PE(TTM)"]["current"] == 12.0
    assert result.item["index_valuation_metrics"]["PB(LF)"]["current"] == 1.2
    assert set(result.item["index_valuation_metrics"]["PE(TTM)"]["percentiles"]) == {
        "3M", "6M", "1Y", "2Y", "3Y", "5Y", "10Y", "今年以来", "成立以来"
    }
    assert result.item["index_dividend_yield"] == 3.5
    assert result.item["index_dividend_yield_average_5y"] != 0.1
    assert result.item["estimate_meta"] == {"date": TARGET_DATE, "status": "estimated"}
    assert result.item["equity_bond_ratio"] == round(100 / 12 - 2.0, 4)
    assert result.item["equity_bond_spread"]["current"] == round(100 / 12 - 2.0, 4)
    assert "ratio_current" in result.item["equity_bond_spread"]
    assert result.pe_history.iloc[-1].to_dict() == {
        "date": pd.Timestamp(TARGET_DATE),
        "pe": 12.0,
    }


def test_apply_estimate_requires_matching_complete_finite_estimate_and_same_day_bond():
    valuation, dividends, bonds = _history()
    bad_estimates = [
        _estimate("2026-08-09"),
        {"estimate_date": TARGET_DATE, "estimates": {"pe_ttm": 0, "pb_lf": 1, "dividend_yield": 1, "dividend_yield_spread": 0, "earnings_yield_spread": 0}},
        {"estimate_date": TARGET_DATE, "estimates": {"pe_ttm": 1, "pb_lf": 1, "dividend_yield": 1, "dividend_yield_spread": float("inf"), "earnings_yield_spread": 0}},
    ]

    for estimate in bad_estimates:
        assert apply_estimate(
            _item(), estimate=estimate, price_date=TARGET_DATE,
            valuation_history=valuation, dividend_history=dividends, bond_history=bonds,
        ) is None

    assert apply_estimate(
        _item(), estimate=_estimate(), price_date=TARGET_DATE,
        valuation_history=valuation, dividend_history=dividends,
        bond_history=bonds[bonds["date"] != pd.Timestamp(TARGET_DATE)],
    ) is None

    malformed_date = _estimate()
    malformed_date["estimate_date"] = []
    assert apply_estimate(
        _item(), estimate=malformed_date, price_date=TARGET_DATE,
        valuation_history=valuation, dividend_history=dividends, bond_history=bonds,
    ) is None


def test_apply_estimate_does_not_modify_input_item_or_histories():
    valuation, dividends, bonds = _history()
    item = _item()
    before_item = deepcopy(item)
    before_valuation = valuation.copy(deep=True)
    before_dividends = dividends.copy(deep=True)
    before_bonds = bonds.copy(deep=True)

    assert apply_estimate(
        item, estimate=_estimate(), price_date=TARGET_DATE,
        valuation_history=valuation, dividend_history=dividends, bond_history=bonds,
    ) is not None

    assert item == before_item
    pd.testing.assert_frame_equal(valuation, before_valuation)
    pd.testing.assert_frame_equal(dividends, before_dividends)
    pd.testing.assert_frame_equal(bonds, before_bonds)


def test_apply_estimate_discards_future_history_so_target_is_the_current_day():
    valuation, dividends, bonds = _history()
    future = pd.Timestamp("2026-08-11")
    valuation.loc[len(valuation)] = {"date": future, "pe_ttm": 50.0, "pb_lf": 5.0}
    dividends.loc[len(dividends)] = {"date": future, "dividend_yield": 1.0}
    bonds.loc[len(bonds)] = {"date": future, "yield_pct": 9.0}

    result = apply_estimate(
        _item(), estimate=_estimate(), price_date=TARGET_DATE,
        valuation_history=valuation, dividend_history=dividends, bond_history=bonds,
    )

    assert result is not None
    assert result.pe_history.iloc[-1]["date"] == pd.Timestamp(TARGET_DATE)
    assert result.item["equity_bond_spread"]["current"] == round(100 / 12 - 2.0, 4)


def test_apply_from_archives_uses_archive_histories_and_returns_none_for_bad_data(tmp_path):
    archive = tmp_path / "archive"
    valuation, dividends, _bonds = _history()
    records = lambda frame, fields: [
        {"trdDt": row.date.strftime("%Y-%m-%d"), **{key: row[source] for key, source in fields.items()}}
        for _, row in frame.iterrows()
    ]
    for dataset, payload in {
        "index_valuation_percentile": records(valuation, {"pETtm": "pe_ttm", "pBLf": "pb_lf"}),
        "index_dividend_ratio": records(dividends, {"dividendYield": "dividend_yield"}),
        "index_eod": [{"trdDt": TARGET_DATE, "pxClose": 1.0}],
    }.items():
        path = archive / dataset / "931052.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(__import__("json").dumps({"records": payload}), encoding="utf-8")

    result = apply_from_archives(
        _item(), estimate=_estimate(), price_date=TARGET_DATE, archive_root=archive,
        bond_history=pd.DataFrame({"date": pd.date_range("2026-07-01", periods=41), "yield_pct": [2.0] * 41}),
    )
    assert result is not None
    assert result.item["index_valuation_metrics"]["PE(TTM)"]["current"] == 12.0

    (archive / "index_dividend_ratio" / "931052.json").write_text("{not json", encoding="utf-8")
    assert apply_from_archives(
        _item(), estimate=_estimate(), price_date=TARGET_DATE, archive_root=archive,
        bond_history=pd.DataFrame({"date": [TARGET_DATE], "yield_pct": [2.0]}),
    ) is None

    (archive / "index_dividend_ratio" / "931052.json").write_text(
        '{"records": []}', encoding="utf-8"
    )
    assert apply_from_archives(
        _item(), estimate=_estimate(), price_date=TARGET_DATE, archive_root=archive,
        bond_history=pd.DataFrame({"date": [TARGET_DATE], "yield_pct": [2.0]}),
    ) is None


def test_latest_price_date_reads_real_independent_index_931052_archive():
    archive_root = Path(__file__).resolve().parents[1] / "data" / "archive"

    assert latest_price_date("931052", archive_root) == "2026-08-10"
    assert latest_price_date("../931052", archive_root) is None


def test_archive_readers_return_none_for_json_dates_with_the_wrong_type(tmp_path):
    archive = tmp_path / "archive"
    eod = archive / "index_eod" / "931052.json"
    eod.parent.mkdir(parents=True)
    eod.write_text('{"records": [{"trdDt": [], "pxClose": 1.0}]}', encoding="utf-8")

    assert latest_price_date("931052", archive) is None
    assert apply_from_archives(
        _item(), estimate=_estimate(), price_date=TARGET_DATE, archive_root=archive,
        bond_history=pd.DataFrame({"date": [TARGET_DATE], "yield_pct": [2.0]}),
    ) is None
