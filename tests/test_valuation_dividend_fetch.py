"""src/valuation/dividend/fetch.py 单测。

覆盖:fetch_data(POST 参数/表单/cookie + ensure_dividend_report_meta 包装 + cookie=None
登录兜底 + 重试)、build_dividend_email_data(关联转债注入 + 拷贝隔离 + 补充池挂载)、
prepare_dividend_email_data(happy path / cb_reference 失败 / 补充池失败告警且主表继续 /
cookie=None 登录)。
"""
from __future__ import annotations

import types
from typing import Any

import pytest
import requests

from src.valuation.dividend import fetch


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
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

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


def _no_sleep(monkeypatch):
    from src.common import alerts as alerts_mod

    monkeypatch.setattr(alerts_mod, "time", types.SimpleNamespace(sleep=lambda *a, **k: None))


# ---------- fetch_data ----------


def test_fetch_data_posts_and_wraps_meta():
    raw = {"rows": [{"cell": {"stock_id": "000001"}}]}
    sess = _FakeSession([_FakeResponse(raw)])
    data = fetch.fetch_data(cookie="c=1", session=sess, timestamp_ms=42)
    method, url, kwargs = sess.calls[0]
    assert method == "POST"
    assert url == fetch.JISILU_DIVIDEND_URL
    assert kwargs["params"] == {"___jsl": "LST___t=42"}
    assert kwargs["data"] == fetch.DIVIDEND_FORM_DATA
    assert kwargs["headers"]["Cookie"] == "c=1"
    # ensure_dividend_report_meta 补齐元数据
    assert data["raw_returned_count"] == 1
    assert data["filter_steps"] == []
    assert data["rows"] == raw["rows"]


def test_fetch_data_logs_in_when_cookie_none(monkeypatch):
    monkeypatch.setattr(fetch, "get_cookie", lambda: "autologin=1")
    sess = _FakeSession([_FakeResponse({"rows": []})])
    fetch.fetch_data(session=sess)
    assert sess.calls[0][2]["headers"]["Cookie"] == "autologin=1"


def test_fetch_data_retries_on_transient(monkeypatch):
    _no_sleep(monkeypatch)
    raw = {"rows": []}
    sess = _FakeSession([requests.exceptions.ConnectionError("x"), _FakeResponse(raw)])
    data = fetch.fetch_data(cookie="c", session=sess)
    assert len(sess.calls) == 2
    assert data["raw_returned_count"] == 0


# ---------- build_dividend_email_data ----------


def test_build_dividend_email_data_injects_linked_bonds():
    data = {"rows": [{"cell": {"stock_id": "000001"}, "id": "x"}]}
    bonds_map = {"000001": [{"bond_id": "111", "bond_nm": "甲", "bond_source": "listed"}]}
    out = fetch.build_dividend_email_data(
        data,
        stock_to_bonds_map=bonds_map,
        linked_bonds_fetch_failed=False,
        email_supplement={"title": "sup"},
        email_supplement_error="",
    )
    cell = out["rows"][0]["cell"]
    assert cell["linked_bonds"] == [{"bond_id": "111", "bond_nm": "甲", "bond_source": "listed"}]
    assert cell["linked_bonds_fetch_failed"] is False
    assert out["email_supplement"] == {"title": "sup"}
    # linked_bonds 是拷贝,不与原映射同引用
    assert cell["linked_bonds"] is not bonds_map["000001"]
    # 原 cell 字段保留
    assert cell["stock_id"] == "000001"


def test_build_dividend_email_data_empty_map_defaults():
    data = {"rows": [{"cell": {"stock_id": "999999"}}]}
    out = fetch.build_dividend_email_data(data)
    assert out["rows"][0]["cell"]["linked_bonds"] == []
    assert out["rows"][0]["cell"]["linked_bonds_fetch_failed"] is False
    assert out["email_supplement"] is None
    assert out["email_supplement_error"] == ""


# ---------- prepare_dividend_email_data ----------


def _data():
    return {"rows": [{"cell": {"stock_id": "000001"}}], "raw_returned_count": 1}


