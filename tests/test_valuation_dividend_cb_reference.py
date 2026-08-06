"""src/valuation/dividend/cb_reference.py 单测。

覆盖:已上市/待发转债行拉取(POST 参数/表头/cookie + 重试)、进度文本清洗、待发名称
兜底、去重 key、build_stock_to_bonds_map(已上市/待发两路 + 去重 + 排序 + 待发附加字段
+ 跳过非法行)、merge_stock_to_bonds_maps(合并去重 + 已上市优先)、便捷入口。
"""
from __future__ import annotations

import types
from typing import Any

import pytest
import requests

from src.valuation.dividend import cb_reference


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


# ---------- fetch_listed_cb_rows / fetch_pending_cb_rows ----------


def test_fetch_listed_cb_rows_posts_with_cookie_and_params():
    rows = [{"cell": {"stock_id": "000001", "bond_id": "123456", "bond_nm": "甲"}}]
    sess = _FakeSession([_FakeResponse({"rows": rows})])
    out = cb_reference.fetch_listed_cb_rows("cookie=abc", session=sess, timestamp_ms=123)
    assert out == rows
    method, url, kwargs = sess.calls[0]
    assert method == "POST"
    assert url == cb_reference.CB_REFERENCE_URL
    assert kwargs["params"] == {"___jsl": "LST___t=123"}
    assert kwargs["data"] == cb_reference.LISTED_CB_FORM_DATA
    assert kwargs["headers"]["Cookie"] == "cookie=abc"
    assert kwargs["headers"]["Referer"] == "https://www.jisilu.cn/data/cbnew/"


def test_fetch_listed_cb_rows_missing_rows_returns_empty():
    sess = _FakeSession([_FakeResponse({})])
    assert cb_reference.fetch_listed_cb_rows("c", session=sess) == []


def test_fetch_pending_cb_rows_uses_pre_url():
    sess = _FakeSession([_FakeResponse({"rows": [{"cell": {}}]})])
    cb_reference.fetch_pending_cb_rows("c", session=sess, timestamp_ms=1)
    assert sess.calls[0][1] == cb_reference.CB_PRE_REFERENCE_URL
    assert sess.calls[0][2]["data"] == {}


def test_fetch_listed_cb_rows_retries_on_transient(monkeypatch):
    _no_sleep(monkeypatch)
    rows = [{"cell": {"stock_id": "000001"}}]
    sess = _FakeSession(
        [requests.exceptions.ConnectionError("boom"), _FakeResponse({"rows": rows})]
    )
    out = cb_reference.fetch_listed_cb_rows("c", session=sess)
    assert out == rows
    assert len(sess.calls) == 2


# ---------- helpers ----------


def test_normalize_progress_text_strips_html():
    assert cb_reference.normalize_progress_text("<b>董事会</b>  通过") == "董事会 通过"
    assert cb_reference.normalize_progress_text(None) == ""


def test_pending_bond_display_name_fallbacks():
    assert cb_reference.pending_bond_display_name({"bond_nm": "甲转债"}) == "甲转债"
    assert cb_reference.pending_bond_display_name({"stock_nm": "乙公司"}) == "乙公司转债"
    assert cb_reference.pending_bond_display_name({}) == ""


def test_bond_dedupe_key():
    assert cb_reference.bond_dedupe_key({"bond_id": "123456"}) == "id:123456"
    assert cb_reference.bond_dedupe_key({"bond_nm": "甲"}) == "name:甲"
    assert cb_reference.bond_dedupe_key({}) == ""


# ---------- build_stock_to_bonds_map ----------


def test_build_map_listed_dedup_and_skip():
    rows = [
        {"cell": {"stock_id": "000001", "bond_id": "123456", "bond_nm": "甲转债"}},
        {"cell": {"stock_id": "000001", "bond_id": "123456", "bond_nm": "甲转债"}},  # 重复
        {"cell": {"stock_id": "000002", "bond_id": "234567", "bond_nm": "乙转债"}},
        {"cell": {"stock_id": "", "bond_id": "999", "bond_nm": "无主"}},  # 无 stock_id
        "not a dict",  # 跳过
        {"cell": "not dict"},  # 跳过
        {"cell": {"stock_id": "000003"}},  # 已上市需 bond_id+nm -> 跳过
    ]
    m = cb_reference.build_stock_to_bonds_map(rows, "listed")
    assert set(m.keys()) == {"000001", "000002"}
    assert len(m["000001"]) == 1  # 去重
    assert m["000001"][0] == {"bond_id": "123456", "bond_nm": "甲转债", "bond_source": "listed"}


