"""董秘互动 section 渲染。

移植自 cb_main.build_irm_messages 的拼装部分:去掉 wechat 分页(MAX_MSG_BYTES/
get_msg_size),改为拼一段 markdown 经 ``email.render_markdown`` 输出单个 HTML 片段,
交给板块 ``compose_sections`` 统一包裹。
"""
from __future__ import annotations

from typing import Any, Dict, List

from ...common import email


HEADER = "**📣 正股董秘互动(最近一周)**"


def build_section_html(stock_qas: List[Dict[str, Any]]) -> str:
    """将 collect_irm_for_rows 的结果渲染为 section HTML 片段。

    无数据时返回空串(板块 ``compose_sections`` 会自动略过)。
    """
    if not stock_qas:
        return ""

    lines = [HEADER]
    for item in stock_qas:
        stock_nm = item.get("stock_nm", "")
        stock_id = item.get("stock_id", "")
        lines.append(f"\n**{stock_nm}**({stock_id})\n")
        for qa in item.get("qas", []):
            lines.append(f"> Q: {qa.get('question', '')}\n")
            lines.append(f"> A: {qa.get('answer', '')}\n")
            if qa.get("url"):
                lines.append(f"> [查看详情]({qa['url']})\n")

    return email.render_markdown("".join(lines))
