"""商品极值板块测试(不依赖网络/akshare)。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.commodity import config as commodity_config
from src.commodity import core as commodity_core
from src.commodity import reporting as commodity_reporting
from src.commodity.config import DelayConfig, MonitorConfig, SymbolConfig, ThresholdConfig


# ----------------------------- core 纯逻辑 -----------------------------


def test_percentile_of_latest_uses_latest_value():
    series = pd.Series([1, 2, 3, 4, 5])
    assert commodity_core.percentile_of_latest(series) == 100.0


def test_compute_window_percentiles_min_points():
    series = pd.Series(range(1, 31))
    windows = {"d21": 21, "d63": 63}
    result = commodity_core.compute_window_percentiles(series, windows, min_points=20)
    assert result["d21"] == 100.0
    assert result["d63"] is None


def test_compute_window_percentiles_insufficient_data():
    series = pd.Series([1, 2, 3, 4, 5])
    windows = {"d21": 21}
    result = commodity_core.compute_window_percentiles(series, windows, min_points=20)
    assert result["d21"] is None


def test_compute_window_percentiles_requires_full_window():
    series = pd.Series(range(1, 757))
    windows = {"y3": 756, "y5": 1260}
    result = commodity_core.compute_window_percentiles(series, windows, min_points=20)
    assert result["y3"] == 100.0
    assert result["y5"] is None


# ----------------------------- config 解析 -----------------------------


def _write_cfg(tmp_path: Path, symbols_block: str) -> Path:
    cfg_path = tmp_path / "commodity.yaml"
    cfg_path.write_text(
        """
scan:
  delay_min_seconds: 1.0
  delay_max_seconds: 2.0
  max_stale_days: 10
  skip_if_no_today_data: true
thresholds:
  high_percentile: 85
  low_percentile: 30
windows:
  d21: 21
  y1: 252
"""
        + symbols_block,
        encoding="utf-8",
    )
    return cfg_path


def test_load_config_basic(tmp_path):
    cfg_path = _write_cfg(
        tmp_path,
        "symbols:\n"
        "  - {code: CL, name: NYMEX原油, market: foreign}\n"
        "  - {code: RB0, name: 螺纹钢主连, market: domestic}\n",
    )
    cfg = commodity_config.load_config(cfg_path)
    assert cfg.thresholds.high_percentile == 85
    assert cfg.thresholds.low_percentile == 30
    assert cfg.windows == {"d21": 21, "y1": 252}
    assert cfg.max_stale_days == 10
    assert len(cfg.symbols) == 2
    assert cfg.symbols[0].code == "CL"
    assert cfg.symbols[0].market == "foreign"
    assert cfg.symbols[1].code == "RB0"
    assert cfg.symbols[1].market == "domestic"


def test_load_config_rejects_low_ge_high(tmp_path):
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text(
        """
scan: {delay_min_seconds: 1.0, delay_max_seconds: 2.0}
thresholds: {high_percentile: 30, low_percentile: 30}
windows: {d21: 21}
symbols: [{code: CL, name: 原油, market: foreign}]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="low_percentile must be smaller"):
        commodity_config.load_config(cfg_path)


