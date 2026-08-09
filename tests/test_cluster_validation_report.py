import json
from pathlib import Path

from src.research.cluster_validation_report import (
    build_cluster_validation_report,
    main,
)


def _event(
    event_id: str,
    *,
    index_code: str,
    max_drawdown: float,
    drawdown_days: int,
    recovery_days: int | None,
    peak_pe: float,
    trough_pe: float,
    peak_pb: float,
    trough_pb: float,
    peak_dividend: float,
    trough_dividend: float,
    peak_bond: float,
    trough_bond: float,
    relative_hs300: float,
    relative_smallcap: float,
    forward_21d: float | None = None,
    forward_63d: float | None = None,
    forward_126d: float | None = None,
    forward_252d: float | None = None,
    full_peak_recovered: bool = False,
):
    return {
        "event_id": event_id,
        "index_code": index_code,
        "max_drawdown": max_drawdown,
        "drawdown_days": drawdown_days,
        "recovery_days": recovery_days,
        "peak_context": {
            "pe_ttm": peak_pe,
            "pb_lf": peak_pb,
            "dividend_yield": peak_dividend,
            "bond_10y": peak_bond,
            "pe_percentile_10y": 80.0,
            "pb_percentile_10y": 70.0,
        },
        "trough_context": {
            "pe_ttm": trough_pe,
            "pb_lf": trough_pb,
            "dividend_yield": trough_dividend,
            "bond_10y": trough_bond,
            "pe_percentile_10y": 30.0,
            "pb_percentile_10y": 20.0,
        },
        "relative_returns_peak_to_trough": {
            "000300": relative_hs300,
            "399303": relative_smallcap,
        },
        "forward_returns": {
            "21d": forward_21d,
            "63d": forward_63d,
            "126d": forward_126d,
            "252d": forward_252d,
        },
        "full_peak_recovered": full_peak_recovered,
    }


def test_build_cluster_validation_report_splits_clusters_and_uses_cluster_specific_metrics():
    dataset = {
        "events": [
            _event(
                "930955:a",
                index_code="930955",
                max_drawdown=-0.12,
                drawdown_days=15,
                recovery_days=20,
                peak_pe=10.0,
                trough_pe=8.0,
                peak_pb=1.2,
                trough_pb=1.0,
                peak_dividend=4.0,
                trough_dividend=5.0,
                peak_bond=2.0,
                trough_bond=1.5,
                relative_hs300=-0.03,
                relative_smallcap=0.02,
            ),
            _event(
                "931052:a",
                index_code="931052",
                max_drawdown=-0.10,
                drawdown_days=18,
                recovery_days=25,
                peak_pe=12.0,
                trough_pe=9.0,
                peak_pb=1.5,
                trough_pb=1.1,
                peak_dividend=3.0,
                trough_dividend=3.4,
                peak_bond=2.1,
                trough_bond=1.8,
                relative_hs300=-0.01,
                relative_smallcap=-0.02,
            ),
            _event(
                "980081:a",
                index_code="980081",
                max_drawdown=-0.14,
                drawdown_days=22,
                recovery_days=30,
                peak_pe=11.0,
                trough_pe=8.8,
                peak_pb=1.4,
                trough_pb=1.0,
                peak_dividend=3.1,
                trough_dividend=3.5,
                peak_bond=2.2,
                trough_bond=1.9,
                relative_hs300=-0.02,
                relative_smallcap=-0.03,
            ),
            _event(
                "399326:a",
                index_code="399326",
                max_drawdown=-0.25,
                drawdown_days=28,
                recovery_days=40,
                peak_pe=40.0,
                trough_pe=28.0,
                peak_pb=5.0,
                trough_pb=3.8,
                peak_dividend=0.8,
                trough_dividend=0.9,
                peak_bond=2.0,
                trough_bond=1.7,
                relative_hs300=-0.08,
                relative_smallcap=-0.04,
            ),
            _event(
                "000300:ignored",
                index_code="000300",
                max_drawdown=-0.08,
                drawdown_days=10,
                recovery_days=15,
                peak_pe=14.0,
                trough_pe=12.0,
                peak_pb=1.6,
                trough_pb=1.4,
                peak_dividend=2.5,
                trough_dividend=2.8,
                peak_bond=2.0,
                trough_bond=1.9,
                relative_hs300=0.0,
                relative_smallcap=0.0,
            ),
        ]
    }

    report = build_cluster_validation_report(dataset)

    assert set(report["clusters"]) == {"红利", "价值", "成长"}
    assert report["clusters"]["红利"]["member_index_codes"] == ["930955"]
    assert report["clusters"]["价值"]["member_index_codes"] == ["931052", "980081"]
    assert report["clusters"]["成长"]["member_index_codes"] == ["399326"]
    assert report["clusters"]["红利"]["event_count"] == 1
    assert report["clusters"]["价值"]["event_count"] == 2
    assert report["clusters"]["成长"]["event_count"] == 1
    assert report["clusters"]["红利"]["mechanism_metrics"]["股息率中位数"] == 5.0
    assert report["clusters"]["红利"]["mechanism_metrics"]["股息率减10年国债中位数"] == 3.5
    assert report["clusters"]["红利"]["mechanism_metrics"]["盈利收益率股债差中位数"] == 11.0
    assert report["clusters"]["价值"]["mechanism_metrics"]["PE分位中位数"] == 30.0
    assert report["clusters"]["价值"]["mechanism_metrics"]["PB分位中位数"] == 20.0
    assert report["clusters"]["成长"]["mechanism_metrics"]["PE压缩幅度中位数"] == -0.3
    assert report["clusters"]["成长"]["mechanism_metrics"]["PB压缩幅度中位数"] == -0.24
    assert report["clusters"]["成长"]["mechanism_metrics"]["相对沪深300超额中位数"] == -0.08


