"""转债正股申万行业 查表/详情页抓取/缓存 测试(纯函数 + mock,不触网)。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible import industry  # noqa: E402


def test_l1_name_of_direct_hit():
    assert industry.l1_name_of("640107") == "机械设备"
    assert industry.l1_name_of("110101") == "农林牧渔"
    assert industry.l1_name_of("220316") == "基础化工"


def test_l1_name_of_transport_special_case():
    # 421xxx 是二级(航空机场 421000 / 航运港口 421100),一级须回退到 420000 交通运输
    assert industry.l1_name_of("421001") == "交通运输"
    assert industry.l1_name_of("421101") == "交通运输"


def test_l1_name_of_fallback_on_missing_l3():
    # 610101 在东财文档缺行,应回溯二级 610100 水泥 -> 610000 建筑材料
    assert industry.l1_name_of("610101") == "建筑材料"


def test_l1_name_of_unknown_and_empty():
    assert industry.l1_name_of("999999") == "999999"  # 完全未知兜底原始代码
    assert industry.l1_name_of("") == "未分类"
    assert industry.l1_name_of(None) == "未分类"


def test_fetch_stock_industry_from_detail_parses(monkeypatch):
    html = (
        '<div><span style="color:#636363;">行业</span> '
        '<a href="/data/stock/dividend_rate/industry-220316">有机硅</a></div>'
    )

    def fake_run_with_retry(name, fn):
        assert name == "stock_detail_industry_300727"
        return fn()

    monkeypatch.setattr(industry.alerts, "run_with_retry", fake_run_with_retry)
    info = industry.fetch_stock_industry_from_detail("300727", "test-cookie")
    assert info == {"sw_cd": "220316", "l3_name": "有机硅", "l1_name": "基础化工"}


def test_fetch_stock_industry_from_detail_no_match(monkeypatch):
    def fake_run_with_retry(name, fn):
        return "<html>无行业链接</html>"

    monkeypatch.setattr(industry.alerts, "run_with_retry", fake_run_with_retry)
    assert industry.fetch_stock_industry_from_detail("300727", "test-cookie") is None


def test_pending_cache_roundtrip(monkeypatch, tmp_path):
    fake_path = tmp_path / "cb_pending_industry.json"
    monkeypatch.setattr(industry, "PENDING_INDUSTRY_STATE_PATH", fake_path)
    industry.save_pending_industry_cache(
        {"300727": {"sw_cd": "220316", "l3_name": "有机硅", "l1_name": "基础化工"}}
    )
    cache = industry.load_pending_industry_cache()
    assert cache["300727"]["l1_name"] == "基础化工"
    assert industry.pending_industry_of("300727")["sw_cd"] == "220316"
    assert industry.pending_industry_of("000000") is None
    # 文件缺失 -> 空 dict
    monkeypatch.setattr(
        industry, "PENDING_INDUSTRY_STATE_PATH", tmp_path / "not_exists.json"
    )
    assert industry.load_pending_industry_cache() == {}


def test_backfill_only_fetches_missing(monkeypatch, tmp_path):
    fake_path = tmp_path / "cb_pending_industry.json"
    monkeypatch.setattr(industry, "PENDING_INDUSTRY_STATE_PATH", fake_path)
    industry.save_pending_industry_cache(
        {"600000": {"sw_cd": "420901", "l3_name": "高速公路", "l1_name": "交通运输"}}
    )
    fetched = []

    def fake_fetch(stock_id, cookie, session=None):
        fetched.append(stock_id)
        return {"sw_cd": "220316", "l3_name": "有机硅", "l1_name": "基础化工"}

    monkeypatch.setattr(industry, "fetch_stock_industry_from_detail", fake_fetch)
    cache = industry.backfill_pending_industries(
        "test-cookie", ["600000", "300727", "600000"], sleep_sec=0
    )
    assert fetched == ["300727"]  # 已缓存 600000 跳过,重复只抓一次
    assert len(cache) == 2
    assert cache["300727"]["l1_name"] == "基础化工"


def test_load_industry_map_smoke():
    data = industry.load_industry_map()
    assert data["version"] == "sw_2021"
    assert len(data["l1"]) == 31
    assert data["l1"]["640000"] == "机械设备"
