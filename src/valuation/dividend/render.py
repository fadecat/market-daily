"""高股息子模块渲染层:主表分组 + 行渲染 + 规则文案 + 邮件片段 + section 编排。

移植自 ``jisilu_ggx/main.py`` 的 L235-332 ``build_dividend_display_groups``、L355-368
``build_rule_msg``、L775-778 主表表头/列样式、L1319-1342 ``format_linked_bonds_html`` /
``_dividend_email_row``、L1369-1404 ``build_dividend_email_html``。

与旧版的差异:

- 退役企业微信路径:``format_stock`` / ``build_messages`` / ``get_msg_size`` /
  ``MAX_MSG_*`` / ``send_wechat`` / webhook 文案均不迁;告警改用 ``common.alerts.notify_alert``。
- ``build_rule_msg`` 删除 ``data=None`` 回退分支(邮件路径始终传 data,回退分支依赖未迁的
  ``STATE_OWNED_WHITELIST_LABEL``,属死代码)。
- 东财补充池渲染(``build_dividend_email_supplement_html``)与关联转债单元格
  (``format_linked_bonds_html_from_items``)已在 ``supplement.py`` 迁好,本模块直接复用,
  不重复迁移。
- ``build_section``:把原「独立高股息邮件」的 [补充池 + 规则 + 主表] 聚合为一个 ``<tr>``
  卡片行片段,供 ``valuation/run.py`` 拼入市场估值卡片(补充池在前、主表在后,保留旧顺序)。

排序键说明:组内按 (PB, PE, -股息率, 代码) 升序;组间按 (组均PB, 组均PE, -组均股息率,
组名) 升序。组均用各组前 2 名(leaders)均值,但展示全部行(不截断 top-2)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...common.alerts import notify_alert
from ...common.email import render_markdown, render_table
from ...common.jisilu import get_cookie
from ..fetch import now_in_beijing
from .fetch import fetch_data, prepare_dividend_email_data
from .filter import (
    build_filter_summary_lines,
    ensure_dividend_report_meta,
    filter_dividend_rows_by_secondary_rules,
    industry_name_of,
)
from .supplement import (
    build_dividend_email_supplement_html,
    format_linked_bonds_html_from_items,
    parse_float,
)

# 主表表头 / 列样式(补充池的 *_SUPPLEMENT_* 变体已在 supplement.py)
DIVIDEND_EMAIL_HEADERS = [
    "#", "行业", "名称", "代码", "价格", "股息率", "PE", "PB", "ROE", "TTM归母净利(亿)", "关联转债",
]
DIVIDEND_EMAIL_COLUMN_STYLES = {
    10: "white-space:normal;word-break:break-word;min-width:180px",
}

# 卡片行分隔色(与 valuation/render.py、style_rotation.py 同源,本模块自备私有副本以解耦)
_EMAIL_BORDER_CARD_SPLIT = "#f0f0f0"


def format_linked_bonds_html(cell: dict) -> str:
    """单行 cell 的关联转债 HTML(对 supplement.format_linked_bonds_html_from_items 的薄封装)。"""
    return format_linked_bonds_html_from_items(
        cell.get("linked_bonds") or [],
        linked_bonds_fetch_failed=cell.get("linked_bonds_fetch_failed", False),
    )


def build_dividend_display_groups(data: dict, max_show: Optional[int] = None) -> Dict[str, Any]:
    """按行业分组 + 组内排序(PB/PE/股息率)+ 组间排序(组均 PB/PE/股息率)。

    返回 ``{"total_count", "shown_count", "groups"}``。每个 group 含
    ``industry_name/industry_count/industry_avg_pb/industry_avg_pe/industry_avg_dividend_rate/rows``;
    每行含 ``cell/pe_rank/pb_rank/valuation_score/valuation_tiebreak/pe_value/pb_value/dividend_rate_value``。
    ``max_show`` 跨组截断展示行数;None 展示全部。
    """
    grouped_rows: Dict[str, list] = {}
    for row in data.get("rows", []):
        cell = dict(row["cell"])
        grouped_rows.setdefault(industry_name_of(cell), []).append({"cell": cell})

    groups = []
    for industry_name, rows in grouped_rows.items():
        pe_sorted = sorted(
            rows,
            key=lambda item: (
                parse_float(item["cell"].get("pe"), float("inf")),
                item["cell"].get("stock_id", ""),
            ),
        )
        pb_sorted = sorted(
            rows,
            key=lambda item: (
                parse_float(item["cell"].get("pb"), float("inf")),
                item["cell"].get("stock_id", ""),
            ),
        )
        pe_rank_map = {item["cell"]["stock_id"]: index for index, item in enumerate(pe_sorted, 1)}
        pb_rank_map = {item["cell"]["stock_id"]: index for index, item in enumerate(pb_sorted, 1)}

        ranked_rows = []
        for item in rows:
            cell = item["cell"]
            pe_rank = pe_rank_map[cell["stock_id"]]
            pb_rank = pb_rank_map[cell["stock_id"]]
            pe_value = parse_float(cell.get("pe"), float("inf"))
            pb_value = parse_float(cell.get("pb"), float("inf"))
            dividend_rate_value = parse_float(cell.get("dividend_rate"), 0.0)
            ranked_rows.append(
                {
                    "cell": cell,
                    "pe_rank": pe_rank,
                    "pb_rank": pb_rank,
                    "valuation_score": pe_rank + pb_rank,
                    "valuation_tiebreak": max(pe_rank, pb_rank),
                    "pe_value": pe_value,
                    "pb_value": pb_value,
                    "dividend_rate_value": dividend_rate_value,
                }
            )

        ranked_rows.sort(
            key=lambda item: (
                item["pb_value"],
                item["pe_value"],
                -item["dividend_rate_value"],
                item["cell"].get("stock_id", ""),
            )
        )
        leaders = ranked_rows[:2]
        groups.append(
            {
                "industry_name": industry_name,
                "industry_count": len(ranked_rows),
                "industry_avg_pb": sum(item["pb_value"] for item in leaders) / len(leaders),
                "industry_avg_pe": sum(item["pe_value"] for item in leaders) / len(leaders),
                "industry_avg_dividend_rate": sum(item["dividend_rate_value"] for item in leaders) / len(leaders),
                "rows": ranked_rows,
            }
        )

    groups.sort(
        key=lambda group: (
            group["industry_avg_pb"],
            group["industry_avg_pe"],
            -group["industry_avg_dividend_rate"],
            group["industry_name"],
        )
    )

    total_count = sum(group["industry_count"] for group in groups)
    if max_show is None:
        return {
            "total_count": total_count,
            "shown_count": total_count,
            "groups": groups,
        }

    shown_groups = []
    shown_count = 0
    for group in groups:
        if shown_count >= max_show:
            break
        remaining = max_show - shown_count
        sliced_rows = group["rows"][:remaining]
        shown_groups.append({**group, "rows": sliced_rows})
        shown_count += len(sliced_rows)

    return {
        "total_count": total_count,
        "shown_count": shown_count,
        "groups": shown_groups,
    }


def build_rule_msg(data: dict) -> str:
    """高股息筛选规则 markdown 文案(企业微信方言,经 ``render_markdown`` 转 HTML)。"""
    lines = [
        "**📋 高股息筛选规则**",
        "> 集思录条件：PE ≤ 15，股息率 ≥ 3%",
        "> 集思录条件：PE温度 ≤ 40，PB温度 ≤ 40",
        "> 集思录条件：平均ROE ≥ 5%，总市值 200~ 无限制",
    ]
    lines.extend(build_filter_summary_lines(data))
    return "\n".join(lines)


def _dividend_email_row(idx: int, ranked_row: dict) -> dict:
    """单行主表 row_spec(仅 cells,行样式由调用方按分组交替注入)。"""
    c = ranked_row["cell"]
    return {
        "cells": [
            str(idx),
            str(c.get("industry_nm", "")),
            str(c.get("stock_nm", "")),
            str(c.get("stock_id", "")),
            str(c.get("price", "")),
            f'<span style="color:#D93026">{c.get("dividend_rate", "")}%</span>',
            str(c.get("pe", "")),
            str(c.get("pb", "")),
            str(c.get("roe", "")),
            str(c.get("ttm_parent_net_profit_yi", "")),
            format_linked_bonds_html(c),
        ]
    }


def build_dividend_email_html(data: dict) -> List[str]:
    """渲染高股息邮件 HTML 片段列表:补充池(前) + 规则 + 表头 + 主表(后)。

    无符合条件的股票时返回 [补充池..., 规则, 表头, "暂无"]。补充池/规则/表头均经
    ``render_markdown`` / ``render_table`` 输出;返回值由 ``build_section`` 包成单个卡片行。
    """
    data = ensure_dividend_report_meta(data)
    grouped = build_dividend_display_groups(data)
    total = grouped["total_count"]
    raw_count = data["raw_returned_count"]
    header_text = f"**集思录高股息筛选** (集思录返回 {raw_count} 只)"
    if total != raw_count:
        header_text += f"\n筛选后剩余 {total} 只"
    supplement_parts = build_dividend_email_supplement_html(data)
    html_parts: List[str] = []
    html_parts.extend(supplement_parts)
    html_parts.append(render_markdown(build_rule_msg(data)))
    html_parts.append(render_markdown(header_text))
    if not grouped["groups"]:
        html_parts.append(render_markdown("暂无符合条件的股票数据"))
        return html_parts

    group_styles = ["background:#FBFCFE", "background:#F7FBF8"]
    row_specs = []
    idx = 1
    for group_index, group in enumerate(grouped["groups"]):
        base_style = group_styles[group_index % len(group_styles)]
        first_row_style = f"{base_style};border-top:2px solid #dfe5ec"
        for row_index, row in enumerate(group["rows"]):
            spec = _dividend_email_row(idx, row)
            spec["row_style"] = first_row_style if row_index == 0 else base_style
            row_specs.append(spec)
            idx += 1
    html_parts.append(
        render_table(
            DIVIDEND_EMAIL_HEADERS,
            row_specs,
            column_styles=DIVIDEND_EMAIL_COLUMN_STYLES,
        )
    )
    return html_parts


def _wrap_dividend_card_row(parts: List[str]) -> str:
    """把高股息片段列表包成 ``<tr>`` 卡片行(border-top 与前序 section 分隔)。"""
    return (
        f'<tr><td style="padding:24px 28px 0 28px">'
        f'<div style="border-top:1px solid {_EMAIL_BORDER_CARD_SPLIT};padding-top:24px">'
        f'{"".join(parts)}'
        f"</div></td></tr>"
    )


def build_section(work_dir: Path, *, cookie: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """高股息 section:取数 + 关联转债/补充池编排 + 渲染。

    单次 ``get_cookie()`` 登录共享给 ``fetch_data`` 与 ``prepare_dividend_email_data``,
    避免重复登录。取数/编排失败 -> ``notify_alert`` 报警并返回 None(跳过该 section,
    不中断整封市场估值邮件)。``work_dir`` 为板块编排约定参数(本 section 无图表,未使用)。
    """
    try:
        if cookie is None:
            cookie = get_cookie()
        data = fetch_data(cookie=cookie)
        data = filter_dividend_rows_by_secondary_rules(data)  # 二次筛选:国资白名单+行业+TTM(旧仓 main.py 同步,移植时遗漏)
        data = prepare_dividend_email_data(data, cookie=cookie)
    except Exception as exc:  # noqa: BLE001
        notify_alert("高股息数据获取失败", str(exc))
        return None

    parts = build_dividend_email_html(data)
    if not parts:
        return None
    return {
        "html": _wrap_dividend_card_row(parts),
        "inline_images": {},
        "as_of_date": now_in_beijing().strftime("%Y-%m-%d"),
    }
