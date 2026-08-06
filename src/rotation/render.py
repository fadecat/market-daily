"""ETF 20 日轮动 邮件正文渲染(纯函数)。

移植自 send_etf_rotation_20d_email.py 的 build_email_text/build_email_html,
去掉自建 SMTP 配置/构建逻辑(改用 common.email)。预览版把 cid 图替换为 base64。
"""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional

from .etf_data import now_in_beijing

NAV_CHART_CID = "etf_rotation_20d_nav_chart"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_nav(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "-"


def _return_color(value: Any) -> str:
    """A 股配色：上涨红、下跌绿。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "#333"
    if v > 0:
        return "#d93025"
    if v < 0:
        return "#2E7D32"
    return "#333"


def build_email_text(report: Dict[str, Any]) -> str:
    lines = [
        "20 日涨幅 ETF 轮动日报",
        f"信号日期: {report.get('as_of_date', '-')}",
        f"当日持仓: {report.get('current_holding_name', '-')} ({report.get('current_holding', '-')})",
        f"组合净值: {_fmt_nav(report.get('current_nav'))}",
        f"累计收益: {_fmt_pct(report.get('total_return'))}",
        f"最大回撤: {_fmt_pct(report.get('max_drawdown'))}",
        f"当前回撤: {_fmt_pct(report.get('current_drawdown'))}",
        f"次日持仓: {report.get('next_holding_name', '-')} ({report.get('next_holding', '-')})",
        "",
        "20 日涨幅排名:",
    ]
    for item in report.get("ranking", []):
        lines.append(f"  {item['name']} ({item['code']}): {_fmt_pct(item['return_20d'])}")
    lines.extend(["", "历史持仓(近 20 日):"])
    for entry in report.get("history", [])[-20:][::-1]:
        lines.append(
            f"  {entry['date']} 持有 {entry['holding']} 净值 {_fmt_nav(entry.get('nav'))} "
            f"日收益 {_fmt_pct(entry.get('daily_return'))}"
        )
    return "\n".join(lines)


def build_email_html(report: Dict[str, Any], chart_cid: str = NAV_CHART_CID) -> str:
    as_of = escape(str(report.get("as_of_date", "-")))
    cur_name = escape(str(report.get("current_holding_name", "-")))
    cur_code = escape(str(report.get("current_holding", "-")))
    next_name = escape(str(report.get("next_holding_name", "-")))
    next_code = escape(str(report.get("next_holding", "-")))
    cur_nav = _fmt_nav(report.get("current_nav"))

    next_code_raw = str(report.get("next_holding", ""))
    is_fallback = next_code_raw == report.get("fallback_code")

    names = report.get("code_names", {})
    ranking_rows = []
    ranking = report.get("ranking", [])
    for idx, item in enumerate(ranking):
        is_top = idx == 0 and float(item.get("return_20d") or 0) > 0
        color = _return_color(item.get("return_20d"))
        weight = "bold" if is_top else "normal"
        ranking_rows.append(
            f"<tr><td style='padding:4px 10px'>{escape(item['name'])}</td>"
            f"<td style='padding:4px 10px'>{escape(item['code'])}</td>"
            f"<td style='padding:4px 10px;color:{color};font-weight:{weight};text-align:right'>"
            f"{_fmt_pct(item['return_20d'])}</td></tr>"
        )
    ranking_html = "\n".join(ranking_rows) if ranking_rows else "<tr><td colspan='3'>无数据</td></tr>"

    history_rows = []
    for entry in report.get("history", [])[-20:][::-1]:
        code = str(entry.get("holding", ""))
        name = names.get(code, "")
        holding_label = f"{escape(name)} ({escape(code)})" if name else escape(code)
        ret_color = _return_color(entry.get("daily_return"))
        history_rows.append(
            f"<tr><td style='padding:3px 10px'>{escape(str(entry['date']))}</td>"
            f"<td style='padding:3px 10px'>{holding_label}</td>"
            f"<td style='padding:3px 10px;text-align:right'>{_fmt_nav(entry.get('nav'))}</td>"
            f"<td style='padding:3px 10px;color:{ret_color};text-align:right'>{_fmt_pct(entry.get('daily_return'))}</td></tr>"
        )
    history_html = "\n".join(history_rows) if history_rows else "<tr><td colspan='4'>无历史</td></tr>"

    next_badge = "（空仓防御）" if is_fallback else ""
    total_return = report.get("total_return")
    max_drawdown = report.get("max_drawdown")
    current_drawdown = report.get("current_drawdown")
    tr_color = _return_color(total_return)
    md_color = _return_color(max_drawdown)
    cd_color = _return_color(current_drawdown)
    generated = now_in_beijing().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    return f"""\
<div style="font-family:-apple-system,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#222;max-width:680px">
  <h2 style="margin:0 0 4px">📊 20 日涨幅 ETF 轮动日报</h2>
  <div style="color:#888;font-size:12px">信号日期 {as_of} · 生成 {generated}</div>
  <table style="border-collapse:collapse;margin:14px 0;font-size:14px">
    <tr><td style="padding:4px 16px 4px 0;color:#888">次日持仓</td>
        <td style="padding:4px 0"><b style="color:#2c7be5">{next_name}</b> ({next_code}){next_badge}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">当日持仓</td>
        <td style="padding:4px 0">{cur_name} ({cur_code})</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">组合净值</td>
        <td style="padding:4px 0">{cur_nav}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">累计收益</td>
        <td style="padding:4px 0;color:{tr_color}">{_fmt_pct(total_return)}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">最大回撤</td>
        <td style="padding:4px 0;color:{md_color}">{_fmt_pct(max_drawdown)}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">当前回撤</td>
        <td style="padding:4px 0;color:{cd_color}">{_fmt_pct(current_drawdown)}</td></tr>
  </table>

  <h3 style="margin:18px 0 6px">20 日涨幅排名</h3>
  <table style="border-collapse:collapse;font-size:13px;width:100%">
    <thead><tr style="background:#f4f6f8;color:#555">
      <th style="padding:6px 10px;text-align:left">名称</th>
      <th style="padding:6px 10px;text-align:left">代码</th>
      <th style="padding:6px 10px;text-align:right">20 日涨幅</th>
    </tr></thead>
    <tbody>
{ranking_html}
    </tbody>
  </table>

  <h3 style="margin:18px 0 6px">组合净值曲线</h3>
  <img src="cid:{chart_cid}" alt="nav chart" style="width:100%;max-width:640px;border:1px solid #e5e5e5;border-radius:6px" />

  <h3 style="margin:18px 0 6px">历史持仓（近 20 日）</h3>
  <table style="border-collapse:collapse;font-size:12px;width:100%">
    <thead><tr style="background:#f4f6f8;color:#555">
      <th style="padding:5px 10px;text-align:left">日期</th>
      <th style="padding:5px 10px;text-align:left">持仓</th>
      <th style="padding:5px 10px;text-align:right">净值</th>
      <th style="padding:5px 10px;text-align:right">日收益</th>
    </tr></thead>
    <tbody>
{history_html}
    </tbody>
  </table>
  <p style="color:#aaa;font-size:11px;margin-top:16px">
    规则：20 日涨幅（收盘价）&gt; 0 中取最大者为次日持仓；全 ≤ 0 时空仓持有 {escape(str(report.get('fallback_name', '')))}。
    T 日净值用 T-1 收盘已决定的持仓更新（无未来函数）。
  </p>
</div>
"""


def build_preview_html(
    report: Dict[str, Any], chart_path: Optional[str | Path] = None
) -> str:
    """预览 HTML:把 cid 图替换为 base64 data URI,单文件可离线查看。"""
    html = build_email_html(report, NAV_CHART_CID)
    if chart_path and Path(chart_path).exists():
        b64 = base64.b64encode(Path(chart_path).read_bytes()).decode("ascii")
        html = html.replace(f"cid:{NAV_CHART_CID}", f"data:image/png;base64,{b64}")
    return html
