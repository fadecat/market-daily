import json

import pandas as pd

from src.valuation.estimate_ledger import build_estimate_records


def _write_archive(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"records": records}), encoding="utf-8")


def _write_estimate_inputs(archive, index_code="930955"):
    _write_archive(
        archive / "index_eod" / f"{index_code}.json",
        [
            {"trdDt": "2026-08-07", "pxClose": 11291.4885},
            {"trdDt": "2026-08-10", "pxClose": 11364.4956},
        ],
    )
    _write_archive(
        archive / "index_valuation_percentile" / f"{index_code}.json",
        [{"trdDt": "2026-08-07", "pETtm": 8.8056, "pBLf": 0.8705}],
    )
    _write_archive(
        archive / "index_dividend_ratio" / f"{index_code}.json",
        [{"trdDt": "2026-08-07", "dividendYield": 4.4536}],
    )


def test_build_estimate_records_uses_price_factor_and_same_day_real_bond(tmp_path):
    archive = tmp_path / "archive"
    _write_estimate_inputs(archive)
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [1.7074]}
    )

    rows = build_estimate_records("930955", archive_root=archive, bond_history=bonds)

    assert len(rows) == 1
    assert rows[0]["estimate_date"] == "2026-08-10"
    assert rows[0]["inputs"]["valuation_price_factor"] == 1.006466
    assert rows[0]["inputs"]["dividend_price_factor"] == 1.006466
    assert rows[0]["inputs"]["valuation_base"] == {
        "date": "2026-08-07",
        "close": 11291.4885,
        "pe_ttm": 8.8056,
        "pb_lf": 0.8705,
    }
    assert rows[0]["inputs"]["dividend_base"] == {
        "date": "2026-08-07",
        "close": 11291.4885,
        "dividend_yield": 4.4536,
    }
    assert rows[0]["inputs"]["bond_10y"] == {
        "date": "2026-08-10",
        "yield_pct": 1.7074,
    }
    assert rows[0]["estimates"] == {
        "pe_ttm": 8.862534,
        "pb_lf": 0.876128,
        "dividend_yield": 4.424989,
        "dividend_yield_spread": 2.717589,
        "earnings_yield_spread": 9.576054,
    }


def test_builder_skips_date_with_all_official_inputs(tmp_path):
    archive = tmp_path / "archive"
    _write_archive(
        archive / "index_eod" / "000300.json",
        [
            {"trdDt": "2026-08-07", "pxClose": 11291.4885},
            {"trdDt": "2026-08-10", "pxClose": 11364.4956},
        ],
    )
    _write_archive(
        archive / "index_valuation_percentile" / "000300.json",
        [
            {"trdDt": "2026-08-07", "pETtm": 8.81, "pBLf": 0.87},
            {"trdDt": "2026-08-10", "pETtm": 8.86, "pBLf": 0.88},
        ],
    )
    _write_archive(
        archive / "index_dividend_ratio" / "000300.json",
        [
            {"trdDt": "2026-08-07", "dividendYield": 4.45},
            {"trdDt": "2026-08-10", "dividendYield": 4.42},
        ],
    )
    bonds = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-08-07", "2026-08-10"]),
            "yield_pct": [1.7114, 1.7074],
        }
    )

    assert build_estimate_records("000300", archive_root=archive, bond_history=bonds) == []


def test_builder_skips_date_without_same_day_bond(tmp_path):
    archive = tmp_path / "archive"
    _write_estimate_inputs(archive, "000300")
    t_minus_one_bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-07"]), "yield_pct": [1.7114]}
    )

    assert build_estimate_records(
        "000300", archive_root=archive, bond_history=t_minus_one_bonds
    ) == []


def test_builder_uses_independent_valuation_and_dividend_bases(tmp_path):
    archive = tmp_path / "archive"
    _write_archive(
        archive / "index_eod" / "000905.json",
        [
            {"trdDt": "2026-08-06", "pxClose": 100.0},
            {"trdDt": "2026-08-07", "pxClose": 110.0},
            {"trdDt": "2026-08-10", "pxClose": 121.0},
        ],
    )
    _write_archive(
        archive / "index_valuation_percentile" / "000905.json",
        [{"trdDt": "2026-08-06", "pETtm": 10.0, "pBLf": 1.0}],
    )
    _write_archive(
        archive / "index_dividend_ratio" / "000905.json",
        [{"trdDt": "2026-08-07", "dividendYield": 4.4}],
    )
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [2.0]}
    )

    rows = build_estimate_records("000905", archive_root=archive, bond_history=bonds)

    assert len(rows) == 1
    inputs = rows[0]["inputs"]
    assert inputs["valuation_base"]["date"] == "2026-08-06"
    assert inputs["valuation_base"]["close"] == 100.0
    assert inputs["valuation_price_factor"] == 1.21
    assert inputs["dividend_base"]["date"] == "2026-08-07"
    assert inputs["dividend_base"]["close"] == 110.0
    assert inputs["dividend_price_factor"] == 1.1
    assert rows[0]["estimates"]["dividend_yield"] == 4.0
    assert rows[0]["estimates"]["dividend_yield_spread"] == 2.0