def test_load_config_rejects_empty_symbols(tmp_path):
    cfg_path = tmp_path / "empty.yaml"
    cfg_path.write_text(
        """
scan: {delay_min_seconds: 1.0, delay_max_seconds: 2.0}
thresholds: {high_percentile: 85, low_percentile: 30}
windows: {d21: 21}
symbols: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="No valid symbols"):
        commodity_config.load_config(cfg_path)


def test_load_config_real_yaml():
    """加载仓库内 config/commodity.yaml,验证品种数与窗口。"""
    cfg = commodity_config.load_config(REPO_ROOT / "config" / "commodity.yaml")
    assert cfg.windows == {"d21": 21, "d63": 63, "y1": 252, "y3": 756, "y5": 1260, "y10": 2520}
    assert cfg.thresholds.high_percentile == 85
    assert cfg.thresholds.low_percentile == 30
    assert len(cfg.symbols) == 75
    # 外盘/内盘各若干
    markets = {s.market for s in cfg.symbols}
    assert markets == {"foreign", "domestic"}


# ----------------------------- reporting 渲染 -----------------------------


def _make_cfg() -> MonitorConfig:
    return MonitorConfig(
        delay=DelayConfig(min_seconds=1.0, max_seconds=2.0),
        thresholds=ThresholdConfig(high_percentile=85, low_percentile=30),
        windows={"d21": 21, "y1": 252},
        symbols=[],
        max_stale_days=10,
        skip_if_no_today_data=True,
    )


def _make_result(code, name, market, pct, highs=None, lows=None, price=100.0, stale_days=0):
    sym = SymbolConfig(code=code, name=name, market=market)
    return commodity_core.SymbolResult(
        symbol=sym,
        latest_date=date.today(),
        latest_price=price,
        window_percentiles=pct,
        high_windows=highs or [],
        low_windows=lows or [],
        stale_days=stale_days,
    )


def test_build_email_html_groups_by_sector():
    cfg = _make_cfg()
    results = [
        _make_result("CL", "NYMEX原油", "foreign", {"d21": 90, "y1": 88}, highs=["d21", "y1"]),
        _make_result("RB0", "螺纹钢主连", "domestic", {"d21": 10, "y1": 12}, lows=["d21", "y1"]),
        _make_result("AU0", "沪金主连", "domestic", {"d21": 50, "y1": 50}),  # 无告警,不出现
    ]
    html_parts, summary = commodity_reporting.build_email_html(results, cfg)
    html = "\n".join(html_parts)
    assert "商品极值监控日报" in html
    assert "能源与化工" in html  # CL
    assert "黑色建材" in html  # RB0
    assert "有色贵金属" not in html  # AU0 无告警 -> 该板块不出现
    assert summary.alert_symbols == 2
    assert summary.high_alerts == 1
    assert summary.low_alerts == 1


def test_build_email_html_high_cell_red_low_cell_green():
    cfg = _make_cfg()
    results = [
        _make_result("CL", "原油", "foreign", {"d21": 90, "y1": 90}, highs=["d21", "y1"]),
        _make_result("M0", "豆粕", "domestic", {"d21": 10, "y1": 10}, lows=["d21", "y1"]),
    ]
    html_parts, _ = commodity_reporting.build_email_html(results, cfg)
    html = "\n".join(html_parts)
    assert "#D93026" in html  # 高位红
    assert "#1AAD19" in html  # 低位绿


def test_build_email_html_no_alerts_message():
    cfg = _make_cfg()
    results = [_make_result("CL", "原油", "foreign", {"d21": 50, "y1": 50})]
    html_parts, summary = commodity_reporting.build_email_html(results, cfg)
    html = "\n".join(html_parts)
    assert "本次无有效告警" in html
    assert summary.alert_symbols == 0


def test_build_email_html_stale_filtered():
    cfg = _make_cfg()
    # stale_days=10 > threshold 5 -> 剔除
    results = [
        _make_result("CL", "原油", "foreign", {"d21": 90, "y1": 90}, highs=["d21"], stale_days=10),
    ]
    html_parts, summary = commodity_reporting.build_email_html(results, cfg, stale_days_threshold=5)
    html = "\n".join(html_parts)
    assert "本次无有效告警" in html
    assert summary.stale_symbols == 1
    assert summary.alert_symbols == 0


def test_section_name_sm_disambiguation():
    # SM(外盘豆粕)->农产品;SM0(内盘锰硅)->黑色建材
    sm = _make_result("SM", "CBOT豆粕", "foreign", {"d21": 90}, highs=["d21"])
    sm0 = _make_result("SM0", "锰硅主连", "domestic", {"d21": 90}, highs=["d21"])
    assert commodity_reporting._section_name_for_symbol(sm) == "农产品"
    assert commodity_reporting._section_name_for_symbol(sm0) == "黑色建材"
