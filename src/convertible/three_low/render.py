"""可转债三低轮动 报告渲染(文本 + HTML 片段 + 预览页)。

移植自 send_cb_three_low_email.py 的渲染部分:去掉 SMTP/load_email_config/build_message,
改由 common.email 发信;基准序列改用 ..index_chart.history;预览图改 base64 内联。
"""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..index_chart import history as cb_index_history
from . import strategy
from .charts import NAV_CHART_CID


def history_updated(prev: Optional[Dict[str, Any]], state: Dict[str, Any]) -> bool:
    """本次运行是否产生了新数据:历史变长(新交易日),或签名变化(当日记录被覆盖重算)。"""
    prev = prev or {}
    if len(state.get("holdings_history", [])) > len(prev.get("holdings_history", [])):
        return True
    return state.get("last_snapshot_signature") != prev.get("last_snapshot_signature")


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _return_color(value: Any) -> str:
    """A 股配色:上涨红、下跌绿。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "#333"
    if v > 0:
        return "#d93025"
    if v < 0:
        return "#2E7D32"
    return "#333"


def load_benchmark_series() -> Optional[List[Dict[str, Any]]]:
    """拉取集思录可转债等权指数序列([{date, value}])。

    优先实时页面(含当日),失败时回退到仓库内已提交的历史文件。
    """
    try:
        series = cb_index_history.build_runtime_index_series()
        if series:
            return series
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 集思录等权指数实时拉取失败,回退本地历史: {exc}")
    try:
        series = [
            {"date": str(r["date"])[:10], "value": float(r["index_value"])}
            for r in cb_index_history.load_history()
            if r.get("date") and r.get("index_value") not in (None, "")
        ]
        return series or None
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 集思录等权指数本地历史读取失败: {exc}")
        return None


def _bond_chip_html(name: Any, pct: Any = None, *, added: bool = False,
                    removed: bool = False, days: int = 0) -> str:
    """单只转债 chip:字色=当日涨跌(红涨绿跌),加粗=调入,删除线=调出,角标=连续持有天数。"""
    base = ("display:inline-block;margin:1px 4px 1px 0;padding:1px 7px;"
            "border:1px solid #e3e5e8;border-radius:9px;white-space:nowrap;")
    if removed:
        style = base + "color:#a6abb3;text-decoration:line-through;background:#f5f6f7"
        return f"<span style='{style}'>{escape(str(name))}</span>"
    color = _return_color(pct) if pct is not None else "#333"
    weight = "font-weight:bold;" if added else ""
    sup = f"<sup style='color:#b4b8be;font-size:10px'>{days}</sup>" if days >= 2 else ""
    style = base + f"color:{color};{weight}background:#fafbfc"
    return f"<span style='{style}'>{escape(str(name))}{sup}</span>"


def _history_entry_chips(full_history: List[Dict[str, Any]], gidx: int) -> tuple:
    """渲染一天持仓的 chip HTML 与换手文本。gidx 为 entry 在完整历史中的下标。"""
    entry = full_history[gidx]
    prev_holdings = full_history[gidx - 1].get("holdings", []) if gidx > 0 else []
    prev_price = {str(h.get("code")): h.get("price") for h in prev_holdings}
    cur_codes = {str(h.get("code")) for h in entry.get("holdings", [])}
    chips = []
    added_n = 0
    for h in entry.get("holdings", []):
        code = str(h.get("code", ""))
        if not prev_holdings and gidx == 0:
            chips.append(_bond_chip_html(h.get("name", "")))  # 建仓日:无涨跌色
            continue
        added = code not in prev_price
        added_n += 1 if added else 0
        pct = None
        if not added:
            try:
                pct = float(h.get("price")) / float(prev_price[code]) - 1.0
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
        days = 0
        j = gidx
        while j >= 0 and any(str(x.get("code")) == code
                             for x in full_history[j].get("holdings", [])):
            days += 1
            j -= 1
        chips.append(_bond_chip_html(h.get("name", ""), pct, added=added, days=days))
    for h in prev_holdings:  # 调出:灰色删除线,排在末尾
        if str(h.get("code", "")) not in cur_codes:
            chips.append(_bond_chip_html(h.get("name", ""), removed=True))
    if gidx == 0:
        tag = "<span style='color:#999;font-size:11px;margin-right:4px'>建仓</span>"
        return tag + "".join(chips), "建仓"
    return "".join(chips), added_n


def build_email_text(report: Dict[str, Any]) -> str:
    lines = [
        "可转债三低轮动日报",
        f"信号日期: {report.get('as_of_date', '-')}",
        f"组合净值: {_fmt_num(report.get('current_nav'), 4)}",
        f"累计收益: {_fmt_pct(report.get('total_return'))}",
        f"等权指数同期: {_fmt_pct(report.get('benchmark_return'))}",
        f"超额收益: {_fmt_pct(report.get('excess_return'))}",
        f"最大回撤: {_fmt_pct(report.get('max_drawdown'))}",
        f"当前回撤: {_fmt_pct(report.get('current_drawdown'))}",
        f"持仓只数: {len(report.get('holdings', []))}",
        "",
        "三低排名:",
    ]
    for item in report.get("ranking", []):
        mark = " *" if item.get("selected") else ""
        lines.append(
            f"  {item['rank']}. {item['name']} ({item['code']}) "
            f"双低 {_fmt_num(item.get('dblow'))} 溢价 {_fmt_num(item.get('premium_rt'))}% "
            f"规模 {_fmt_num(item.get('curr_iss_amt'))}{mark}"
        )
    lines.extend(["", "历史持仓(近 20 日):"])
    full_history = report.get("history", [])
    window = full_history[-20:]
    offset = len(full_history) - len(window)
    for widx in range(len(window) - 1, -1, -1):
        entry = window[widx]
        gidx = offset + widx
        prev_holdings = full_history[gidx - 1].get("holdings", []) if gidx > 0 else []
        prev_codes = {str(h.get("code")) for h in prev_holdings}
        cur_codes = {str(h.get("code")) for h in entry.get("holdings", [])}
        parts = []
        for h in entry.get("holdings", []):
            mark = "[入]" if gidx > 0 and str(h.get("code")) not in prev_codes else ""
            parts.append(f"{h.get('name', '')}{mark}")
        for h in prev_holdings:
            if str(h.get("code")) not in cur_codes:
                parts.append(f"{h.get('name', '')}[出]")
        names = "、".join(parts)
        lines.append(
            f"  {entry['date']} 净值 {_fmt_num(entry.get('nav'), 4)} "
            f"日收益 {_fmt_pct(entry.get('daily_return'))} 持有 {names}"
        )
    return "\n".join(lines)


def build_email_html(report: Dict[str, Any], chart_cid: str) -> str:
    as_of = escape(str(report.get("as_of_date", "-")))
    cur_nav = _fmt_num(report.get("current_nav"), 4)
    total_return = report.get("total_return")
    max_drawdown = report.get("max_drawdown")
    current_drawdown = report.get("current_drawdown")
    benchmark_return = report.get("benchmark_return")
    excess_return = report.get("excess_return")
    tr_color = _return_color(total_return)
    md_color = _return_color(max_drawdown)
    cd_color = _return_color(current_drawdown)
    br_color = _return_color(benchmark_return)
    er_color = _return_color(excess_return)
    generated = strategy.now_in_beijing().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    # 三低排名表
    ranking_rows = []
    for item in report.get("ranking", []):
        bg = "background:#eef5ff" if item.get("selected") else ""
        ranking_rows.append(
            f"<tr style='{bg}'>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{escape(str(item.get('rank', '')))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{escape(str(item.get('name', '')))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{escape(str(item.get('code', '')))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_num(item.get('price'))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_num(item.get('dblow'))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_num(item.get('premium_rt'))}%</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_num(item.get('curr_iss_amt'))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:right'>{_fmt_num(item.get('total_score'), 1)}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:center'>{'✓' if item.get('selected') else ''}</td></tr>"
        )
    ranking_html = "\n".join(ranking_rows) if ranking_rows else "<tr><td colspan='9'>无数据</td></tr>"

    # 历史持仓表(移动友好:窄列指标行 + 全宽 chip 行,两行一天)
    full_history = report.get("history", [])
    window = full_history[-20:]
    offset = len(full_history) - len(window)
    target_count = report.get("target_count", 10)
    history_rows = []
    for widx in range(len(window) - 1, -1, -1):
        entry = window[widx]
        chips_html, turnover = _history_entry_chips(full_history, offset + widx)
        if isinstance(turnover, int):
            turnover_txt = f"{turnover / target_count * 100:.0f}%" if target_count else "-"
        else:
            turnover_txt = turnover  # 建仓
        ret_color = _return_color(entry.get("daily_return"))
        history_rows.append(
            f"<tr><td style='padding:5px 10px 0;white-space:nowrap'>{escape(str(entry.get('date', '')))}</td>"
            f"<td style='padding:5px 10px 0;text-align:right;white-space:nowrap'>{_fmt_num(entry.get('nav'), 4)}</td>"
            f"<td style='padding:5px 10px 0;text-align:right;white-space:nowrap;color:{ret_color}'>{_fmt_pct(entry.get('daily_return'))}</td>"
            f"<td style='padding:5px 10px 0;text-align:right;white-space:nowrap;color:#888'>{turnover_txt}</td></tr>"
            f"<tr><td colspan='4' style='padding:2px 10px 8px;border-bottom:1px solid #eee'>{chips_html}</td></tr>"
        )
    history_html = "\n".join(history_rows) if history_rows else "<tr><td colspan='4'>无历史</td></tr>"

    return f"""\
