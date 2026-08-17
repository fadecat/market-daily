from __future__ import annotations

from html import escape
from typing import Mapping

from .charts import ChartResult

STATE_LABELS = {
    "failed_recovery": "修复失败",
    "temporary_recovery": "临时修复",
    "confirmed_recovery": "确认修复",
}

SECTION_CONFIG = [
    (
        "price",
        "价格与回撤",
        "看当前离近{years}年高点还有多远，以及回撤发生在什么价格位置。",
        "drawdown_peak = close / 近{years}年滚动高点 - 1",
    ),
    (
        "spread",
        "利率相对吸引力",
        "看红利相对10年国债是否更有吸引力。",
        "dividend_yield_spread = 股息率 - 10年国债收益率；earnings_yield_spread = 100 / PE - 10年国债收益率；两者均取近{years}年百分位",
    ),
    (
        "valuation",
        "绝对定价",
        "看红利现在在近{years}年估值里偏贵还是偏便宜。",
        "pe_ttm_percentile / pb_lf_percentile = 近{years}年历史百分位",
    ),
    (
        "style",
        "风格挤压",
        "看市场是否仍处在成长拥挤、红利受压的阶段。",
        "style_rotation_spread_percentile = 风格轮动收益率差值近{years}年百分位",
    ),
]


def _fmt_pct(value: float | None, *, scale_100: bool = True) -> str:
    if value is None:
        return "-"
    number = value * 100 if scale_100 else value
    return f"{number:.1f}%"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _state_label(value: str | None) -> str:
    if not value:
        return "-"
    return STATE_LABELS.get(value, value)


def _render_card_grid(latest: dict, years: int, index_code: str) -> str:
    return (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:18px">'
        f'{_render_card("最新日期", escape(str(latest.get("date") or "-")), "观察对象 " + escape(index_code))}'
        f'{_render_card(f"距近{years}年高点", escape(_fmt_pct(latest.get("drawdown_peak"))), f"最新收盘 {escape(_fmt_num(latest.get('index_close')))}")}'
        f'{_render_card("绝对估值", "PE分位 " + escape(_fmt_pct(latest.get("pe_ttm_percentile"), scale_100=False)), "PB分位 " + escape(_fmt_pct(latest.get("pb_lf_percentile"), scale_100=False)))}'
        f'{_render_card("当前状态", escape(_state_label(latest.get("event_state"))), "风格挤压 " + escape(_fmt_pct(latest.get("style_rotation_spread_percentile"), scale_100=False)))}'
        "</div>"
    )


def _render_card(label: str, value: str, meta: str) -> str:
    return (
        '<div style="min-width:0;padding:16px;border-radius:18px;border:1px solid rgba(23,33,43,0.10);background:rgba(255,255,255,0.72)">'
        f'<div style="font-size:12px;color:#667085;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px">{label}</div>'
        f'<div style="white-space:nowrap;font-size:clamp(20px,5vw,30px);line-height:1.05;font-weight:700;color:#17212b">{value}</div>'
        f'<div style="margin-top:8px;font-size:13px;color:#667085">{meta}</div>'
        "</div>"
    )


def _render_section(
    title: str,
    note: str,
    formula: str,
    chart: ChartResult | None,
    image_src: str | None,
) -> str:
    if chart is not None and chart.error:
        chart_html = (
            '<div style="height:290px;display:flex;align-items:center;justify-content:center;'
            'border:1px dashed rgba(23,33,43,0.15);border-radius:16px;color:#667085">'
            f"{escape(chart.error)}"
            "</div>"
        )
    elif image_src:
        chart_html = (
            f'<img src="{escape(image_src)}" alt="{escape(title)}" '
            'style="width:100%;display:block;border-radius:16px;border:1px solid rgba(23,33,43,0.08)">'
        )
    else:
        chart_html = (
            '<div style="height:290px;display:flex;align-items:center;justify-content:center;'
            'border:1px dashed rgba(23,33,43,0.15);border-radius:16px;color:#667085">该图暂无数据</div>'
        )
    return (
        '<section style="background:rgba(255,255,255,0.82);border:1px solid rgba(23,33,43,0.10);'
        'border-radius:24px;box-shadow:0 18px 40px rgba(23,33,43,0.10);padding:20px;margin-top:16px">'
        f'<div style="margin:0 0 12px;font-size:15px;letter-spacing:0.08em;text-transform:uppercase;color:#17212b">{escape(title)}</div>'
        f'<div style="margin:-4px 0 4px;font-size:13px;line-height:1.6;color:#667085">{escape(note)}</div>'
        f'<div style="margin:0 0 12px;font-size:12px;line-height:1.6;color:#667085;font-family:Consolas,\'SFMono-Regular\',\'Microsoft YaHei\',monospace">{escape(formula)}</div>'
        f"{chart_html}"
        "</section>"
    )


def _build_html(
    payload: dict,
    charts: Mapping[str, ChartResult],
    image_src_map: Mapping[str, str],
) -> str:
    meta = payload.get("meta") or {}
    latest = payload.get("latest") or {}
    analysis_years = int(meta.get("analysis_window_years") or 3)
    display_years = int(meta.get("display_window_years") or analysis_years)
    sections = []
    for key, title, note_tpl, formula_tpl in SECTION_CONFIG:
        note_years = display_years if key == "price" else analysis_years
        chart = charts.get(key)
        sections.append(
            _render_section(
                title,
                note_tpl.format(years=note_years),
                formula_tpl.format(years=analysis_years),
                chart,
                image_src_map.get(key),
            )
        )
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>红利观察图</title></head>"
        "<body style=\"margin:0;color:#17212b;font-family:'Avenir Next','Microsoft YaHei','PingFang SC',sans-serif;background:radial-gradient(circle at top left, rgba(170,106,22,0.10), transparent 30%), linear-gradient(180deg, #f7f2ea 0%, #f3efe7 100%)\">"
        '<div style="max-width:1360px;margin:0 auto;padding:32px 18px 56px">'
        '<section style="background:rgba(255,255,255,0.82);border:1px solid rgba(23,33,43,0.10);border-radius:24px;box-shadow:0 18px 40px rgba(23,33,43,0.10);padding:28px;margin-bottom:18px">'
        f"<h1 style=\"margin:0;font-size:clamp(28px,4vw,44px);line-height:1.05;font-family:Georgia,'STSong',serif\">红利观察图 · {escape(str(meta.get('index_name') or '红利低波100'))}</h1>"
        f"<p style=\"margin:12px 0 0;color:#667085;line-height:1.7;max-width:70ch\">围绕 {escape(str(meta.get('index_name') or '红利低波100'))}({escape(str(meta.get('index_code') or '930955'))})，本页只展示价格位置、绝对定价、利率相对吸引力、风格挤压和修复状态，不输出买卖建议。</p>"
        f"{_render_card_grid(latest, display_years, str(meta.get('index_code') or '930955'))}"
        "</section>"
        f"{''.join(sections)}"
        "</div></body></html>"
    )


def build_email_html(payload: dict, charts: Mapping[str, ChartResult]) -> str:
    image_src_map = {
        name: f"cid:{item.cid}"
        for name, item in charts.items()
        if item.image_path
    }
    return _build_html(payload, charts, image_src_map)


def build_preview_html(
    payload: dict,
    charts: Mapping[str, ChartResult],
    data_uri_map: Mapping[str, str],
) -> str:
    return _build_html(payload, charts, data_uri_map)
