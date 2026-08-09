from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_EVENT_RECOVERY_AUDIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "event_recovery_audit.json"
)
DEFAULT_WALK_FORWARD_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "walk_forward_similarity_test.json"
)
DEFAULT_CLUSTER_REPORT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "cluster_validation_report.json"
)
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "research_status_update.md"
)


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value) if value else "- 无"
    if isinstance(value, dict):
        return "\n".join(f"- {key}: {subvalue}" for key, subvalue in value.items()) if value else "- 无"
    return str(value)


def _summary_line(payload: dict[str, Any]) -> str:
    return (
        f"执行结果：{payload.get('执行结果', '未提供')}\n\n"
        f"数据证据：\n{_format_value(payload.get('数据证据', '未提供'))}\n\n"
        f"风险：\n{_format_value(payload.get('风险', '未提供'))}\n\n"
        f"是否通过：{payload.get('是否通过', '未提供')}"
    )


def build_research_status_update(
    *,
    event_recovery_audit: dict[str, Any],
    walk_forward_payload: dict[str, Any],
    cluster_report: dict[str, Any],
) -> str:
    walk_summaries = walk_forward_payload.get("walk_forward_similarity_test", {}).get(
        "summaries",
        [],
    )
    weak_indices = [
        summary["index"]
        for summary in walk_summaries
        if any(
            (summary.get(f"information_increment_{horizon}d") or 0) < 0
            for horizon in (21, 63, 126, 252)
        )
    ]
    dividend_groups = (
        cluster_report.get("clusters", {})
        .get("红利", {})
        .get("candidate_indicator_validation", {})
        .get("groups", [])
    )
    unable_groups = [
        group["group"] for group in dividend_groups if group.get("判断") == "无法判断"
    ]
    content = [
        "# 研究状态更新",
        "",
        "## 已完成",
        "",
        _summary_line(
            {
                "执行结果": "已完成",
                "数据证据": [
                    "事件可靠性审计已生成 event_recovery_audit.json。",
                    "walk-forward 相似事件验证已生成 walk_forward_similarity_test.json。",
                    "资产机制拆分与红利候选指标验证已生成 cluster_validation_report.json。",
                ],
                "风险": ["当前阶段未新增流程阻断。"],
                "是否通过": "已完成阶段性交付",
            }
        ),
        "",
        "## 未通过验证",
        "",
        _summary_line(
            {
                "执行结果": "已审阅",
                "数据证据": [
                    f"walk-forward 中未稳定跑赢随机基线的指数包括：{', '.join(weak_indices) if weak_indices else '无'}。",
                    f"红利股息率差分组中当前无法判断的分组：{', '.join(unable_groups) if unable_groups else '无'}。",
                ],
                "风险": [
                    "当前不能把相似事件匹配直接视为稳定有效的预测框架。",
                    "当前不能把股息率差直接上升为已验证机制。",
                ],
                "是否通过": "未通过",
            }
        ),
        "",
        "## 新发现风险",
        "",
        _summary_line(
            {
                "执行结果": event_recovery_audit.get("执行结果"),
                "数据证据": {
                    "假修复检查汇总": event_recovery_audit.get("数据证据", {}).get("假修复检查汇总"),
                    "事件污染检查汇总": event_recovery_audit.get("数据证据", {}).get("事件污染检查汇总"),
                },
                "风险": event_recovery_audit.get("风险"),
                "是否通过": event_recovery_audit.get("是否通过"),
            }
        ),
        "",
        "## 下一阶段建议",
        "",
        _summary_line(
            {
                "执行结果": "待执行",
                "数据证据": [
                    "优先补红利簇更长历史或更多可比红利指数，提升股息率差分组样本量。",
                    "把价值簇与成长簇分别做同口径候选指标扩展验证，不再回到统一模型。",
                    "继续保留研究分级：事实、统计结果、投资解释、无法证明。",
                ],
                "风险": [
                    "若直接把当前候选指标写入打分公式，会把描述性统计误当成确认信号。",
                ],
                "是否通过": "建议进入下一轮研究，不建议直接进入信号化",
            }
        ),
        "",
    ]
    return "\n".join(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-recovery-audit",
        type=Path,
        default=DEFAULT_EVENT_RECOVERY_AUDIT_PATH,
    )
    parser.add_argument("--walk-forward", type=Path, default=DEFAULT_WALK_FORWARD_PATH)
    parser.add_argument("--cluster-report", type=Path, default=DEFAULT_CLUSTER_REPORT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    event_recovery_audit = json.loads(args.event_recovery_audit.read_text(encoding="utf-8"))
    walk_forward_payload = json.loads(args.walk_forward.read_text(encoding="utf-8"))
    cluster_report = json.loads(args.cluster_report.read_text(encoding="utf-8"))
    content = build_research_status_update(
        event_recovery_audit=event_recovery_audit,
        walk_forward_payload=walk_forward_payload,
        cluster_report=cluster_report,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"写入 research status update: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
