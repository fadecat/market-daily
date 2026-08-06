"""巨潮财报 cninfo + cninfo_cache 测试(纯函数 + tmp 归档 round-trip,不触网)。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.valuation.dividend import cninfo, cninfo_cache  # noqa: E402


# ── compute_ttm_from_cumulative_values ────────────────────────────────────────
def test_ttm_annual():
    result = cninfo.compute_ttm_from_cumulative_values({"2023-12-31": 100})
    assert result["basis"] == "annual"
    assert result["ttm_value"] == 100
    assert result["latest_period"] == "2023-12-31"


def test_ttm_rolling():
    values = {"2023-12-31": 100, "2023-09-30": 70, "2024-09-30": 80}
    result = cninfo.compute_ttm_from_cumulative_values(values)
    # ttm = prior_annual(100) + latest(80) - prior_same(70) = 110
    assert result["basis"] == "rolling"
    assert result["ttm_value"] == 110
    assert result["latest_period"] == "2024-09-30"
    assert result["components"]["prior_same_period"] == "2023-09-30"


def test_ttm_missing_annual_raises():
    with pytest.raises(ValueError):
        cninfo.compute_ttm_from_cumulative_values({"2024-09-30": 80})


def test_ttm_empty_raises():
    with pytest.raises(ValueError):
        cninfo.compute_ttm_from_cumulative_values({})


def test_ttm_skips_none_periods_raises_when_same_missing():
    values = {"2023-12-31": 100, "2023-09-30": None, "2024-09-30": 80}
    with pytest.raises(ValueError):
        cninfo.compute_ttm_from_cumulative_values(values)


# ── get_statement_row / _statement_period_key ────────────────────────────────
def test_get_statement_row():
    statement = {"rows": [{"name": "营收", "values": {}}, {"name": "归属母公司净利润", "values": {"2024-09-30": 1}}]}
    row = cninfo.get_statement_row(statement, "归属母公司净利润")
    assert row is not None and row["values"]["2024-09-30"] == 1
    assert cninfo.get_statement_row(statement, "不存在") is None


def test_statement_period_key():
    assert cninfo._statement_period_key("year", "2024") == "2024-12-31"
    assert cninfo._statement_period_key("three", "2024") == "2024-09-30"


# ── cninfo_cache: normalize / hash ────────────────────────────────────────────
def test_normalize_stock_code():
    assert cninfo_cache._normalize_stock_code("  600001.SH ") == "600001"
    assert cninfo_cache._normalize_stock_code("1") == "000001"
    assert cninfo_cache._normalize_stock_code("") == ""


def test_bundle_hash_deterministic():
    bundle = {"a": 1, "b": [2, 3]}
    assert cninfo_cache.compute_bundle_hash(bundle) == cninfo_cache.compute_bundle_hash(bundle)
    # 顺序不同但内容相同 -> 同 hash(sort_keys)
    assert cninfo_cache.compute_bundle_hash({"b": [2, 3], "a": 1}) == cninfo_cache.compute_bundle_hash(bundle)


# ── fixture bundle ────────────────────────────────────────────────────────────
def _fixture_bundle():
    return {
        "company": {"stock_code": "600001", "sec_name": "测试股份"},
        "head_strip": {"report_date": "2024-09-30"},
        "main_indicators": {
            "latest_report": {
                "report_date": "2024-09-30",
                "by_label": {"每股净资产": {"value": 5.0}, "ROE": {"value": 10.0}},
            }
        },
        "income_statement": {
            "unit": "万元",
            "rows": [{"name": "归属母公司净利润", "values": {"2023-12-31": 10000, "2023-09-30": 7000, "2024-09-30": 8000}}],
        },
    }


def test_derive_metrics_from_bundle():
    derived = cninfo_cache.derive_metrics_from_bundle(_fixture_bundle())
    # ttm = 10000 + 8000 - 7000 = 11000 万 = 1.1 亿
    assert derived["ttm_parent_net_profit_wan"] == 11000.0
    assert derived["ttm_parent_net_profit_yi"] == 1.1
    assert derived["latest_book_value_per_share"] == 5.0
    assert derived["latest_roe"] == 10.0


# ── archive / load round-trip ─────────────────────────────────────────────────
def test_archive_and_load_roundtrip(tmp_path):
    bundle = _fixture_bundle()
    payload = cninfo_cache.build_financial_snapshot_payload(bundle, fetched_at="2024-10-01T10:00:00+08:00")
    result = cninfo_cache.archive_financial_snapshot(tmp_path, payload)
    assert result["status"] == "created"
    assert Path(result["latest_path"]).exists()

    # 加载命中
    loaded = cninfo_cache.load_cached_financial_snapshot("600001", root_dir=tmp_path)
    assert loaded is not None
    assert loaded["stock_code"] == "600001"
    assert loaded["derived"]["ttm_parent_net_profit_yi"] == 1.1

    # 同 hash 再归档 -> unchanged
    result2 = cninfo_cache.archive_financial_snapshot(tmp_path, payload)
    assert result2["status"] == "unchanged"


def test_load_cached_returns_none_for_missing(tmp_path):
    assert cninfo_cache.load_cached_financial_snapshot("999999", root_dir=tmp_path) is None


def test_fetch_cached_or_live_hits_cache(tmp_path):
    bundle = _fixture_bundle()
    payload = cninfo_cache.build_financial_snapshot_payload(bundle, fetched_at="2024-10-01T10:00:00+08:00")
    cninfo_cache.archive_financial_snapshot(tmp_path, payload)

    def _should_not_call(_code):
        raise AssertionError("应命中缓存,不应触网")

    result = cninfo_cache.fetch_cached_or_live_ttm_parent_net_profit(
        "600001", root_dir=tmp_path, bundle_fetcher=_should_not_call
    )
    assert result["ttm_value_yi"] == 1.1
    assert result["stock_code"] == "600001"


# ── is_snapshot_fresh ─────────────────────────────────────────────────────────
def test_is_snapshot_fresh():
    tz = ZoneInfo("Asia/Shanghai")
    now_iso = datetime.now(tz).isoformat(timespec="seconds")
    old_iso = (datetime.now(tz) - timedelta(days=40)).isoformat(timespec="seconds")
    assert cninfo_cache.is_snapshot_fresh({"fetched_at": now_iso}, 30) is True
    assert cninfo_cache.is_snapshot_fresh({"fetched_at": old_iso}, 30) is False
    assert cninfo_cache.is_snapshot_fresh({}, 30) is False
    assert cninfo_cache.is_snapshot_fresh({"fetched_at": "not-a-date"}, 30) is False
