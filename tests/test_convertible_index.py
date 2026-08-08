"""转债指数图(history + charts)测试,不触网。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible.index_chart import charts, history, run  # noqa: E402


PAGE_BODY = """
<html><script>
var __date = ['2026-08-01','2026-08-05'];
var __data = {
  'price':[100.0,101.5],
  'mid_price':[115.0,116.0],
  'avg_ytm_rt':[2.10,2.20],
  'count':[500,501]
};
</script></html>
"""


def test_parse_jisilu_clean_names_and_index_value():
    records = history.parse_jisilu(PAGE_BODY)
    assert len(records) == 2
    first = records[0]
    assert first["date"] == "2026-08-01"
    # price -> index_value(供三低基准)
    assert first["index_value"] == "100.0"
    # 原始名归一为干净名
    assert first["median_price"] == "115.0"
    assert first["avg_ytm"] == "2.10"
    assert first["count"] == "500"
    # 不应残留原始字段名
    assert "mid_price" not in first
    assert "avg_ytm_rt" not in first
    assert "price" not in first


def test_normalize_record_maps_raw_names():
    raw = {"date": "2026-08-01", "price": "100", "mid_price": "115", "avg_ytm_rt": "2.1"}
    out = history._normalize_record(raw)
    assert out["index_value"] == "100"
    assert out["median_price"] == "115"
    assert out["avg_ytm"] == "2.1"
    # 已是干净名的字段原样保留
    clean = {"date": "2026-08-01", "median_price": "115"}
    assert history._normalize_record(clean)["median_price"] == "115"


def test_merge_records_overlap_and_add():
    hist = [{"date": "2026-08-01", "median_price": "115.0", "avg_ytm": "2.10"}]
    live = [
        {"date": "2026-08-01", "median_price": "115.5"},
        {"date": "2026-08-05", "median_price": "116.0", "avg_ytm": "2.20"},
    ]
    merged, stats = history.merge_records(hist, live)
    assert stats["history"] == 1
    assert stats["updated"] == 1
    assert stats["added"] == 1
    by_date = {r["date"]: r for r in merged}
    # 重叠日:线上 median_price 覆盖,旧 avg_ytm 保留
    assert by_date["2026-08-01"]["median_price"] == "115.5"
    assert by_date["2026-08-01"]["avg_ytm"] == "2.10"
    assert by_date["2026-08-05"]["median_price"] == "116.0"


def test_build_runtime_index_series_from_records():
    records = [
        {"date": "2026-08-01", "index_value": "100.0"},
        {"date": "2026-08-05", "index_value": "101.5"},
        {"date": "2026-08-06"},  # 无 index_value,跳过
    ]
    series = history.build_runtime_index_series(records)
    assert series == [
        {"date": "2026-08-01", "value": 100.0},
        {"date": "2026-08-05", "value": 101.5},
    ]


def test_build_runtime_index_series_empty_raises():
    with pytest.raises(ValueError):
        history.build_runtime_index_series([{"date": "2026-08-01"}])


def test_save_history_dedup(tmp_path):
    path = tmp_path / "cb_index_history.json"
    records = [{"date": "2026-08-01", "index_value": "100.0", "median_price": "115.0"}]
    assert history.save_history(records, path) is True  # 首次写入
    assert path.exists()
    assert history.save_history(records, path) is False  # 内容不变,跳过


def test_load_history_normalizes_old_raw_names(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(
        '[{"date":"2026-08-01","price":"100","mid_price":"115","avg_ytm_rt":"2.1"}]',
        encoding="utf-8",
    )
    loaded = history.load_history(path)
    assert len(loaded) == 1
    assert loaded[0]["index_value"] == "100"
    assert loaded[0]["median_price"] == "115"
    assert loaded[0]["avg_ytm"] == "2.1"


def test_generate_cb_index_chart_with_records(tmp_path):
    records = [
        {"date": f"2024-01-{day:02d}", "median_price": 115 + day * 0.1, "avg_ytm": 2.0 + day * 0.01}
        for day in range(1, 36)
    ]
    out = charts.generate_cb_index_chart(tmp_path / "cb_index.png", records=records)
    assert out is not None
    assert out.exists()


def test_generate_cb_index_chart_insufficient_data(tmp_path):
    # 少于 30 条有效记录 -> 不出图
    records = [{"date": "2024-01-01", "median_price": 115.0, "avg_ytm": 2.0}]
    out = charts.generate_cb_index_chart(tmp_path / "cb_index.png", records=records)
    assert out is None


def test_convertible_index_section_mentions_close_update(monkeypatch, tmp_path):
    monkeypatch.setattr(
        run.history,
        "build_merged_history",
        lambda: ([{"date": "2026-08-07", "value": 100}], {}),
    )
    monkeypatch.setattr(
        run.charts,
        "generate_cb_index_chart",
        lambda path, records: path.write_bytes(b"png") or path,
    )
    section = run.build_section(tmp_path)
    assert "A股收盘后更新" in section["html"]
    assert "2026-08-07" in section["html"]
