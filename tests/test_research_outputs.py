import json
from pathlib import Path

from src.research.event_recovery_audit import build_event_recovery_audit, main as event_main
from src.research.research_status_update import build_research_status_update, main as status_main


def test_build_event_recovery_audit_combines_false_repair_and_sample_pollution():
    false_repair = [
        {
            "event_id": "930955:a",
            "index": "930955",
            "end_method": "rebound_stability",
            "20d_failed": False,
            "60d_failed": True,
            "120d_failed": True,
            "post_event_max_drawdown": -0.12,
        }
    ]
    sample_pollution = [
        {
            "index": "930955",
            "severity": "major",
            "event_count": 10,
            "median_recovery_days": 20,
            "high_overlap_ratio": 0.4,
            "status": "存在明显样本污染风险",
        }
    ]

    payload = build_event_recovery_audit(false_repair, sample_pollution)

    assert payload["执行结果"] == "已完成"
    assert payload["数据证据"]["假修复检查汇总"]["60日内失效事件数"] == 1
    assert payload["数据证据"]["事件污染检查汇总"]["风险分组数"] == 1
    assert payload["是否通过"] == "未完全通过"


def test_build_research_status_update_contains_required_sections():
    event_recovery_audit = {
        "执行结果": "已完成",
        "数据证据": {
            "假修复检查汇总": {
                "总事件数": 10,
                "120日内失效事件数": 4,
            },
            "事件污染检查汇总": {
                "风险分组数": 1,
            },
        },
        "风险": ["rebound_stability 仍有失效事件"],
        "是否通过": "未完全通过",
    }
    walk_forward = {
        "walk_forward_similarity_test": {
            "summaries": [
                {
                    "index": "930955",
                    "sample_count": 12,
                    "information_increment_21d": -0.02,
                    "information_increment_63d": -0.03,
                    "information_increment_126d": -0.01,
                    "information_increment_252d": 0.01,
                }
            ]
        }
    }
    cluster_report = {
        "clusters": {
            "红利": {
                "candidate_indicator_validation": {
                    "feature": "dividend_yield_spread",
                    "groups": [
                        {
                            "group": "high",
                            "samples": 3,
                            "判断": "无法判断",
                        }
                    ],
                }
            }
        }
    }

    content = build_research_status_update(
        event_recovery_audit=event_recovery_audit,
        walk_forward_payload=walk_forward,
        cluster_report=cluster_report,
    )

    assert "## 已完成" in content
    assert "## 未通过验证" in content
    assert "## 新发现风险" in content
    assert "## 下一阶段建议" in content
    assert "执行结果" in content
    assert "数据证据" in content
    assert "风险" in content
    assert "是否通过" in content


def test_output_mains_write_required_files(tmp_path):
    false_repair_path = tmp_path / "rebound_stability_audit.json"
    false_repair_path.write_text(
        json.dumps(
            [
                {
                    "event_id": "930955:a",
                    "index": "930955",
                    "end_method": "rebound_stability",
                    "20d_failed": False,
                    "60d_failed": True,
                    "120d_failed": True,
                    "post_event_max_drawdown": -0.12,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    walk_forward_path = tmp_path / "walk_forward_similarity_test.json"
    walk_forward_path.write_text(
        json.dumps(
            {
                "sample_pollution_audit": [
                    {
                        "index": "930955",
                        "severity": "major",
                        "event_count": 10,
                        "median_recovery_days": 20,
                        "high_overlap_ratio": 0.4,
                        "status": "存在明显样本污染风险",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    event_output = tmp_path / "event_recovery_audit.json"

    code = event_main(
        [
            "--false-repair",
            str(false_repair_path),
            "--walk-forward",
            str(walk_forward_path),
            "--output",
            str(event_output),
        ]
    )

    assert code == 0
    event_payload = json.loads(event_output.read_text(encoding="utf-8"))
    assert event_payload["执行结果"] == "已完成"

    cluster_report_path = tmp_path / "cluster_validation_report.json"
    cluster_report_path.write_text(
        json.dumps(
            {
                "clusters": {
                    "红利": {
                        "candidate_indicator_validation": {
                            "feature": "dividend_yield_spread",
                            "groups": [{"group": "high", "samples": 3, "判断": "无法判断"}],
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    status_output = tmp_path / "research_status_update.md"
    code = status_main(
        [
            "--event-recovery-audit",
            str(event_output),
            "--walk-forward",
            str(walk_forward_path),
            "--cluster-report",
            str(cluster_report_path),
            "--output",
            str(status_output),
        ]
    )

    assert code == 0
    content = status_output.read_text(encoding="utf-8")
    assert "## 已完成" in content
