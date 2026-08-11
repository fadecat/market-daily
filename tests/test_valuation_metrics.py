"""市场估值 metrics 测试(纯函数,不触网)。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.valuation import metrics  # noqa: E402


# ── parse_float ───────────────────────────────────────────────────────────────
def test_parse_float():
    assert metrics.parse_float("1.5") == 1.5
    assert metrics.parse_float("1,234.5") == 1234.5
    assert metrics.parse_float("-") is None
    assert metrics.parse_float("") is None
    assert metrics.parse_float(None) is None
    assert metrics.parse_float("abc") is None
    assert metrics.parse_float(8) == 8.0


# ── get_index_valuation_metric ────────────────────────────────────────────────
def test_get_index_valuation_metric():
    item = {"index_valuation_metrics": {"PE(TTM)": {"current": "10"}, "PB": {}}}
    assert metrics.get_index_valuation_metric(item, "PE(TTM)") == {"current": "10"}
    assert metrics.get_index_valuation_metric(item, "MISSING") == {}
    assert metrics.get_index_valuation_metric({}, "PE(TTM)") == {}
    assert metrics.get_index_valuation_metric({"index_valuation_metrics": "x"}, "PE(TTM)") == {}


# ── compute_equity_bond_spread_percentiles ────────────────────────────────────
def _fixture(n=30):
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    pe_df = pd.DataFrame({"date": dates, "pe": [10.0 + i * 0.1 for i in range(n)]})
    bond_df = pd.DataFrame({"date": dates, "yield_pct": [2.5 + (i % 5) * 0.1 for i in range(n)]})
    return pe_df, bond_df


def test_compute_spread_structure():
    pe_df, bond_df = _fixture(30)
    result = metrics.compute_equity_bond_spread_percentiles(pe_df, bond_df)
    assert "current" in result and "percentiles" in result and "average_5y" in result
    # yield_pct 恒 >0 -> ratio 系列也存在
    assert "ratio_current" in result and "ratio_percentiles" in result
    # 30 天都在 1Y 窗口内 -> 1Y 分位计算
    assert "1Y" in result["percentiles"]
    assert 0.0 <= result["percentiles"]["1Y"] <= 100.0
    # current = 1/pe_last * 100 - yield_last
    pe_last, yield_last = 10.0 + 29 * 0.1, 2.5 + (29 % 5) * 0.1
    assert result["current"] == round((1.0 / pe_last) * 100.0 - yield_last, 4)


def test_compute_spread_insufficient_samples():
    pe_df, bond_df = _fixture(10)  # <20
    assert metrics.compute_equity_bond_spread_percentiles(pe_df, bond_df) == {}


def test_compute_spread_drops_nonpositive_pe():
    pe_df, bond_df = _fixture(30)
    pe_df.loc[0, "pe"] = -1.0  # 负 PE 应被 drop
    pe_df.loc[1, "pe"] = 0.0   # 0 PE 应被 drop
    result = metrics.compute_equity_bond_spread_percentiles(pe_df, bond_df)
    assert result  # 28 行仍 >=20


# ── attach_equity_bond_ratio ──────────────────────────────────────────────────
def test_attach_equity_bond_ratio_computes():
    item = {"index_valuation_metrics": {"PE(TTM)": {"current": "10"}}}
    metrics.attach_equity_bond_ratio(item, 3.0)
    # (1/10)*100 - 3.0 = 7.0
    assert item["equity_bond_ratio"] == 7.0
    assert item["cn_10y_bond_yield"] == 3.0
    assert item["cn_10y_bond_yield_data_source"] == "live"
    assert item["cn_10y_bond_yield_archive_latest_date"] is None
    assert item["cn_10y_bond_yield_backup_date"] is None


def test_attach_equity_bond_ratio_archive_source():
    item = {"index_valuation_metrics": {"PE(TTM)": {"current": "12.5"}}}
    metrics.attach_equity_bond_ratio(
        item, 2.5, data_source="archive", archive_latest_date="2026-01-01", bond_backup_date="2026-08-11"
    )
    # (1/12.5)*100 - 2.5 = 8.0 - 2.5 = 5.5
    assert item["equity_bond_ratio"] == 5.5
    assert item["cn_10y_bond_yield_data_source"] == "archive"
    assert item["cn_10y_bond_yield_archive_latest_date"] == "2026-01-01"
    assert item["cn_10y_bond_yield_backup_date"] == "2026-08-11"


def test_attach_equity_bond_ratio_no_metric_no_change():
    item = {"index_valuation_metrics": {}}
    metrics.attach_equity_bond_ratio(item, 3.0)
    assert "equity_bond_ratio" not in item


def test_attach_equity_bond_ratio_nonpositive_pe():
    item = {"index_valuation_metrics": {"PE(TTM)": {"current": "0"}}}
    metrics.attach_equity_bond_ratio(item, 3.0)
    assert "equity_bond_ratio" not in item
