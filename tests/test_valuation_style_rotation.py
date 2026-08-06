"""src/valuation/style_rotation.py 单测。

纯 pandas 逻辑(归一化/收益率差值/payload/summary/片段)用确定性数据覆盖;图表与
build_section 用 Agg 后端校验落盘/编排,不触网(fetch_index_data 在 build_section 用例中 mock)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest

from src.valuation import style_rotation as sr


def _price_series(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"date": dates, "close": closes})


# ---------- normalize_price_frame ----------


def test_normalize_price_frame_dedup_and_sort():
    df = pd.DataFrame({
        "date": ["2026-01-03", "2026-01-01", "2026-01-02", "2026-01-02"],
        "close": [3.0, 1.0, 2.0, 99.0],  # 重复 01-02 取 last
    })
    out = sr.normalize_price_frame(df)
    assert list(out["date"].dt.strftime("%Y-%m-%d")) == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert out["close"].iloc[1] == 99.0  # keep="last"


def test_normalize_price_frame_empty():
    out = sr.normalize_price_frame(pd.DataFrame())
    assert out.empty


def test_normalize_price_frame_missing_columns_raises():
    with pytest.raises(ValueError, match="date and close"):
        sr.normalize_price_frame(pd.DataFrame({"foo": [1]}))


# ---------- calculate_style_rotation_preview ----------


def test_calculate_style_rotation_preview_spread():
    left = _price_series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    right = _price_series([10, 9, 8, 7, 6, 5, 4, 3, 2, 1])
    out = sr.calculate_style_rotation_preview(
        left_df=left, right_df=right, return_window_days=5, display_window_days=0
    )
    # 10 点 + window=5 -> 前 5 个 NaN 丢弃,剩 5 个;display_window_days=0 不裁剪
    assert len(out["spread"]) == 5
    # 末点:left_return=(10/5-1)*100=100, right_return=(1/6-1)*100=-83.33..., spread≈183.33
    assert round(out["spread"][-1], 2) == 183.33


def test_calculate_style_rotation_preview_zero_window_raises():
    with pytest.raises(ValueError, match="greater than 0"):
        sr.calculate_style_rotation_preview(
            left_df=_price_series([1, 2]), right_df=_price_series([1, 2]), return_window_days=0
        )


def test_calculate_style_rotation_preview_display_window_clips():
    left = _price_series([float(i) for i in range(1, 31)])
    right = _price_series([float(30 - i) for i in range(30)])
    out = sr.calculate_style_rotation_preview(
        left_df=left, right_df=right, return_window_days=5, display_window_days=10
    )
    assert len(out["spread"]) == 10  # 裁剪到最后 10 个


# ---------- build_style_rotation_preview_payload ----------


def test_build_payload_structure():
    left = _price_series([1, 2, 3, 4, 5, 6])
    right = _price_series([6, 5, 4, 3, 2, 1])
    payload = sr.build_style_rotation_preview_payload(
        left_df=left, right_df=right, return_window_days=2, display_window_days=0
    )
    assert payload["meta"]["left_name"] == sr.FIXED_LEFT_NAME
    assert payload["meta"]["right_symbol"] == sr.FIXED_RIGHT_SYMBOL
    assert set(payload["series"].keys()) == {"dates", "left_return", "right_return", "spread"}


# ---------- resolve_as_of_label ----------


def test_resolve_as_of_label_from_dates():
    payload = {"series": {"dates": ["2026-01-01", "2026-08-04"]}}
    assert sr.resolve_as_of_label(payload) == "2026-08-04"


def test_resolve_as_of_label_fallback(monkeypatch):
    monkeypatch.setattr(sr, "now_in_beijing", lambda: pd.Timestamp("2026-08-06 10:00").to_pydatetime())
    assert sr.resolve_as_of_label({}) == "2026-08-06"


# ---------- _extract_series ----------


def test_extract_series_ok():
    payload = {"series": {"dates": ["2026-01-01", "2026-01-02"], "spread": [1.0, 2.0]}}
    dates, spread = sr._extract_series(payload)
    assert len(dates) == 2
    assert spread.iloc[-1] == 2.0


def test_extract_series_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        sr._extract_series({"series": {"dates": ["2026-01-01"], "spread": [1.0, 2.0]}})


def test_extract_series_invalid_spread_raises():
    with pytest.raises(ValueError, match="invalid numeric"):
        sr._extract_series({"series": {"dates": ["2026-01-01"], "spread": ["abc"]}})


# ---------- 图表标题 / footer ----------


def test_build_chart_title():
    meta = {"left_name": "小盘成长", "left_symbol": "399376", "right_name": "大盘价值", "right_symbol": "399373"}
    title = sr._build_chart_title(meta)
    assert "小盘成长(399376)" in title and "大盘价值(399373)" in title


def test_build_footer_text():
    payload = {"series": {"dates": ["2026-08-04"], "spread": [12.34]}}
    text = sr._build_footer_text(payload)
    assert "2026年8月4日" in text
    assert "12.34%" in text


# ---------- summary ----------


def test_summary_and_latest_spread():
    payload = {"meta": {"left_name": "A", "right_name": "B", "return_window_days": 250, "display_window_days": 1260}, "series": {"spread": [1.0, 2.5]}}
    summary = sr._build_style_rotation_summary(payload)
    assert summary["left_name"] == "A"
    assert summary["latest_spread"] == 2.5


def test_summary_none_payload():
    assert sr._build_style_rotation_summary(None) is None
    assert sr._build_style_rotation_summary({"meta": "x"}) is None


def test_latest_spread_non_numeric_returns_none():
    payload = {"series": {"spread": ["abc"]}}
    assert sr._get_latest_style_rotation_spread(payload) is None


# ---------- 图表生成 ----------


def test_generate_style_rotation_chart(tmp_path):
    left = _price_series([float(i) for i in range(1, 51)])
    right = _price_series([float(51 - i) for i in range(50)])
    payload = sr.build_style_rotation_preview_payload(
        left_df=left, right_df=right, return_window_days=5, display_window_days=20
    )
    out = sr.generate_style_rotation_chart(payload, tmp_path)
    assert out is not None and out.exists()
    assert out.name == "style_rotation_preview.png"
    assert out.stat().st_size > 0


# ---------- _render_fragment ----------


def test_render_fragment_with_chart(tmp_path):
    chart = tmp_path / "x.png"
    chart.write_bytes(b"\x89PNG")
    payload = {"meta": {"left_name": "A", "right_name": "B", "return_window_days": 250, "display_window_days": 1260}, "series": {"spread": [1.0, 2.5]}}
    html = sr._render_fragment(payload, "2026-08-04", chart)
    assert "风格轮动收益率差值" in html
    assert f"cid:{sr.STYLE_ROTATION_CHART_CID}" in html
    assert "2.50%" in html
    assert "2026-08-04" in html


def test_render_fragment_without_chart():
    payload = {"meta": {"left_name": "A", "right_name": "B", "return_window_days": 250, "display_window_days": 1260}, "series": {"spread": [1.0]}}
    html = sr._render_fragment(payload, "2026-08-04", None)
    assert "cid:" not in html


def test_render_fragment_no_summary_returns_empty():
    assert sr._render_fragment(None, "x", None) == ""


# ---------- build_section ----------


def _fake_payload() -> Dict[str, Any]:
    return {
        "meta": {"left_name": "小盘成长", "right_name": "大盘价值", "return_window_days": 250, "display_window_days": 1260},
        "series": {"dates": ["2026-01-01", "2026-08-04"], "spread": [1.0, 2.5]},
    }


def test_build_section_success(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "collect_style_rotation_preview_payload", lambda **k: _fake_payload())
    # 跳过真实作图,返回一个假路径
    fake_png = tmp_path / "fake.png"
    fake_png.write_bytes(b"\x89PNG")
    monkeypatch.setattr(sr, "generate_style_rotation_chart", lambda payload, outdir: fake_png)
    result = sr.build_section(tmp_path)
    assert result is not None
    assert "风格轮动收益率差值" in result["html"]
    assert result["as_of_date"] == "2026-08-04"
    assert result["inline_images"][sr.STYLE_ROTATION_CHART_CID] == str(fake_png)


def test_build_section_data_failure_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sr, "collect_style_rotation_preview_payload",
        lambda **k: (_ for _ in ()).throw(RuntimeError("net")),
    )
    assert sr.build_section(tmp_path) is None


def test_build_section_chart_failure_still_returns_html(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "collect_style_rotation_preview_payload", lambda **k: _fake_payload())
    monkeypatch.setattr(
        sr, "generate_style_rotation_chart",
        lambda payload, outdir: (_ for _ in ()).throw(RuntimeError("font")),
    )
    result = sr.build_section(tmp_path)
    assert result is not None
    assert "cid:" not in result["html"]  # 无图
    assert result["inline_images"] == {}
