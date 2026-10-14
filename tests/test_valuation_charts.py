"""src/valuation/charts.py 单测。

不触网:PE 历史 / 汇率历史均以预取 DataFrame 传入(Agg 后端已设)。仅校验产出的 png
路径存在且非空,或数据不足时返回 None(GLM 模型不读图,仅断言文件落盘)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.valuation import charts


def _pe_history(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range(end="2026-08-04", periods=n, freq="D")
    return pd.DataFrame({"date": dates, "pe": [10.0 + i * 0.1 for i in range(n)]})


def _fx_history(n: int = 20) -> pd.DataFrame:
    dates = pd.date_range(end="2026-08-04", periods=n, freq="D")
    return pd.DataFrame({
        "日期": dates,
        "市场价": [7.10 + i * 0.01 for i in range(n)],
        "代码": "USDCNH",
        "名称": "美元",
    })


# ---------- 估值分位图 ----------


def test_valuation_chart_with_prefetched_history(tmp_path):
    item = {"index_code": "000300", "index_name": "沪深300"}
    out = charts.generate_valuation_percentile_chart(item, tmp_path, pe_history=_pe_history())
    assert out is not None
    assert out.exists()
    assert out.name == "valuation_percentile_000300.png"
    assert out.stat().st_size > 0


def test_valuation_chart_insufficient_history_returns_none(tmp_path):
    item = {"index_code": "000300"}
    out = charts.generate_valuation_percentile_chart(item, tmp_path, pe_history=_pe_history(10))
    assert out is None


def test_valuation_chart_no_index_code_returns_none(tmp_path):
    out = charts.generate_valuation_percentile_chart({}, tmp_path, pe_history=_pe_history())
    assert out is None


def test_valuation_chart_self_fetches_when_pe_history_none(monkeypatch, tmp_path):
    """pe_history=None 时自取(带归档回退);mock 返回有效历史。"""
    monkeypatch.setattr(
        charts,
        "fetch_index_valuation_metric_history_with_archive_fallback",
        lambda code, metric="pe", url="": (
            _pe_history(),
            {"data_source": "live", "archive_latest_date": None},
        ),
    )
    out = charts.generate_valuation_percentile_chart(
        {"index_code": "930955", "index_valuation_percentile_source": "http://x"}, tmp_path
    )
    assert out is not None and out.exists()


def test_valuation_chart_uses_item_name_fallback(tmp_path):
    item = {"index_code": "000300", "name": "沪深300"}  # 无 index_name,回退 name
    out = charts.generate_valuation_percentile_chart(item, tmp_path, pe_history=_pe_history())
    assert out is not None and out.exists()


def test_valuation_chart_marks_estimated_latest_pe():
    data = charts._prepare_valuation_data(
        {
            "index_code": "000300",
            "estimate_meta": {"date": "2026-08-10", "status": "estimated"},
        },
        pe_history=_pe_history(),
    )
    assert data is not None
    figure, axis = charts.plt.subplots()
    try:
        charts._draw_valuation_main(axis, data)
        assert any("（预估）" in text.get_text() for text in axis.texts)
    finally:
        charts.plt.close(figure)


# ---------- PB 走势图(同一套渲染,metric="pb") ----------


def _pb_history(n: int = 30) -> pd.DataFrame:
    dates = pd.date_range(end="2026-08-04", periods=n, freq="D")
    return pd.DataFrame({"date": dates, "pb": [0.80 + i * 0.01 for i in range(n)]})


def test_pb_chart_filename_has_pb_suffix(tmp_path):
    item = {"index_code": "930709", "index_name": "香港证券指数"}
    out = charts.generate_valuation_percentile_chart(item, tmp_path, metric="pb", pe_history=_pb_history())
    assert out is not None
    assert out.exists()
    assert out.name == "valuation_percentile_pb_930709.png"
    assert out.stat().st_size > 0


def test_pe_chart_filename_unchanged(tmp_path):
    """PE 图文件名保持原样,避免邮件渲染时的 cid / 路径回归。"""
    item = {"index_code": "930709"}
    out = charts.generate_valuation_percentile_chart(item, tmp_path, pe_history=_pe_history())
    assert out is not None
    assert out.name == "valuation_percentile_930709.png"


def test_pb_chart_title_is_pb():
    data = charts._prepare_valuation_data(
        {"index_code": "930709"}, pe_history=_pb_history(), metric="pb"
    )
    assert data is not None
    assert data["title"] == "PB走势"
    assert data["column"] == "pb"
    assert data["metric"] == "pb"


def test_pb_chart_never_draws_spread_overlay(tmp_path):
    """股债利差只对 PE 有意义;PB 图即使给了国债历史也不叠右轴。"""
    bond = pd.DataFrame({
        "date": pd.date_range(end="2026-08-04", periods=30, freq="D"),
        "yield_pct": [2.0] * 30,
    })
    data = charts._prepare_valuation_data(
        {"index_code": "930709"}, pe_history=_pb_history(), bond_history=bond, metric="pb"
    )
    assert data is not None
    assert data["spread_history"] is None


def test_unknown_metric_falls_back_to_pe(tmp_path):
    """未知指标降级为 PE,不抛异常(配置笔误时邮件仍出图)。"""
    item = {"index_code": "000300"}
    out = charts.generate_valuation_percentile_chart(
        item, tmp_path, metric="ps", pe_history=_pe_history()
    )
    assert out is not None
    assert out.name == "valuation_percentile_000300.png"


# ---------- 汇率图 ----------


def test_fx_chart_with_prefetched_history(tmp_path):
    out = charts.generate_fx_chart(tmp_path, fx_history=_fx_history())
    assert out is not None
    assert out.exists()
    assert out.name == "fx_usd_cny_vs_mid_10y.png"
    assert out.stat().st_size > 0


def test_fx_chart_empty_history_returns_none(tmp_path):
    out = charts.generate_fx_chart(tmp_path, fx_history=pd.DataFrame(columns=["日期", "市场价", "代码", "名称"]))
    assert out is None


def test_fx_chart_self_fetches_when_fx_history_none(monkeypatch, tmp_path):
    monkeypatch.setattr(charts, "fetch_fx_history_with_archive_fallback", lambda symbol="USDCNH": _fx_history())
    out = charts.generate_fx_chart(tmp_path)
    assert out is not None and out.exists()


def test_fx_chart_custom_slug(tmp_path):
    out = charts.generate_fx_chart(tmp_path, fx_history=_fx_history(), slug="custom_fx")
    assert out is not None
    assert out.name == "custom_fx.png"
