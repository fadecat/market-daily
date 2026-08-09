from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any


DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "value_growth_drawdown_events.json"
)
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "research"
    / "cluster_validation_report.json"
)

CLUSTERS = {
    "红利": ["930955"],
    "价值": ["931052", "980081"],
    "成长": ["399326"],
}
MIN_DIVIDEND_GROUP_SAMPLES = 5


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return round(median(usable), 6) if usable else None


def _earnings_yield_spread(context: dict[str, Any]) -> float | None:
    pe = _safe_float(context.get("pe_ttm"))
    bond = _safe_float(context.get("bond_10y"))
    if pe is None or bond is None or pe <= 0:
        return None
    return 100.0 / pe - bond


def _cluster_events(dataset: dict[str, Any], member_codes: list[str]) -> list[dict[str, Any]]:
    return [
        event
        for event in dataset.get("events", [])
        if str(event.get("index_code")) in member_codes
    ]


def _common_event_profile(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "最大回撤中位数": _median([_safe_float(event.get("max_drawdown")) for event in events]),
        "回撤耗时中位数": _median([_safe_float(event.get("drawdown_days")) for event in events]),
        "修复耗时中位数": _median([_safe_float(event.get("recovery_days")) for event in events]),
    }


def _dividend_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    trough_contexts = [event.get("trough_context") or {} for event in events]
    peak_contexts = [event.get("peak_context") or {} for event in events]
    spreads = []
    spread_changes = []
    earnings_spreads = []
    for peak_context, trough_context in zip(peak_contexts, trough_contexts):
        peak_dividend = _safe_float(peak_context.get("dividend_yield"))
        trough_dividend = _safe_float(trough_context.get("dividend_yield"))
        peak_bond = _safe_float(peak_context.get("bond_10y"))
        trough_bond = _safe_float(trough_context.get("bond_10y"))
        if trough_dividend is not None and trough_bond is not None:
            spreads.append(trough_dividend - trough_bond)
        if (
            peak_dividend is not None
            and peak_bond is not None
            and trough_dividend is not None
            and trough_bond is not None
        ):
            spread_changes.append(
                (trough_dividend - trough_bond) - (peak_dividend - peak_bond)
            )
        earnings_spreads.append(_earnings_yield_spread(trough_context))
    return {
        "股息率中位数": _median(
            [_safe_float(context.get("dividend_yield")) for context in trough_contexts]
        ),
        "股息率减10年国债中位数": _median(spreads),
        "盈利收益率股债差中位数": _median(earnings_spreads),
        "股息率减10年国债改善中位数": _median(spread_changes),
    }


def _value_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    peak_contexts = [event.get("peak_context") or {} for event in events]
    trough_contexts = [event.get("trough_context") or {} for event in events]
    earnings_spreads = [_earnings_yield_spread(context) for context in trough_contexts]
    earnings_spread_changes = []
    bond_changes = []
    for peak_context, trough_context in zip(peak_contexts, trough_contexts):
        peak_spread = _earnings_yield_spread(peak_context)
        trough_spread = _earnings_yield_spread(trough_context)
        peak_bond = _safe_float(peak_context.get("bond_10y"))
        trough_bond = _safe_float(trough_context.get("bond_10y"))
        if peak_spread is not None and trough_spread is not None:
            earnings_spread_changes.append(trough_spread - peak_spread)
        if peak_bond is not None and trough_bond is not None:
            bond_changes.append(trough_bond - peak_bond)
    return {
        "PE分位中位数": _median(
            [_safe_float(context.get("pe_percentile_10y")) for context in trough_contexts]
        ),
        "PB分位中位数": _median(
            [_safe_float(context.get("pb_percentile_10y")) for context in trough_contexts]
        ),
        "盈利收益率股债差中位数": _median(earnings_spreads),
        "盈利收益率股债差改善中位数": _median(earnings_spread_changes),
        "利率环境变化中位数": _median(bond_changes),
    }


