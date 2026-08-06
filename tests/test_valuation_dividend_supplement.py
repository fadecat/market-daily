"""src/valuation/dividend/supplement.py 单测。

覆盖:东财 xuangu 客户端(extract_xc_id / fingerprint / detail / payload / 嵌套查找 /
分页去重 / 重试 / cookie 懒读)、行字段提取与格式化、补充池行级指标(PE/股息率/ROE/
总市值/本地TTM)、ROE 动态列解析、表头/标题/条件摘要、本地二次过滤(行业+PE-TTM)、
行业分组排序、关联转债展示、组装、HTML 片段、编排入口与失败告警。
"""
from __future__ import annotations

import types
from typing import Any

import pytest
import requests

from src.valuation.dividend import supplement


# ---------- fakes ----------


class _FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    """按序返回预设响应(GET/POST 共用一个队列);可抛异常模拟失败。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next()

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._next()

    def _next(self):
        if not self._responses:
            raise AssertionError("no more fake responses")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _ttm_fetcher_yi(yi: float):
    """返回一个恒定 TTM 归母净利润(亿元)的 fake fetcher。"""
    return lambda code: {"ttm_value_yi": yi}


def _no_sleep(monkeypatch):
    """禁用 common.alerts 的 time.sleep,加速重试测试。"""
    from src.common import alerts as alerts_mod

    monkeypatch.setattr(alerts_mod, "time", types.SimpleNamespace(sleep=lambda *a, **k: None))


def _page(rows, total, *, columns=None):
    columns = columns if columns is not None else [{"title": "代码", "key": "SECURITY_CODE"}]
    return {"code": "100", "data": {"result": {"list": columns, "rows": rows, "total": total}}}


# ---------- parse_float / xc_id / cookie / fingerprint ----------


def test_parse_float_percent_default_none():
    assert supplement.parse_float("3.5%") == 3.5
    assert supplement.parse_float("12.5") == 12.5
    assert supplement.parse_float(None) is None
    assert supplement.parse_float("abc", default=9.9) == 9.9
    assert supplement.parse_float("", default=float("inf")) == float("inf")


def test_extract_xc_id_from_raw_url_and_text():
    assert supplement.extract_xc_id("xc12fd39e81b0700714b") == "xc12fd39e81b0700714b"
    assert supplement.extract_xc_id("https://xuangu.eastmoney.com/?id=xcABC123") == "xcABC123"
    assert supplement.extract_xc_id("分享链接 xcZZZ999 结尾") == "xcZZZ999"


def test_extract_xc_id_raises_on_empty():
    with pytest.raises(ValueError, match="缺少 xcId"):
        supplement.extract_xc_id("")
    with pytest.raises(ValueError, match="无法从输入中提取"):
        supplement.extract_xc_id("no xc id here")


def test_parse_cookie_string():
    assert supplement.parse_cookie_string("a=1; b=2") == {"a": "1", "b": "2"}
    assert supplement.parse_cookie_string("") == {}
    assert supplement.parse_cookie_string("malformed; x=1") == {"x": "1"}


def test_infer_fingerprint_prefers_qgqp_b_id():
    cookie = "qgqp_b_id=abc123; other=1"
    assert supplement.infer_fingerprint(cookie) == "abc123"


def test_infer_fingerprint_md5_fallback(monkeypatch):
    monkeypatch.setattr(supplement.time, "time", lambda: 1234567890.0)
    fp = supplement.infer_fingerprint("plaincookie")
    # md5 hexdigest -> 32 个十六进制字符
    assert len(fp) == 32
    assert all(c in "0123456789abcdef" for c in fp)


# ---------- _require_code_100 / fetch_xuangu_detail ----------


def test_require_code_100_accepts_string_100():
    assert supplement._require_code_100({"code": "100", "data": 1}, "lbl") == {"code": "100", "data": 1}


def test_require_code_100_rejects_non_100():
    with pytest.raises(RuntimeError, match="lbl"):
        supplement._require_code_100({"code": "500", "msg": "bad"}, "lbl")
    with pytest.raises(RuntimeError, match="lbl"):
        supplement._require_code_100({"code": 200, "msg": "x"}, "lbl")  # int 200 -> str "200"


def test_fetch_xuangu_detail_extracts_data():
    detail = {"xcId": "xc123", "contentNew": "国企", "keywordInfoNew": {"dxInfo": []}}
    sess = _FakeSession([_FakeResponse({"code": "100", "msg": "ok", "data": detail})])
    out = supplement.fetch_xuangu_detail("xc123", session=sess)
    assert out == detail
    method, url, kwargs = sess.calls[0]
    assert method == "GET"
    assert url == supplement.DETAIL_URL
    assert kwargs["params"] == {"xcId": "xc123"}


def test_fetch_xuangu_detail_retries_on_transient_error(monkeypatch):
    _no_sleep(monkeypatch)
    detail = {"xcId": "xc123"}
    sess = _FakeSession(
        [
            requests.exceptions.ConnectionError("boom"),
            _FakeResponse({"code": "100", "data": detail}),
        ]
    )
    out = supplement.fetch_xuangu_detail("xc123", session=sess)
    assert out == detail
    assert len(sess.calls) == 2  # 失败一次后重试成功


def test_fetch_xuangu_detail_no_retry_on_logical_error(monkeypatch):
    _no_sleep(monkeypatch)
    sess = _FakeSession([_FakeResponse({"code": "500", "msg": "bad"})])
    with pytest.raises(RuntimeError, match="getXcIdDetail"):
        supplement.fetch_xuangu_detail("xc123", session=sess)
    assert len(sess.calls) == 1  # 逻辑错误不重试


def test_fetch_xuangu_detail_propagates_cookie_header():
    sess = _FakeSession([_FakeResponse({"code": "100", "data": {}})])
    supplement.fetch_xuangu_detail("xc123", cookie_text="k=v", session=sess)
    assert sess.calls[0][2]["headers"]["Cookie"] == "k=v"


# ---------- build_condition_from_detail / build_search_payload_from_condition ----------


def test_build_condition_from_detail_with_fallbacks():
    detail = {"contentNew": "国企", "keywordInfoNew": {"dxInfo": [1], "senInfo": [2]}, "customDataNew": "{}"}
    cond = supplement.build_condition_from_detail(detail)
    assert cond == {"keyWordNew": "国企", "customDataNew": "{}", "dxInfoNew": [1], "senInfoNew": [2]}

    # contentNew 缺失回退 content
    cond2 = supplement.build_condition_from_detail({"content": "回退", "keywordInfoNew": {}})
    assert cond2["keyWordNew"] == "回退"
    assert cond2["dxInfoNew"] == []
    assert cond2["customDataNew"] == "[]"


def test_build_search_payload_filters_none_timestamp_requestid():
    cond = {"keyWordNew": "国企", "dxInfoNew": [{"k": 1}], "senInfoNew": [], "customDataNew": "[]"}
    payload = supplement.build_search_payload_from_condition(cond, fingerprint="fp", page_no=2, page_size=100)
    assert payload["pageNo"] == 2
    assert payload["pageSize"] == 100
    assert payload["fingerprint"] == "fp"
    assert payload["keyWordNew"] == "国企"
    assert payload["dxInfoNew"] == [{"k": 1}]
    assert "timestamp" not in payload
    assert "requestId" not in payload
    assert payload["biz"] == "web_ai_select_stocks"
    assert payload["client"] == "WEB"


# ---------- finders / dedupe ----------


def test_find_result_columns_rows_total_nested():
    payload = _page([{"SECURITY_CODE": "000001", "SECURITY_SHORT_NAME": "甲"}], 1)
    assert len(supplement.find_result_columns(payload)) == 1
    rows = supplement.find_result_rows(payload)
    assert rows[0]["SECURITY_CODE"] == "000001"
    assert supplement.find_total_count(payload) == 1


def test_find_total_count_string_and_none():
    assert supplement.find_total_count({"total": "42"}) == 42
    assert supplement.find_total_count({"data": {"foo": "bar"}}) is None


def test_find_result_rows_empty_returns_empty():
    assert supplement.find_result_rows({"data": {"result": {"rows": []}}}) == []


def test_dedupe_rows_by_security_code():
    rows = [
        {"SECURITY_CODE": "000001", "v": 1},
        {"SECURITY_CODE": "000002", "v": 2},
        {"SECURITY_CODE": "000001", "v": 3},
    ]
    out = supplement._dedupe_rows(rows)
    assert [r["v"] for r in out] == [1, 2]


# ---------- pagination ----------


def test_fetch_all_results_single_page_when_total_missing():
    page = {"code": "100", "data": {"result": {"list": [{"title": "代码", "key": "SECURITY_CODE"}], "rows": [{"SECURITY_CODE": "000001"}]}}}
    sess = _FakeSession([_FakeResponse(page)])
    result = supplement.fetch_all_results_by_condition({"keyWordNew": "x"}, session=sess)
    assert result["row_count"] == 1
    assert result["total_count"] is None
    assert len(sess.calls) == 1


def test_fetch_all_results_paginates_and_dedupes():
    page1 = _page([{"SECURITY_CODE": f"{i:06d}"} for i in range(100)], 150)
    page2 = _page([{"SECURITY_CODE": f"{i:06d}"} for i in range(100, 150)] + [{"SECURITY_CODE": "000050"}], 150)
    sess = _FakeSession([_FakeResponse(page1), _FakeResponse(page2)])
    result = supplement.fetch_all_results_by_condition(
        {"keyWordNew": "x"}, cookie_text="", page_size=100, session=sess
    )
    assert result["total_count"] == 150
    assert result["page_count"] == 2
    assert len(sess.calls) == 2
    assert result["row_count"] == 150  # 000050 重复,去重后 150


def test_fetch_all_results_respects_max_pages():
    page1 = _page([{"SECURITY_CODE": f"{i:06d}"} for i in range(100)], 350)
    sess = _FakeSession([_FakeResponse(page1)])
    result = supplement.fetch_all_results_by_condition(
        {"keyWordNew": "x"}, page_size=100, max_pages=1, session=sess
    )
    assert len(sess.calls) == 1  # max_pages=1 -> 只取首页
    assert result["row_count"] == 100


def test_fetch_all_results_by_xcid_reads_cookie_from_env(monkeypatch):
    def fake_get(name, default=""):
        return "emcookie=abc" if name == "EASTMONEY_XUANGU_COOKIE" else default

    monkeypatch.setattr(supplement.env, "get", fake_get)
    detail = {"contentNew": "国企", "keywordInfoNew": {}}
    sess = _FakeSession(
        [_FakeResponse({"code": "100", "data": detail}), _FakeResponse(_page([], 0))]
    )
    result = supplement.fetch_all_results_by_xcid("xcabc", session=sess)
    assert result["xc_id"] == "xcabc"
    assert result["condition_text"] == "国企"
    assert sess.calls[0][2]["headers"]["Cookie"] == "emcookie=abc"  # GET
    assert sess.calls[1][2]["headers"]["Cookie"] == "emcookie=abc"  # POST


# ---------- 行字段提取与格式化 ----------


def test_first_value_by_prefix_date_suffix():
    row = {"PETTMDEDUCTED{2026-08-01}": "12.5", "PB": "0.8"}
    assert supplement._first_value_by_prefix(row, "PETTMDEDUCTED") == "12.5"
    assert supplement._first_value_by_prefix(row, "MISSING") == ""


def test_leading_metric_text_strips_units():
    assert supplement._leading_metric_text("12.5亿|其他") == "12.5"
    assert supplement._leading_metric_text("3.5%") == "3.5"
    assert supplement._leading_metric_text("") == ""


def test_format_decimal_and_percent_text():
    assert supplement._format_decimal_text("10.5") == "10.50"
    assert supplement._format_decimal_text("abc") == "abc"
    assert supplement._format_percent_text("6.0%") == "6.00%"


def test_format_prefixed_percent_text():
    row = {"DIVIDEND_NEWRATIO_HYY{2026-08-01}": "5.5%"}
    assert supplement._format_prefixed_percent_text(row, "DIVIDEND_NEWRATIO_HYY") == "5.50%"


def test_parse_yi_amount_text():
    assert supplement._parse_yi_amount_text("100亿") == 100.0
    assert supplement._parse_yi_amount_text("1.5万亿") == 15000.0
    assert supplement._parse_yi_amount_text("50") == 50.0
    assert supplement._parse_yi_amount_text("") is None
    assert supplement._parse_yi_amount_text("100亿|备注") == 100.0
    assert supplement._parse_yi_amount_text("1,200亿") == 1200.0


# ---------- 补充池行级指标 ----------


def test_supplement_industry_name_of_fallback():
    assert supplement._supplement_industry_name_of({"INDUSTRY_LV3": "煤炭"}) == "煤炭"
    assert supplement._supplement_industry_name_of({"INDUSTRY": "煤炭开采"}) == "煤炭开采"
    assert supplement._supplement_industry_name_of({"INDUSTRY_LV1": "能源"}) == "能源"
    assert supplement._supplement_industry_name_of({}) == "未分类"


def test_supplement_pe_value_prefers_dynamic():
    assert supplement._supplement_pe_value({"PE_DYNAMIC": "8.5"}) == 8.5
    assert supplement._supplement_pe_value({"PETTMDEDUCTED{2026-08-01}": "12.0"}) == 12.0
    assert supplement._supplement_pe_value({}) == float("inf")


def test_supplement_pe_text():
    assert supplement._supplement_pe_text({"PE_DYNAMIC": "8.5"}) == "8.50"
    assert supplement._supplement_pe_text({"PETTMDEDUCTED{2026-08-01}": "12.0"}) == "12.00"
    assert supplement._supplement_pe_text({}) == ""


def test_supplement_dividend_rate_value_default_zero():
    assert supplement._supplement_dividend_rate_value({"DIVIDEND_NEWRATIO_HYY{2026-08-01}": "5.5%"}) == 5.5
    assert supplement._supplement_dividend_rate_value({}) == 0.0


def test_supplement_market_value_yi_typo_field():
    # 东财字段名是 TOAL_MARKET_VALUE(拼写如此)
    assert supplement._supplement_market_value_yi({"TOAL_MARKET_VALUE": "200亿"}) == 200.0
    assert supplement._supplement_market_value_yi({"TOAL_MARKET_VALUE": "1.2万亿"}) == 12000.0
    assert supplement._supplement_market_value_yi({}) is None


def test_supplement_ttm_metrics_computes_pe_and_caches():
    calls = []

    def fetcher(code):
        calls.append(code)
        return {"ttm_value_yi": 10.0}

    row = {"TOAL_MARKET_VALUE": "200亿"}
    m1 = supplement._supplement_ttm_metrics(row, "000001", ttm_fetcher=fetcher)
    assert m1["ttm_text"] == "10.00"
    assert m1["pe_ttm_text"] == "20.00"  # 200 / 10
    assert m1["ttm_value_yi"] == 10.0
    # 缓存:第二次不再调用 fetcher
    m2 = supplement._supplement_ttm_metrics(row, "000001", ttm_fetcher=fetcher)
    assert m2 is m1
    assert len(calls) == 1


def test_supplement_ttm_metrics_failure_returns_empty():
    def boom(code):
        raise RuntimeError("cninfo down")

    row = {"TOAL_MARKET_VALUE": "200亿"}
    m = supplement._supplement_ttm_metrics(row, "000001", ttm_fetcher=boom)
    assert m == {"ttm_text": "", "ttm_value_yi": None, "pe_ttm_text": ""}


def test_supplement_ttm_metrics_no_market_value_empty_pe():
    def fetcher(code):
        return {"ttm_value_yi": 10.0}

    m = supplement._supplement_ttm_metrics({}, "000001", ttm_fetcher=fetcher)
    assert m["ttm_text"] == "10.00"
    assert m["pe_ttm_text"] == ""  # 无总市值 -> PE 空


def test_supplement_ttm_metrics_none_yi_empty():
    def fetcher(code):
        return {"ttm_value_yi": None}

    m = supplement._supplement_ttm_metrics({"TOAL_MARKET_VALUE": "100亿"}, "000001", ttm_fetcher=fetcher)
    assert m["pe_ttm_text"] == ""


# ---------- ROE 动态列解析 ----------


def test_resolve_roe_column_from_columns():
    cols = [
        {"title": "代码", "key": "SECURITY_CODE"},
        {"title": "ROE", "key": "ROE_WEIGHT{2026-08-01}"},
    ]
    out = supplement.resolve_dividend_email_supplement_roe_column(cols, [])
    assert out["key"] == "ROE_WEIGHT{2026-08-01}"
    assert out["header"] == "ROE"


def test_resolve_roe_column_title_not_exact_roe():
    cols = [{"title": "净资产收益率", "key": "ROE_X"}]
    out = supplement.resolve_dividend_email_supplement_roe_column(cols, [])
    assert out["header"] == "净资产收益率"


def test_resolve_roe_column_fallback_to_rows():
    row = {"ROE_WEIGHT{2026-08-01}": "15.0"}
    out = supplement.resolve_dividend_email_supplement_roe_column([], [row])
    assert out["key"] == "ROE_WEIGHT{2026-08-01}"


def test_resolve_roe_column_default():
    assert supplement.resolve_dividend_email_supplement_roe_column([], []) == {"key": "", "header": "ROE"}


def test_supplement_roe_text_by_key_fallback():
    row = {"ROE_WEIGHT{2026-08-01}": "15.0%"}
    assert supplement._supplement_roe_text_by_key(row, "ROE_WEIGHT{2026-08-01}") == "15.00"
    # key 缺值 -> 扫描行内含 ROE 的键
    row2 = {"SOME_ROE": "8.0%"}
    assert supplement._supplement_roe_text_by_key(row2, "") == "8.00"


# ---------- 表头 / 标题 / 条件摘要 ----------


def test_build_headers_default_and_custom_roe():
    headers = supplement.build_dividend_email_supplement_headers()
    assert headers[-3] == "ROE"
    assert "PE-TTM" in headers
    assert headers[-1] == "关联转债"
    assert supplement.build_dividend_email_supplement_headers("净资产收益率")[-3] == "净资产收益率"


def test_build_title():
    assert supplement.build_dividend_email_supplement_title("xc123") == "东财条件补充池·xc123"
    assert supplement.build_dividend_email_supplement_title("") == "东财条件补充池"


def test_condition_lines_parses_text():
    text = (
        "企业性质包含中央国有企业或地方国有企业;"
        "市盈率TTM(扣非)大于等于0倍小于等于20倍;"
        "最新股息率>3%;"
        "不要ST股及不要退市股;不要北交所;"
        "不要东财三级行业包含基建市政工程;"
        "股息率倒序"
    )
    lines = supplement.build_dividend_email_supplement_condition_lines(text)
    joined = "\n".join(lines)
    assert "国企" in joined
    assert "扣非PE 0~20" in joined
    assert "最新股息率 > 3%" in joined
    assert "ST/退市" in joined and "北交所" in joined
    assert "剔除工程链" in joined
    assert "三级行业分组" in joined


def test_condition_lines_empty():
    assert supplement.build_dividend_email_supplement_condition_lines("") == []


# ---------- 本地二次过滤 ----------


def test_filter_excludes_industry():
    rows = [{"SECURITY_CODE": "000001", "SECURITY_SHORT_NAME": "甲", "INDUSTRY_LV3": "基建市政工程"}]
    kept, excluded = supplement.filter_dividend_email_supplement_rows(rows, ttm_fetcher=_ttm_fetcher_yi(10.0))
    assert kept == []
    assert "行业命中排除名单" in excluded[0]["_exclude_reason"]


def test_filter_excludes_pe_ttm_over_max():
    row = {"SECURITY_CODE": "000001", "TOAL_MARKET_VALUE": "300亿"}
    kept, excluded = supplement.filter_dividend_email_supplement_rows([row], ttm_fetcher=_ttm_fetcher_yi(10.0))
    # pe_ttm = 300 / 10 = 30 > 15 -> 剔除
    assert kept == []
    assert "PE-TTM命中排除条件" in excluded[0]["_exclude_reason"]


def test_filter_keeps_empty_pe_ttm():
    row = {"SECURITY_CODE": "000001"}  # 无总市值 -> 不算 PE-TTM -> 保留
    kept, excluded = supplement.filter_dividend_email_supplement_rows([row], ttm_fetcher=_ttm_fetcher_yi(10.0))
    assert len(kept) == 1
    assert excluded == []


def test_filter_keeps_pe_ttm_at_boundary():
    row = {"SECURITY_CODE": "000001", "TOAL_MARKET_VALUE": "150亿"}
    kept, excluded = supplement.filter_dividend_email_supplement_rows([row], ttm_fetcher=_ttm_fetcher_yi(10.0))
    # pe_ttm = 15, 不 > 15 -> 保留
    assert len(kept) == 1
    assert excluded == []


def test_filter_summary_counts():
    rows = [
        {"SECURITY_CODE": "000001", "INDUSTRY_LV3": "基建市政工程"},
        {"SECURITY_CODE": "000002", "TOAL_MARKET_VALUE": "300亿"},
        {"SECURITY_CODE": "000003"},
    ]
    kept, excluded = supplement.filter_dividend_email_supplement_rows(rows, ttm_fetcher=_ttm_fetcher_yi(10.0))
    summary = supplement.summarize_dividend_email_supplement_exclusions(excluded)
    assert summary["industry_excluded_count"] == 1
    assert summary["pe_ttm_excluded_count"] == 1
    assert len(kept) == 1


# ---------- 行业分组 + 排序 ----------


def test_groups_sort_within_and_across():
    rows = [
        {"SECURITY_CODE": "000001", "INDUSTRY_LV3": "煤炭", "PB": "1.0", "DIVIDEND_NEWRATIO_HYY": "5.0%"},
        {"SECURITY_CODE": "000002", "INDUSTRY_LV3": "煤炭", "PB": "0.8", "DIVIDEND_NEWRATIO_HYY": "6.0%"},
        {"SECURITY_CODE": "000003", "INDUSTRY_LV3": "钢铁", "PB": "0.5", "DIVIDEND_NEWRATIO_HYY": "4.0%"},
    ]
    groups = supplement.build_dividend_email_supplement_groups(rows)
    coal = next(g for g in groups if g["industry_name"] == "煤炭")
    # 组内按股息率降序:000002(6%) 在 000001(5%) 前
    assert coal["rows"][0]["row"]["SECURITY_CODE"] == "000002"
    assert coal["rows"][1]["row"]["SECURITY_CODE"] == "000001"
    # 组均值(前 2 名龙头):煤炭 (6+5)/2=5.5 > 钢铁 4.0 -> 煤炭在前
    assert groups[0]["industry_name"] == "煤炭"
    assert groups[1]["industry_name"] == "钢铁"
    assert coal["industry_avg_dividend_rate"] == 5.5


# ---------- 关联转债展示 ----------


def test_format_linked_bonds_normal():
    items = [{"bond_nm": "兴财转债", "bond_id": "123456"}]
    assert supplement.format_linked_bonds_html_from_items(items) == "兴财转债(123456)"


def test_format_linked_bonds_multiple():
    items = [
        {"bond_nm": "甲转债", "bond_id": "111"},
        {"bond_nm": "乙转债", "bond_id": "222"},
    ]
    assert supplement.format_linked_bonds_html_from_items(items) == "甲转债(111)<br>乙转债(222)"


def test_format_linked_bonds_pending_with_progress():
    items = [{"bond_source": "pending", "bond_nm": "待发债", "bond_id": "789", "progress_nm": "董事会预案"}]
    assert supplement.format_linked_bonds_html_from_items(items) == "待发: 待发债(789, 董事会预案)"


def test_format_linked_bonds_pending_no_progress_no_id():
    items = [{"bond_source": "pending", "bond_nm": "待发债", "bond_id": "", "progress_nm": ""}]
    assert supplement.format_linked_bonds_html_from_items(items) == "待发: 待发债"


def test_format_linked_bonds_empty_and_failed():
    assert supplement.format_linked_bonds_html_from_items([], linked_bonds_fetch_failed=True) == "查询失败"
    assert supplement.format_linked_bonds_html_from_items([], linked_bonds_fetch_failed=False) == "-"


# ---------- 组装 ----------


def test_build_supplement_assembly():
    rows = [
        {
            "SECURITY_CODE": "000001",
            "SECURITY_SHORT_NAME": "甲股",
            "INDUSTRY_LV3": "煤炭",
            "NEWEST_PRICE": "10.5",
            "PB": "0.8",
            "DIVIDEND_NEWRATIO_HYY": "6.0%",
            "PE_DYNAMIC": "8.0",
            "TOAL_MARKET_VALUE": "100亿",
        }
    ]
    columns = [
        {"title": "代码", "key": "SECURITY_CODE"},
        {"title": "ROE", "key": "ROE_WEIGHT{2026-08-01}"},
    ]
    result = {"rows": rows, "columns": columns, "xc_id": "xc123", "condition_text": "国企"}
    sup = supplement.build_dividend_email_supplement(result, ttm_fetcher=_ttm_fetcher_yi(10.0))

    assert sup["title"] == "东财条件补充池·xc123"
    assert sup["xc_id"] == "xc123"
    assert sup["condition_text"] == "国企"
    assert len(sup["summary_lines"]) == 6
    assert "PE-TTM" in sup["headers"]
    assert sup["headers"][-3] == "ROE"

    assert len(sup["rows"]) == 1
    spec = sup["rows"][0]
    assert len(spec["cells"]) == 11
    assert spec["cells"][0] == "1"  # 序号
    assert spec["cells"][1] == "煤炭"  # 行业
    assert spec["cells"][2] == "甲股"  # 名称
    assert spec["cells"][3] == "000001"  # 代码
    assert spec["cells"][4] == "10.50"  # 价格
    assert spec["cells"][5] == "6.00%"  # 股息率
    assert spec["cells"][6] == "10.00"  # PE-TTM = 100/10(本地)
    assert spec["cells"][7] == "0.80"  # PB
    assert spec["cells"][10] == "-"  # 关联转债空
    assert "border-top" in spec["row_style"]  # 组首行
    assert sup["excluded_rows"] == []


def test_build_supplement_assembly_two_groups_alternating_styles():
    rows = [
        {"SECURITY_CODE": "000001", "INDUSTRY_LV3": "煤炭", "DIVIDEND_NEWRATIO_HYY": "5.0%", "TOAL_MARKET_VALUE": "100亿"},
        {"SECURITY_CODE": "000002", "INDUSTRY_LV3": "钢铁", "DIVIDEND_NEWRATIO_HYY": "4.0%", "TOAL_MARKET_VALUE": "100亿"},
    ]
    result = {"rows": rows, "columns": [], "xc_id": "xc1", "condition_text": ""}
    sup = supplement.build_dividend_email_supplement(result, ttm_fetcher=_ttm_fetcher_yi(10.0))
    styles = [r["row_style"] for r in sup["rows"]]
    # 两个行业各一行,首行都带 border-top,底色交替
    assert all("border-top" in s for s in styles)
    assert "#FBFCFE" in styles[0]
    assert "#F7FBF8" in styles[1]


# ---------- HTML 片段 ----------


def test_supplement_html_error():
    html = supplement.build_dividend_email_supplement_html(
        {"email_supplement_error": "获取失败: boom"}
    )
    assert len(html) == 1
    assert "东财条件补充池" in html[0]
    assert "获取失败: boom" in html[0]


def test_supplement_html_empty_when_no_supplement():
    assert supplement.build_dividend_email_supplement_html({}) == []


def test_supplement_html_normal():
    data = {
        "email_supplement": {
            "title": "东财条件补充池·xc123",
            "summary_lines": ["行1"],
            "xc_id": "xc123",
            "headers": ["#", "代码"],
            "rows": [{"cells": ["1", "000001"], "row_style": ""}],
        }
    }
    html = supplement.build_dividend_email_supplement_html(data)
    assert len(html) == 2
    assert "东财条件补充池·xc123" in html[0]
    assert "xcid: xc123" in html[0]
    assert "<table" in html[1]


# ---------- 编排入口 / 失败告警 ----------


def test_fetch_supplement_returns_none_when_no_xcid():
    assert supplement.fetch_dividend_email_supplement(xc_id="") is None


def test_fetch_supplement_uses_fetch_and_build(monkeypatch):
    rows = [
        {"SECURITY_CODE": "000001", "SECURITY_SHORT_NAME": "甲", "INDUSTRY_LV3": "煤炭", "TOAL_MARKET_VALUE": "100亿"}
    ]
    fake_result = {"rows": rows, "columns": [], "xc_id": "xc123", "condition_text": "国企"}
    monkeypatch.setattr(
        supplement, "fetch_all_results_by_xcid", lambda xc_id, page_size=100, **kw: fake_result
    )
    sup = supplement.fetch_dividend_email_supplement(xc_id="xc123", ttm_fetcher=_ttm_fetcher_yi(10.0))
    assert sup is not None
    assert sup["xc_id"] == "xc123"
    assert len(sup["rows"]) == 1


def test_failed_alert_text():
    text = supplement.build_dividend_email_supplement_failed_alert_text("xc123", "boom")
    assert "xc123" in text
    assert "boom" in text
    assert "主表继续发送" in text
