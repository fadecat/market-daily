"""高股息二次筛选:国资白名单 + 行业排除 + TTM 归母净利润三步。

移植自 jisilu_ggx/main.py L176-233 + L551-681:白名单/行业为纯内存过滤,TTM 走巨潮
缓存(``cninfo_cache.fetch_cached_or_live_ttm_parent_net_profit``)。secondary_rules 先
白名单+行业(无网)再 TTM,白名单外或行业已排除的标的不触发巨潮抓取,省请求降限流。
``normalize_stock_code`` / 白名单加载走 ``common.whitelist``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...common import env
from ...common.whitelist import (
    STATE_OWNED_WHITELIST_XLSX,
    load_stock_code_whitelist_from_xlsx,
    normalize_stock_code,
)
from .cninfo_cache import fetch_cached_or_live_ttm_parent_net_profit


TTM_PARENT_NET_PROFIT_MIN_YI = float(env.get("TTM_PARENT_NET_PROFIT_MIN_YI", "10") or "10")
EXCLUDED_DIVIDEND_INDUSTRIES = frozenset({"基建市政工程"})


def industry_name_of(cell: dict) -> str:
    return str(cell.get("industry_nm") or "未分类").strip() or "未分类"


def ensure_dividend_report_meta(data: dict) -> dict:
    """补齐高股息日报元数据,保留集思录原始返回数量与筛选步骤。"""
    return {
        **data,
        "raw_returned_count": data.get("raw_returned_count", len(data.get("rows", []))),
        "filter_steps": list(data.get("filter_steps") or []),
    }


def format_filter_exclusion(cell: dict, reason: str) -> dict:
    return {
        "stock_id": normalize_stock_code(cell.get("stock_id")),
        "stock_nm": str(cell.get("stock_nm", "")).strip(),
        "reason": str(reason),
    }


def print_filter_exclusion(cell: dict, reason: str) -> dict:
    detail = format_filter_exclusion(cell, reason)
    print(f"[过滤排除] {detail['stock_id']} {detail['stock_nm']}: {detail['reason']}")
    return detail


def append_filter_step(
    data: dict, step_name: str, rule_text: str, before_count: int, after_count: int,
    excluded_rows: list, **extra,
) -> dict:
    step = {
        "step_name": step_name,
        "rule_text": rule_text,
        "before_count": before_count,
        "after_count": after_count,
        "excluded_count": len(excluded_rows),
        "excluded_rows": list(excluded_rows),
    }
    step.update(extra)
    summary = (
        f"[过滤汇总] {step_name}: {rule_text};输入 {before_count} 只,"
        f"剔除 {step['excluded_count']} 只,剩余 {after_count} 只"
    )
    if step.get("fetch_failed_count"):
        summary += f",其中财报抓取失败 {step['fetch_failed_count']} 只"
    print(summary)

    meta = ensure_dividend_report_meta(data)
    return {**meta, "filter_steps": [*meta["filter_steps"], step]}


def build_filter_summary_lines(data: dict) -> list[str]:
    meta = ensure_dividend_report_meta(data)
    lines = [
        f"> 集思录返回 {meta['raw_returned_count']} 只(非会员通常仅显示前 100 个)",
    ]
    for step in meta.get("filter_steps", []):
        line = (
            f"> {step['step_name']}:{step['rule_text']};"
            f"剔除 {step['excluded_count']} 只,剩余 {step['after_count']} 只"
        )
        if step.get("fetch_failed_count"):
            line += f"(其中财报抓取失败 {step['fetch_failed_count']} 只)"
        lines.append(line)
    return lines


def filter_dividend_rows_by_ttm_net_profit(
    data: dict,
    min_ttm_net_profit_yi: float = TTM_PARENT_NET_PROFIT_MIN_YI,
    fetcher=fetch_cached_or_live_ttm_parent_net_profit,
) -> dict:
    data = ensure_dividend_report_meta(data)
    before_count = len(data.get("rows", []))
    filtered_rows = []
    fetch_failed = []
    excluded_rows = []
    for row in data.get("rows", []):
        cell = row["cell"]
        try:
            ttm_info = fetcher(cell["stock_id"])
        except Exception as e:  # noqa: BLE001
            # 单只股票财报抓取失败(重试耗尽后)不应中断整个日报,排除该标的并继续。
            reason = str(e)
            exclusion = print_filter_exclusion(cell, f"财报数据获取失败: {reason}")
            excluded_rows.append(exclusion)
            fetch_failed.append({**exclusion, "reason": reason})
            continue
        if ttm_info["ttm_value_yi"] < min_ttm_net_profit_yi:
            excluded_rows.append(print_filter_exclusion(
                cell,
                f"TTM归母净利润 {ttm_info['ttm_value_yi']:.2f} 亿 < {min_ttm_net_profit_yi:g} 亿",
            ))
            continue

        filtered_cell = dict(cell)
        filtered_cell["ttm_parent_net_profit_yi"] = ttm_info["ttm_value_yi"]
        filtered_cell["ttm_parent_net_profit_latest_period"] = ttm_info.get("latest_period")
        filtered_cell["ttm_parent_net_profit_basis"] = ttm_info.get("basis")
        filtered_rows.append({**row, "cell": filtered_cell})

    result = {**data, "rows": filtered_rows, "ttm_fetch_failed": fetch_failed}
    return append_filter_step(
        result,
        step_name="TTM归母净利润",
        rule_text=f"TTM归母净利润 >= {min_ttm_net_profit_yi:g} 亿",
        before_count=before_count,
        after_count=len(filtered_rows),
        excluded_rows=excluded_rows,
        fetch_failed_count=len(fetch_failed),
    )


def filter_dividend_rows_by_stock_code_whitelist(
    data: dict,
    stock_code_whitelist: frozenset | None = None,
    whitelist_path: str = STATE_OWNED_WHITELIST_XLSX,
) -> dict:
    data = ensure_dividend_report_meta(data)
    stock_codes = stock_code_whitelist
    if stock_codes is None:
        stock_codes = load_stock_code_whitelist_from_xlsx(whitelist_path)

    before_count = len(data.get("rows", []))
    filtered_rows = []
    excluded_rows = []
    for row in data.get("rows", []):
        cell = row["cell"]
        stock_code = normalize_stock_code(cell.get("stock_id"))
        if stock_code not in stock_codes:
            excluded_rows.append(print_filter_exclusion(cell, "不在国资白名单"))
            continue
        filtered_cell = dict(cell)
        filtered_cell["stock_id"] = stock_code
        filtered_rows.append({**row, "cell": filtered_cell})
    result = {**data, "rows": filtered_rows}
    return append_filter_step(
        result,
        step_name="国资白名单",
        rule_text=f"仅保留《{Path(whitelist_path).name}》名单内标的",
        before_count=before_count,
        after_count=len(filtered_rows),
        excluded_rows=excluded_rows,
    )


def filter_dividend_rows_by_excluded_industries(
    data: dict,
    excluded_industries: frozenset = EXCLUDED_DIVIDEND_INDUSTRIES,
) -> dict:
    data = ensure_dividend_report_meta(data)
    excluded_set = frozenset(str(industry).strip() for industry in excluded_industries if str(industry).strip())
    before_count = len(data.get("rows", []))
    filtered_rows = []
    excluded_rows = []
    for row in data.get("rows", []):
        cell = row["cell"]
        industry_name = industry_name_of(cell)
        if industry_name in excluded_set:
            excluded_rows.append(
                print_filter_exclusion(cell, f"行业命中排除名单: {industry_name}")
            )
            continue
        filtered_rows.append(row)

    result = {**data, "rows": filtered_rows}
    return append_filter_step(
        result,
        step_name="行业排除",
        rule_text=f"剔除指定行业({'、'.join(sorted(excluded_set))})",
        before_count=before_count,
        after_count=len(filtered_rows),
        excluded_rows=excluded_rows,
    )


def filter_dividend_rows_by_secondary_rules(
    data: dict,
    min_ttm_net_profit_yi: float = TTM_PARENT_NET_PROFIT_MIN_YI,
    fetcher=fetch_cached_or_live_ttm_parent_net_profit,
    stock_code_whitelist: frozenset | None = None,
    excluded_industries: frozenset = EXCLUDED_DIVIDEND_INDUSTRIES,
    whitelist_path: str = STATE_OWNED_WHITELIST_XLSX,
) -> dict:
    """先白名单+行业(纯内存、无网络),再对候选标的抓财报做 TTM 筛选。

    白名单外或行业已排除的标的不触发巨潮抓取,既省请求、降低限流概率,
    也让 ttm_fetch_failed 只记录「本会进日报」的最终候选标的。
    """
    data = filter_dividend_rows_by_stock_code_whitelist(
        data,
        stock_code_whitelist=stock_code_whitelist,
        whitelist_path=whitelist_path,
    )
    data = filter_dividend_rows_by_excluded_industries(
        data,
        excluded_industries=excluded_industries,
    )
    return filter_dividend_rows_by_ttm_net_profit(
        data,
        min_ttm_net_profit_yi=min_ttm_net_profit_yi,
        fetcher=fetcher,
    )