def _growth_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    peak_contexts = [event.get("peak_context") or {} for event in events]
    trough_contexts = [event.get("trough_context") or {} for event in events]
    pe_compressions = []
    pb_compressions = []
    relative_hs300 = []
    relative_smallcap = []
    for event, peak_context, trough_context in zip(events, peak_contexts, trough_contexts):
        peak_pe = _safe_float(peak_context.get("pe_ttm"))
        trough_pe = _safe_float(trough_context.get("pe_ttm"))
        peak_pb = _safe_float(peak_context.get("pb_lf"))
        trough_pb = _safe_float(trough_context.get("pb_lf"))
        if peak_pe is not None and peak_pe > 0 and trough_pe is not None:
            pe_compressions.append(trough_pe / peak_pe - 1.0)
        if peak_pb is not None and peak_pb > 0 and trough_pb is not None:
            pb_compressions.append(trough_pb / peak_pb - 1.0)
        relative = event.get("relative_returns_peak_to_trough") or {}
        relative_hs300.append(_safe_float(relative.get("000300")))
        relative_smallcap.append(_safe_float(relative.get("399303")))
    return {
        "PE压缩幅度中位数": _median(pe_compressions),
        "PB压缩幅度中位数": _median(pb_compressions),
        "相对沪深300超额中位数": _median(relative_hs300),
        "相对国证2000超额中位数": _median(relative_smallcap),
    }


def _dividend_yield_spread_group_rows(
    events: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], float]], float | None]:
    rows: list[tuple[dict[str, Any], float]] = []
    for event in events:
        trough_context = event.get("trough_context") or {}
        dividend_yield = _safe_float(trough_context.get("dividend_yield"))
        bond_10y = _safe_float(trough_context.get("bond_10y"))
        if dividend_yield is None or bond_10y is None:
            continue
        rows.append((event, dividend_yield - bond_10y))
    if not rows:
        return [], None
    threshold = median([spread for _, spread in rows])
    return rows, threshold


def _full_peak_recovery_rate(events: list[dict[str, Any]]) -> float | None:
    if not events:
        return None
    values = [1.0 if event.get("full_peak_recovered") else 0.0 for event in events]
    return round(sum(values) / len(values), 6)


def _group_validation_summary(
    group_name: str,
    rows: list[tuple[dict[str, Any], float]],
) -> dict[str, Any]:
    events = [event for event, _ in rows]
    return {
        "feature": "dividend_yield_spread",
        "group": group_name,
        "samples": len(events),
        "median_21d_return": _median(
            [_safe_float((event.get("forward_returns") or {}).get("21d")) for event in events]
        ),
        "median_63d_return": _median(
            [_safe_float((event.get("forward_returns") or {}).get("63d")) for event in events]
        ),
        "median_126d_return": _median(
            [_safe_float((event.get("forward_returns") or {}).get("126d")) for event in events]
        ),
        "median_252d_return": _median(
            [_safe_float((event.get("forward_returns") or {}).get("252d")) for event in events]
        ),
        "full_peak_recovery_rate": _full_peak_recovery_rate(events),
        "判断": "无法判断" if len(events) < MIN_DIVIDEND_GROUP_SAMPLES else "可做描述性统计",
    }


def _dividend_candidate_validation(events: list[dict[str, Any]]) -> dict[str, Any]:
    rows, threshold = _dividend_yield_spread_group_rows(events)
    if threshold is None:
        return {
            "feature": "dividend_yield_spread",
            "split_method": "trough_median_split",
            "sample_minimum": MIN_DIVIDEND_GROUP_SAMPLES,
            "threshold": None,
            "groups": [
                {
                    "feature": "dividend_yield_spread",
                    "group": "high",
                    "samples": 0,
                    "median_21d_return": None,
                    "median_63d_return": None,
                    "median_126d_return": None,
                    "median_252d_return": None,
                    "full_peak_recovery_rate": None,
                    "判断": "无法判断",
                },
                {
                    "feature": "dividend_yield_spread",
                    "group": "low",
                    "samples": 0,
                    "median_21d_return": None,
                    "median_63d_return": None,
                    "median_126d_return": None,
                    "median_252d_return": None,
                    "full_peak_recovery_rate": None,
                    "判断": "无法判断",
                },
            ],
        }

    high_rows = [(event, spread) for event, spread in rows if spread >= threshold]
    low_rows = [(event, spread) for event, spread in rows if spread < threshold]
    return {
        "feature": "dividend_yield_spread",
        "split_method": "trough_median_split",
        "sample_minimum": MIN_DIVIDEND_GROUP_SAMPLES,
        "threshold": round(float(threshold), 6),
        "groups": [
            _group_validation_summary("high", high_rows),
            _group_validation_summary("low", low_rows),
        ],
    }


