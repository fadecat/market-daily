import json
from pathlib import Path

from src.research.dividend_observation_chart_preview import (
    build_display_payload,
    build_preview_html,
    run_preview,
)


def _payload() -> dict:
    return {
        "meta": {
            "index_code": "930955",
            "index_name": "红利低波100",
            "analysis_window_years": 3,
            "display_window_years": 3,
            "window": {
                "drawdown_days": 756,
                "valuation_days": 756,
                "spread_days": 756,
                "style_days": 756,
            },
        },
        "series": {
            "dates": ["2026-01-01", "2026-01-02"],
            "index_close": [100.0, 99.0],
            "drawdown_peak": [0.0, -0.01],
            "pe_ttm_percentile": [60.0, 45.0],
            "pb_lf_percentile": [55.0, 42.0],
            "dividend_yield_spread_percentile": [50.0, 65.0],
            "earnings_yield_spread_percentile": [48.0, 62.0],
            "style_rotation_spread_percentile": [80.0, 92.0],
            "event_state": [None, "temporary_recovery"],
        },
        "latest": {
            "date": "2026-01-02",
            "index_close": 99.0,
            "drawdown_peak": -0.01,
            "pe_ttm_percentile": 45.0,
            "pb_lf_percentile": 42.0,
            "dividend_yield_spread_percentile": 65.0,
            "earnings_yield_spread_percentile": 62.0,
            "style_rotation_spread_percentile": 92.0,
            "event_state": "temporary_recovery",
        },
    }


def test_build_preview_html_contains_four_layers_and_payload():
    html = build_preview_html(_payload())
    assert "echarts.min.js" in html
    assert 'id="price-chart"' in html
    assert 'id="valuation-chart"' in html
    assert 'id="spread-chart"' in html
    assert 'id="style-chart"' in html
    assert html.count('class="panel span-12 stack-chart"') == 3
    assert 'class="panel span-6"' not in html
    assert '<div class="section-title">状态色带</div>' not in html
    assert 'id="state-ribbon"' not in html
    assert '"index_code": "930955"' in html
    assert "yAxisIndex: 1" in html
    assert '<div class="section-title">方法说明</div>' not in html
    assert "看当前离近3年高点还有多远，以及回撤发生在什么价格位置。" in html
    assert "drawdown_peak = close / 近3年滚动高点 - 1" in html
    assert "看红利现在在近3年估值里偏贵还是偏便宜。" in html
    assert "pe_ttm_percentile / pb_lf_percentile = 近3年历史百分位" in html
    assert "看红利相对10年国债是否更有吸引力。" in html
    assert "dividend_yield_spread = 股息率 - 10年国债收益率" in html
    assert "earnings_yield_spread = 100 / PE - 10年国债收益率" in html
    assert "看市场是否仍处在成长拥挤、红利受压的阶段。" in html
    assert "style_rotation_spread_percentile = 风格轮动收益率差值近3年百分位" in html
    assert "当前状态" in html
    assert "临时修复" in html
    assert "showMaxLabel: true" in html
    assert "if (index === dates.length - 1)" not in html
    assert html.count("grid: { left: 56, right: 56, top: 34, bottom: 42 }") == 2
    assert html.index('id="spread-chart"') < html.index('id="valuation-chart"')
    assert 'priceChart.group = "dividend-observation"' in html
    assert 'spreadChart.group = "dividend-observation"' in html
    assert 'valuationChart.group = "dividend-observation"' in html
    assert 'styleChart.group = "dividend-observation"' in html
    assert 'echarts.connect("dividend-observation")' in html


def test_build_preview_html_uses_generic_field_names_and_configured_display_window():
    payload = _payload()
    dates = [f"2024-01-{(index % 28) + 1:02d}" for index in range(900)]
    payload["series"]["dates"] = dates
    payload["series"]["index_close"] = list(range(900))
    payload["series"]["drawdown_peak"] = [0.0 for _ in dates]
    payload["series"]["pe_ttm_percentile"] = [50.0 for _ in dates]
    payload["series"]["pb_lf_percentile"] = [50.0 for _ in dates]
    payload["series"]["dividend_yield_spread_percentile"] = [50.0 for _ in dates]
    payload["series"]["earnings_yield_spread_percentile"] = [50.0 for _ in dates]
    payload["series"]["style_rotation_spread_percentile"] = [50.0 for _ in dates]
    payload["series"]["event_state"] = [None for _ in dates]
    payload["latest"]["date"] = dates[-1]
    payload["latest"]["index_close"] = 899.0

    html = build_preview_html(payload)

    assert '"display_window_days": 756' in html
    assert "drawdown_peak" in html
    assert "dividend_yield_spread_percentile" in html
    assert "earnings_yield_spread_percentile" in html
    assert "style_rotation_spread_percentile" in html
    assert "drawdown_5y_peak" not in html
    assert "dividend_yield_spread_percentile_5y" not in html
    assert "earnings_yield_spread_percentile_5y" not in html
    assert "style_rotation_spread_percentile_5y" not in html
    assert "const chartPalette =" not in html


def test_build_display_payload_clips_all_series_to_recent_window():
    payload = {
        "meta": {
            "index_code": "930955",
            "index_name": "红利低波100",
            "analysis_window_years": 3,
            "display_window_years": 3,
        },
        "series": {
            "dates": [f"2026-01-{day:02d}" for day in range(1, 11)],
            "index_close": list(range(10)),
            "drawdown_peak": [-(day / 100.0) for day in range(10)],
            "pe_ttm_percentile": list(range(10, 20)),
            "pb_lf_percentile": list(range(20, 30)),
            "dividend_yield_spread_percentile": list(range(30, 40)),
            "earnings_yield_spread_percentile": list(range(40, 50)),
            "style_rotation_spread_percentile": list(range(50, 60)),
            "event_state": [None, None, None, "temporary_recovery", "temporary_recovery", None, None, None, None, None],
        },
        "latest": {"date": "2026-01-10", "index_close": 9.0},
    }

    clipped = build_display_payload(payload, display_window_days=4)

    assert clipped["meta"]["display_window_days"] == 4
    assert clipped["series"]["dates"] == [
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-10",
    ]
    assert clipped["series"]["index_close"] == [6, 7, 8, 9]
    assert clipped["series"]["event_state"] == [None, None, None, None]


def test_run_preview_writes_html(tmp_path):
    input_path = tmp_path / "dividend_observation_930955.json"
    output_path = tmp_path / "dividend_observation_930955.html"
    input_path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")

    rendered = run_preview(input_path=input_path, output_path=output_path)

    assert rendered == output_path
    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    assert "红利观察图" in html
    assert "红利低波100" in html
