import json
from pathlib import Path

from src.research.rebound_stability_audit import (
    audit_rebound_stability_event,
    build_rebound_stability_audit,
    main,
)


def test_audit_rebound_stability_event_flags_future_break_of_trough_by_horizon():
    event = {
        "event_id": "930955:2026-01-01:2026-02-01",
        "index_code": "930955",
        "index_name": "红利低波100",
        "recovery_rule": "rebound_stability",
        "recovery_date": "2026-02-10",
        "trough_close": 70.0,
    }
    prices = [
        ("2026-02-09", 75.0),
        ("2026-02-10", 77.0),
        ("2026-02-11", 78.0),
        ("2026-02-12", 76.0),
        ("2026-02-13", 74.0),
        ("2026-02-14", 72.0),
        ("2026-02-15", 71.0),
        ("2026-02-16", 69.0),
        ("2026-02-17", 68.0),
    ]

    audit = audit_rebound_stability_event(event, prices, horizons=(3, 5, 7))

    assert audit["event_id"] == "930955:2026-01-01:2026-02-01"
    assert audit["index"] == "930955"
    assert audit["end_method"] == "rebound_stability"
    assert audit["3d_failed"] is False
    assert audit["5d_failed"] is False
    assert audit["7d_failed"] is True
    assert audit["post_event_max_drawdown"] == -0.116883


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_rebound_stability_audit_filters_events_and_uses_archive_prices(tmp_path):
    dataset = {
        "events": [
            {
                "event_id": "930955:2026-01-01:2026-02-01",
                "index_code": "930955",
                "index_name": "红利低波100",
                "recovery_rule": "rebound_stability",
                "recovery_date": "2026-02-10",
                "trough_close": 70.0,
            },
            {
                "event_id": "930955:2026-03-01:2026-03-10",
                "index_code": "930955",
                "index_name": "红利低波100",
                "recovery_rule": "full_peak_recovery",
                "recovery_date": "2026-03-20",
                "trough_close": 80.0,
            },
        ]
    }
    archive = tmp_path / "archive"
    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-02-09", "pxClose": 75.0},
                {"trdDt": "2026-02-10", "pxClose": 77.0},
                {"trdDt": "2026-02-11", "pxClose": 78.0},
                {"trdDt": "2026-02-12", "pxClose": 76.0},
                {"trdDt": "2026-02-13", "pxClose": 74.0},
                {"trdDt": "2026-02-14", "pxClose": 72.0},
                {"trdDt": "2026-02-15", "pxClose": 71.0},
                {"trdDt": "2026-02-16", "pxClose": 69.0},
                {"trdDt": "2026-02-17", "pxClose": 68.0},
            ]
        },
    )

    audits = build_rebound_stability_audit(dataset, archive_root=archive, horizons=(3, 5, 7))

    assert len(audits) == 1
    assert audits[0]["event_id"] == "930955:2026-01-01:2026-02-01"
    assert audits[0]["7d_failed"] is True


def test_main_writes_rebound_stability_audit_json(tmp_path):
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    archive = tmp_path / "archive"
    output = tmp_path / "rebound_stability_audit.json"

    _write(
        dataset_path,
        {
            "events": [
                {
                    "event_id": "930955:2026-01-01:2026-02-01",
                    "index_code": "930955",
                    "index_name": "红利低波100",
                    "recovery_rule": "rebound_stability",
                    "recovery_date": "2026-02-10",
                    "trough_close": 70.0,
                }
            ]
        },
    )
    _write(
        archive / "index_eod" / "930955.json",
        {
            "records": [
                {"trdDt": "2026-02-09", "pxClose": 75.0},
                {"trdDt": "2026-02-10", "pxClose": 77.0},
                {"trdDt": "2026-02-11", "pxClose": 78.0},
                {"trdDt": "2026-02-12", "pxClose": 76.0},
                {"trdDt": "2026-02-13", "pxClose": 74.0},
                {"trdDt": "2026-02-14", "pxClose": 72.0},
                {"trdDt": "2026-02-15", "pxClose": 71.0},
                {"trdDt": "2026-02-16", "pxClose": 69.0},
                {"trdDt": "2026-02-17", "pxClose": 68.0},
            ]
        },
    )

    code = main(
        [
            "--dataset",
            str(dataset_path),
            "--archive-root",
            str(archive),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["event_id"] == "930955:2026-01-01:2026-02-01"
