import json
from pathlib import Path

from src.research.dividend_observation_chart import (
    build_dividend_observation_payload,
    main,
)


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_config(path: Path, *, analysis_window_years: int = 3, display_window_years: int = 3) -> None:
    _write(
        path,
        {
            "analysis_window_years": analysis_window_years,
            "display_window_years": display_window_years,
        },
    )


def _write_estimate_overlay_inputs(archive: Path, estimate_root: Path) -> None:
    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-08-07", "pxClose": 100.0},
                {"trdDt": "2026-08-10", "pxClose": 110.0},
            ]
        },
    )
    _write(
        archive / "index_valuation_percentile" / "930955.json",
        {"records": [{"trdDt": "2026-08-07", "pETtm": 10.0, "pBLf": 1.0}]},
    )
    _write(
        archive / "index_dividend_ratio" / "930955.json",
        {"records": [{"trdDt": "2026-08-07", "dividendYield": 4.0}]},
    )
    _write(
        archive / "bond_10y" / "china_10y.json",
        [
            {"日期": "2026-08-07", "中国国债收益率10年": 2.0},
            {"日期": "2026-08-10", "中国国债收益率10年": 2.0},
        ],
    )
    _write(
        estimate_root / "930955.json",
        {
            "index_code": "930955",
            "records": [
                {
                    "estimate_date": "2026-08-10",
                    "status": "estimated",
                    "estimates": {
                        "pe_ttm": 11.0,
                        "pb_lf": 1.1,
                        "dividend_yield_spread": 2.2,
                        "earnings_yield_spread": 7.4,
                    },
                }
            ],
        },
    )


def test_payload_uses_same_date_estimate_when_official_values_are_missing(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)

    payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    assert payload["latest"]["pe_ttm_percentile"] == 100.0
    assert payload["latest"]["pb_lf_percentile"] == 100.0
    assert payload["latest"]["dividend_yield_spread_percentile"] == 100.0
    assert payload["latest"]["earnings_yield_spread_percentile"] == 50.0
    assert payload["meta"]["latest_estimate"] == {
        "date": "2026-08-10",
        "pe_ttm": 11.0,
        "pb_lf": 1.1,
        "dividend_yield_spread": 2.2,
        "earnings_yield_spread": 7.4,
    }


def test_payload_generates_missing_latest_estimate_from_local_archives(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)
    (estimate_root / "930955.json").unlink()

    payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    assert (estimate_root / "930955.json").exists()
    assert payload["meta"]["latest_estimate"]["date"] == "2026-08-10"
    assert payload["meta"]["latest_estimate"]["pe_ttm"] == 11.0


def test_payload_prefers_same_date_complete_official_values_over_estimate(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)
    _write(
        archive / "index_valuation_percentile" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-08-07", "pETtm": 10.0, "pBLf": 1.0},
                {"trdDt": "2026-08-10", "pETtm": 9.0, "pBLf": 0.9},
            ]
        },
    )
    _write(
        archive / "index_dividend_ratio" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-08-07", "dividendYield": 4.0},
                {"trdDt": "2026-08-10", "dividendYield": 4.5},
            ]
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    assert payload["latest"]["pe_ttm_percentile"] == 50.0
    assert payload["latest"]["pb_lf_percentile"] == 50.0
    assert payload["latest"]["dividend_yield_spread_percentile"] == 100.0
    assert payload["latest"]["earnings_yield_spread_percentile"] == 100.0
    assert "latest_estimate" not in payload["meta"]


