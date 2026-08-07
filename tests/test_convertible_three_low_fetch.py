"""可转债三低 fetch 重试测试(不触网,mock run_with_retry)。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible.three_low import strategy  # noqa: E402


def test_fetch_cb_list_uses_run_with_retry(monkeypatch):
    """fetch_cb_list 应包 run_with_retry,网络层重试交给它。"""
    captured = {}

    def fake_retry(name, fn, **kw):
        captured["name"] = name
        return [{"id": i} for i in range(31)]

    monkeypatch.setattr(strategy.alerts, "run_with_retry", fake_retry)
    rows = strategy.fetch_cb_list(session=MagicMock())
    assert len(rows) == 31
    assert captured["name"] == "转债三低 cb_list"


def test_fetch_cb_list_inner_raises_on_short_rows(monkeypatch):
    """run_with_retry 直接执行 fn 时,≤30 条应抛 ValueError(会话失效,不重试)。"""
    monkeypatch.setattr(strategy.alerts, "run_with_retry", lambda name, fn, **kw: fn())
    session = MagicMock()
    session.post.return_value.json.return_value = {"rows": [{"id": 1}] * 10}
    session.post.return_value.raise_for_status.return_value = None
    with pytest.raises(ValueError):
        strategy.fetch_cb_list(session)


def test_fetch_redeem_list_uses_run_with_retry(monkeypatch):
    captured = {}

    def fake_retry(name, fn, **kw):
        captured["name"] = name
        return [{"id": 1}]

    monkeypatch.setattr(strategy.alerts, "run_with_retry", fake_retry)
    rows = strategy.fetch_redeem_list(session=MagicMock())
    assert rows == [{"id": 1}]
    assert captured["name"] == "转债三低 redeem_list"
