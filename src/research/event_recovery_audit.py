from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_FALSE_REPAIR_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "rebound_stability_audit.json"
)
DEFAULT_WALK_FORWARD_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "walk_forward_similarity_test.json"
)
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "event_recovery_audit.json"
)


def build_event_recovery_audit(
    false_repair_rows: list[dict[str, Any]],
    sample_pollution_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    failed_20 = sum(1 for row in false_repair_rows if row.get("20d_failed"))
    failed_60 = sum(1 for row in false_repair_rows if row.get("60d_failed"))
    failed_120 = sum(1 for row in false_repair_rows if row.get("120d_failed"))
    pollution_risks = [
        row for row in sample_pollution_rows if row.get("status") == "存在明显样本污染风险"
    ]
    risks: list[str] = []
    if failed_60 > 0:
        risks.append("rebound_stability 结束规则后 60 日内仍存在重新失效事件。")
    if pollution_risks:
        risks.append("部分指数分组存在样本污染风险，历史收益分布不能直接当作独立样本。")
    passed = failed_60 == 0 and failed_120 == 0 and not pollution_risks
    return {
        "执行结果": "已完成",
        "数据证据": {
            "假修复检查汇总": {
                "总事件数": len(false_repair_rows),
                "20日内失效事件数": failed_20,
                "60日内失效事件数": failed_60,
                "120日内失效事件数": failed_120,
            },
            "事件污染检查汇总": {
                "总分组数": len(sample_pollution_rows),
                "风险分组数": len(pollution_risks),
            },
            "假修复检查明细": false_repair_rows,
            "事件污染检查明细": sample_pollution_rows,
        },
        "风险": risks or ["当前两类审计未发现新增阻断性风险。"],
        "是否通过": "通过" if passed else "未完全通过",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--false-repair", type=Path, default=DEFAULT_FALSE_REPAIR_PATH)
    parser.add_argument("--walk-forward", type=Path, default=DEFAULT_WALK_FORWARD_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    false_repair_rows = json.loads(args.false_repair.read_text(encoding="utf-8"))
    walk_forward_payload = json.loads(args.walk_forward.read_text(encoding="utf-8"))
    sample_pollution_rows = walk_forward_payload.get("sample_pollution_audit", [])
    payload = build_event_recovery_audit(false_repair_rows, sample_pollution_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"写入 event recovery audit: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