def test_payload_ignores_incomplete_or_wrong_date_estimate(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)
    _write(
        estimate_root / "930955.json",
        {
            "index_code": "930955",
            "records": [
                {
                    "estimate_date": "2026-08-09",
                    "status": "estimated",
                    "estimates": {
                        "pe_ttm": 11.0,
                        "pb_lf": 1.1,
                        "dividend_yield_spread": 2.2,
                        "earnings_yield_spread": 7.4,
                    },
                },
                {
                    "estimate_date": "2026-08-10",
                    "status": "estimated",
                    "estimates": {
                        "pe_ttm": 11.0,
                        "pb_lf": 1.1,
                        "dividend_yield_spread": 2.2,
                    },
                },
            ],
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    assert payload["latest"]["pe_ttm_percentile"] is None
    assert payload["latest"]["pb_lf_percentile"] is None
    assert payload["latest"]["dividend_yield_spread_percentile"] is None
    assert payload["latest"]["earnings_yield_spread_percentile"] is None
    assert "latest_estimate" not in payload["meta"]


def test_payload_ignores_estimate_ledger_for_a_different_index(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)
    _write(
        estimate_root / "930955.json",
        {
            "index_code": "000300",
            "records": [
                {
                    "estimate_date": "2026-08-10",
                    "status": "estimated",
                    "estimates": {
                        "pe_ttm": 11.0,
                        "pb_lf": 1.1,
                        "dividend_yield_spread": 2.2,
                        "earnings_yield_spread": 7.4,
                    },
                }
            ],
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    assert payload["latest"]["pe_ttm_percentile"] is None
    assert "latest_estimate" not in payload["meta"]


def test_payload_ignores_malformed_or_non_list_estimate_ledger(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)
    ledger_path = estimate_root / "930955.json"

    ledger_path.write_text("{invalid json", encoding="utf-8")
    malformed_payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )
    _write(ledger_path, {"index_code": "930955", "records": {}})
    non_list_payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    for payload in (malformed_payload, non_list_payload):
        assert payload["latest"]["pe_ttm_percentile"] is None
        assert "latest_estimate" not in payload["meta"]


def test_payload_uses_estimate_as_a_unit_when_same_date_official_data_is_partial(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)
    _write(
        archive / "index_valuation_percentile" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-08-07", "pETtm": 10.0, "pBLf": 1.0},
                {"trdDt": "2026-08-10", "pETtm": 9.0},
            ]
        },
    )
    _write(
        archive / "index_dividend_ratio" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-08-07", "dividendYield": 4.0},
                {"trdDt": "2026-08-10", "dividendYield": 4.5},
            ]
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    assert payload["latest"]["pe_ttm_percentile"] == 100.0
    assert payload["latest"]["pb_lf_percentile"] == 100.0
    assert payload["latest"]["dividend_yield_spread_percentile"] == 100.0
    assert payload["latest"]["earnings_yield_spread_percentile"] == 50.0
    assert payload["meta"]["latest_estimate"]["pe_ttm"] == 11.0


def test_payload_normalizes_archive_and_ledger_dates_to_iso_day(tmp_path):
    archive = tmp_path / "archive"
    estimate_root = tmp_path / "index_valuation_estimates"
    _write_estimate_overlay_inputs(archive, estimate_root)
    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-08-07T15:00:00", "pxClose": 100.0},
                {"trdDt": "2026-08-10T15:00:00", "pxClose": 110.0},
            ]
        },
    )
    _write(
        estimate_root / "930955.json",
        {
            "index_code": "930955",
            "records": [
                {
                    "estimate_date": "2026-08-10T18:00:00",
                    "status": "estimated",
                    "estimates": {
                        "pe_ttm": 11.0,
                        "pb_lf": 1.1,
                        "dividend_yield_spread": 2.2,
                        "earnings_yield_spread": 7.4,
                    },
                }
            ],
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        estimate_root=estimate_root,
        dataset_path=tmp_path / "events.json",
        event_state_model_path=tmp_path / "states.json",
        drawdown_window_days=2,
        valuation_window_days=2,
        spread_window_days=2,
        style_window_days=2,
    )

    assert payload["series"]["dates"] == ["2026-08-07", "2026-08-10"]
    assert payload["meta"]["latest_estimate"]["date"] == "2026-08-10"


def test_build_dividend_observation_payload_emits_required_series(tmp_path):
    archive = tmp_path / "archive"
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    event_state_path = tmp_path / "event_state_model.json"

    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pxClose": 100.0},
                {"trdDt": "2026-01-02", "pxClose": 98.0},
                {"trdDt": "2026-01-03", "pxClose": 96.0},
                {"trdDt": "2026-01-04", "pxClose": 97.0},
                {"trdDt": "2026-01-05", "pxClose": 99.0},
            ]
        },
    )
    _write(
        archive / "index_valuation_percentile" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pETtm": 10.0, "pETtm10Y": 60.0, "pBLf": 1.2, "pBLf10Y": 55.0},
                {"trdDt": "2026-01-02", "pETtm": 9.5, "pETtm10Y": 50.0, "pBLf": 1.1, "pBLf10Y": 45.0},
                {"trdDt": "2026-01-03", "pETtm": 9.0, "pETtm10Y": 40.0, "pBLf": 1.0, "pBLf10Y": 35.0},
                {"trdDt": "2026-01-04", "pETtm": 9.2, "pETtm10Y": 44.0, "pBLf": 1.02, "pBLf10Y": 37.0},
                {"trdDt": "2026-01-05", "pETtm": 9.4, "pETtm10Y": 48.0, "pBLf": 1.05, "pBLf10Y": 40.0},
            ]
        },
    )
    _write(
        archive / "index_dividend_ratio" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "dividendYield": 4.0},
                {"trdDt": "2026-01-02", "dividendYield": 4.2},
                {"trdDt": "2026-01-03", "dividendYield": 4.5},
                {"trdDt": "2026-01-04", "dividendYield": 4.4},
                {"trdDt": "2026-01-05", "dividendYield": 4.3},
            ]
        },
    )
    _write(
        archive / "bond_10y" / "china_10y.json",
        [
            {"日期": "2026-01-01", "中国国债收益率10年": 2.0},
            {"日期": "2026-01-02", "中国国债收益率10年": 1.9},
            {"日期": "2026-01-03", "中国国债收益率10年": 1.8},
            {"日期": "2026-01-04", "中国国债收益率10年": 1.85},
            {"日期": "2026-01-05", "中国国债收益率10年": 1.9},
        ],
    )
    _write(
        archive / "index_eod" / "399376.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pxClose": 100.0},
                {"trdDt": "2026-01-02", "pxClose": 102.0},
                {"trdDt": "2026-01-03", "pxClose": 106.0},
                {"trdDt": "2026-01-04", "pxClose": 108.0},
                {"trdDt": "2026-01-05", "pxClose": 110.0},
            ]
        },
    )
    _write(
        archive / "index_eod" / "399373.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pxClose": 100.0},
                {"trdDt": "2026-01-02", "pxClose": 101.0},
                {"trdDt": "2026-01-03", "pxClose": 101.5},
                {"trdDt": "2026-01-04", "pxClose": 102.0},
                {"trdDt": "2026-01-05", "pxClose": 102.5},
            ]
        },
    )
    _write(
        dataset_path,
        {
            "events": [
                {
                    "event_id": "930955:2025-12-20:2026-01-03",
                    "index_code": "930955",
                    "recovery_date": "2026-01-04",
                    "recovery_rule": "rebound_stability",
                }
            ]
        },
    )
    _write(
        event_state_path,
        {
            "event_state_model": [
                {
                    "event_id": "930955:2025-12-20:2026-01-03",
                    "index": "930955",
                    "original_end_method": "rebound_stability",
                    "new_state": "temporary_recovery",
                    "state_confirm_date": "2026-01-05",
                    "validation": {
                        "break_low_after_recovery": False,
                        "days_to_failure": None,
                        "max_drawdown_after_recovery": -0.01,
                    },
                }
            ]
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        dataset_path=dataset_path,
        event_state_model_path=event_state_path,
        drawdown_window_days=3,
        valuation_window_days=3,
        spread_window_days=3,
        style_window_days=3,
    )

    assert payload["meta"]["index_code"] == "930955"
    assert payload["meta"]["window"]["drawdown_days"] == 3
    series = payload["series"]
    assert series["dates"] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
        "2026-01-05",
    ]
    assert len(series["index_close"]) == 5
    assert len(series["drawdown_peak"]) == 5
    assert len(series["pe_ttm_percentile"]) == 5
    assert len(series["pb_lf_percentile"]) == 5
    assert len(series["dividend_yield_spread_percentile"]) == 5
    assert len(series["earnings_yield_spread_percentile"]) == 5
    assert len(series["style_rotation_spread_percentile"]) == 5
    assert payload["latest"]["event_state"] == "temporary_recovery"