def test_build_map_listed_sorted_by_bond_id():
    rows = [
        {"cell": {"stock_id": "000001", "bond_id": "222", "bond_nm": "乙"}},
        {"cell": {"stock_id": "000001", "bond_id": "111", "bond_nm": "甲"}},
    ]
    m = cb_reference.build_stock_to_bonds_map(rows, "listed")
    # bond_id 归一化为 6 位后按字符串排序
    assert [b["bond_id"] for b in m["000001"]] == ["000111", "000222"]


def test_build_map_pending_extras_and_name_fallback():
    rows = [
        {
            "cell": {
                "stock_id": "000001",
                "bond_id": "111",
                "bond_nm": "待发甲",
                "progress_nm": "<b>董事会</b>",
                "progress_dt": "2026-01-01",
                "list_date": "2026-03-01",
            }
        },
        {"cell": {"stock_id": "000002", "bond_id": "", "bond_nm": "", "stock_nm": "乙公司", "progress_nm": "股东大会"}},
    ]
    m = cb_reference.build_stock_to_bonds_map(rows, "pending")
    assert m["000001"][0]["progress_nm"] == "董事会"  # HTML 已清洗
    assert m["000001"][0]["progress_dt"] == "2026-01-01"
    assert m["000001"][0]["list_date"] == "2026-03-01"
    assert m["000001"][0]["bond_source"] == "pending"
    # bond_nm 空时用 stock_nm 兜底
    assert m["000002"][0]["bond_nm"] == "乙公司转债"
    assert m["000002"][0]["bond_source"] == "pending"


# ---------- merge_stock_to_bonds_maps ----------


def test_merge_dedup_listed_first():
    listed = {"000001": [{"bond_id": "111", "bond_nm": "甲", "bond_source": "listed"}]}
    pending = {
        "000001": [
            {"bond_id": "222", "bond_nm": "待发甲", "bond_source": "pending"},
            {"bond_id": "111", "bond_nm": "甲", "bond_source": "pending"},  # 与已上市同 id -> 去重
        ]
    }
    merged = cb_reference.merge_stock_to_bonds_maps(listed, pending)
    assert len(merged["000001"]) == 2  # listed 111 + pending 222
    assert merged["000001"][0]["bond_source"] == "listed"  # 已上市在前
    assert merged["000001"][1]["bond_id"] == "222"


def test_merge_disjoint_stocks():
    a = {"000001": [{"bond_id": "111", "bond_nm": "甲", "bond_source": "listed"}]}
    b = {"000002": [{"bond_id": "222", "bond_nm": "乙", "bond_source": "listed"}]}
    merged = cb_reference.merge_stock_to_bonds_maps(a, b)
    assert set(merged.keys()) == {"000001", "000002"}


# ---------- fetch_stock_to_listed_bonds_map ----------


def test_fetch_stock_to_listed_bonds_map_integrates(monkeypatch):
    listed = [{"cell": {"stock_id": "000001", "bond_id": "111", "bond_nm": "甲"}}]
    pending = [{"cell": {"stock_id": "000001", "bond_id": "222", "bond_nm": "待发", "progress_nm": "预案"}}]
    monkeypatch.setattr(
        cb_reference, "fetch_listed_cb_rows", lambda cookie, session=None, timestamp_ms=None: listed
    )
    monkeypatch.setattr(
        cb_reference, "fetch_pending_cb_rows", lambda cookie, session=None, timestamp_ms=None: pending
    )
    m = cb_reference.fetch_stock_to_listed_bonds_map("cookie")
    ids = [b["bond_id"] for b in m["000001"]]
    assert "000111" in ids and "000222" in ids
    sources = {b["bond_source"] for b in m["000001"]}
    assert sources == {"listed", "pending"}
