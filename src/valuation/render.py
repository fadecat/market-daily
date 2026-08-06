"""市场估值板块渲染层:格式化工具 + 估值核心 item 区块 + 果仁/汇率 section + 邮件卡片组装。

移植自 ``monitor_drawdown.py`` 的 ``_render_*`` 系列(L325-340 归档后缀、L1473-1498
数值/百分数格式化、L1781-1930 股息率/股债收益差/比值配色与单元格、L2000-2088 估值 item
区块、L2136-2234 果仁行业估值 section、L2237-2408 ``build_email_html_content`` 卡片组装)。

与旧版的差异(对齐迁移计划 L22/L149 的板块顺序 估值+高股息+果仁+风格轮动+汇率图):

- 退役回撤路径:``_render_email_summary_table`` / ``triggered_items`` / webhook markdown /
  SMTP 封装(``build_email_message``)均不迁;SMTP 改用 ``common.email.send_email``。
- 汇率图从「区块顶部」移到「板块末尾」:旧 ``build_email_html_content`` 把 fx 图放在 item
  区块之前,新顺序按计划把汇率图作为最后一个 section,故 fx 图改由 ``render_fx_chart_section``
  产出 ``<tr>`` 片段,由 ``run.py`` 拼到 ``extra_sections`` 末尾。
- 卡片布局:本板块用自定义 ``assemble_email_html`` 包裹 ``<tr>`` 片段(非
  ``common.email.compose_sections`` 的 ``<div>+<hr>``),与 ``style_rotation`` / ``dividend``
  各自返回的 ``<tr>`` 卡片行片段一致。

配色语义(中文惯例:红=高/贵/涨,绿=低/便宜/跌):

- ``_format_percentile_cell``(PE/PB/果仁分位):**成本**着色 —— ≥80 红(偏贵)、≤20 绿(偏便宜)。
- ``_spread_main_color``(股息率/股债收益差/比值):**价值**着色 —— ≥80 绿(价值高)、≤20 红(价值低),
  与上面相反;主值低于阈值 par 时单独标红。
- ``_format_signed_return_cell``(果仁涨幅):正涨红、负跌绿。
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fetch import now_in_beijing
from .metrics import get_index_valuation_metric, parse_float

# ---------- 配色 / 排版常量(与 style_rotation.py 的 _EMAIL_* 私有副本同源) ----------

EMAIL_PERCENTILE_LABELS = ["1Y", "3Y", "5Y"]
EMAIL_ACCENT_COLOR = "#2c7be5"
EMAIL_ALERT_COLOR = "#d93025"
EMAIL_HIGH_COLOR = "#D32F2F"
EMAIL_LOW_COLOR = "#2E7D32"
EMAIL_DIVIDEND_COLOR = "#2E7D32"
EMAIL_MUTED_COLOR = "#6e6e73"
EMAIL_LABEL_COLOR = "#86868b"
EMAIL_BORDER_COLOR = "#e5e5e5"
EMAIL_TEXT_PRIMARY = "#1d1d1f"
EMAIL_BG_PAGE = "#f5f5f7"
EMAIL_BG_TABLE_HEAD = "#fafafa"
EMAIL_BG_TAG = "#E8F5E9"
EMAIL_BORDER_SPLIT = "#EEEEEE"
EMAIL_BORDER_CARD_SPLIT = "#f0f0f0"
EMAIL_BORDER_ROW = "#f5f5f7"
EMAIL_PERCENTILE_HIGH_THRESHOLD = 80.0
EMAIL_PERCENTILE_LOW_THRESHOLD = 20.0
EMAIL_FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "'Helvetica Neue','PingFang SC','Microsoft YaHei',Arial,sans-serif"
)
EMAIL_BASE_FONT = (
    f"font-family:{EMAIL_FONT_STACK};font-size:14px;line-height:1.6;color:{EMAIL_TEXT_PRIMARY}"
)

# 内嵌图 cid 约定(HTML 用 src="cid:{cid}",send_email 内部包 <>)
STYLE_ROTATION_CHART_CID = "style_rotation_chart"
FX_CHART_CID = "fx_usd_cny_vs_mid_10y"


def equity_bond_chart_cid(code: str) -> str:
    """单只指数 PE 分位图的 cid。"""
    return f"equity_bond_{code}"


# ---------- 格式化工具 ----------


def format_number(value: float, decimals: int = 4, strip: bool = True) -> str:
    text = f"{value:.{decimals}f}"
    if strip:
        text = text.rstrip("0").rstrip(".")
    return text if text else "0"


def format_percent(value: float, decimals: int = 2, strip: bool = True) -> str:
    text = f"{value:.{decimals}f}"
    if strip:
        text = text.rstrip("0").rstrip(".")
    return text if text else "0"


def format_optional_number(value: object, decimals: int = 2, strip: bool = True) -> str:
    parsed = parse_float(value)
    if parsed is None:
        return "-"
    return format_number(parsed, decimals=decimals, strip=strip)


def format_optional_percent(value: object, decimals: int = 2, strip: bool = True) -> str:
    parsed = parse_float(value)
    if parsed is None:
        return "-"
    return f"{format_percent(parsed, decimals=decimals, strip=strip)}%"


def _archive_suffix(data_source: object, archive_latest_date: object) -> str:
    if str(data_source or "").strip() != "archive":
        return ""
    latest = str(archive_latest_date or "").strip()
    return f" (archive, {latest})" if latest else " (archive)"


def _archive_html_suffix(data_source: object, archive_latest_date: object) -> str:
    if str(data_source or "").strip() != "archive":
        return ""
    latest = str(archive_latest_date or "").strip()
    label = f"(archive, {latest})" if latest else "(archive)"
    return (
        f'<span style="font-size:11px;color:{EMAIL_LABEL_COLOR};'
        f'font-weight:500;margin-left:6px">{escape(label)}</span>'
    )


def _signed_percent(value: float) -> str:
    """带 Unicode 减号的百分比,正负号视觉对称。"""
    return f"{value:+.2f}%".replace("-", "−")


def _spread_main_color(
    value: float, pct_10y: Optional[float], par: float
) -> Tuple[str, Optional[str]]:
    """返回 (主值颜色, 分位颜色)。分位颜色仅在极值分位触发时非 None。

    价值着色(与 PE/PB 成本着色相反):分位 ≥80 -> 价值高(绿)、≤20 -> 价值低(红);
    分位未触发但主值低于 par 阈值时主值单独标红。
    """
    if pct_10y is not None:
        if pct_10y >= EMAIL_PERCENTILE_HIGH_THRESHOLD:
            return EMAIL_LOW_COLOR, EMAIL_LOW_COLOR
        if pct_10y <= EMAIL_PERCENTILE_LOW_THRESHOLD:
            return EMAIL_HIGH_COLOR, EMAIL_HIGH_COLOR
    if value < par:
        return EMAIL_HIGH_COLOR, None
    return EMAIL_TEXT_PRIMARY, None


def _render_spread_cell(
    label: str,
    main_html: str,
    pct: Optional[float],
    avg: Optional[float],
    avg_formatter,
    pct_color: Optional[str],
    border_left: bool,
    width: str = "50%",
) -> str:
    cell_style = (
        "padding-right:10px"
        if not border_left
        else f"padding-left:10px;padding-right:10px;border-left:1px solid {EMAIL_BORDER_SPLIT}"
    )
    pct_html = "-"
    if pct is not None:
        pct_text = f"{pct:.2f}%"
        if pct_color:
            pct_html = f'<b style="color:{pct_color}">{escape(pct_text)}</b>'
        else:
            pct_html = f'<span style="color:{EMAIL_MUTED_COLOR};font-weight:600">{escape(pct_text)}</span>'
    avg_html = (
        f'<span style="color:{EMAIL_MUTED_COLOR};font-weight:600">{avg_formatter(avg)}</span>'
        if avg is not None
        else "-"
    )
    return (
        f'<td width="{width}" valign="top" style="{cell_style}">'
        f'<div style="font-size:12px;color:{EMAIL_MUTED_COLOR};letter-spacing:0.2px">{escape(label)}</div>'
        f'<div style="margin-top:2px;line-height:1.2">{main_html}</div>'
        f'<div style="font-size:11px;color:{EMAIL_LABEL_COLOR};margin-top:4px">'
        f"5Y分位 {pct_html}&nbsp;·&nbsp;5Y均值 {avg_html}"
        f"</div>"
        f"</td>"
    )


def _format_percentile_cell(value: object) -> str:
    """PE/PB/果仁分位单元格 —— 成本着色:≥80 红(偏贵)、≤20 绿(偏便宜)。"""
    parsed = parse_float(value)
    if parsed is None:
        return "-"
    text = escape(f"{format_percent(parsed, decimals=2, strip=False)}%")
    if parsed >= EMAIL_PERCENTILE_HIGH_THRESHOLD:
        return f'<b style="color:{EMAIL_HIGH_COLOR}">{text}</b>'
    if parsed <= EMAIL_PERCENTILE_LOW_THRESHOLD:
        return f'<b style="color:{EMAIL_LOW_COLOR}">{text}</b>'
    return text


def _format_signed_return_cell(value: object) -> str:
    """带符号涨幅单元格(果仁近一月/近一年)。value 为小数(0.045=4.5%),内部 *100。
    中文惯例:正涨红、负跌绿。"""
    parsed = parse_float(value)
    if parsed is None:
        return "-"
    text = escape(format_optional_percent(parsed * 100, decimals=2, strip=False))
    if parsed > 0:
        return f'<b style="color:{EMAIL_HIGH_COLOR}">{text}</b>'
    if parsed < 0:
        return f'<b style="color:{EMAIL_LOW_COLOR}">{text}</b>'
    return text


# ---------- 估值核心 item 区块 ----------


def _render_valuation_spread_row(item: Dict) -> str:
    """股息率 + 股债收益差 + 股债比值法 合并为一行平均分布。"""
    specs: List[Tuple] = []

    dy = parse_float(item.get("index_dividend_yield"))
    if dy is not None:
        dy_pcts = item.get("index_dividend_yield_percentiles") or {}
        dy_avg = parse_float(item.get("index_dividend_yield_average_5y"))
        dy_pct = parse_float(dy_pcts.get("5Y"))
        dy_color, dy_pct_color = _spread_main_color(dy, dy_pct, 0.0)
        dy_main = (
            f'<span style="font-size:22px;font-weight:700;color:{dy_color}">'
            f'{escape(f"{dy:.2f}%")}</span>'
            f'{_archive_html_suffix(item.get("index_dividend_yield_data_source"), item.get("index_dividend_yield_archive_latest_date"))}'
        )
        specs.append(
            ("股息率", dy_main, dy_pct, dy_avg, lambda v: escape(f"{v:.2f}%"), dy_pct_color)
        )

    ebr = parse_float(item.get("equity_bond_ratio"))
    if ebr is not None:
        spread_data = item.get("equity_bond_spread") or {}
        spread_pcts = spread_data.get("percentiles") or {}
        spread_avg = parse_float(spread_data.get("average_5y"))
        spread_pct = parse_float(spread_pcts.get("5Y"))
        spread_color, spread_pct_color = _spread_main_color(ebr, spread_pct, 0.0)
        spread_main = (
            f'<span style="font-size:22px;font-weight:700;color:{spread_color}">'
            f'{escape(_signed_percent(ebr))}</span>'
            f'{_archive_html_suffix(item.get("equity_bond_spread_data_source") or item.get("cn_10y_bond_yield_data_source"), item.get("equity_bond_spread_archive_latest_date") or item.get("cn_10y_bond_yield_archive_latest_date"))}'
        )
        specs.append(
            (
                "股债收益差",
                spread_main,
                spread_pct,
                spread_avg,
                lambda v: escape(_signed_percent(v)),
                spread_pct_color,
            )
        )

        ratio_cur = parse_float(spread_data.get("ratio_current"))
        if ratio_cur is not None:
            ratio_pcts = spread_data.get("ratio_percentiles") or {}
            ratio_avg = parse_float(spread_data.get("ratio_average_5y"))
            ratio_pct = parse_float(ratio_pcts.get("5Y"))
            ratio_color, ratio_pct_color = _spread_main_color(ratio_cur, ratio_pct, 1.0)
            ratio_main = (
                f'<span style="font-size:22px;font-weight:700;color:{ratio_color}">'
                f'{escape(f"{ratio_cur:.2f}x")}</span>'
            )
            specs.append(
                (
                    "股债比值法",
                    ratio_main,
                    ratio_pct,
                    ratio_avg,
                    lambda v: escape(f"{v:.2f}x"),
                    ratio_pct_color,
                )
            )

    if not specs:
        return ""

    width = f"{100 // len(specs)}%"
    cells_html = "".join(
        _render_spread_cell(
            label, main_html, pct_10y, avg_10y, fmt, pct_color,
            border_left=(i > 0), width=width,
        )
        for i, (label, main_html, pct_10y, avg_10y, fmt, pct_color) in enumerate(specs)
    )
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse;margin-top:20px">'
        f"<tr>{cells_html}</tr></table>"
    )


def render_email_item_percentile_block(item: Dict) -> str:
    """单只指数的估值分位区块:标题 + PE/PB 表 + 股息率/股债收益差/比值行。

    无 PE/PB 指标时返回 ""。区块本身不含 PE 分位图 ``<img>`` —— 图由
    ``assemble_email_html`` 紧跟在区块 ``<tr>`` 之后单独成行(cid=equity_bond_{code})。
    """
    th_base = (
        f"padding:8px 10px;background:{EMAIL_BG_TABLE_HEAD};color:{EMAIL_LABEL_COLOR};"
        f"font-weight:500;font-size:11.5px;letter-spacing:0.3px;white-space:nowrap;"
        f"border-bottom:1px solid {EMAIL_BORDER_COLOR}"
    )
    td_label_style = (
        f"padding:8px;border-bottom:1px solid {EMAIL_BORDER_ROW};white-space:nowrap;"
        f"color:{EMAIL_TEXT_PRIMARY};font-weight:600"
    )
    td_num_style = (
        f"padding:8px;text-align:right;border-bottom:1px solid {EMAIL_BORDER_ROW};"
        f"white-space:nowrap;color:{EMAIL_TEXT_PRIMARY}"
    )

    rows_html: List[str] = []
    metrics_to_render = [
        (name, get_index_valuation_metric(item, name)) for name in ("PE(TTM)", "PB(LF)")
    ]
    metrics_to_render = [(n, m) for n, m in metrics_to_render if m]
    for idx, (metric_name, metric) in enumerate(metrics_to_render):
        is_last = idx == len(metrics_to_render) - 1
        label_style = (
            td_label_style
            if not is_last
            else td_label_style.replace(f"border-bottom:1px solid {EMAIL_BORDER_ROW};", "")
        )
        num_style = (
            td_num_style
            if not is_last
            else td_num_style.replace(f"border-bottom:1px solid {EMAIL_BORDER_ROW};", "")
        )
        percentiles = metric.get("percentiles") if isinstance(metric.get("percentiles"), dict) else {}
        current_cell = format_optional_number(metric.get("current"), decimals=2, strip=False)
        if metric_name == "PE(TTM)":
            current_cell += _archive_suffix(
                item.get("index_valuation_data_source"),
                item.get("index_valuation_archive_latest_date"),
            )
        cells = [
            f'<td align="left" style="{label_style}">{escape(metric_name)}</td>',
            f'<td align="right" style="{num_style}">{escape(current_cell)}</td>',
        ]
        for label in EMAIL_PERCENTILE_LABELS:
            cells.append(
                f'<td align="right" style="{num_style}">'
                f"{_format_percentile_cell(percentiles.get(label))}</td>"
            )
        rows_html.append(f'<tr>{"".join(cells)}</tr>')

    if not rows_html:
        return ""

    headers_html = (
        f'<th align="left" style="{th_base};text-align:left;text-transform:uppercase">指标</th>'
        f'<th align="right" style="{th_base};text-align:right;text-transform:uppercase">当前</th>'
        + "".join(
            f'<th align="right" style="{th_base};text-align:right">{escape(label)}</th>'
            for label in EMAIL_PERCENTILE_LABELS
        )
    )

    index_code = str(item.get("index_code") or item.get("code") or "").strip()
    index_name = str(
        item.get("index_name") or item.get("index_short_name") or item.get("name") or ""
    ).strip()

    title_parts = [
        f'<span style="font-size:17px;font-weight:700;color:{EMAIL_TEXT_PRIMARY};'
        f'letter-spacing:-0.2px">{escape(index_name)}</span>'
    ] if index_name else []
    if index_code:
        title_parts.append(
            f'<span style="font-size:12px;color:{EMAIL_LABEL_COLOR};'
            f'margin-left:8px;font-weight:500">{escape(index_code)}</span>'
        )
    title_html = f'<div style="margin-bottom:14px;line-height:1.4">{"".join(title_parts)}</div>'

    table_html = (
        '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch">'
        '<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse;font-size:13px">'
        f"<thead><tr>{headers_html}</tr></thead>"
        f'<tbody>{"".join(rows_html)}</tbody>'
        "</table>"
        "</div>"
    )

    valuation_row = _render_valuation_spread_row(item)
    return title_html + table_html + valuation_row


# ---------- 果仁行业估值 section ----------


def render_guorn_section(
    *,
    industry_rows: Optional[List[Dict[str, Any]]],
    latest_date: Optional[str],
    error_message: Optional[str],
) -> str:
    """果仁行业估值 section,返回 ``<tr>`` 卡片行片段(无数据/出错且无行返回 "")。

    industry_rows 每行字段:ticker/name/PE/PB/PEPB(数值)、PEPercentile/PBPercentile/
    PEPBPercentile(0-1 小数)、month_return/year_return(0-1 小数)。
    """
    def _guorn_sort_key(row: Dict[str, Any]) -> Tuple[int, float]:
        percentile = parse_float(row.get("PBPercentile"))
        if percentile is None:
            return (1, 0.0)
        return (0, percentile)

    title = (
        f'<div style="font-size:18px;font-weight:700;color:{EMAIL_TEXT_PRIMARY}">果仁行业估值</div>'
        f'<div style="font-size:12px;color:{EMAIL_LABEL_COLOR};margin-top:4px">'
        f'数据日期 {escape(str(latest_date or "-"))}</div>'
    )

    if error_message:
        return (
            f'<tr><td style="padding:24px 28px 0 28px">'
            f'<div style="border-top:1px solid {EMAIL_BORDER_CARD_SPLIT};padding-top:24px">'
            f"{title}"
            f'<div style="font-size:12px;color:{EMAIL_MUTED_COLOR};margin-top:10px">'
            f"{escape(error_message)}</div>"
            f"</div></td></tr>"
        )

    rows = sorted(industry_rows or [], key=_guorn_sort_key)
    if not rows:
        return ""

    headers = [
        "序号", "指数代码", "指数名称", "近一月涨幅", "近一年涨幅",
        "PE", "PE5年分位点", "PB", "PB5年分位点", "PEXPB", "PEXPB5年分位点",
    ]
    header_html = "".join(
        f'<th style="padding:8px 10px;border-bottom:1px solid {EMAIL_BORDER_CARD_SPLIT};'
        f'text-align:left;font-size:12px;color:{EMAIL_LABEL_COLOR};white-space:nowrap">{escape(label)}</th>'
        for label in headers
    )

    body_rows = []
    for idx, row in enumerate(rows, start=1):
        month_return = parse_float(row.get("month_return"))
        year_return = parse_float(row.get("year_return"))
        pe_percentile = parse_float(row.get("PEPercentile"))
        pb_percentile = parse_float(row.get("PBPercentile"))
        pepb_percentile = parse_float(row.get("PEPBPercentile"))
        row_background = "#ffffff" if idx % 2 == 1 else EMAIL_BORDER_ROW
        cells = [
            str(idx),
            str(row.get("ticker") or "-"),
            str(row.get("name") or "-"),
            _format_signed_return_cell(month_return),
            _format_signed_return_cell(year_return),
            format_optional_number(row.get("PE"), decimals=2, strip=False),
            _format_percentile_cell(pe_percentile * 100) if pe_percentile is not None else "-",
            format_optional_number(row.get("PB"), decimals=2, strip=False),
            _format_percentile_cell(pb_percentile * 100) if pb_percentile is not None else "-",
            format_optional_number(row.get("PEPB"), decimals=2, strip=False),
            _format_percentile_cell(pepb_percentile * 100) if pepb_percentile is not None else "-",
        ]
        body_rows.append(
            f'<tr style="background:{row_background}">'
            + "".join(
                f'<td style="padding:8px 10px;border-bottom:1px solid {EMAIL_BORDER_CARD_SPLIT};'
                f'font-size:12px;color:{EMAIL_TEXT_PRIMARY};white-space:nowrap">{cell}</td>'
                for cell in cells
            )
            + "</tr>"
        )

    table_html = (
        f'<div style="margin-top:14px;overflow-x:auto">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;border-collapse:collapse">'
        f"<thead><tr>{header_html}</tr></thead>"
        f'<tbody>{"".join(body_rows)}</tbody>'
        f"</table></div>"
    )

    return (
        f'<tr><td style="padding:24px 28px 0 28px">'
        f'<div style="border-top:1px solid {EMAIL_BORDER_CARD_SPLIT};padding-top:24px">'
        f"{title}{table_html}"
        f"</div></td></tr>"
    )


# ---------- 汇率图 section ----------


def render_fx_chart_section(fx_chart_path: Optional[Path]) -> str:
    """美元人民币汇率图 section,返回 ``<tr>`` 卡片行片段(无图返回 "")。"""
    if not fx_chart_path:
        return ""
    return (
        f'<tr><td style="padding:24px 28px 0 28px">'
        f'<div style="border-top:1px solid {EMAIL_BORDER_CARD_SPLIT};padding-top:24px">'
        f'<div style="font-size:18px;font-weight:700;color:{EMAIL_TEXT_PRIMARY}">美元人民币汇率走势</div>'
        f'<div style="padding:14px 0 0 0">'
        f'<img src="cid:{escape(FX_CHART_CID)}" alt="美元人民币汇率对比图" '
        f'style="width:100%;max-width:100%;height:auto;display:block">'
        f"</div>"
        f"</div></td></tr>"
    )


# ---------- 邮件卡片组装 ----------


def _build_global_info(
    now_str: str, valuation_date: str, bond_yield: Optional[float]
) -> str:
    info_rows: List[str] = []
    time_bits = [
        f'<span style="color:{EMAIL_LABEL_COLOR}">触发时间</span>'
        f' <b style="color:{EMAIL_TEXT_PRIMARY};margin-left:6px">{now_str}</b>'
    ]
    if valuation_date:
        time_bits.append(
            f'<span style="color:{EMAIL_LABEL_COLOR}">估值基准日</span>'
            f' <b style="color:{EMAIL_TEXT_PRIMARY};margin-left:6px">{escape(valuation_date)}</b>'
        )
    if bond_yield is not None:
        time_bits.append(
            f'<span style="color:{EMAIL_LABEL_COLOR}">10Y国债</span>'
            f' <b style="color:{EMAIL_TEXT_PRIMARY};margin-left:6px">{bond_yield:.2f}%</b>'
        )
    sep = f'<span style="color:#d2d2d7;margin:0 10px">|</span>'
    info_rows.append(f"<div>{sep.join(time_bits)}</div>")
    info_rows.append(
        f'<div style="margin-top:6px">'
        f'<span style="color:{EMAIL_LABEL_COLOR}">分位提示</span>'
        f'<span style="display:inline-block;width:8px;height:8px;background:{EMAIL_HIGH_COLOR};'
        f'border-radius:50%;margin:0 6px 0 8px"></span>高估 ≥ {int(EMAIL_PERCENTILE_HIGH_THRESHOLD)}%'
        f'<span style="display:inline-block;width:8px;height:8px;background:{EMAIL_LOW_COLOR};'
        f'border-radius:50%;margin:0 6px 0 16px"></span>低估 ≤ {int(EMAIL_PERCENTILE_LOW_THRESHOLD)}%'
        f"</div>"
    )
    info_rows.append(
        f'<div style="margin-top:6px;color:{EMAIL_LABEL_COLOR};font-size:11.5px">'
        f"公式 · 股债收益差 = 1/PE − 10Y国债 &nbsp;·&nbsp; "
        f"股债比值 = (1/PE) ÷ 10Y国债"
        f"</div>"
    )
    info_rows.append(
        f'<div style="margin-top:6px">'
        f'<span style="color:{EMAIL_LABEL_COLOR}">配色</span>'
        f" · PE(TTM) / PB(LF)"
        f'<span style="display:inline-block;width:8px;height:8px;background:{EMAIL_HIGH_COLOR};'
        f'border-radius:50%;margin:0 6px 0 10px"></span>≥ {int(EMAIL_PERCENTILE_HIGH_THRESHOLD)}% 偏贵'
        f'<span style="display:inline-block;width:8px;height:8px;background:{EMAIL_LOW_COLOR};'
        f'border-radius:50%;margin:0 6px 0 14px"></span>≤ {int(EMAIL_PERCENTILE_LOW_THRESHOLD)}% 偏便宜'
        f"</div>"
    )
    info_rows.append(
        f'<div style="margin-top:2px">'
        f'<span style="color:{EMAIL_LABEL_COLOR}">　　</span>'
        f" · 股息率 / 股债收益差 / 股债比值"
        f'<span style="display:inline-block;width:8px;height:8px;background:{EMAIL_LOW_COLOR};'
        f'border-radius:50%;margin:0 6px 0 10px"></span>≥ {int(EMAIL_PERCENTILE_HIGH_THRESHOLD)}% 价值高'
        f'<span style="display:inline-block;width:8px;height:8px;background:{EMAIL_HIGH_COLOR};'
        f'border-radius:50%;margin:0 6px 0 14px"></span>≤ {int(EMAIL_PERCENTILE_LOW_THRESHOLD)}% 价值低'
        f"</div>"
    )
    return (
        f'<div style="background:{EMAIL_BG_PAGE};border-radius:10px;padding:14px 16px;'
        f'font-size:12.5px;color:{EMAIL_MUTED_COLOR};line-height:1.75">'
        + "".join(info_rows)
        + "</div>"
    )


def assemble_email_html(
    *,
    current_time: Optional[datetime] = None,
    valuation_items: Optional[List[Dict]] = None,
    chart_paths: Optional[Dict[str, Path]] = None,
    extra_sections: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, str]]:
    """组装市场估值邮件完整 HTML(卡片布局)。

    参数:
        current_time: 触发时间(None 取北京时间现在)。
        valuation_items: 估值核心的指数 item 列表(来自 ``fetch.fetch_target_index_metrics``
            + ``metrics.attach_equity_bond_*``)。从中提取估值基准日 / 10Y 国债,并渲染每个
            item 的 PE/PB 分位区块 + 紧随的 PE 分位图。
        chart_paths: ``{index_code: PE 分位图 Path}``,由 ``run.py`` 经
            ``charts.generate_valuation_percentile_chart`` 生成。有图则在该 item 区块后插
            ``<img src="cid:equity_bond_{code}">``。
        extra_sections: 额外 ``<tr>`` 卡片行片段列表(高股息/果仁/风格轮动/汇率图),按
            传入顺序追加在估值核心之后、页脚之前。

    返回 ``(html, inline_images)``。``inline_images`` 仅含估值核心的 per-item PE 分位图
    (cid=``equity_bond_{code}``);汇率图/风格轮动/果仁/高股息等额外 section 的内嵌图由
    调用方(``run.py``)各自累积后与本次返回值合并,再传给 ``common.email.send_email``。
    """
    now_str = escape((current_time or now_in_beijing()).strftime("%Y-%m-%d %H:%M"))
    items = list(valuation_items or [])
    chart_paths = chart_paths or {}

    valuation_date = ""
    bond_yield: Optional[float] = None
    for it in items:
        vd = str(it.get("index_valuation_date") or "").strip()
        if vd and not valuation_date:
            valuation_date = vd
        by = parse_float(it.get("cn_10y_bond_yield"))
        if by is not None and bond_yield is None:
            bond_yield = by

    global_info = _build_global_info(now_str, valuation_date, bond_yield)

    blocks: List[str] = []
    inline_images: Dict[str, str] = {}
    for item in items:
        block = render_email_item_percentile_block(item)
        if not block:
            continue
        blocks.append(block)
        code = str(item.get("index_code") or item.get("code") or "").strip()
        chart_path = chart_paths.get(code) if code else None
        if chart_path:
            inline_images[equity_bond_chart_cid(code)] = str(chart_path)

    divider = (
        f'<tr><td style="padding:24px 28px 0 28px">'
        f'<div style="height:1px;background:{EMAIL_BORDER_CARD_SPLIT};line-height:0;font-size:0">&nbsp;</div>'
        f"</td></tr>"
    )

    card_rows: List[str] = []
    for i, block in enumerate(blocks):
        card_rows.append(f'<tr><td style="padding:24px 28px 0 28px">{block}</td></tr>')
        code = str(items[i].get("index_code") or items[i].get("code") or "").strip()
        chart_path = chart_paths.get(code) if code else None
        if chart_path:
            card_rows.append(
                f'<tr><td style="padding:14px 0 0 0">'
                f'<img src="cid:{escape(equity_bond_chart_cid(code))}" alt="估值分位走势图" '
                f'style="width:100%;max-width:100%;height:auto;display:block">'
                f"</td></tr>"
            )
        if i < len(blocks) - 1:
            card_rows.append(divider)

    for section in extra_sections or []:
        if section:
            card_rows.append(section)

    footer = (
        f'<tr><td style="padding:28px 28px 22px 28px;border-top:1px solid {EMAIL_BORDER_CARD_SPLIT};margin-top:24px">'
        f'<div style="font-size:11px;color:{EMAIL_LABEL_COLOR};text-align:center;line-height:1.6">'
        f"数据来源 · 易方达估值中心 &nbsp;·&nbsp; AKShare 国债收益率<br>"
        f"本邮件由 GitHub Actions 自动发送,非投资建议"
        f"</div></td></tr>"
    )

    html = (
        "<!doctype html>"
        '<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>指数估值监控</title></head>"
        f'<body style="margin:0;padding:0;background:{EMAIL_BG_PAGE};'
        f"font-family:{EMAIL_FONT_STACK}\">"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="background:{EMAIL_BG_PAGE}">'
        '<tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="background:#ffffff;border-radius:12px;overflow:hidden">'
        f'<tr><td style="padding:22px 28px 16px 28px;border-bottom:1px solid {EMAIL_BORDER_CARD_SPLIT}">'
        f'<div style="font-size:20px;font-weight:700;color:{EMAIL_TEXT_PRIMARY};letter-spacing:-0.2px">'
        f"📊 指数估值监控</div>"
        f'<div style="font-size:12px;color:{EMAIL_LABEL_COLOR};margin-top:2px">'
        f"Daily Valuation Digest</div>"
        f"</td></tr>"
        f'<tr><td style="padding:14px 28px 0 28px">{global_info}</td></tr>'
        + "".join(card_rows)
        + footer
        + "</table></td></tr></table></body></html>"
    )
    return html, inline_images