def test_build_dividend_observation_payload_reads_window_years_from_config(tmp_path):
    archive = tmp_path / "archive"
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    event_state_path = tmp_path / "event_state_model.json"
    config_path = tmp_path / "dividend_observation_config.json"

    _write(
        archive / "index_eod" / "930955.json",
        {"records": [{"trdDt": "2026-01-01", "pxClose": 100.0}]},
    )
    _write(archive / "index_valuation_percentile" / "930955.json", {"records": []})
    _write(archive / "index_dividend_ratio" / "930955.json", {"records": []})
    _write(archive / "bond_10y" / "china_10y.json", [])
    _write(archive / "index_eod" / "399376.json", {"records": []})
    _write(archive / "index_eod" / "399373.json", {"records": []})
    _write(dataset_path, {"events": []})
    _write(event_state_path, {"event_state_model": []})
    _write_config(config_path, analysis_window_years=3, display_window_years=3)

    payload = build_dividend_observation_payload(
        archive_root=archive,
        dataset_path=dataset_path,
        event_state_model_path=event_state_path,
        config_path=config_path,
    )

    assert payload["meta"]["window"] == {
        "drawdown_days": 252 * 3,
        "valuation_days": 252 * 3,
        "spread_days": 252 * 3,
        "style_days": 252 * 3,
    }
    assert payload["meta"]["analysis_window_years"] == 3
    assert payload["meta"]["display_window_years"] == 3
    assert "drawdown_peak" in payload["series"]
    assert "dividend_yield_spread_percentile" in payload["series"]
    assert "earnings_yield_spread_percentile" in payload["series"]
    assert "style_rotation_spread_percentile" in payload["series"]
    assert payload["latest"]["drawdown_peak"] == 0.0


