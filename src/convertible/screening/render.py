"""可转债低价债筛选 section 渲染。

移植自 jisilu_ggx/cb_main.py 的邮件渲染部分:用 common.email.render_markdown /
render_table 拼装,去掉 wechat 分页(format_cb/build_cb_messages)与指数图
(chart 归 index_chart section)。build_section_html 返回单段 HTML 片段。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...common import email
from .archive import get_cb_adjust_days_text
from .strategy import get_enterprise_nature, is_force_redeem_triggered, load_enterprise_nature_map


CB_EMAIL_HEADERS = [
    "#", "转债", "代码", "价格", "溢价率", "规模",
    "评级", "剩余年限", "到期收益率", "正股", "正股价", "企业性质", "下修天计数",
]


def build_cb_rule_msg(max_price: int) -> str:
    return (
        "**📋 可转债筛选规则**\n"
        f"> 价格:≤ {max_price}\n"
        "> 评级:AAA ~ A-\n"
        "> 已上市,排除停牌\n"
        "> 排除正股含 ST\n"
        "> 排除已公告强赎、到期赎回\n"
        "> 排除剩余年限<1年且到期税前收益率<0\n"
        "> 排序:双低/规模排名得分相加,总分越高越靠前"
    )


def build_cb_index_quote_message(index_data: Optional[Dict[str, Any]]) -> str:
    """构建可转债市场概览消息。"""
    if not index_data:
        return ""
    return (
        "**📈 可转债市场概览**\n"
        f"> 转债等权指数:{float(index_data.get('cur_index', 0)):.3f}"
        f" {float(index_data.get('cur_increase_val', 0)):+.3f}"
        f" {float(index_data.get('cur_increase_rt', 0)):+.3f}%\n"
        f"> 温度:{float(index_data.get('temperature', 0)):.2f}\n"
        f"> 成交额:{float(index_data.get('volume', 0)):.2f}亿元\n"
        f"> 价格中位数:{float(index_data.get('mid_price', 0)):.3f}\n"
        f"> 溢价率中位数:{float(index_data.get('mid_premium_rt', 0)):.2f}%\n"
        f"> 到期收益率:{float(index_data.get('avg_ytm_rt', 0)):.2f}%"
    )


# ── 单元格着色(A 股配色:红涨绿跌;低价/低溢价/正收益绿,反之红)──────────────────
def _cb_email_price(value: Any) -> str:
    if value in (None, ""):
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v < 100:
        return f'<span style="color:#1AAD19">{value}</span>'
    if v > 130:
        return f'<span style="color:#D93026">{value}</span>'
    return str(value)


def _cb_email_premium(value: Any) -> str:
    if value in (None, ""):
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"{value}%"
    if v < 20:
        return f'<span style="color:#1AAD19">{value}%</span>'
    if v > 50:
        return f'<span style="color:#D93026">{value}%</span>'
    return f"{value}%"


def _cb_email_ytm(value: Any) -> str:
    if value in (None, ""):
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"{value}%"
    color = "#1AAD19" if v > 0 else "#D93026"
    return f'<span style="color:{color}">{value}%</span>'


def _cb_email_sprice(value: Any) -> str:
    if value in (None, ""):
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v < 5:
        return f'<b><span style="color:#D93026">{value}</span></b>'
    return str(value)


def _cb_email_row(
    idx: int, c: Dict[str, Any], nature_map: Optional[Dict[str, str]] = None,
    archive_map: Optional[Dict[str, Any]] = None,
) -> dict:
    cells = [
        str(idx),
        f'📌 {c.get("bond_nm", "")}',
        str(c.get("bond_id", "")),
        _cb_email_price(c.get("price")),
        _cb_email_premium(c.get("premium_rt")),
        str(c.get("curr_iss_amt", "--")),
        str(c.get("rating_cd", "--")),
        f'{c.get("year_left", "--")}年',
        _cb_email_ytm(c.get("ytm_rt")),
        str(c.get("stock_nm", "--")),
        _cb_email_sprice(c.get("sprice")),
        get_enterprise_nature(c, nature_map),
        get_cb_adjust_days_text(c, archive_map),
    ]
    spec: dict = {"cells": cells}
    if is_force_redeem_triggered(c):
        spec["note"] = "⚠已触发强赎(未公告)"
    return spec


def build_section_html(
    filtered_rows: List[Dict[str, Any]],
    index_data: Optional[Dict[str, Any]],
    archive_map: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """组装筛选 section HTML 片段(规则 + 概览 + 表格)。"""
    config = config or {}
    max_show = int(config.get("max_show", 50))
    max_price = int(config.get("max_price", 120))
    nature_map = load_enterprise_nature_map()

    total = len(filtered_rows)
    show_rows = filtered_rows[:max_show]
    parts = [email.render_markdown(build_cb_rule_msg(max_price))]
    index_msg = build_cb_index_quote_message(index_data)
    if index_msg:
        parts.append(email.render_markdown(index_msg))
    if not show_rows:
        parts.append(email.render_markdown("暂无符合条件的可转债数据"))
        return "".join(parts)

    header_text = f"**集思录可转债筛选** (共 {total} 只)"
    if total > max_show:
        header_text += f"\n以下展示前 {max_show} 只"
    parts.append(email.render_markdown(header_text))
    parts.append(
        email.render_table(
            CB_EMAIL_HEADERS,
            [_cb_email_row(i, r["cell"], nature_map, archive_map) for i, r in enumerate(show_rows, 1)],
        )
    )
    return "".join(parts)
