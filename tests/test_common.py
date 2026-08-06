"""common 公共层测试。不依赖网络/真实凭据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 确保 repo 根目录在 sys.path(用 ``python -m pytest`` 时通常已就绪)
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common import alerts, env, storage  # noqa: E402


# ---------- env ----------

def test_parse_env_file(tmp_path: Path):
    f = tmp_path / ".env.local"
    f.write_text('# 注释\nFOO=bar\nexport BAZ="hello world"\nEMPTY=\n', encoding="utf-8")
    parsed = env.parse_env_file(f)
    assert parsed == {"FOO": "bar", "BAZ": "hello world", "EMPTY": ""}


def test_get_prefers_real_env(monkeypatch, tmp_path: Path):
    f = tmp_path / ".env.local"
    f.write_text("MY_VAR=from_file\n", encoding="utf-8")
    monkeypatch.setenv("MY_VAR", "from_env")
    assert env.get_env_value("MY_VAR", env.parse_env_file(f)) == "from_env"
    # 没有真实 env 时回退文件
    monkeypatch.delenv("MY_VAR", raising=False)
    assert env.get_env_value("MY_VAR", env.parse_env_file(f)) == "from_file"
    # 都没有时用默认
    assert env.get_env_value("NOPE", env.parse_env_file(f), default="d") == "d"


def test_require_raises(monkeypatch):
    monkeypatch.delenv("MUST_EXIST", raising=False)
    env._loaded = {}  # 清掉缓存,避免读到真实 .env.local
    try:
        with pytest.raises(RuntimeError):
            env.require("MUST_EXIST")
    finally:
        env._loaded = None  # 恢复


# ---------- storage ----------

def test_content_hash_stable():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}  # 顺序不同
    assert storage.content_hash(a) == storage.content_hash(b)


def test_state_roundtrip(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(storage, "STATE_DIR", tmp_path / "state")
    storage.save_state("demo", {"k": [1, 2, 3], "date": "2026-08-06"})
    loaded = storage.load_state("demo")
    assert loaded == {"k": [1, 2, 3], "date": "2026-08-06"}
    assert storage.load_state("missing", default={"x": 1}) == {"x": 1}


def test_save_snapshot_dedup(tmp_path: Path):
    path = tmp_path / "snap.json"
    assert storage.save_snapshot(path, {"v": 1}) is True   # 首次写入
    assert storage.save_snapshot(path, {"v": 1}) is False   # 同内容,跳过
    assert storage.save_snapshot(path, {"v": 2}) is True    # 内容变,写入
    snap = storage.load_snapshot(path)
    assert snap["data"] == {"v": 2}
    assert snap["content_hash"] == storage.content_hash({"v": 2})


def test_merge_records_by_key():
    existing = [{"date": "2026-08-01", "v": 1}, {"date": "2026-08-02", "v": 2}]
    incoming = [{"date": "2026-08-02", "v": 20}, {"date": "2026-08-03", "v": 3}]
    merged = storage.merge_records_by_key(existing, incoming, key="date")
    assert [r["date"] for r in merged] == ["2026-08-01", "2026-08-02", "2026-08-03"]
    assert merged[1]["v"] == 20  # incoming 覆盖


def test_merge_archive(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(storage, "ARCHIVE_DIR", tmp_path / "archive")
    p1 = storage.merge_archive(
        "index_eod", {"index_code": "000300"}, [{"date": "2026-08-05", "close": 4000}],
        merge_key="date", source="test", updated_at="2026-08-06T00:00:00",
    )
    assert p1 is not None and p1.exists()
    # 同数据再合,无变更
    p2 = storage.merge_archive(
        "index_eod", {"index_code": "000300"}, [{"date": "2026-08-05", "close": 4000}],
        merge_key="date", source="test", updated_at="2026-08-06T00:00:00",
    )
    assert p2 is None
    # 新日期
    p3 = storage.merge_archive(
        "index_eod", {"index_code": "000300"}, [{"date": "2026-08-06", "close": 4050}],
        merge_key="date", source="test", updated_at="2026-08-07T00:00:00",
    )
    assert p3 is not None
    records = storage.load_existing_records(p3)
    assert {r["date"] for r in records} == {"2026-08-05", "2026-08-06"}


# ---------- alerts ----------

def test_run_with_retry_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert alerts.run_with_retry("t", fn) == "ok"
    assert calls["n"] == 1


def test_run_with_retry_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(alerts.time, "sleep", lambda s: None)  # 跳过真实等待
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise requests_connection_error()
        return "ok"

    assert alerts.run_with_retry("t", fn, retries=3) == "ok"
    assert state["n"] == 3


def test_run_with_retry_non_retryable_raises():
    def fn():
        raise ValueError("bad arg")

    with pytest.raises(ValueError):
        alerts.run_with_retry("t", fn)


def test_is_retryable_error():
    import requests as _r
    assert alerts.is_retryable_error(_r.exceptions.ConnectionError()) is True
    assert alerts.is_retryable_error(_r.exceptions.Timeout("timed out")) is True
    assert alerts.is_retryable_error(ValueError("bad")) is False


def test_notify_alert_no_webhook(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK", raising=False)
    env._loaded = {}
    try:
        assert alerts.notify_alert("title", "detail") is False  # 无 webhook,仅日志
    finally:
        env._loaded = None


def requests_connection_error():
    import requests as _r
    return _r.exceptions.ConnectionError("connection aborted")


# ---------- email ----------

def test_render_table_contains_table():
    from src.common import email
    html = email.render_table(["A", "B"], [{"cells": ["1", "2"]}])
    assert "<table" in html and "<th" in html and "<td" in html


def test_compose_sections_joins():
    from src.common import email
    out = email.compose_sections(["<div>sec1</div>", "<div>sec2</div>"])
    assert "sec1" in out and "sec2" in out and "<hr" in out


def test_build_message_subject_and_inline(tmp_path: Path):
    from src.common import email
    img = tmp_path / "chart.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG 头
    msg = email.build_message(
        subject="测试",
        html=email.compose_sections(['<img src="cid:chart">']),
        sender="a@b.com",
        recipients=["x@y.com"],
        inline_images={"chart": str(img)},
    )
    assert msg["Subject"] == "测试"
    assert msg["To"] == "x@y.com"
    html_part = msg.get_body(preferencelist=("html",))
    assert html_part is not None


# ---------- fonts ----------

def test_resolve_font_returns_str():
    from src.common import fonts
    assert isinstance(fonts.resolve_font(), str)


# ---------- jisilu ----------

def test_jslencode_deterministic():
    from src.common import jisilu
    try:
        from Crypto.Cipher import AES  # noqa: F401
    except ImportError:
        pytest.skip("pycryptodome 未安装")
    a = jisilu.jslencode("hello")
    b = jisilu.jslencode("hello")
    assert a == b and len(a) > 0
    # 不同输入不同输出
    assert jisilu.jslencode("hello") != jisilu.jslencode("world")