def test_build_dividend_observation_payload_uses_local_style_rotation_json(tmp_path):
    archive = tmp_path / "archive"
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    event_state_path = tmp_path / "event_state_model.json"
    style_rotation_path = tmp_path / "style_rotation_preview.json"

    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pxClose": 100.0},
                {"trdDt": "2026-01-02", "pxClose": 101.0},
                {"trdDt": "2026-01-03", "pxClose": 102.0},
            ]
        },
    )
    _write(archive / "index_valuation_percentile" / "930955.json", {"records": []})
    _write(archive / "index_dividend_ratio" / "930955.json", {"records": []})
    _write(archive / "bond_10y" / "china_10y.json", [])
    _write(dataset_path, {"events": []})
    _write(event_state_path, {"event_state_model": []})
    _write(
        style_rotation_path,
        {
            "meta": {
                "left_symbol": "399376",
                "left_name": "国证小盘成长",
                "right_symbol": "399373",
                "right_name": "国证大盘价值",
                "return_window_days": 250,
                "display_window_days": 1260,
            },
            "series": {
                "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "spread": [2.0, 1.0, 3.0],
            },
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        dataset_path=dataset_path,
        event_state_model_path=event_state_path,
        style_rotation_payload_path=style_rotation_path,
        drawdown_window_days=3,
        valuation_window_days=3,
        spread_window_days=3,
        style_window_days=3,
    )

    assert payload["series"]["style_rotation_spread_percentile"] == [100.0, 50.0, 100.0]
    assert payload["latest"]["style_rotation_spread_percentile"] == 100.0


def test_build_dividend_observation_payload_fetches_and_persists_style_rotation_json(tmp_path):
    archive = tmp_path / "archive"
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    event_state_path = tmp_path / "event_state_model.json"
    style_rotation_path = tmp_path / "style_rotation_preview.json"

    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pxClose": 100.0},
                {"trdDt": "2026-01-02", "pxClose": 101.0},
                {"trdDt": "2026-01-03", "pxClose": 102.0},
            ]
        },
    )
    _write(archive / "index_valuation_percentile" / "930955.json", {"records": []})
    _write(archive / "index_dividend_ratio" / "930955.json", {"records": []})
    _write(archive / "bond_10y" / "china_10y.json", [])
    _write(dataset_path, {"events": []})
    _write(event_state_path, {"event_state_model": []})

    fetch_calls: list[str] = []

    def fake_style_rotation_fetcher() -> dict:
        fetch_calls.append("called")
        return {
            "meta": {
                "left_symbol": "399376",
                "left_name": "国证小盘成长",
                "right_symbol": "399373",
                "right_name": "国证大盘价值",
                "return_window_days": 250,
                "display_window_days": 1260,
            },
            "series": {
                "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "spread": [1.0, 2.0, 1.0],
            },
        }

    payload = build_dividend_observation_payload(
        archive_root=archive,
        dataset_path=dataset_path,
        event_state_model_path=event_state_path,
        style_rotation_payload_path=style_rotation_path,
        style_rotation_fetcher=fake_style_rotation_fetcher,
        drawdown_window_days=3,
        valuation_window_days=3,
        spread_window_days=3,
        style_window_days=3,
    )

    assert fetch_calls == ["called"]
    assert style_rotation_path.exists()
    assert payload["series"]["style_rotation_spread_percentile"] == [100.0, 100.0, 66.6667]


