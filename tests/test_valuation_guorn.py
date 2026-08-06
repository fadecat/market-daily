"""src/valuation/guorn.py 单测。

覆盖:headers 校验、fetch payload(成功/status 非 ok/非 dict)、latest_date 解析(斜杠日期
归一化、缺失)、industry rows 提取(过滤非 dict、缺失/空)、归档(created/unchanged/updated
+ content_hash 去重 + 原始 payload 格式)、便捷入口 fetch_industry_valuation。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.valuation import guorn


def _payload(
    *,
    latest_date: str = "2026/07/30",
    industry: List[Dict[str, Any]] | None = None,
    status: str = "ok",
    extra_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if industry is None:
        industry = [
            {"ticker": "801010", "name": "农林牧渔", "PE": 30.1, "PBPercentile": 0.12},
            {"ticker": "801020", "name": "采掘", "PE": 8.4, "PBPercentile": 0.05},
        ]
    data: Dict[str, Any] = {"latest_date": latest_date, "pepb": {"industry": industry}}
    if extra_data:
        data.update(extra_data)
    return {"status": status, "data": data}


class _FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


# ---------- build_guorn_meta_headers ----------


def test_build_headers_raises_on_empty_cookie():
    with pytest.raises(RuntimeError, match="GUORN_COOKIE"):
        guorn.build_guorn_meta_headers("")
    with pytest.raises(RuntimeError, match="GUORN_COOKIE"):
        guorn.build_guorn_meta_headers("   ")


def test_build_headers_includes_cookie_and_referer():
    headers = guorn.build_guorn_meta_headers("sessid=abc123")
    assert headers["Cookie"] == "sessid=abc123"
    assert headers["Referer"] == "https://guorn.com/stock/query/"
    assert headers["X-Requested-With"] == "XMLHttpRequest"


# ---------- fetch_guorn_meta_payload ----------


def test_fetch_payload_ok(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return _FakeResponse(_payload())

    monkeypatch.setattr(guorn.requests, "get", fake_get)
    payload = guorn.fetch_guorn_meta_payload("sessid=abc", request_ts=1700000000000)
    assert payload["status"] == "ok"
    assert captured["url"] == guorn.GUORN_META_URL
    assert captured["params"] == {"_": 1700000000000}
    assert captured["headers"]["Cookie"] == "sessid=abc"
    assert captured["timeout"] == guorn.DEFAULT_GUORN_TIMEOUT


def test_fetch_payload_status_not_ok(monkeypatch):
    monkeypatch.setattr(guorn.requests, "get", lambda *a, **k: _FakeResponse({"status": "error"}))
    with pytest.raises(ValueError, match="status not ok"):
        guorn.fetch_guorn_meta_payload("c")


def test_fetch_payload_non_dict(monkeypatch):
    monkeypatch.setattr(guorn.requests, "get", lambda *a, **k: _FakeResponse(["not", "an", "object"]))
    with pytest.raises(ValueError, match="must be an object"):
        guorn.fetch_guorn_meta_payload("c")


def test_fetch_payload_retries_on_transient_error(monkeypatch):
    """run_with_retry 对网络异常自动重试,最终成功。"""
    import requests as real_requests

    calls = {"n": 0}

    def flaky_get(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise real_requests.exceptions.ConnectionError("boom")
        return _FakeResponse(_payload())

    monkeypatch.setattr(guorn.requests, "get", flaky_get)
    monkeypatch.setattr(guorn.time, "sleep", lambda *_: None)  # 跳过退避
    payload = guorn.fetch_guorn_meta_payload("c")
    assert payload["status"] == "ok"
    assert calls["n"] == 2


# ---------- extract_guorn_latest_date ----------


def test_extract_latest_date_normalizes_slash_format():
    assert guorn.extract_guorn_latest_date(_payload(latest_date="2026/07/30")) == "2026-07-30"


def test_extract_latest_date_missing_raises():
    with pytest.raises(ValueError, match="latest_date missing"):
        guorn.extract_guorn_latest_date({"status": "ok", "data": {"pepb": {"industry": []}}})


def test_extract_latest_date_missing_data_raises():
    with pytest.raises(ValueError, match="missing data"):
        guorn.extract_guorn_latest_date({"status": "ok"})


# ---------- extract_guorn_industry_valuation_rows ----------


def test_extract_industry_rows_returns_dicts():
    rows = guorn.extract_guorn_industry_valuation_rows(_payload())
    assert len(rows) == 2
    assert rows[0]["name"] == "农林牧渔"


def test_extract_industry_rows_filters_non_dict():
    payload = _payload(industry=[{"ticker": "1"}, "junk", 42, None, {"ticker": "2"}])
    rows = guorn.extract_guorn_industry_valuation_rows(payload)
    assert [r["ticker"] for r in rows] == ["1", "2"]


def test_extract_industry_rows_missing_pepb_raises():
    with pytest.raises(ValueError, match="missing pepb"):
        guorn.extract_guorn_industry_valuation_rows({"status": "ok", "data": {"latest_date": "2026/07/30"}})


def test_extract_industry_rows_empty_raises():
    with pytest.raises(ValueError, match="rows missing"):
        guorn.extract_guorn_industry_valuation_rows(_payload(industry=[]))


def test_extract_industry_rows_missing_data_raises():
    with pytest.raises(ValueError, match="missing data"):
        guorn.extract_guorn_industry_valuation_rows({"status": "ok"})


# ---------- archive_guorn_meta_snapshot ----------


def test_archive_creates_then_unchanged_then_updated(tmp_path):
    payload = _payload()
    result = guorn.archive_guorn_meta_snapshot(payload, archive_root=tmp_path)
    assert result["status"] == "created"
    out: Path = result["path"]
    assert out == tmp_path / "guorn_meta" / "2026-07-30.json"
    assert out.exists()

    # 同内容再归档 -> unchanged,不重写
    result2 = guorn.archive_guorn_meta_snapshot(payload, archive_root=tmp_path)
    assert result2["status"] == "unchanged"

    # 改内容 -> updated,覆盖
    payload2 = _payload(industry=[{"ticker": "801010", "name": "农林牧渔", "PE": 31.0}])
    result3 = guorn.archive_guorn_meta_snapshot(payload2, archive_root=tmp_path)
    assert result3["status"] == "updated"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["data"]["pepb"]["industry"][0]["PE"] == 31.0


def test_archive_writes_raw_payload_format(tmp_path):
    """归档文件是原始 payload(顶层 data/status),不包 content_hash 信封,兼容旧快照。"""
    guorn.archive_guorn_meta_snapshot(_payload(), archive_root=tmp_path)
    out = tmp_path / "guorn_meta" / "2026-07-30.json"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert set(written.keys()) == {"status", "data"}
    assert written["status"] == "ok"
    assert "latest_date" in written["data"]
    assert "pepb" in written["data"]


def test_archive_overwrites_corrupt_file(tmp_path):
    """既有文件损坏(非法 JSON)时,直接覆盖而非卡在解析异常。"""
    out = tmp_path / "guorn_meta" / "2026-07-30.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{not valid json", encoding="utf-8")
    result = guorn.archive_guorn_meta_snapshot(_payload(), archive_root=tmp_path)
    assert result["status"] == "updated"
    json.loads(out.read_text(encoding="utf-8"))  # 现在是合法 JSON


# ---------- fetch_industry_valuation ----------


def test_fetch_industry_valuation_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(
        guorn,
        "fetch_guorn_meta_payload",
        lambda cookie, request_ts=None: _payload(),
    )
    snapshot = guorn.fetch_industry_valuation("sessid=abc", archive_root=tmp_path)
    assert snapshot.latest_date == "2026-07-30"
    assert len(snapshot.industry_rows) == 2
    assert snapshot.industry_rows[1]["name"] == "采掘"
    # archive 也真实落盘到 tmp_path
    assert (tmp_path / "guorn_meta" / "2026-07-30.json").exists()