<div style="font-family:-apple-system,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#222">
  <div style="margin:8px 0"><b>📊 可转债三低轮动日报</b><br></div>
  <div style="color:#888;font-size:12px">信号日期 {as_of} · 生成 {generated}</div>
  <table style="border-collapse:collapse;margin:14px 0;font-size:14px">
    <tr><td style="padding:4px 16px 4px 0;color:#888">组合净值</td>
        <td style="padding:4px 0">{cur_nav}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">累计收益</td>
        <td style="padding:4px 0;color:{tr_color}">{_fmt_pct(total_return)}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">等权指数同期</td>
        <td style="padding:4px 0;color:{br_color}">{_fmt_pct(benchmark_return)}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">超额收益</td>
        <td style="padding:4px 0;color:{er_color}">{_fmt_pct(excess_return)}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">最大回撤</td>
        <td style="padding:4px 0;color:{md_color}">{_fmt_pct(max_drawdown)}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">当前回撤</td>
        <td style="padding:4px 0;color:{cd_color}">{_fmt_pct(current_drawdown)}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#888">持仓只数</td>
        <td style="padding:4px 0">{len(report.get('holdings', []))} / {target_count} 等权</td></tr>
  </table>

  <div style="margin:8px 0"><b>三低排名（✓ = 次日持仓，双低值 + 溢价率 + 剩余规模）</b><br></div>
  <table style="border-collapse:collapse;font-size:13px;width:100%;margin:8px 0">
    <thead><tr>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">排名</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:left">名称</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:left">代码</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">收盘价</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">双低</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">溢价率</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">规模(亿)</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">得分</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:center">持仓</th>
    </tr></thead>
    <tbody>
{ranking_html}
    </tbody>
  </table>

  <div style="margin:8px 0"><b>组合净值 vs 集思录等权指数</b><br></div>
  <div style="margin:8px 0;text-align:center"><img src="cid:{chart_cid}" alt="nav chart" style="max-width:100%;height:auto" /></div>

  <div style="margin:8px 0"><b>历史持仓（近 20 日）</b><br></div>
  <table style="border-collapse:collapse;font-size:13px;width:100%;margin:8px 0">
    <thead><tr>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:left">日期</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">净值</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">日收益</th>
      <th style="padding:6px 10px;border-bottom:2px solid #333;background:#f0f0f0;font-weight:bold;white-space:nowrap;text-align:right">换手</th>
    </tr></thead>
    <tbody>
{history_html}
    </tbody>
  </table>
  <p style="color:#aaa;font-size:11px;margin-top:4px">
    债名字色 = 当日涨跌（红涨绿跌），<b>加粗</b> = 调入，<s style="color:#a6abb3">删除线</s> = 调出，右上角小字 = 连续持有天数。
  </p>
  <p style="color:#aaa;font-size:11px;margin-top:16px">
    规则：三低策略（双低值+溢价率+剩余规模综合评分）取前 {target_count} 只等权持仓，日频再平衡，容差保留已持有且仍在池内的转债。
    T 日净值用 T-1 收盘已决定的持仓更新（无未来函数）。模拟盘，不发真实委托。
  </p>
</div>
"""


def build_preview_html(report: Dict[str, Any], chart_path: Optional[Path] = None) -> str:
    """生成独立预览页:净值图以 base64 内联,其余复用 build_email_html。"""
    fragment = build_email_html(report, NAV_CHART_CID)
    if chart_path is not None and Path(chart_path).exists():
        data = base64.b64encode(Path(chart_path).read_bytes()).decode("ascii")
        fragment = fragment.replace(
            f'src="cid:{NAV_CHART_CID}"',
            f'src="data:image/png;base64,{data}"',
        )
    else:
        fragment = fragment.replace(
            f'<div style="margin:8px 0;text-align:center"><img src="cid:{NAV_CHART_CID}" alt="nav chart" style="max-width:100%;height:auto" /></div>',
            '<p style="color:#aaa;font-size:12px">(净值图未生成)</p>',
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>可转债三低轮动预览 {escape(str(report.get('as_of_date', '')))}</title></head>
<body style="margin:0;padding:20px;background:#f5f6f7">
{fragment}
</body></html>
"""
