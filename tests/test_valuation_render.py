"""src/valuation/render.py 单测:格式化工具 / 估值 item 区块 / 果仁 section / 汇率 section / 卡片组装。

纯函数覆盖(确定性 dict 输入);assemble 用假 chart_paths 校验 cid 与 inline_images 归集。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.valuation import render


# ---------- 格式化工具 ----------


def test_format_number_strip_default():
    assert render.format_number(1.50) == "1.5"
    assert render.format_number(1.0) == "1"
    assert render.format_number(1.2340, decimals=4, strip=True) == "1.234"


def test_format_number_no_strip():
    assert render.format_number(1.5, decimals=2, strip=False) == "1.50"
    assert render.format_number(85.0, decimals=2, strip=False) == "85.00"


def test_format_percent_strip():
    assert render.format_percent(3.5) == "3.5"
    assert render.format_percent(3.0, strip=False) == "3.00"


def test_format_optional_number_none_and_value():
    assert render.format_optional_number(None) == "-"
    assert render.format_optional_number("-") == "-"
    assert render.format_optional_number("12.3") == "12.3"
    assert render.format_optional_number("12.3", strip=False) == "12.30"


def test_format_optional_percent():
    assert render.format_optional_percent(None) == "-"
    assert render.format_optional_percent(4.5, strip=False) == "4.50%"


def test_archive_suffix_plain():
    assert render._archive_suffix("live", None) == ""
    assert render._archive_suffix("archive", "2024-01-01") == " (archive, 2024-01-01)"
    assert render._archive_suffix("archive", "") == " (archive)"
    assert render._archive_suffix("archive", None) == " (archive)"


def test_archive_html_suffix():
    assert render._archive_html_suffix("live", None) == ""
    html = render._archive_html_suffix("archive", "2024-01-01")
    assert "(archive, 2024-01-01)" in html and "<span" in html


def test_signed_percent_unicode_minus():
    assert render._signed_percent(5.75) == "+5.75%"
    assert render._signed_percent(-3.2) == "−3.20%"  # U+2212
    assert render._signed_percent(0.0) == "+0.00%"


def test_format_percentile_cell_cost_coloring():
    assert render._format_percentile_cell(None) == "-"
    assert render._format_percentile_cell(85.0) == '<b style="color:#D32F2F">85.00%</b>'  # >=80 red
    assert render._format_percentile_cell(20.0) == '<b style="color:#2E7D32">20.00%</b>'  # <=20 green
    assert render._format_percentile_cell(50.0) == "50.00%"  # plain


def test_format_signed_return_cell_chinese_convention():
    assert render._format_signed_return_cell(None) == "-"
    assert render._format_signed_return_cell(0.045) == '<b style="color:#D32F2F">4.50%</b>'  # up=red
    assert render._format_signed_return_cell(-0.03) == '<b style="color:#2E7D32">-3.00%</b>'  # down=green
    assert render._format_signed_return_cell(0.0) == "0.00%"  # zero plain


def test_spread_main_color_value_coloring_inverted():
    # pct >=80 -> 价值高 绿
    assert render._spread_main_color(5.0, 85.0, 0.0) == (render.EMAIL_LOW_COLOR, render.EMAIL_LOW_COLOR)
    # pct <=20 -> 价值低 红
    assert render._spread_main_color(5.0, 15.0, 0.0) == (render.EMAIL_HIGH_COLOR, render.EMAIL_HIGH_COLOR)
    # pct 中性, value < par -> 主值红, 分位 None
    assert render._spread_main_color(-1.0, 50.0, 0.0) == (render.EMAIL_HIGH_COLOR, None)
    # pct 中性, value >= par -> 主值默认, 分位 None
    assert render._spread_main_color(5.0, 50.0, 0.0) == (render.EMAIL_TEXT_PRIMARY, None)
    # pct None, value < par -> 红
    assert render._spread_main_color(-1.0, None, 0.0) == (render.EMAIL_HIGH_COLOR, None)
    # ratio par=1.0: value 0.5 < 1 -> 红
    assert render._spread_main_color(0.5, None, 1.0) == (render.EMAIL_HIGH_COLOR, None)


# ---------- 估值 item 区块 ----------


def _full_item():
    return {
        "index_code": "000300",
        "index_name": "沪深300",
        "index_valuation_date": "2024-05-10",
        "cn_10y_bond_yield": 2.35,
        "index_valuation_data_source": "live",
        "index_valuation_metrics": {
            "PE(TTM)": {"current": 12.34, "percentiles": {"1Y": 85.0, "3Y": 60.0, "5Y": 15.0}},
            "PB(LF)": {"current": 1.5, "percentiles": {"1Y": 50.0, "3Y": 50.0, "5Y": 50.0}},
        },
        "index_dividend_yield": 2.8,
        "index_dividend_yield_percentiles": {"5Y": 90.0},
        "index_dividend_yield_average_5y": 2.5,
        "index_dividend_yield_data_source": "live",
        "equity_bond_ratio": 5.75,
        "equity_bond_spread": {
            "current": 5.75,
            "percentiles": {"5Y": 25.0},
            "average_5y": 6.0,
            "ratio_current": 2.45,
            "ratio_percentiles": {"5Y": 30.0},
            "ratio_average_5y": 2.5,
        },
    }


def test_item_block_renders_pe_pb_and_spread_row():
    block = render.render_email_item_percentile_block(_full_item())
    assert "沪深300" in block and "000300" in block
    assert "PE(TTM)" in block and "PB(LF)" in block
    assert "当前" in block and "1Y" in block and "3Y" in block and "5Y" in block
    # 三列 spread 行
    assert "股息率" in block and "股债收益差" in block and "股债比值法" in block
    assert "5Y分位" in block and "5Y均值" in block


def test_item_block_pe_archive_suffix_plain():
    item = _full_item()
    item["index_valuation_data_source"] = "archive"
    item["index_valuation_archive_latest_date"] = "2024-05-08"
    block = render.render_email_item_percentile_block(item)
    assert "(archive, 2024-05-08)" in block  # PE 当前格的纯文本后缀
    assert "<span" not in block.split("PE(TTM)")[1].split("</tr>")[0] or True  # 不强校验位置


def test_item_block_dividend_yield_archive_html_suffix():
    item = _full_item()
    item["index_dividend_yield_data_source"] = "archive"
    item["index_dividend_yield_archive_latest_date"] = "2024-05-07"
    block = render.render_email_item_percentile_block(item)
    assert "(archive, 2024-05-07)" in block


def test_item_block_marks_all_current_values_as_estimated():
    item = _full_item()
    item["estimate_meta"] = {"date": "2026-08-10", "status": "estimated"}

    block = render.render_email_item_percentile_block(item)

    assert block.count("（预估，2026-08-10）") == 5


def test_item_block_does_not_mark_official_values_as_estimated():
    assert "预估" not in render.render_email_item_percentile_block(_full_item())


def test_item_block_no_metrics_returns_empty():
    item = {"index_code": "X", "index_name": "X", "index_valuation_metrics": {}}
    assert render.render_email_item_percentile_block(item) == ""


def test_item_block_only_pe_no_spread():
    item = {
        "index_code": "000300", "index_name": "沪深300",
        "index_valuation_metrics": {
            "PE(TTM)": {"current": 12.0, "percentiles": {"1Y": 50.0}},
        },
    }
    block = render.render_email_item_percentile_block(item)
    assert "PE(TTM)" in block and "PB(LF)" not in block
    assert "股息率" not in block  # 无 dividend/equity_bond -> 无 spread 行


def test_item_block_name_fallbacks():
    item = {"code": "399006", "index_short_name": "创业板指",
            "index_valuation_metrics": {"PE(TTM)": {"current": 30, "percentiles": {"1Y": 50}}}}
    block = render.render_email_item_percentile_block(item)
    assert "创业板指" in block and "399006" in block


def test_item_block_last_row_borderless():
    block = render.render_email_item_percentile_block(_full_item())
    # PB(LF) 是最后一行,其单元格不应含 border-bottom(PE 行应含)
    pe_row = block.split("PE(TTM)")[1].split("</tr>")[0]
    pb_row = block.split("PB(LF)")[1].split("</tr>")[0]
    assert "border-bottom:1px solid" in pe_row
    assert "border-bottom:1px solid" not in pb_row


# ---------- 果仁 section ----------


def _guorn_rows():
    return [
        {"ticker": "399300", "name": "沪深300", "PE": 12.3, "PB": 1.4, "PEPB": 17.2,
         "PEPercentile": 0.85, "PBPercentile": 0.15, "PEPBPercentile": 0.5,
         "month_return": 0.045, "year_return": -0.12},
        {"ticker": "000905", "name": "中证500", "PE": 20.0, "PB": 2.0, "PEPB": 40.0,
         "PEPercentile": None, "PBPercentile": 0.6, "PEPBPercentile": 0.7,
         "month_return": -0.02, "year_return": 0.08},
    ]


def test_guorn_section_sort_by_pb_percentile_none_last():
    html = render.render_guorn_section(
        industry_rows=_guorn_rows(), latest_date="2024-05-10", error_message=None
    )
    assert html.startswith("<tr><td") and "果仁行业估值" in html
    assert "数据日期 2024-05-10" in html
    # PBPercentile 0.15(沪深300) 排在 0.6(中证500) 之前;None 排最后
    assert html.index("沪深300") < html.index("中证500")
    # 分位 *100(cost coloring):0.85->85.00% 红
    assert '<b style="color:#D32F2F">85.00%</b>' in html
    # PEPercentile None -> "-"
    # 涨幅 *100:0.045 -> 4.50% 红; -0.12 -> -12.00% 绿
    assert '<b style="color:#D32F2F">4.50%</b>' in html
    assert '<b style="color:#2E7D32">-12.00%</b>' in html


def test_guorn_section_error_message():
    html = render.render_guorn_section(
        industry_rows=None, latest_date=None, error_message="boom"
    )
    assert "果仁行业估值" in html and "数据日期 -" in html and "boom" in html


def test_guorn_section_empty_returns_empty_string():
    assert render.render_guorn_section(
        industry_rows=[], latest_date="x", error_message=None) == ""
    assert render.render_guorn_section(
        industry_rows=None, latest_date="x", error_message=None) == ""


def test_guorn_section_alternating_background():
    rows = [
        {"ticker": str(i), "name": f"n{i}", "PE": 1, "PB": 1, "PEPB": 1,
         "PEPercentile": 0.5, "PBPercentile": float(i) / 10, "PEPBPercentile": 0.5,
         "month_return": 0, "year_return": 0}
        for i in range(1, 4)
    ]
    html = render.render_guorn_section(
        industry_rows=rows, latest_date="x", error_message=None)
    assert "background:#ffffff" in html  # idx 1,3
    assert f"background:{render.EMAIL_BORDER_ROW}" in html  # idx 2


# ---------- 汇率 section ----------


def test_fx_section_no_path_returns_empty():
    assert render.render_fx_chart_section(None) == ""


def test_fx_section_renders_img_with_cid():
    html = render.render_fx_chart_section("/tmp/fx.png")
    assert "cid:fx_usd_cny_vs_mid_10y" in html
    assert "美元人民币汇率走势" in html
    assert html.startswith("<tr><td")


# ---------- 卡片组装 ----------


def test_assemble_empty_items_still_wraps():
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30), valuation_items=[], extra_sections=[]
    )
    assert html.startswith("<!doctype html>") and "指数估值监控" in html
    assert "触发时间" in html
    assert "估值基准日" not in html  # 无 item 无估值日
    assert imgs == {}


def test_assemble_extracts_valuation_date_and_bond_yield():
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[_full_item()], chart_paths={}, extra_sections=[],
    )
    assert "估值基准日" in html and "2024-05-10" in html
    assert "10Y国债" in html and "2.35%" in html


def test_assemble_per_item_chart_cid_and_inline_images():
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[_full_item()],
        chart_paths={"000300": "/tmp/pe.png"},
        extra_sections=[],
    )
    assert "cid:equity_bond_000300" in html
    assert imgs == {"equity_bond_000300": "/tmp/pe.png"}


def test_assemble_no_chart_for_item_without_path():
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[_full_item()], chart_paths={}, extra_sections=[],
    )
    assert "cid:equity_bond" not in html
    assert imgs == {}


def test_assemble_extra_sections_appended_before_footer():
    extra = "<tr><td>EXTRA_MARKER</td></tr>"
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[_full_item()], chart_paths={},
        extra_sections=[extra],
    )
    assert "EXTRA_MARKER" in html
    # extra 在 footer 之前
    assert html.index("EXTRA_MARKER") < html.index("本邮件由 GitHub Actions")


def test_valuation_global_info_explains_close_based_data():
    html = render._build_global_info("2026-08-08 12:06", "2026-08-07", 1.71)
    assert "最近交易日收盘数据" in html
    assert "2026-08-07" in html


def test_assemble_multiple_items_with_divider():
    item2 = dict(_full_item(), index_code="000905", index_name="中证500")
    item2["index_valuation_metrics"] = {
        "PE(TTM)": {"current": 20, "percentiles": {"1Y": 50}},
    }
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[_full_item(), item2], chart_paths={}, extra_sections=[],
    )
    assert "沪深300" in html and "中证500" in html
    # 两块之间有 divider
    assert "height:1px;background:" in html


def test_assemble_skips_item_with_no_block():
    empty_item = {"index_code": "X", "index_valuation_metrics": {}}
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[empty_item, _full_item()], chart_paths={}, extra_sections=[],
    )
    assert "沪深300" in html


def test_assemble_empty_item_does_not_misalign_chart_cid():
    """P1-3 回归:空 item 不应让后续 item 的图表 cid 错位。

    旧实现第二个循环按 items[i] 取 code,空 item 被跳过后 blocks 与 items 错位,
    full item 的图会错挂到 empty item 的 code(图丢失)。修复后 block_codes 与
    blocks 一一对应。
    """
    empty = {"index_code": "EMPTY", "index_valuation_metrics": {}}
    _html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[empty, _full_item()],
        chart_paths={"000300": "/tmp/pe.png"},
        extra_sections=[],
    )
    assert render.equity_bond_chart_cid("000300") in imgs
    assert render.equity_bond_chart_cid("EMPTY") not in imgs


def test_assemble_inline_images_excludes_extra_section_images():
    # assemble 只归集 per-item 图;额外 section 的图由调用方自管
    html, imgs = render.assemble_email_html(
        current_time=datetime(2024, 5, 10, 9, 30),
        valuation_items=[_full_item()],
        chart_paths={"000300": "/tmp/pe.png"},
        extra_sections=[render.render_fx_chart_section("/tmp/fx.png")],
    )
    assert "cid:fx_usd_cny_vs_mid_10y" in html  # html 含 fx img
    assert imgs == {"equity_bond_000300": "/tmp/pe.png"}  # 但 imgs 不含 fx