def _dividend_research_levels(
    events: list[dict[str, Any]],
    candidate_validation: dict[str, Any],
) -> dict[str, list[str]]:
    groups = {item["group"]: item for item in candidate_validation.get("groups", [])}
    high = groups.get("high", {})
    low = groups.get("low", {})
    facts = [
        f"红利簇样本事件数为 {len(events)}。",
        f"股息率差分组采用事件最低点时点的中位数切分，阈值为 {candidate_validation.get('threshold')}。",
    ]
    stats = [
        f"高股息率差组样本数 {high.get('samples')}，126日收益中位数 {high.get('median_126d_return')}，收复前高概率 {high.get('full_peak_recovery_rate')}。",
        f"低股息率差组样本数 {low.get('samples')}，126日收益中位数 {low.get('median_126d_return')}，收复前高概率 {low.get('full_peak_recovery_rate')}。",
    ]
    interpretations: list[str] = []
    if (
        high.get("判断") != "无法判断"
        and low.get("判断") != "无法判断"
        and high.get("median_126d_return") is not None
        and low.get("median_126d_return") is not None
    ):
        if high["median_126d_return"] > low["median_126d_return"]:
            interpretations.append("高股息率差组历史收益中位数更高，可能反映红利资产在利差更宽时修复弹性更强。")
        elif high["median_126d_return"] < low["median_126d_return"]:
            interpretations.append("高股息率差组历史收益中位数未高于低组，候选指标暂未显示稳定优势。")
        else:
            interpretations.append("高低组历史收益中位数接近，候选指标未显示明显区分度。")
    else:
        interpretations.append("当前分组样本不足，股息率差仍只能作为候选变量观察，不能上升为已验证机制。")
    unprovable = [
        "无法证明股息率差本身导致后续修复，仅能说明历史分组统计差异。",
        "无法把指数修复直接等同为真实投资组合修复。",
    ]
    return {
        "事实": facts,
        "统计结果": stats,
        "投资解释": interpretations,
        "无法证明": unprovable,
    }


def _generic_research_levels(cluster_name: str, events: list[dict[str, Any]]) -> dict[str, list[str]]:
    facts = [f"{cluster_name}簇样本事件数为 {len(events)}。"]
    stats = [f"{cluster_name}簇机制指标来自历史事件最低点或回撤期的直接统计。"]
    interpretations = [f"{cluster_name}簇当前只做机制画像，不输出交易信号。"]
    unprovable = [
        "无法仅凭历史分位或压缩幅度证明未来一定修复。",
        "无法证明指数层统计可以直接迁移到真实持仓组合。",
    ]
    return {
        "事实": facts,
        "统计结果": stats,
        "投资解释": interpretations,
        "无法证明": unprovable,
    }


def build_cluster_validation_report(dataset: dict[str, Any]) -> dict[str, Any]:
    clusters: dict[str, Any] = {}
    for cluster_name, member_codes in CLUSTERS.items():
        events = _cluster_events(dataset, member_codes)
        if cluster_name == "红利":
            mechanism_metrics = _dividend_metrics(events)
            candidate_indicator_validation = _dividend_candidate_validation(events)
            research_levels = _dividend_research_levels(events, candidate_indicator_validation)
        elif cluster_name == "价值":
            mechanism_metrics = _value_metrics(events)
            candidate_indicator_validation = None
            research_levels = _generic_research_levels(cluster_name, events)
        else:
            mechanism_metrics = _growth_metrics(events)
            candidate_indicator_validation = None
            research_levels = _generic_research_levels(cluster_name, events)
        clusters[cluster_name] = {
            "member_index_codes": member_codes,
            "event_count": len(events),
            "event_profile": _common_event_profile(events),
            "mechanism_metrics": mechanism_metrics,
            "candidate_indicator_validation": candidate_indicator_validation,
            "research_levels": research_levels,
        }
    return {
        "clusters": clusters,
        "note": "按红利、价值、成长分簇分别统计，不再使用统一机制口径。",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    payload = build_cluster_validation_report(dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"写入 cluster validation report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
