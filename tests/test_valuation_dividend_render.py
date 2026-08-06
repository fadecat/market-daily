"""src/valuation/dividend/render.py 单测:分组 / 行渲染 / 规则文案 / 主表 / build_section。

纯函数覆盖(确定性 data dict);build_section 用 monkeypatch mock fetch_data /
prepare_dividend_email_data / get_cookie / notify_alert,不触网。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.valuation.dividend import render as dr


# ---------- 数据 fixture ----------


def _cell(stock_id, stock_nm, industry, pe, pb, dividend, ttm=1000, bonds=None):
    return {
        "stock_id": stock_id, "stock_nm": stock_nm, "industry_nm": industry,
        "price": "7.5", "dividend_rate": dividend, "pe": pe, "pb": pb, "roe": "12",
        "ttm_parent_net_profit_yi": ttm,
        "linked_bonds": bonds or [], "linked_bonds_fetch_failed": False,
    }


def _two_group_data():
    return {
        "rows": [
            {"cell": _cell("600000", "浦发银行", "银行", "4.5", "0.5", "5.2")},
            {"cell": _cell("601398", "工商银行", "银行", "5.0", "0.6", "6.0")},
            {"cell": _cell("601666", "平煤股份", "煤炭", "8.0", "1.0", "4.0")},
        ],
        "raw_returned_count": 10,
        "filter_steps": [],
    }


# ---------- build_dividend_display_groups ----------


def test_groups_grouping_and_within_group_sort():
    groups = dr.build_dividend_display_groups(_two_group_data())
    assert groups["total_count"] == 3
    assert groups["shown_count"] == 3
    assert len(groups["groups"]) == 2
    # 组间按 avg_pb 升序:银行(0.55) < 煤炭(1.0)
    assert groups["groups"][0]["industry_name"] == "银行"
    assert groups["groups"][1]["industry_name"] == "煤炭"
    # 银行组内按 (pb, pe, -div, code):浦发(0.5) 在工商(0.6) 前
    bank = groups["groups"][0]
    assert bank["rows"][0]["cell"]["stock_id"] == "600000"
    assert bank["rows"][1]["cell"]["stock_id"] == "601398"


def test_groups_leaders_avg_metrics():
    groups = dr.build_dividend_display_groups(_two_group_data())
    bank = groups["groups"][0]
    # leaders = 浦发+工商;avg_pb=(0.5+0.6)/2=0.55, avg_pe=(4.5+5.0)/2=4.75, avg_div=(5.2+6.0)/2=5.6
    assert bank["industry_avg_pb"] == pytest.approx(0.55)
    assert bank["industry_avg_pe"] == pytest.approx(4.75)
    assert bank["industry_avg_dividend_rate"] == pytest.approx(5.6)
    assert bank["industry_count"] == 2


def test_groups_pe_pb_ranks():
    groups = dr.build_dividend_display_groups(_two_group_data())
    bank = groups["groups"][0]
    pufa = bank["rows"][0]
    gongshang = bank["rows"][1]
    assert pufa["pe_rank"] == 1 and pufa["pb_rank"] == 1
    assert gongshang["pe_rank"] == 2 and gongshang["pb_rank"] == 2
    assert pufa["valuation_score"] == 2  # pe_rank + pb_rank
    assert pufa["valuation_tiebreak"] == 1  # max(pe_rank, pb_rank)


def test_groups_missing_pe_pb_sinks_via_inf():
    data = {
        "rows": [
            {"cell": _cell("600000", "浦发", "银行", None, "0.5", "5.2")},
            {"cell": _cell("601398", "工商", "银行", "5.0", "0.6", "6.0")},
        ],
        "raw_returned_count": 2, "filter_steps": [],
    }
    groups = dr.build_dividend_display_groups(data)
    bank = groups["groups"][0]
    # pe=None -> inf,pe_rank=2;但 pb=0.5 仍最小,组内排序 pb 优先 -> 浦发仍第一
    assert bank["rows"][0]["cell"]["stock_id"] == "600000"
    assert bank["rows"][0]["pe_value"] == float("inf")
    assert bank["rows"][0]["pe_rank"] == 2


def test_groups_max_show_truncation():
    groups = dr.build_dividend_display_groups(_two_group_data(), max_show=1)
    assert groups["total_count"] == 3  # 总数不变
    assert groups["shown_count"] == 1
    assert len(groups["groups"][0]["rows"]) == 1
    assert groups["groups"][0]["rows"][0]["cell"]["stock_id"] == "600000"


def test_groups_single_leader_avg():
    # 煤炭组仅 1 行,leaders=1 行,avg=自身
    groups = dr.build_dividend_display_groups(_two_group_data())
    coal = groups["groups"][1]
    assert coal["industry_avg_pb"] == pytest.approx(1.0)
    assert coal["industry_avg_pe"] == pytest.approx(8.0)


def test_groups_empty_data():
    groups = dr.build_dividend_display_groups({"rows": [], "raw_returned_count": 0, "filter_steps": []})
    assert groups["total_count"] == 0
    assert groups["groups"] == []


# ---------- build_rule_msg ----------


def test_build_rule_msg_constant_lines_with_full_width_punctuation():
    data = {"rows": [], "raw_returned_count": 0, "filter_steps": []}
    msg = dr.build_rule_msg(data)
    assert "**📋 高股息筛选规则**" in msg
    # 全角冒号/逗号(与旧仓库一致)
    assert "集思录条件：PE ≤ 15，股息率 ≥ 3%" in msg
    assert "集思录条件：平均ROE ≥ 5%，总市值 200~ 无限制" in msg


def test_build_rule_msg_extends_filter_summary():
    data = {
        "rows": [], "raw_returned_count": 100, "filter_steps": [
            {"step_name": "行业排除", "rule_text": "剔除基建", "before_count": 100,
             "after_count": 80, "excluded_count": 20, "excluded_rows": []},
        ],
    }
    msg = dr.build_rule_msg(data)
    assert "集思录返回 100 只" in msg
    assert "行业排除:剔除基建" in msg
    assert "剩余 80 只" in msg


# ---------- _dividend_email_row / format_linked_bonds_html ----------


def test_dividend_email_row_eleven_cells_with_colored_dividend():
    row = {"cell": _cell("600000", "浦发银行", "银行", "4.5", "0.5", "5.2",
                          bonds=[{"bond_id": "113000", "bond_nm": "浦发转债"}])}
    spec = dr._dividend_email_row(1, row)
    assert len(spec["cells"]) == 11
    assert spec["cells"][0] == "1"
    assert spec["cells"][1] == "银行"
    assert spec["cells"][2] == "浦发银行"
    assert spec["cells"][3] == "600000"
    assert spec["cells"][5] == '<span style="color:#D93026">5.2%</span>'
    assert spec["cells"][6] == "4.5"
    assert "浦发转债" in spec["cells"][10]


def test_format_linked_bonds_html_wrapper():
    cell = {"linked_bonds": [{"bond_id": "113000", "bond_nm": "X转"}], "linked_bonds_fetch_failed": False}
    assert "X转" in dr.format_linked_bonds_html(cell)
    cell2 = {"linked_bonds": [], "linked_bonds_fetch_failed": True}
    # 失败 -> 查询失败文案(supplement.LINKED_BONDS_FETCH_FAILED_TEXT)
    assert "查询失败" in dr.format_linked_bonds_html(cell2)


# ---------- build_dividend_email_html ----------


def test_build_dividend_email_html_with_rows():
    data = _two_group_data()
    parts = dr.build_dividend_email_html(data)
    # 顺序:supplement([]) + rule + header + table
    assert any("高股息筛选规则" in p for p in parts)
    assert any("集思录高股息筛选" in p for p in parts)
    assert any("浦发银行" in p for p in parts)  # 主表
    # raw=10, total=3 -> "筛选后剩余 3 只"
    assert any("筛选后剩余 3 只" in p for p in parts)


def test_build_dividend_email_html_no_filtered_remainder_when_equal():
    data = _two_group_data()
    data["raw_returned_count"] = 3  # == total
    parts = dr.build_dividend_email_html(data)
    assert not any("筛选后剩余" in p for p in parts)


def test_build_dividend_email_html_empty_rows():
    data = {"rows": [], "raw_returned_count": 0, "filter_steps": []}
    parts = dr.build_dividend_email_html(data)
    assert any("暂无符合条件的股票数据" in p for p in parts)
    # 无主表 render_table
    assert not any("浦发银行" in p for p in parts)


def test_build_dividend_email_html_group_alternating_styles():
    data = {
        "rows": [
            {"cell": _cell("600000", "A", "银行", "4", "0.5", "5")},
            {"cell": _cell("601398", "B", "银行", "5", "0.6", "6")},
            {"cell": _cell("601666", "C", "煤炭", "8", "1.0", "4")},
            {"cell": _cell("601888", "D", "煤炭", "9", "1.1", "3")},
        ],
        "raw_returned_count": 4, "filter_steps": [],
    }
    parts = dr.build_dividend_email_html(data)
    table_html = [p for p in parts if "<table" in p][0]
    # 两组交替底色 + 每组首行 border-top
    assert "background:#FBFCFE" in table_html
    assert "background:#F7FBF8" in table_html
    assert "border-top:2px solid #dfe5ec" in table_html


def test_wrap_dividend_card_row():
    wrapped = dr._wrap_dividend_card_row(["<div>A</div>", "<div>B</div>"])
    assert wrapped.startswith("<tr><td")
    assert "border-top:1px solid" in wrapped
    assert "<div>A</div><div>B</div>" in wrapped


# ---------- build_section ----------


def test_build_section_success(monkeypatch, tmp_path):
    data = _two_group_data()
    monkeypatch.setattr(dr, "fetch_data", lambda cookie=None: data)
    monkeypatch.setattr(dr, "prepare_dividend_email_data", lambda d, cookie=None: d)
    monkeypatch.setattr(dr, "get_cookie", lambda: "should_not_be_called")
    result = dr.build_section(tmp_path, cookie="shared_cookie")
    assert result is not None
    assert result["html"].startswith("<tr><td")
    assert result["inline_images"] == {}
    assert result["as_of_date"]  # YYYY-MM-DD


def test_build_section_cookie_none_calls_get_cookie_once(monkeypatch, tmp_path):
    data = _two_group_data()
    captured = []

    def fake_fetch(cookie=None):
        captured.append(("fetch", cookie))
        return data

    def fake_prep(d, cookie=None):
        captured.append(("prep", cookie))
        return d

    cookie_calls = []
    monkeypatch.setattr(dr, "fetch_data", fake_fetch)
    monkeypatch.setattr(dr, "prepare_dividend_email_data", fake_prep)
    monkeypatch.setattr(dr, "get_cookie", lambda: cookie_calls.append(1) or "single_login")
    result = dr.build_section(tmp_path)  # cookie=None
    assert result is not None
    assert len(cookie_calls) == 1  # 仅一次登录
    # 同一 cookie 传给 fetch 与 prep
    assert captured[0] == ("fetch", "single_login")
    assert captured[1] == ("prep", "single_login")


def test_build_section_fetch_failure_returns_none_and_alerts(monkeypatch, tmp_path):
    alerts = []
    monkeypatch.setattr(dr, "fetch_data", lambda cookie=None: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(dr, "prepare_dividend_email_data", lambda d, cookie=None: d)
    monkeypatch.setattr(dr, "notify_alert", lambda title, detail="": alerts.append((title, detail)))
    result = dr.build_section(tmp_path, cookie="c")
    assert result is None
    assert len(alerts) == 1
    assert alerts[0][0] == "高股息数据获取失败"
    assert "boom" in alerts[0][1]


def test_build_section_prepare_failure_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(dr, "fetch_data", lambda cookie=None: _two_group_data())
    monkeypatch.setattr(
        dr, "prepare_dividend_email_data",
        lambda d, cookie=None: (_ for _ in ()).throw(RuntimeError("prep fail")),
    )
    monkeypatch.setattr(dr, "notify_alert", lambda title, detail="": None)
    assert dr.build_section(tmp_path, cookie="c") is None
