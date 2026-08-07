"""集思录登录层测试(P2-16):run_with_retry 包裹 + 账密错空串 + message 不误导。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common import alerts, jisilu  # noqa: E402


def test_login_jisilu_uses_run_with_retry(monkeypatch):
    """P2-16: 登录 POST 应包 run_with_retry(网络层退避重试)。"""
    captured = {}

    def fake_retry(name, fn, **kw):
        captured["name"] = name
        return "fake_cookie"

    monkeypatch.setattr(alerts, "run_with_retry", fake_retry)
    assert jisilu.login_jisilu(username="u", password="p") == "fake_cookie"
    assert captured["name"] == "集思录登录"


def test_login_jisilu_bad_credentials_returns_empty(monkeypatch):
    """P2-16: 账密错(code!=200)返回空串,不重试(fn 内 return "")。"""
    monkeypatch.setattr(alerts, "run_with_retry", lambda name, fn, **kw: fn())
    session = MagicMock()
    session.post.return_value.json.return_value = {"code": 400, "msg": "密码错误"}
    session.post.return_value.raise_for_status.return_value = None
    assert jisilu.login_jisilu(username="u", password="p", session=session) == ""


def test_make_session_failure_message_mentions_network(monkeypatch):
    """P2-16: 登录失败 RuntimeError 不应只指账密,需提示网络(避免网络故障被报成账密错)。"""
    monkeypatch.setattr(jisilu, "login_jisilu", lambda *a, **k: "")
    with pytest.raises(RuntimeError, match="网络"):
        jisilu.make_session()