def test_cluster_validation_report_validates_dividend_yield_spread_by_high_low_groups():
    dataset = {
        "events": [
            _event(
                "930955:1",
                index_code="930955",
                max_drawdown=-0.10,
                drawdown_days=10,
                recovery_days=15,
                peak_pe=10.0,
                trough_pe=8.0,
                peak_pb=1.2,
                trough_pb=1.0,
                peak_dividend=4.0,
                trough_dividend=6.0,
                peak_bond=2.0,
                trough_bond=1.0,
                relative_hs300=-0.02,
                relative_smallcap=0.0,
                forward_21d=0.06,
                forward_63d=0.10,
                forward_126d=0.18,
                forward_252d=0.22,
                full_peak_recovered=True,
            ),
            _event(
                "930955:2",
                index_code="930955",
                max_drawdown=-0.11,
                drawdown_days=11,
                recovery_days=16,
                peak_pe=10.0,
                trough_pe=8.0,
                peak_pb=1.2,
                trough_pb=1.0,
                peak_dividend=4.1,
                trough_dividend=5.8,
                peak_bond=2.0,
                trough_bond=1.1,
                relative_hs300=-0.02,
                relative_smallcap=0.0,
                forward_21d=0.05,
                forward_63d=0.09,
                forward_126d=0.17,
                forward_252d=0.20,
                full_peak_recovered=True,
            ),
            _event(
                "930955:3",
                index_code="930955",
                max_drawdown=-0.09,
                drawdown_days=9,
                recovery_days=14,
                peak_pe=10.0,
                trough_pe=8.0,
                peak_pb=1.2,
                trough_pb=1.0,
                peak_dividend=4.0,
                trough_dividend=5.6,
                peak_bond=2.0,
                trough_bond=1.2,
                relative_hs300=-0.02,
                relative_smallcap=0.0,
                forward_21d=0.04,
                forward_63d=0.08,
                forward_126d=0.16,
                forward_252d=0.18,
                full_peak_recovered=True,
            ),
            _event(
                "930955:4",
                index_code="930955",
                max_drawdown=-0.08,
                drawdown_days=8,
                recovery_days=13,
                peak_pe=10.0,
                trough_pe=8.0,
                peak_pb=1.2,
                trough_pb=1.0,
                peak_dividend=4.0,
                trough_dividend=4.5,
                peak_bond=2.0,
                trough_bond=1.5,
                relative_hs300=-0.02,
                relative_smallcap=0.0,
                forward_21d=0.00,
                forward_63d=0.02,
                forward_126d=0.05,
                forward_252d=0.08,
                full_peak_recovered=False,
            ),
            _event(
                "930955:5",
                index_code="930955",
                max_drawdown=-0.07,
                drawdown_days=7,
                recovery_days=12,
                peak_pe=10.0,
                trough_pe=8.0,
                peak_pb=1.2,
                trough_pb=1.0,
                peak_dividend=4.0,
                trough_dividend=4.3,
                peak_bond=2.0,
                trough_bond=1.6,
                relative_hs300=-0.02,
                relative_smallcap=0.0,
                forward_21d=-0.01,
                forward_63d=0.01,
                forward_126d=0.04,
                forward_252d=0.07,
                full_peak_recovered=False,
            ),
            _event(
                "930955:6",
                index_code="930955",
                max_drawdown=-0.06,
                drawdown_days=6,
                recovery_days=11,
                peak_pe=10.0,
                trough_pe=8.0,
                peak_pb=1.2,
                trough_pb=1.0,
                peak_dividend=4.0,
                trough_dividend=4.1,
                peak_bond=2.0,
                trough_bond=1.7,
                relative_hs300=-0.02,
                relative_smallcap=0.0,
                forward_21d=-0.02,
                forward_63d=0.00,
                forward_126d=0.03,
                forward_252d=0.06,
                full_peak_recovered=False,
            ),
        ]
    }

    report = build_cluster_validation_report(dataset)
    validation = report["clusters"]["红利"]["candidate_indicator_validation"]
    groups = {item["group"]: item for item in validation["groups"]}

    assert validation["feature"] == "dividend_yield_spread"
    assert groups["high"]["samples"] == 3
    assert groups["high"]["median_126d_return"] == 0.17
    assert groups["high"]["full_peak_recovery_rate"] == 1.0
    assert groups["high"]["判断"] == "无法判断"
    assert groups["low"]["samples"] == 3
    assert groups["low"]["median_126d_return"] == 0.04
    assert groups["low"]["full_peak_recovery_rate"] == 0.0
    assert report["clusters"]["红利"]["research_levels"]["事实"]
    assert report["clusters"]["红利"]["research_levels"]["统计结果"]
    assert report["clusters"]["红利"]["research_levels"]["无法证明"]


def test_main_writes_cluster_validation_report_json(tmp_path):
    dataset_path = tmp_path / "value_growth_drawdown_events.json"
    output = tmp_path / "cluster_validation_report.json"
    dataset_path.write_text(
        json.dumps(
            {
                "events": [
                    _event(
                        "930955:a",
                        index_code="930955",
                        max_drawdown=-0.12,
                        drawdown_days=15,
                        recovery_days=20,
                        peak_pe=10.0,
                        trough_pe=8.0,
                        peak_pb=1.2,
                        trough_pb=1.0,
                        peak_dividend=4.0,
                        trough_dividend=5.0,
                        peak_bond=2.0,
                        trough_bond=1.5,
                        relative_hs300=-0.03,
                        relative_smallcap=0.02,
                    )
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = main(
        [
            "--dataset",
            str(dataset_path),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "clusters" in payload
    assert "红利" in payload["clusters"]
    assert "candidate_indicator_validation" in payload["clusters"]["红利"]
