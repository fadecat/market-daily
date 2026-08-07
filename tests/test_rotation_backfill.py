"""rotation backfill/replay_forward/run_strategy 测试(P1-1)。纯函数 + mock,不触网。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.rotation import strategy  # noqa: E402


def _frame(n: int = 6) -> pd.DataFrame:
    dates = [f"2024-01-{i:02d}" for i in range(1, n + 1)]
    return pd.DataFrame(
        {"A": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0][:n],
         "B": [10.0, 9.0, 8.0, 7.0, 6.0, 5.0][:n]},
        index=pd.Index(dates, name="date"),
    )


def test_replay_forward_nav_continuity_from_start_nav():
    """replay_forward 净值从 start_nav 连续累乘(无跳变)。"""
    frame = _frame()
    entries, nav, _ = strategy.replay_forward(
        frame, None, ["A", "B"], "B", lookback=1, start_idx=1, start_nav=2.0
    )
    assert entries[0]["prev_nav"] == pytest.approx(2.0)
    for e in entries:
        assert e["nav"] == pytest.approx(e["prev_nav"] * (1 + e["daily_return"]))
    assert nav == pytest.approx(entries[-1]["nav"], rel=1e-4)


def test_replay_forward_signal_decides_next_holding():
    """无未来函数:start_idx 的信号决定 start_idx+1 的持仓。"""
    frame = _frame()
    entries, _, _ = strategy.replay_forward(
        frame, None, ["A", "B"], "B", lookback=1, start_idx=1, start_nav=1.0
    )
    signal = strategy.compute_returns_at(frame, 1, ["A", "B"], lookback=1)
    assert strategy.select_holding(signal, "B") == "A"  # A 涨 B 跌 -> 选 A
    assert entries[0]["holding"] == "A"


def test_backfill_starts_at_lookback():
    """backfill 从 lookback 之后开始,首条 date = frame.index[lookback+1]。"""
    frame = _frame(6)
    entries, _, _ = strategy.backfill(
        frame, None, ["A", "B"], "B", lookback=2, initial_nav=1.0
    )
    assert entries[0]["date"] == frame.index[3]


def _mock_config() -> dict:
    return {
        "universe": [{"code": "A", "jisilu_category": "etf"}, {"code": "B", "jisilu_category": "etf"}],
        "fallback_holding": {"code": "C"},
        "strategy": {"initial_nav": 1.0, "lookback_days": 1},
    }


def test_run_strategy_incremental_extends_history(monkeypatch):
    """增量分支:last_date in index -> 续接旧 portfolio_nav + extend history。"""
    monkeypatch.setattr(strategy, "load_strategy_config", lambda path=None: _mock_config())
    frame = _frame(6)
    monkeypatch.setattr(strategy, "load_universe_prices", lambda *a, **k: (frame, None, frame.index[-1], {}))
    old_state = {
        "last_run_date": frame.index[2],
        "portfolio_nav": 2.0,
        "holdings_history": [{"date": frame.index[1], "nav": 1.5}, {"date": frame.index[2], "nav": 2.0}],
        "next_holding": "A",
    }
    monkeypatch.setattr(strategy, "load_state", lambda: old_state)
    monkeypatch.setattr(strategy, "save_state", lambda s: None)
    state = strategy.run_strategy(cookie="fake", session=MagicMock())
    assert state["last_run_date"] == frame.index[-1]
    assert state["holdings_history"][0]["date"] == frame.index[1]  # 旧 history 保留
    assert len(state["holdings_history"]) > 2  # extend 新交易日


def test_run_strategy_backfill_preserves_history_and_alerts(monkeypatch):
    """重回填分支:last_date not in index -> 保留旧 history + 续接旧 nav + 告警(不归零/不静默)。"""
    monkeypatch.setattr(strategy, "load_strategy_config", lambda path=None: _mock_config())
    frame = _frame(6)
    monkeypatch.setattr(strategy, "load_universe_prices", lambda *a, **k: (frame, None, frame.index[-1], {}))
    old_state = {
        "last_run_date": "2023-01-01",  # 不在 frame -> 触发重回填
        "portfolio_nav": 2.5,
        "holdings_history": [{"date": "2023-01-01", "nav": 2.5}],
        "next_holding": "A",
    }
    monkeypatch.setattr(strategy, "load_state", lambda: old_state)
    monkeypatch.setattr(strategy, "save_state", lambda s: None)
    alerted = []
    monkeypatch.setattr(strategy.alerts, "notify_alert", lambda t, d: alerted.append((t, d)))
    state = strategy.run_strategy(cookie="fake", session=MagicMock())
    # 旧 history 保留
    assert state["holdings_history"][0]["date"] == "2023-01-01"
    # 续接旧 nav(backfill initial_nav=2.5),不归零 1.0
    assert state["portfolio_nav"] != pytest.approx(1.0)
    # 告警(不静默)
    assert alerted and "不在数据窗口" in alerted[0][1]
