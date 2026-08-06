"""高股息 dividend/filter 测试(纯内存过滤 + mock TTM fetcher,不触网/不依赖 xlsx)。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.valuation.dividend import filter as flt  # noqa: E402


def _row(stock_id, stock_nm, industry_nm):
    return {"cell": {"stock_id": stock_id, "stock_nm": stock_nm, "industry_nm": industry_nm}}


def _data(*rows):
    return {"rows": list(rows)}


# ── 行业排除 ──────────────────────────────────────────────────────────────────
def test_filter_by_excluded_industries():
    data = _data(_row("600001", "A", "基建市政工程"), _row("000001", "B", "银行"))
    result = flt.filter_dividend_rows_by_excluded_industries(data, frozenset({"基建市政工程"}))
    assert [r["cell"]["stock_nm"] for r in result["rows"]] == ["B"]
    assert result["filter_steps"][-1]["excluded_count"] == 1


def test_industry_name_of_fallback():
    assert flt.industry_name_of({}) == "未分类"
    assert flt.industry_name_of({"industry_nm": "  "}) == "未分类"
    assert flt.industry_name_of({"industry_nm": "银行"}) == "银行"


# ── 白名单 ────────────────────────────────────────────────────────────────────
def test_filter_by_whitelist():
    data = _data(_row("600001", "A", "银行"), _row("000002", "B", "银行"))
    result = flt.filter_dividend_rows_by_stock_code_whitelist(
        data, stock_code_whitelist=frozenset({"600001"})
    )
    assert [r["cell"]["stock_id"] for r in result["rows"]] == ["600001"]
    # 白名单内标的的 stock_id 被规范化写回
    assert result["rows"][0]["cell"]["stock_id"] == "600001"


def test_filter_by_whitelist_normalizes_code():
    data = _data(_row("1", "A", "银行"))  # "1" -> "000001"
    result = flt.filter_dividend_rows_by_stock_code_whitelist(
        data, stock_code_whitelist=frozenset({"000001"})
    )
    assert len(result["rows"]) == 1
    assert result["rows"][0]["cell"]["stock_id"] == "000001"


# ── TTM ───────────────────────────────────────────────────────────────────────
def _mock_fetcher(table):
    def _fetch(stock_id):
        stock_id = stock_id.zfill(6) if stock_id.isdigit() else stock_id
        if stock_id not in table:
            raise RuntimeError(f"未知股票 {stock_id}")
        return {
            "ttm_value_yi": table[stock_id],
            "latest_period": "2024-09-30",
            "basis": "rolling",
        }
    return _fetch


def test_filter_by_ttm_keeps_above_threshold():
    data = _data(_row("600001", "A", "银行"), _row("000002", "B", "银行"))
    result = flt.filter_dividend_rows_by_ttm_net_profit(
        data, min_ttm_net_profit_yi=10, fetcher=_mock_fetcher({"600001": 15, "000002": 5})
    )
    assert [r["cell"]["stock_nm"] for r in result["rows"]] == ["A"]
    assert result["rows"][0]["cell"]["ttm_parent_net_profit_yi"] == 15
    assert result["rows"][0]["cell"]["ttm_parent_net_profit_basis"] == "rolling"


def test_filter_by_ttm_records_fetch_failure():
    data = _data(_row("600001", "A", "银行"))

    def _boom(_stock_id):
        raise RuntimeError("巨潮炸了")

    result = flt.filter_dividend_rows_by_ttm_net_profit(data, fetcher=_boom)
    assert result["rows"] == []
    assert len(result["ttm_fetch_failed"]) == 1
    assert result["filter_steps"][-1]["fetch_failed_count"] == 1


# ── secondary_rules 组合 ──────────────────────────────────────────────────────
def test_secondary_rules_whitelist_then_industry_then_ttm():
    data = _data(
        _row("600001", "A", "银行"),            # 白名单内 + 行业 OK + TTM 15 -> 保留
        _row("000002", "B", "基建市政工程"),    # 白名单内 + 行业排除 -> 滞留
        _row("600003", "C", "银行"),            # 白名单内 + 行业 OK + TTM 5 -> TTM 排除
        _row("600099", "D", "银行"),            # 不在白名单 -> 白名单排除(不触发 TTM)
    )
    fetcher = _mock_fetcher({"600001": 15, "600003": 5})  # D 不应被 fetch
    result = flt.filter_dividend_rows_by_secondary_rules(
        data,
        min_ttm_net_profit_yi=10,
        fetcher=fetcher,
        stock_code_whitelist=frozenset({"600001", "000002", "600003"}),
        excluded_industries=frozenset({"基建市政工程"}),
    )
    assert [r["cell"]["stock_nm"] for r in result["rows"]] == ["A"]
    # 三步 filter_steps
    assert [s["step_name"] for s in result["filter_steps"]] == ["国资白名单", "行业排除", "TTM归母净利润"]


def test_build_filter_summary_lines():
    data = flt.filter_dividend_rows_by_excluded_industries(
        _data(_row("600001", "A", "基建市政工程")), frozenset({"基建市政工程"})
    )
    lines = flt.build_filter_summary_lines(data)
    assert any("集思录返回" in line for line in lines)
    assert any("行业排除" in line for line in lines)
