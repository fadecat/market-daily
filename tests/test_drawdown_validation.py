import json
from pathlib import Path

from src.research.drawdown_validation import (
    build_sample_pollution_audit,
    evaluate_walkforward_information_increment,
    main,
    prior_closed_events,
)


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_prior_closed_events_only_include_events_closed_before_target_start_date():
    target = {
        "event_id": "target",
        "index_code": "930955",
        "start_date": "2020-06-01",
        "recovered": False,
        "recovery_date": None,
    }
    earlier_closed = {
        "event_id": "earlier-closed",
        "index_code": "930955",
        "start_date": "2020-01-10",
        "recovered": True,
        "recovery_date": "2020-02-01",
    }
    earlier_but_open_then = {
        "event_id": "earlier-open",
        "index_code": "930955",
        "start_date": "2020-05-10",
        "recovered": True,
        "recovery_date": "2020-06-10",
    }
    future_closed = {
        "event_id": "future-closed",
        "index_code": "930955",
        "start_date": "2020-07-01",
        "recovered": True,
        "recovery_date": "2020-07-20",
    }

    result = prior_closed_events(
        [target, earlier_closed, earlier_but_open_then, future_closed],
        target,
    )

    assert [event["event_id"] for event in result] == ["earlier-closed"]


def test_build_sample_pollution_audit_marks_overlap_risk_by_index_and_severity(tmp_path):
    dataset = {
        "events": [
            {
                "event_id": "930955:a",
                "index_code": "930955",
                "severity": "major",
                "trough_date": "2020-01-02",
                "recovery_days": 6,
            },
            {
                "event_id": "930955:b",
                "index_code": "930955",
                "severity": "major",
                "trough_date": "2020-01-04",
                "recovery_days": 6,
            },
            {
                "event_id": "930955:c",
                "index_code": "930955",
                "severity": "major",
                "trough_date": "2020-01-10",
                "recovery_days": 6,
            },
            {
                "event_id": "399326:a",
                "index_code": "399326",
                "severity": "major",
                "trough_date": "2020-01-02",
                "recovery_days": 3,
            },
            {
                "event_id": "399326:b",
                "index_code": "399326",
                "severity": "major",
                "trough_date": "2020-01-10",
                "recovery_days": 3,
            },
        ]
    }
    archive = tmp_path / "archive"
    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2020-01-01", "pxClose": 100.0},
                {"trdDt": "2020-01-02", "pxClose": 99.0},
                {"trdDt": "2020-01-03", "pxClose": 98.0},
                {"trdDt": "2020-01-04", "pxClose": 97.0},
                {"trdDt": "2020-01-05", "pxClose": 96.0},
                {"trdDt": "2020-01-06", "pxClose": 95.0},
                {"trdDt": "2020-01-07", "pxClose": 94.0},
                {"trdDt": "2020-01-08", "pxClose": 93.0},
                {"trdDt": "2020-01-09", "pxClose": 92.0},
                {"trdDt": "2020-01-10", "pxClose": 91.0},
            ]
        },
    )
    _write(
        archive / "index_eod" / "399326.json",
        {
            "records": [
                {"trdDt": "2020-01-01", "pxClose": 200.0},
                {"trdDt": "2020-01-02", "pxClose": 199.0},
                {"trdDt": "2020-01-03", "pxClose": 198.0},
                {"trdDt": "2020-01-04", "pxClose": 197.0},
                {"trdDt": "2020-01-05", "pxClose": 196.0},
                {"trdDt": "2020-01-06", "pxClose": 195.0},
                {"trdDt": "2020-01-07", "pxClose": 194.0},
                {"trdDt": "2020-01-08", "pxClose": 193.0},
                {"trdDt": "2020-01-09", "pxClose": 192.0},
                {"trdDt": "2020-01-10", "pxClose": 191.0},
            ]
        },
    )

    audit = build_sample_pollution_audit(dataset, archive_root=archive)

    assert audit == [
        {
            "index": "399326",
            "severity": "major",
            "event_count": 2,
            "median_recovery_days": 3,
            "high_overlap_ratio": 0.0,
            "status": "可以作为方向性统计",
        },
        {
            "index": "930955",
            "severity": "major",
            "event_count": 3,
            "median_recovery_days": 6,
            "high_overlap_ratio": 0.5,
            "status": "存在明显样本污染风险",
        },
    ]


