"""可转债三低轮动 测试(纯函数,不触网)。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible.three_low import charts, render, strategy  # noqa: E402


def _row(code, name, *, dblow, premium_rt, curr_iss_amt, price, **extra):
    cell = {
        "bond_id": code, "bond_nm": name, "dblow": dblow,
        "premium_rt": premium_rt, "curr_iss_amt": curr_iss_amt, "price": price,
    }
    cell.update(extra)
    return {"cell": cell}


# ── 通用工具 ──────────────────────────────────────────────────────────────────
def test_to_float():
    assert strategy.to_float("1.5") == 1.5
    assert strategy.to_float(None) is None
    assert strategy.to_float("") is None
    assert strategy.to_float("abc") is None
    assert strategy.to_float("x", 0.0) == 0.0


def test_normalize_bond_code():
    assert strategy.normalize_bond_code("110092") == "110092.SH"
    assert strategy.normalize_bond_code("128119") == "128119.SZ"
    assert strategy.normalize_bond_code("110092.SH") == "110092.SH"
    assert strategy.normalize_bond_code("") == ""
    assert strategy.normalize_bond_code(None) == ""


def test_build_bond_code_match_set_accepts_both_forms():
    match_set = strategy.build_bond_code_match_set(["118027.SH", "128119"])
    assert "118027.SH" in match_set and "128119.SZ" in match_set
    assert "128119" in match_set  # 原始写法也保留


# ── 过滤 ──────────────────────────────────────────────────────────────────────
def test_get_cb_filter_reasons_st_and_icons():
    assert "正股含ST或*ST" in strategy.get_cb_filter_reasons(
        {"stock_nm": "*ST 测试"}, ["R", "O", "B"]
    )
    assert "已公告强赎" in strategy.get_cb_filter_reasons(
        {"icons": {"R": 1}}, ["R", "O", "B"]
    )
    assert strategy.get_cb_filter_reasons({"stock_nm": "正常"}, ["R", "O", "B"]) == []


def test_check_exclusion_rules_missing_value_not_excluded():
    rules = [{"field": "pb", "op": "lt", "threshold": 1, "label": "破净"}]
    # 取不到值不排除
    assert strategy.check_exclusion_rules({"pb": ""}, rules) == []
    assert strategy.check_exclusion_rules({}, rules) == []
    # 命中阈值才排除
    assert strategy.check_exclusion_rules({"pb": 0.5}, rules) == ["破净"]
    assert strategy.check_exclusion_rules({"pb": 1.5}, rules) == []


# ── 选债 ──────────────────────────────────────────────────────────────────────
def test_assign_factor_scores_ascending():
    rows = [_row("A", "a", dblow="120", premium_rt="10", curr_iss_amt="5", price="100"),
            _row("B", "b", dblow="110", premium_rt="20", curr_iss_amt="10", price="100")]
    strategy.assign_factor_scores(rows, "dblow", ascending=True, weight=1.0)
    # dblow 越小排名越靠前 -> B(110) 得分 2,A(120) 得分 1
    assert rows[0]["dblow_score"] == 1
    assert rows[1]["dblow_score"] == 2


def test_three_low_strategy_ranking_and_topn():
    rows = [
        _row("A", "a", dblow="120", premium_rt="10", curr_iss_amt="5", price="100"),
        _row("B", "b", dblow="110", premium_rt="20", curr_iss_amt="10", price="100"),
        _row("C", "c", dblow="130", premium_rt="5", curr_iss_amt="2", price="100"),
    ]
    factors = [
        {"field": "dblow", "weight": 1.0, "ascending": True},
        {"field": "premium_rt", "weight": 1.0, "ascending": True},
        {"field": "curr_iss_amt", "weight": 1.0, "ascending": True},
    ]
    top = strategy.three_low_strategy(rows, factors, top_n=2)
    # C:1+3+3=7, A:2+2+2=6, B:3+1+1=5 -> [C, A]
    assert [strategy.bond_code(r) for r in top] == ["C", "A"]


def test_holdings_from_keep_pool_tolerance_retention():
    keep_pool = [
        {"cell": {"bond_id": "c1", "bond_nm": "n1", "price": "100"}, "rank": 1},
        {"cell": {"bond_id": "c2", "bond_nm": "n2", "price": "101"}, "rank": 2},
        {"cell": {"bond_id": "c3", "bond_nm": "n3", "price": "102"}, "rank": 3},
    ]
    today_close = {"c1": 100.0, "c2": 101.0, "c3": 102.0}
    # 已持有 c3(排名 3,仍在池内)应保留;空缺按 rank 补 c1
    holdings = strategy.holdings_from_keep_pool(keep_pool, target_count=2, today_close_map=today_close,
                                                prev_holdings=[{"code": "c3", "name": "n3", "price": 99}])
    codes = [h["code"] for h in holdings]
    assert codes == ["c1", "c3"]  # 按 keep_pool rank 排序
    assert holdings[0]["rank"] == 1 and holdings[1]["rank"] == 3
    assert holdings[1]["price"] == 102.0


def test_compute_portfolio_return_normal_and_missing():
    prev = [{"code": "c1", "price": 100}, {"code": "c2", "price": 50}]
    # 正常:0.1 与 0.0 -> 0.05
    ret, missing = strategy.compute_portfolio_return(prev, {"c1": 110, "c2": 50})
    assert pytest.approx(ret) == 0.05
    assert missing == []
    # c2 缺价:按 0 兜底,记入 missing
    ret, missing = strategy.compute_portfolio_return(prev, {"c1": 110})
    assert pytest.approx(ret) == 0.05
    assert missing == ["c2"]


def test_compute_snapshot_signature_stable():
    pool = [{"cell": {"bond_id": "c1", "price": "100"}}, {"cell": {"bond_id": "c2", "price": "101"}}]
    sig_a = strategy.compute_snapshot_signature(pool)
    sig_b = strategy.compute_snapshot_signature(list(pool))
    assert sig_a == sig_b and len(sig_a) == 32
    # 价格变化 -> 签名变化
    pool2 = [{"cell": {"bond_id": "c1", "price": "100"}}, {"cell": {"bond_id": "c2", "price": "102"}}]
    assert strategy.compute_snapshot_signature(pool2) != sig_a


# ── 报告 ──────────────────────────────────────────────────────────────────────
def _sample_history():
    return [
        {"date": "2026-01-01", "nav": 1.0, "daily_return": 0.0,
         "holdings": [{"code": "c1", "name": "n1", "price": 100, "rank": 1}], "selection": []},
        {"date": "2026-01-02", "nav": 1.1, "daily_return": 0.1,
         "holdings": [{"code": "c1", "name": "n1", "price": 110, "rank": 1}], "selection": []},
        {"date": "2026-01-03", "nav": 0.99, "daily_return": -0.1,
         "holdings": [{"code": "c1", "name": "n1", "price": 99, "rank": 1}], "selection": []},
        {"date": "2026-01-04", "nav": 1.05, "daily_return": 0.0606,
         "holdings": [{"code": "c1", "name": "n1", "price": 105, "rank": 1}], "selection": []},
    ]


def test_compute_drawdown_stats():
    stats = strategy.compute_drawdown_stats(_sample_history(), 1.0)
    # navs=[1.0,1.0,1.1,0.99,1.05]; 最大回撤 0.99/1.1-1 = -0.1
    assert pytest.approx(stats["max_drawdown"], rel=1e-3) == -0.1
    assert pytest.approx(stats["total_return"], rel=1e-3) == 0.05
    assert stats["current_drawdown"] < 0


def test_find_max_drawdown_window():
    window = strategy.find_max_drawdown_window(_sample_history(), 1.0)
    assert window is not None
    assert window["trough_date"] == "2026-01-03"
    assert window["peak_date"] == "2026-01-02"  # 前高 1.1
    assert pytest.approx(window["max_drawdown"], rel=1e-3) == -0.1


def test_align_benchmark_and_comparison():
    history = [{"date": "2026-01-01", "nav": 1.0}, {"date": "2026-01-02", "nav": 1.1}]
    bench = [{"date": "2026-01-01", "value": 100}, {"date": "2026-01-02", "value": 105}]
    aligned = strategy.align_benchmark(history, bench)
    assert aligned[0]["strategy_return"] == 0.0 and aligned[0]["benchmark_return"] == 0.0
    assert pytest.approx(aligned[1]["strategy_return"]) == 0.1
    assert pytest.approx(aligned[1]["benchmark_return"]) == 0.05
    comp = strategy.compute_benchmark_comparison(history, bench)
    assert pytest.approx(comp["excess_return"]) == 0.05


def test_compute_benchmark_comparison_empty():
    assert strategy.compute_benchmark_comparison([], []) == {
        "benchmark_return": None, "excess_return": None,
    }


# ── 配置 ──────────────────────────────────────────────────────────────────────
def test_load_strategy_config_reads_yaml():
    config = strategy.load_strategy_config()
    assert config["strategy"]["target_count"] == 10
    assert config["strategy"]["hold_tolerance"] == 5
    assert len(config["strategy"]["factors"]) == 3
    assert "state_path" not in config  # 新仓库无 state_path


# ── 渲染 ──────────────────────────────────────────────────────────────────────
def _sample_report():
    history = _sample_history()
    return {
        "as_of_date": "2026-01-04",
        "holdings": [{"code": "c1", "name": "n1", "price": 105.0, "rank": 1}],
        "current_nav": 1.05,
        "total_return": 0.05,
        "max_drawdown": -0.09,
        "current_drawdown": -0.045,
        "benchmark_return": 0.03,
        "excess_return": 0.02,
        "ranking": [{"code": "c1", "name": "n1", "price": 105.0, "rank": 1,
                     "dblow": 120.0, "premium_rt": 10.0, "curr_iss_amt": 5.0,
                     "total_score": 6.0, "selected": True}],
        "target_count": 10,
        "history": history,
    }


def test_build_email_html_contains_key_blocks():
    html = render.build_email_html(_sample_report(), charts.NAV_CHART_CID)
    assert "可转债三低轮动日报" in html
    assert f"cid:{charts.NAV_CHART_CID}" in html
    assert "次日持仓" in html
    assert "三低排名" in html
    assert "历史持仓" in html


def test_build_preview_html_without_chart():
    html = render.build_preview_html(_sample_report(), chart_path=None)
    assert html.startswith("<!DOCTYPE html>")
    assert "(净值图未生成)" in html


def test_history_updated_detection():
    prev = {"holdings_history": [{"date": "d1"}], "last_snapshot_signature": "a"}
    # 历史变长 -> True
    assert render.history_updated(prev, {"holdings_history": [{"date": "d1"}, {"date": "d2"}],
                                         "last_snapshot_signature": "a"}) is True
    # 签名变化(同日覆盖重算) -> True
    assert render.history_updated(prev, {"holdings_history": [{"date": "d1"}],
                                         "last_snapshot_signature": "b"}) is True
    # 完全一致 -> False
    assert render.history_updated(prev, {"holdings_history": [{"date": "d1"}],
                                         "last_snapshot_signature": "a"}) is False
