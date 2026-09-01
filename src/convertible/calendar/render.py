"""集思录日历提醒 section 渲染。

把多规则命中事件聚合成一个 HTML 片段,经 common.email.render_markdown 渲染
(其 _markdown_to_html 已把企业微信方言的 ``<font color="warning">`` 等映射为 span)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ...common import email
from .calendar import format_event_time, now_in_beijing


def build_section_html(
    matched_rules: List[Dict[str, Any]], *, current_time: Optional[datetime] = None
) -> str:
    """matched_rules: [{"rule_name": str, "events": [...]}]。返回 section HTML 片段。"""
    now_text = (current_time or now_in_beijing()).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "**📅 集思录日历提醒**",
        f"> 触发时间: <font color=\"comment\">{now_text}</font>",
        "",
    ]
    for mr in matched_rules:
        lines.append(f"> 规则: <font color=\"info\">{mr['rule_name']}</font>")
        for event in mr["events"]:
            event_line = (
                f"• <font color=\"warning\">{format_event_time(event.get('event_time'))}</font> "
                f"{event['title']}"
            )
            code = str(event.get("code", "")).strip()
            if code:
                event_line = f"{event_line} ({code})"
            industry_name = str(event.get("industry") or "").strip()
            if industry_name:
                event_line = f"{event_line} [{industry_name}]"
            stock_price = str(event.get("stock_price") or "").strip()
            if stock_price:
                event_line = f"{event_line} 正股价 {stock_price}"
            lines.append(event_line)
        lines.append("")
    return email.render_markdown("\n".join(lines).strip())