def _walkforward_event(
    event_id: str,
    *,
    start_date: str,
    recovery_date: str | None,
    start_close: float,
    start_pe: float,
    forward_21d: float | None,
    recovered_to_peak_within_252d: bool,
    index_code: str = "930955",
):
    return {
        "event_id": event_id,
        "index_code": index_code,
        "peak_date": "2020-01-01",
        "peak_close": 100.0,
        "start_date": start_date,
        "start_close": start_close,
        "recovered": recovery_date is not None,
        "recovery_date": recovery_date,
        "peak_context": {"pe_ttm": 20.0, "dividend_yield": 3.0, "bond_10y": 2.0},
        "start_context": {"pe_ttm": start_pe, "dividend_yield": 3.2, "bond_10y": 1.9},
        "start_forward_returns": {"21d": forward_21d},
        "recovered_to_peak_within_252d": recovered_to_peak_within_252d,
    }


def test_evaluate_walkforward_information_increment_uses_prior_closed_events_only():
    events = [
        _walkforward_event(
            "early-good",
            start_date="2020-01-10",
            recovery_date="2020-02-01",
            start_close=95.0,
            start_pe=16.0,
            forward_21d=0.10,
            recovered_to_peak_within_252d=True,
        ),
        _walkforward_event(
            "early-bad",
            start_date="2020-03-10",
            recovery_date="2020-04-01",
            start_close=95.0,
            start_pe=28.0,
            forward_21d=-0.10,
            recovered_to_peak_within_252d=False,
        ),
        _walkforward_event(
            "target",
            start_date="2020-05-10",
            recovery_date="2020-06-01",
            start_close=95.0,
            start_pe=15.0,
            forward_21d=0.12,
            recovered_to_peak_within_252d=True,
        ),
        _walkforward_event(
            "future-lookalike",
            start_date="2020-07-10",
            recovery_date=None,
            start_close=95.0,
            start_pe=15.0,
            forward_21d=0.50,
            recovered_to_peak_within_252d=True,
        ),
    ]

    payload = evaluate_walkforward_information_increment(
        {"events": events},
        horizons=(21,),
        top_n=1,
    )

    summary = payload["summaries"][0]
    evaluation = next(
        row for row in payload["evaluations"] if row["event_id"] == "target"
    )

    assert evaluation["candidate_pool_size"] == 2
    assert evaluation["matched_event_ids"] == ["early-good"]
    assert evaluation["similar_prediction"]["21d"] == 0.1
    assert evaluation["random_prediction"]["21d"] == 0.0
    assert summary["index"] == "930955"
    assert summary["sample_count"] == 1
    assert summary["similar_method_median_21d_return"] == 0.1
    assert summary["random_median_21d_return"] == 0.0
    assert summary["information_increment_21d"] == 0.1
    assert summary["similar_recovery_to_peak_rate"] == 1.0
    assert summary["random_recovery_to_peak_rate"] == 0.5
    assert summary["recovery_information_increment"] == 0.5


def test_main_writes_walkforward_validation_json(tmp_path):
    archive_root = Path(__file__).resolve().parents[1] / "data" / "archive"
    dataset_path = Path(__file__).resolve().parents[1] / "data" / "research" / "value_growth_drawdown_events.json"
    output = tmp_path / "walk_forward_similarity_test.json"

    code = main(
        [
            "--dataset",
            str(dataset_path),
            "--archive-root",
            str(archive_root),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "sample_pollution_audit" in payload
    assert "walk_forward_similarity_test" in payload
