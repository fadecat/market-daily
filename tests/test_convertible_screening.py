"""可转债低价债筛选 测试(纯函数,不触网)。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible.screening import archive, render, strategy  # noqa: E402


def _row(code, name, *, dblow, curr_iss_amt, premium_rt="10", price="110", **extra):
    cell = {
        "bond_id": code, "bond_nm": name, "dblow": dblow,
        "curr_iss_amt": curr_iss_amt, "premium_rt": premium_rt, "price": price,
    }
    cell.update(extra)
    return {"cell": cell}


# ── strategy ──────────────────────────────────────────────────────────────────
def test_to_float_default_inf():
    assert strategy.to_float("1.5") == 1.5
    assert strategy.to_float("x") == float("inf")
    assert strategy.to_float(None, default=0.0) == 0.0


def test_is_short_term_negative_ytm_cb():
    assert strategy.is_short_term_negative_ytm_cb({"year_left": "0.5", "ytm_rt": "-1"}) is True
    assert strategy.is_short_term_negative_ytm_cb({"year_left": "2", "ytm_rt": "-1"}) is False
    assert strategy.is_short_term_negative_ytm_cb({"year_left": "0.5", "ytm_rt": "1"}) is False
    assert strategy.is_short_term_negative_ytm_cb({"year_left": ""}) is False


def test_get_cb_filter_reasons():
    assert "正股含ST" in strategy.get_cb_filter_reasons({"stock_nm": "*ST 股", "icons": {}})
    assert "已公告强赎(O)" in strategy.get_cb_filter_reasons({"stock_nm": "正常", "icons": {"O": 1}})
    assert "到期赎回(R)" in strategy.get_cb_filter_reasons({"icons": {"R": 1}})
    assert strategy.get_cb_filter_reasons({"stock_nm": "正常", "icons": {}}) == []


def test_is_force_redeem_triggered():
    assert strategy.is_force_redeem_triggered({"sprice": "10", "force_redeem_price": "9"}) is True
    # 已公告强赎(O) -> False
    assert strategy.is_force_redeem_triggered({"sprice": "10", "force_redeem_price": "9", "icons": {"O": 1}}) is False
    assert strategy.is_force_redeem_triggered({"sprice": "8", "force_redeem_price": "9"}) is False


def test_normalize_stock_code():
    assert strategy.normalize_stock_code("  600001.SH ") == "600001"
    assert strategy.normalize_stock_code("000001") == "000001"
    assert strategy.normalize_stock_code("abc") == ""
    assert strategy.normalize_stock_code(None) == ""


def test_sort_cb_rows_ranking_and_tiebreak():
    rows = [
        _row("A", "a", dblow="150", curr_iss_amt="10"),
        _row("B", "b", dblow="120", curr_iss_amt="5"),
        _row("C", "c", dblow="130", curr_iss_amt="2"),
    ]
    factors = [{"field": "dblow"}, {"field": "curr_iss_amt"}]
    sorted_rows = strategy.sort_cb_rows(rows, factors)
    # dblow 分(越小越高):B=3,C=2,A=1;规模分:C=3,B=2,A=1
    # total: A=2, B=5, C=5;B/C 同分按 dblow 升序 B(120)<C(130)
    assert [r["cell"]["bond_id"] for r in sorted_rows] == ["B", "C", "A"]


def test_filter_cb_excludes_st_and_sorts():
    rows = [
        _row("A", "正常A", dblow="120", curr_iss_amt="5"),
        _row("S", "坏", dblow="110", curr_iss_amt="3", stock_nm="*ST坏"),  # ST 排除
    ]
    config = {"factors": [{"field": "dblow"}, {"field": "curr_iss_amt"}]}
    filtered = strategy.filter_cb(rows, config)
    assert [r["cell"]["bond_id"] for r in filtered] == ["A"]


def test_get_enterprise_nature():
    # 显式 nature_map
    assert strategy.get_enterprise_nature({"stock_id": "600001"}, {"600001": "中央国有企业"}) == "中央国有企业"
    assert strategy.get_enterprise_nature({"stock_id": "600001"}, {}) == ""
    assert strategy.get_enterprise_nature({"stock_id": ""}, {"600001": "x"}) == ""
    # normalize:"1" -> "000001"
    assert strategy.get_enterprise_nature({"stock_id": "1"}, {"000001": "地方国有企业"}) == "地方国有企业"


def test_load_enterprise_nature_map_from_whitelist():
    nature_map = strategy.load_enterprise_nature_map()
    assert isinstance(nature_map, dict)
    # 真实白名单存在时应非空(已接回 common/whitelist)
    from src.common.whitelist import DEFAULT_WHITELIST_XLSX
    if DEFAULT_WHITELIST_XLSX.exists():
        assert len(nature_map) > 0


def test_load_config_reads_yaml():
    config = strategy.load_config()
    assert config["max_price"] == 120
    assert config["max_show"] == 50
    assert [f["field"] for f in config["factors"]] == ["dblow", "curr_iss_amt"]


# ── archive ───────────────────────────────────────────────────────────────────
def test_parse_chinese_number():
    assert archive._parse_chinese_number("30") == 30
    assert archive._parse_chinese_number("三十") == 30
    assert archive._parse_chinese_number("十五") == 15
    assert archive._parse_chinese_number("") is None


def test_parse_adjust_clause_days():
    window, required = archive._parse_adjust_clause_days("连续30个交易日中至少有15个交易日")
    assert window == 30 and required == 15
    assert archive._parse_adjust_clause_days("无条款") == (None, None)


def test_derive_cb_adjust_metrics_display_text():
    detail = {
        "bond_id": "128001", "adjust_trigger_price": "5.0",
        "adjust_clause_text": "连续30个交易日中至少有15个交易日",
    }
    hist = {
        "rows": [
            {"cell": {"sprice": "4.0", "last_chg_dt": "2026-01-02", "convert_price_tips": ""}},
            {"cell": {"sprice": "6.0", "last_chg_dt": "2026-01-01", "convert_price_tips": ""}},
        ]
    }
    derived = archive.derive_cb_adjust_metrics(detail, hist, as_of_date="2026-01-02")
    # trigger_price=5.0,2 个交易日,1 个 sprice<5 -> hit_days=1
    assert derived["trigger_hit_days_30"] == 1
    assert derived["display_text"] == "1/15 | 30"
    assert derived["trigger_required_days"] == 15


def test_build_cb_adjust_archive_failed_alert_text():
    text = archive.build_cb_adjust_archive_failed_alert_text([
        {"bond_id": "128001", "bond_nm": "测试", "reason": "超时"},
    ])
    assert "1 只转债下修数据刷新失败" in text
    assert "128001" in text and "测试" in text


# ── render ────────────────────────────────────────────────────────────────────
def test_build_cb_rule_msg_contains_max_price():
    assert "≤ 120" in render.build_cb_rule_msg(120)


def test_colorizers_thresholds():
    assert "color:#1AAD19" in render._cb_email_price("95")     # <100 绿
    assert "color:#D93026" in render._cb_email_price("135")    # >130 红
    assert render._cb_email_price("110") == "110"
    assert render._cb_email_price("") == "--"
    assert "color:#1AAD19" in render._cb_email_premium("10")   # <20 绿
    assert "color:#D93026" in render._cb_email_premium("60")   # >50 红
    assert "color:#1AAD19" in render._cb_email_ytm("2")        # >0 绿
    assert "color:#D93026" in render._cb_email_ytm("-1")       # <0 红


def test_build_section_html_no_rows():
    html = render.build_section_html([], None, config={"max_show": 50, "max_price": 120})
    assert "暂无符合条件的可转债数据" in html
    assert "可转债筛选规则" in html


def test_build_section_html_with_rows():
    rows = [_row("128001", "测试债", dblow="120", curr_iss_amt="5")]
    html = render.build_section_html(rows, {"cur_index": 100}, config={"max_show": 50, "max_price": 120})
    assert "可转债筛选" in html
    assert "📌 测试债" in html
    assert "正股(价)" in html  # 10 列精简后的合并表头(代码/企业性质/正股价 已删并)
    assert "下修" in html
    assert "可转债市场概览" in html  # index_data 非空 -> 概览段
