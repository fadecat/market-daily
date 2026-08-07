"""rotation run_send 告警验证(P1-2)。不触网。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common import alerts  # noqa: E402
from src.rotation import run, strategy  # noqa: E402


def test_run_send_no_data_alerts_and_returns_1(monkeypatch):
    """P1-2: 策略无数据(state is None)应告警并 return 1,而非静默退出。"""
    monkeypatch.setattr(strategy, "load_state", lambda: None)
    monkeypatch.setattr(strategy, "run_strategy", lambda: None)
    alerted = []
    monkeypatch.setattr(alerts, "notify_alert", lambda title, detail: alerted.append((title, detail)))
    assert run.run_send() == 1
    assert alerted, "无数据应触发告警"
    assert "无数据" in alerted[0][1]


def test_run_send_exception_alerts_and_reraises(monkeypatch):
    """P1-2: 策略异常应告警并向上抛,便于 CI 暴露失败。"""
    monkeypatch.setattr(strategy, "load_state", lambda: None)

    def boom():
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(strategy, "run_strategy", boom)
    alerted = []
    monkeypatch.setattr(alerts, "notify_alert", lambda title, detail: alerted.append((title, detail)))
    with pytest.raises(RuntimeError):
        run.run_send()
    assert alerted and "运行失败" in alerted[0][0]