def test_prepare_happy_path():
    def fake_cb(cookie):
        return {"000001": [{"bond_id": "111", "bond_nm": "甲", "bond_source": "listed"}]}

    def fake_supplement(stock_to_bonds_map=None, linked_bonds_fetch_failed=False, ttm_fetcher=None):
        return {"title": "sup"}

    out = fetch.prepare_dividend_email_data(
        _data(),
        cookie="c",
        cb_reference_fetcher=fake_cb,
        supplement_fetcher=fake_supplement,
        ttm_fetcher=lambda code: {"ttm_value_yi": 10.0},
        alert_sender=lambda title, detail: None,
    )
    assert out["email_supplement"] == {"title": "sup"}
    assert out["email_supplement_error"] == ""
    assert out["rows"][0]["cell"]["linked_bonds"][0]["bond_id"] == "111"
    assert out["rows"][0]["cell"]["linked_bonds_fetch_failed"] is False


def test_prepare_cb_reference_failure_sets_flag_but_supplement_continues():
    sup_calls = []

    def boom_cb(cookie):
        raise RuntimeError("jisilu down")

    def fake_supplement(stock_to_bonds_map=None, linked_bonds_fetch_failed=False, ttm_fetcher=None):
        sup_calls.append(linked_bonds_fetch_failed)
        return {"title": "sup"}

    out = fetch.prepare_dividend_email_data(
        _data(),
        cookie="c",
        cb_reference_fetcher=boom_cb,
        supplement_fetcher=fake_supplement,
        ttm_fetcher=lambda code: {"ttm_value_yi": 10.0},
        alert_sender=lambda title, detail: None,
    )
    assert out["rows"][0]["cell"]["linked_bonds_fetch_failed"] is True
    assert out["rows"][0]["cell"]["linked_bonds"] == []
    # 补充池仍被调用,且收到 linked_bonds_fetch_failed=True
    assert sup_calls == [True]
    assert out["email_supplement"] == {"title": "sup"}


def test_prepare_supplement_failure_alerts_and_keeps_main_table():
    alerts = []

    def fake_supplement(stock_to_bonds_map=None, linked_bonds_fetch_failed=False, ttm_fetcher=None):
        raise RuntimeError("xuangu boom")

    out = fetch.prepare_dividend_email_data(
        _data(),
        cookie="c",
        cb_reference_fetcher=lambda cookie: {},
        supplement_fetcher=fake_supplement,
        ttm_fetcher=lambda code: {"ttm_value_yi": 10.0},
        alert_sender=lambda title, detail: alerts.append((title, detail)),
    )
    assert out["email_supplement"] is None
    assert "东财条件补充池获取失败" in out["email_supplement_error"]
    assert "xuangu boom" in out["email_supplement_error"]
    assert len(alerts) == 1
    assert alerts[0][0] == "高股息补充池获取失败"
    assert "xc" in alerts[0][1]  # detail 含 xcid
    # 主表数据仍在
    assert "rows" in out
    assert out["rows"][0]["cell"]["linked_bonds_fetch_failed"] is False


def test_prepare_supplement_failure_alert_send_error_swallowed():
    def bad_alert(title, detail):
        raise RuntimeError("webhook down")

    def fake_supplement(stock_to_bonds_map=None, linked_bonds_fetch_failed=False, ttm_fetcher=None):
        raise RuntimeError("xuangu boom")

    # 告警本身失败不应中断主表构建
    out = fetch.prepare_dividend_email_data(
        _data(),
        cookie="c",
        cb_reference_fetcher=lambda cookie: {},
        supplement_fetcher=fake_supplement,
        ttm_fetcher=lambda code: {"ttm_value_yi": 10.0},
        alert_sender=bad_alert,
    )
    assert "东财条件补充池获取失败" in out["email_supplement_error"]
    assert "rows" in out


def test_prepare_cookie_none_logs_in(monkeypatch):
    monkeypatch.setattr(fetch, "get_cookie", lambda: "auto=1")
    cb_calls = []

    def fake_cb(cookie):
        cb_calls.append(cookie)
        return {}

    fetch.prepare_dividend_email_data(
        _data(),
        cb_reference_fetcher=fake_cb,
        supplement_fetcher=lambda **kw: None,
        ttm_fetcher=lambda code: {"ttm_value_yi": 10.0},
        alert_sender=lambda title, detail: None,
    )
    assert cb_calls == ["auto=1"]
