import json
import os
from pathlib import Path

import pandas as pd
import pytest

from src.valuation import estimate_ledger as ledger
from src.valuation.estimate_ledger import build_estimate_records, refresh_estimate_ledger


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


def test_builder_uses_runtime_close_missing_from_archive(tmp_path):
    archive = tmp_path / "archive"
    _write_archive(
        archive / "index_eod" / "931233.json",
        [{"trdDt": "2026-08-10", "pxClose": 100.0}],
    )
    _write_archive(
        archive / "index_valuation_percentile" / "931233.json",
        [{"trdDt": "2026-08-10", "pETtm": 10.0, "pBLf": 1.0}],
    )
    _write_archive(
        archive / "index_dividend_ratio" / "931233.json",
        [{"trdDt": "2026-08-10", "dividendYield": 4.0}],
    )
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10", "2026-08-11"]), "yield_pct": [1.7074, 1.7161]}
    )

    rows = build_estimate_records(
        "931233",
        archive_root=archive,
        bond_history=bonds,
        latest_close={"date": "2026-08-11", "close": 120.0},
    )

    assert [row["estimate_date"] for row in rows] == ["2026-08-11"]
    assert rows[0]["inputs"]["estimate_close"] == 120.0
    assert rows[0]["estimates"]["pe_ttm"] == 12.0


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


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_refresh_estimate_ledger_upserts_and_preserves_reconciliation(tmp_path):
    archive = tmp_path / "archive"
    _write_estimate_inputs(archive)
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [1.7074]}
    )
    output = tmp_path / "estimates" / "930955.json"
    _write_json(
        output,
        {
            "schema_version": 1,
            "index_code": "930955",
            "future_payload_field": {"keep": True},
            "records": [
                {
                    "estimate_date": "2026-08-10",
                    "reconciliation": {"official_value": {"pe_ttm": 8.86}},
                },
                {"estimate_date": "2026-08-11", "future_record_field": "keep"},
            ],
        },
    )

    changed = refresh_estimate_ledger(
        "930955",
        archive_root=archive,
        output_root=output.parent,
        bond_history_fetcher=lambda **_: (bonds, {"data_source": "live"}),
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert changed is True
    assert saved["schema_version"] == 1
    assert saved["index_code"] == "930955"
    assert saved["future_payload_field"] == {"keep": True}
    assert [record["estimate_date"] for record in saved["records"]] == [
        "2026-08-10",
        "2026-08-11",
    ]
    assert saved["records"][0]["reconciliation"]["official_value"]["pe_ttm"] == 8.86
    assert saved["records"][0]["estimates"]["pe_ttm"] == 8.862534
    assert saved["records"][1]["future_record_field"] == "keep"


def test_refresh_estimate_ledger_does_not_rewrite_unchanged_content(tmp_path):
    archive = tmp_path / "archive"
    _write_estimate_inputs(archive)
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [1.7074]}
    )
    output_root = tmp_path / "estimates"
    fetcher = lambda **_: (bonds, {"data_source": "live"})

    assert refresh_estimate_ledger(
        "930955", archive_root=archive, output_root=output_root, bond_history_fetcher=fetcher
    ) is True
    output = output_root / "930955.json"
    before_content = output.read_text(encoding="utf-8")
    before_mtime = os.stat(output).st_mtime_ns

    assert refresh_estimate_ledger(
        "930955", archive_root=archive, output_root=output_root, bond_history_fetcher=fetcher
    ) is False
    assert output.read_text(encoding="utf-8") == before_content
    assert os.stat(output).st_mtime_ns == before_mtime


def test_refresh_estimate_ledger_uses_supplied_bond_history_without_fetching(tmp_path):
    archive = tmp_path / "archive"
    _write_estimate_inputs(archive)
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [1.7074]}
    )

    changed = refresh_estimate_ledger(
        "930955",
        archive_root=archive,
        output_root=tmp_path / "estimates",
        bond_history=bonds,
        bond_history_fetcher=lambda **_: pytest.fail("supplied history must not fetch data"),
    )

    assert changed is True