def test_build_dividend_observation_payload_force_refreshes_existing_style_rotation_json(tmp_path):
    archive = tmp_path / "archive"
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    event_state_path = tmp_path / "event_state_model.json"
    style_rotation_path = tmp_path / "style_rotation_preview.json"

    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pxClose": 100.0},
                {"trdDt": "2026-01-02", "pxClose": 101.0},
                {"trdDt": "2026-01-03", "pxClose": 102.0},
            ]
        },
    )
    _write(archive / "index_valuation_percentile" / "930955.json", {"records": []})
    _write(archive / "index_dividend_ratio" / "930955.json", {"records": []})
    _write(archive / "bond_10y" / "china_10y.json", [])
    _write(dataset_path, {"events": []})
    _write(event_state_path, {"event_state_model": []})
    _write(
        style_rotation_path,
        {
            "meta": {"left_symbol": "399376", "right_symbol": "399373"},
            "series": {
                "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "spread": [9.0, 9.0, 9.0],
            },
        },
    )

    fetch_calls: list[str] = []

    def fake_style_rotation_fetcher() -> dict:
        fetch_calls.append("called")
        return {
            "meta": {
                "left_symbol": "399376",
                "left_name": "国证小盘成长",
                "right_symbol": "399373",
                "right_name": "国证大盘价值",
                "return_window_days": 250,
                "display_window_days": 1260,
            },
            "series": {
                "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "spread": [1.0, 2.0, 1.0],
            },
        }

    payload = build_dividend_observation_payload(
        archive_root=archive,
        dataset_path=dataset_path,
        event_state_model_path=event_state_path,
        style_rotation_payload_path=style_rotation_path,
        style_rotation_fetcher=fake_style_rotation_fetcher,
        force_refresh_style_rotation_payload=True,
        drawdown_window_days=3,
        valuation_window_days=3,
        spread_window_days=3,
        style_window_days=3,
    )

    assert fetch_calls == ["called"]
    assert payload["series"]["style_rotation_spread_percentile"] == [100.0, 100.0, 66.6667]
    persisted = json.loads(style_rotation_path.read_text(encoding="utf-8"))
    assert persisted["series"]["spread"] == [1.0, 2.0, 1.0]


