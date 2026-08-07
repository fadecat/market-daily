"""rotation run_send 告警 + 发信守卫验证(P1-1/P1-2)。不触网。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common import alerts, email  # noqa: E402
from src.rotation import charts, render, run, strategy  # noqa: E402


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


def test_run_send_skips_when_no_new_trade_day(monkeypatch):
    """P1-1 守卫: last_run_date 未变(无新交易日)应跳过邮件。"""
    prev = {"last_run_date": "2024-01-01", "holdings_history": [{}]}
    state = {"last_run_date": "2024-01-01", "holdings_history": [{}], "portfolio_nav": 1.0}
    monkeypatch.setattr(strategy, "load_state", lambda: prev)
    monkeypatch.setattr(strategy, "run_strategy", lambda **k: state)
    sent = []
    monkeypatch.setattr(email, "send_email", lambda *a, **k: sent.append(1))
    assert run.run_send() == 0
    assert not sent  # 无新日,跳过发信


def test_run_send_sends_after_backfill_despite_shorter_history(monkeypatch):
    """P1-1 守卫: 重回填后 history 变短但 last_run_date 变 -> 应发信,不误跳过。

    回归 run.py 旧守卫 len(history)<=prev_count:重回填后 history 只剩窗口内 ~50 条
    < 旧累计 -> 误判无新日跳过邮件,用户无感知净值重置。
    """
    prev = {"last_run_date": "2024-01-01", "holdings_history": [{}] * 100}
    state = {
        "last_run_date": "2024-02-01",
        "holdings_history": [{"date": "2024-02-01", "nav": 1.2}],  # 重回填后变短
        "portfolio_nav": 1.2,
    }
    monkeypatch.setattr(strategy, "load_state", lambda: prev)
    monkeypatch.setattr(strategy, "run_strategy", lambda **k: state)
    monkeypatch.setattr(strategy, "load_strategy_config", lambda *a, **k: {})
    monkeypatch.setattr(strategy, "build_report", lambda s, c: {"history": state["holdings_history"], "as_of_date": "2024-02-01"})
    monkeypatch.setattr(charts, "generate_nav_chart", lambda *a, **k: None)
    monkeypatch.setattr(render, "build_email_html", lambda *a, **k: "<html>")
    sent = []
    monkeypatch.setattr(email, "send_email", lambda *a, **k: sent.append(1))
    assert run.run_send() == 0
    assert sent  # last_run_date 变,发信(不因 history 变短跳过)