def test_load_estimate_record_returns_matching_estimate(tmp_path):
    output_root = tmp_path / "estimates"
    record = {
        "estimate_date": "2026-08-10",
        "status": "estimated",
        "estimates": {"pe_ttm": 8.862534},
    }
    _write_json(
        output_root / "930955.json",
        {"schema_version": 1, "index_code": "930955", "records": [record]},
    )

    assert ledger.load_estimate_record("930955", "2026-08-10", output_root) == record


@pytest.mark.parametrize(
    "index_code, estimate_date, payload",
    [
        ("bad", "2026-08-10", None),
        ("930955", "2026-08-10", []),
        ("930955", "2026-08-10", {"index_code": "930955", "records": {}}),
        ("930955", "2026-08-10", {"index_code": "000300", "records": []}),
        (
            "930955",
            "2026-08-10",
            {"index_code": "930955", "records": [{"estimate_date": "2026-08-09", "estimates": {}}]},
        ),
        (
            "930955",
            "2026-08-10",
            {"index_code": "930955", "records": [{"estimate_date": "2026-08-10", "estimates": []}]},
        ),
    ],
)
def test_load_estimate_record_returns_none_for_invalid_input_or_payload(
    tmp_path, index_code, estimate_date, payload
):
    output_root = tmp_path / "estimates"
    if payload is not None:
        _write_json(output_root / "930955.json", payload)

    assert ledger.load_estimate_record(index_code, estimate_date, output_root) is None


def test_load_estimate_record_returns_none_for_invalid_json_or_read_error(monkeypatch, tmp_path):
    output_root = tmp_path / "estimates"
    output = output_root / "930955.json"
    output.parent.mkdir(parents=True)
    output.write_text("{", encoding="utf-8")

    assert ledger.load_estimate_record("930955", "2026-08-10", output_root) is None

    def raise_os_error(*_args, **_kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", raise_os_error)
    assert ledger.load_estimate_record("930955", "2026-08-10", output_root) is None


def test_main_writes_only_requested_index(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    _write_estimate_inputs(archive, "930955")
    _write_estimate_inputs(archive, "000300")
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [1.7074]}
    )
    output_root = tmp_path / "estimates"
    monkeypatch.setattr(
        ledger.fetch,
        "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda **_: (bonds, {"data_source": "live"}),
    )

    assert ledger.main(
        [
            "--index-code",
            "930955",
            "--archive-root",
            str(archive),
            "--output-root",
            str(output_root),
        ]
    ) == 0
    assert (output_root / "930955.json").exists()
    assert not (output_root / "000300.json").exists()


@pytest.mark.parametrize("index_code", ["", "../../x", "93095", "930955x", "９３０９５５"])
def test_refresh_estimate_ledger_rejects_non_six_digit_index_codes(tmp_path, index_code):
    with pytest.raises(ValueError, match="six ASCII digits"):
        refresh_estimate_ledger(
            index_code,
            archive_root=tmp_path / "archive",
            output_root=tmp_path / "estimates",
            bond_history_fetcher=lambda **_: pytest.fail("invalid code must not fetch data"),
        )


def test_refresh_estimate_ledger_rejects_mismatched_existing_index_code(tmp_path):
    output = tmp_path / "estimates" / "930955.json"
    _write_json(output, {"schema_version": 1, "index_code": "000300", "records": []})

    with pytest.raises(ValueError, match="does not match requested index_code"):
        refresh_estimate_ledger(
            "930955",
            archive_root=tmp_path / "archive",
            output_root=output.parent,
            bond_history_fetcher=lambda **_: pytest.fail("mismatched ledger must not fetch data"),
        )


def test_refresh_estimate_ledger_writes_via_atomic_replace(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    _write_estimate_inputs(archive)
    bonds = pd.DataFrame(
        {"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [1.7074]}
    )
    output_root = tmp_path / "estimates"
    replace_calls = []
    original_replace = os.replace

    def recording_replace(source, destination):
        replace_calls.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr(ledger.os, "replace", recording_replace)

    assert refresh_estimate_ledger(
        "930955",
        archive_root=archive,
        output_root=output_root,
        bond_history_fetcher=lambda **_: (bonds, {"data_source": "live"}),
    ) is True

    output = output_root / "930955.json"
    assert len(replace_calls) == 1
    temporary_path, destination_path = replace_calls[0]
    assert temporary_path.parent == output.parent
    assert destination_path == output
    assert not temporary_path.exists()
    assert output.exists()
