"""资产轮动板块测试(纯函数,不触网)。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.rotation import charts, etf_data, render, strategy  # noqa: E402


def test_parse_float():
    assert strategy.parse_float("1,234.5") == 1234.5
    assert strategy.parse_float("-") is None
    assert strategy.parse_float(None) is None
    assert strategy.parse_float("  3.14 ") == 3.14


def test_normalize_dataframe():
    df = pd.DataFrame({"日期": ["2024-01-02", "2024-01-03"], "收盘": [10.0, 11.0]})
    out = etf_data._normalize_dataframe(df)
    assert list(out.columns) == ["date", "close"]
    assert len(out) == 2
    assert float(out.iloc[1]["close"]) == 11.0


def test_add_exchange_prefix():
    assert etf_data._add_exchange_prefix_if_needed("511880") == "sh511880"
    assert etf_data._add_exchange_prefix_if_needed("159967") == "sz159967"
    assert etf_data._add_exchange_prefix_if_needed("sh510300") == "sh510300"


def test_build_tickflow_etf_symbols():
    assert "511880.SH" in etf_data._build_tickflow_etf_symbols("511880")
    assert "159967.SZ" in etf_data._build_tickflow_etf_symbols("159967")


def test_build_aligned_frame_and_returns():
    series = {
        "A": {"2024-01-01": 10.0, "2024-01-02": 11.0, "2024-01-03": 12.0},
        "B": {"2024-01-01": 5.0, "2024-01-02": 5.0, "2024-01-03": 4.0},
    }
    frame = strategy.build_aligned_frame(series)
    assert list(frame.columns) == ["A", "B"]
    returns = strategy.compute_returns_at(frame, 1, ["A", "B"], lookback=1)
    assert returns["A"] == pytest.approx(0.1)
    assert returns["B"] == 0.0


def test_select_holding():
    assert strategy.select_holding({"A": 0.05, "B": 0.08}, "C") == "B"
    assert strategy.select_holding({"A": -0.05, "B": -0.01}, "C") == "C"


def test_compute_drawdown_stats():
    history = [
        {"nav": 1.05},
        {"nav": 1.10},
        {"nav": 0.99},  # 回撤
        {"nav": 1.02},
    ]
    stats = strategy.compute_drawdown_stats(history, initial_nav=1.0)
    assert stats["total_return"] == pytest.approx(0.02)
    assert stats["max_drawdown"] < 0
    assert stats["current_drawdown"] < 0


def test_load_strategy_config_real():
    config = strategy.load_strategy_config()
    assert config["strategy"]["lookback_days"] == 20
    assert config["fallback_holding"]["code"] == "511880"
    assert len(config["universe"]) == 7


def test_build_report_and_render():
    config = strategy.load_strategy_config()
    state = {
        "strategy": "etf_rotation_20d",
        "last_run_date": "2024-01-03",
        "portfolio_nav": 1.02,
        "next_holding": "159934",
        "initial_nav": 1.0,
        "lookback_days": 20,
        "updated_at": "2024-01-03 18:00:00",
        "holdings_history": [
            {
                "date": "2024-01-02",
                "holding": "512040",
                "nav": 1.01,
                "prev_nav": 1.0,
                "daily_return": 0.01,
                "signals": {"512040": 0.02, "159934": 0.05},
                "unit_navs": {},
            },
            {
                "date": "2024-01-03",
                "holding": "159934",
                "nav": 1.02,
                "prev_nav": 1.01,
                "daily_return": 0.0099,
                "signals": {"512040": 0.01, "159934": 0.06},
                "unit_navs": {},
            },
        ],
    }
    report = strategy.build_report(state, config)
    assert report["next_holding"] == "159934"
    assert report["ranking"][0]["code"] == "159934"  # 0.06 最大

    html = render.build_email_html(report)
    assert f"cid:{render.NAV_CHART_CID}" in html
    assert "159934" in html
    # 空仓防御徽标不应出现(next_holding 不是 fallback)
    assert "空仓防御" not in html


def test_build_preview_html_embeds_base64(tmp_path):
    config = strategy.load_strategy_config()
    state = {
        "last_run_date": "2024-01-03",
        "portfolio_nav": 1.02,
        "next_holding": "159934",
        "initial_nav": 1.0,
        "lookback_days": 20,
        "holdings_history": [
            {
                "date": "2024-01-03",
                "holding": "159934",
                "nav": 1.02,
                "prev_nav": 1.0,
                "daily_return": 0.02,
                "signals": {},
                "unit_navs": {},
            }
        ],
    }
    report = strategy.build_report(state, config)
    chart_path = tmp_path / "nav.png"
    charts.generate_nav_chart(report["history"], chart_path)
    assert chart_path.exists()

    html = render.build_preview_html(report, chart_path)
    assert "data:image/png;base64," in html
    assert f"cid:{render.NAV_CHART_CID}" not in html