def test_build_dividend_observation_payload_maps_event_state_ribbon(tmp_path):
    archive = tmp_path / "archive"
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    event_state_path = tmp_path / "event_state_model.json"

    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-01-01", "pxClose": 100.0},
                {"trdDt": "2026-01-02", "pxClose": 99.0},
                {"trdDt": "2026-01-03", "pxClose": 98.0},
                {"trdDt": "2026-01-04", "pxClose": 97.0},
                {"trdDt": "2026-01-05", "pxClose": 98.0},
                {"trdDt": "2026-01-06", "pxClose": 99.0},
            ]
        },
    )
    _write(
        archive / "index_valuation_percentile" / "930955.json",
        {"records": []},
    )
    _write(
        archive / "index_dividend_ratio" / "930955.json",
        {"records": []},
    )
    _write(
        archive / "bond_10y" / "china_10y.json",
        [],
    )
    _write(archive / "index_eod" / "399376.json", {"records": []})
    _write(archive / "index_eod" / "399373.json", {"records": []})
    _write(
        dataset_path,
        {
            "events": [
                {
                    "event_id": "930955:2025-12-20:2026-01-02",
                    "index_code": "930955",
                    "recovery_date": "2026-01-03",
                    "recovery_rule": "rebound_stability",
                },
                {
                    "event_id": "930955:2025-12-28:2026-01-04",
                    "index_code": "930955",
                    "recovery_date": "2026-01-05",
                    "recovery_rule": "rebound_stability",
                },
            ]
        },
    )
    _write(
        event_state_path,
        {
            "event_state_model": [
                {
                    "event_id": "930955:2025-12-20:2026-01-02",
                    "index": "930955",
                    "original_end_method": "rebound_stability",
                    "new_state": "failed_recovery",
                    "state_confirm_date": "2026-01-04",
                    "validation": {},
                },
                {
                    "event_id": "930955:2025-12-28:2026-01-04",
                    "index": "930955",
                    "original_end_method": "rebound_stability",
                    "new_state": "confirmed_recovery",
                    "state_confirm_date": "2026-01-06",
                    "validation": {},
                },
            ]
        },
    )

    payload = build_dividend_observation_payload(
        archive_root=archive,
        dataset_path=dataset_path,
        event_state_model_path=event_state_path,
        drawdown_window_days=3,
        valuation_window_days=3,
        spread_window_days=3,
        style_window_days=3,
    )

    assert payload["series"]["event_state"] == [
        None,
        None,
        "failed_recovery",
        "failed_recovery",
        "confirmed_recovery",
        "confirmed_recovery",
    ]


def test_main_writes_dividend_observation_payload(tmp_path):
    archive = tmp_path / "archive"
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    event_state_path = tmp_path / "event_state_model.json"
    style_rotation_path = tmp_path / "style_rotation_preview.json"
    output = tmp_path / "dividend_observation_930955.json"

    _write(
        archive / "index_eod" / "930955.json",
        {"records": [{"trdDt": "2026-01-01", "pxClose": 100.0}]},
    )
    _write(archive / "index_valuation_percentile" / "930955.json", {"records": []})
    _write(archive / "index_dividend_ratio" / "930955.json", {"records": []})
    _write(archive / "bond_10y" / "china_10y.json", [])
    _write(dataset_path, {"events": []})
    _write(event_state_path, {"event_state_model": []})
    _write(
        style_rotation_path,
        {
            "meta": {"left_symbol": "399376", "right_symbol": "399373"},
            "series": {"dates": ["2026-01-01"], "spread": [1.0]},
        },
    )

    code = main(
        [
            "--archive-root",
            str(archive),
            "--dataset",
            str(dataset_path),
            "--event-state-model",
            str(event_state_path),
            "--style-rotation-payload",
            str(style_rotation_path),
            "--output",
            str(output),
            "--drawdown-window-days",
            "3",
            "--valuation-window-days",
            "3",
            "--spread-window-days",
            "3",
            "--style-window-days",
            "3",
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "meta" in payload
    assert "series" in payload
    assert "latest" in payload
