from src.dividend_observation import render
from src.dividend_observation.charts import ChartResult


def _payload() -> dict:
    return {
        "meta": {
            "index_code": "930955",
            "index_name": "红利低波100",
            "analysis_window_years": 3,
            "display_window_years": 2,
        },
        "latest": {
            "date": "2026-08-07",
            "index_close": 11291.48,
            "drawdown_peak": -0.0955,
            "pe_ttm_percentile": 74.47,
            "pb_lf_percentile": 72.88,
            "style_rotation_spread_percentile": 83.73,
            "event_state": "temporary_recovery",
        },
    }


def test_build_preview_html_embeds_base64_sources():
    chart_bundle = {
        "price": ChartResult(cid="price", image_path=None, error=None),
        "spread": ChartResult(cid="spread", image_path=None, error=None),
        "valuation": ChartResult(cid="valuation", image_path=None, error=None),
        "style": ChartResult(cid="style", image_path=None, error=None),
    }
    data_uri_map = {key: "data:image/png;base64,AAAA" for key in chart_bundle}

    html = render.build_preview_html(_payload(), chart_bundle, data_uri_map)

    assert "data:image/png;base64,AAAA" in html
    assert "价格与回撤" in html
    assert "利率相对吸引力" in html
    assert "近2年高点" in html
    assert "近3年滚动高点" in html
    assert "auto-fit" in html
    assert "PE分位" in html
    assert "PB分位" in html
    assert "距近2年高点" in html
    assert "本地研究观察页" not in html
    assert "邮件版观察页" not in html
    assert "Observation Preview" not in html
    assert "OBSERVATION PREVIEW" not in html


def test_build_email_html_uses_cid_and_failure_placeholder():
    chart_bundle = {
        "price": ChartResult(cid="price", image_path="price.png", error=None),
        "spread": ChartResult(cid="spread", image_path=None, error="该图暂无数据"),
        "valuation": ChartResult(cid="valuation", image_path="valuation.png", error=None),
        "style": ChartResult(cid="style", image_path="style.png", error=None),
    }

    html = render.build_email_html(_payload(), chart_bundle)

    assert 'src="cid:price"' in html
    assert "该图暂无数据" in html
    assert "距近2年高点" in html
