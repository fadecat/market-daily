import datetime as dt
from pathlib import Path

from src.dividend_observation import charts


def _payload() -> dict:
    return {
        "meta": {"index_name": "红利低波100", "analysis_window_years": 3},
        "series": {
            "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "index_close": [100.0, 98.0, 99.0],
            "drawdown_peak": [0.0, -0.02, -0.01],
            "dividend_yield_spread_percentile": [40.0, 60.0, 70.0],
            "earnings_yield_spread_percentile": [35.0, 55.0, 65.0],
            "pe_ttm_percentile": [60.0, 45.0, 50.0],
            "pb_lf_percentile": [58.0, 44.0, 48.0],
            "style_rotation_spread_percentile": [80.0, 78.0, 82.0],
        },
    }


def test_generate_all_chart_images_writes_pngs(tmp_path):
    bundle = charts.generate_chart_bundle(_payload(), tmp_path)

    assert set(bundle.keys()) == {"price", "spread", "valuation", "style"}
    assert all(item.image_path and Path(item.image_path).exists() for item in bundle.values())
    assert all(item.error is None for item in bundle.values())


def test_generate_chart_bundle_marks_section_error_when_series_missing(tmp_path):
    payload = _payload()
    payload["series"]["style_rotation_spread_percentile"] = [None, None, None]

    bundle = charts.generate_chart_bundle(payload, tmp_path)

    assert bundle["style"].image_path is None
    assert "暂无数据" in bundle["style"].error


class _FakeAxis:
    def __init__(self):
        self.plot_calls = []
        self.fill_calls = []
        self.legend_kwargs = None
        self.ylabel = None
        self.ylim = None
        self.transAxes = object()
        self.xticks = None
        self.xlim = None
        self.yaxis = type("YAxis", (), {"set_major_formatter": lambda *args, **kwargs: None})()
        self.spines = {
            "top": type("Spine", (), {"set_visible": lambda *args, **kwargs: None})(),
            "left": type("Spine", (), {"set_visible": lambda *args, **kwargs: None})(),
            "right": type(
                "Spine",
                (),
                {
                    "set_color": lambda *args, **kwargs: None,
                    "set_linewidth": lambda *args, **kwargs: None,
                },
            )(),
        }

    def plot(self, dates, values, **kwargs):
        self.plot_calls.append(kwargs)
        return [object()]

    def set_ylabel(self, value):
        self.ylabel = value

    def fill_between(self, dates, values, baseline, **kwargs):
        self.fill_calls.append(kwargs)

    def set_ylim(self, low, high):
        self.ylim = (low, high)

    def set_xticks(self, ticks, labels=None):
        self.xticks = (ticks, labels)

    def set_xlim(self, left, right):
        self.xlim = (left, right)

    def legend(self, *args, **kwargs):
        self.legend_kwargs = kwargs
        return None

    def text(self, *args, **kwargs):
        return None

    def tick_params(self, *args, **kwargs):
        return None


class _FakeFigure:
    def autofmt_xdate(self, *args, **kwargs):
        return None


def test_price_chart_uses_thin_lines_and_light_fill(monkeypatch, tmp_path):
    primary_axis = _FakeAxis()
    secondary_axis = _FakeAxis()
    primary_axis.twinx = lambda: secondary_axis

    monkeypatch.setattr(
        charts,
        "_series_points",
        lambda payload, *keys: (
            [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)],
            [[100.0, 98.0, 99.0], [0.0, -0.02, -0.01]],
        ),
    )
    monkeypatch.setattr(charts, "_base_axis", lambda: (_FakeFigure(), primary_axis))
    monkeypatch.setattr(charts, "_save_figure", lambda fig, output_path: str(output_path))

    result = charts._safe_render_price_chart(_payload(), tmp_path / "price.png")

    assert result.image_path is not None
    assert primary_axis.plot_calls[0]["linewidth"] == 1.0
    assert secondary_axis.plot_calls[0]["linewidth"] == 1.0
    assert secondary_axis.fill_calls[0]["alpha"] <= 0.05
    assert primary_axis.xticks is not None


def test_percentile_charts_use_thin_lines(monkeypatch, tmp_path):
    axis = _FakeAxis()

    monkeypatch.setattr(
        charts,
        "_series_points",
        lambda payload, *keys: (
            [dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)],
            [[40.0, 60.0, 70.0], [35.0, 55.0, 65.0]],
        ),
    )
    monkeypatch.setattr(charts, "_base_axis", lambda: (_FakeFigure(), axis))
    monkeypatch.setattr(charts, "_save_figure", lambda fig, output_path: str(output_path))

    result = charts._safe_render_two_line_chart(
        _payload(),
        tmp_path / "spread.png",
        "dividend_yield_spread_percentile",
        "earnings_yield_spread_percentile",
        "利率相对吸引力",
        charts.SPREAD_CHART_CID,
    )

    assert result.image_path is not None
    assert axis.plot_calls[0]["linewidth"] == 1.0
    assert axis.plot_calls[1]["linewidth"] == 1.0
    assert axis.xticks is not None
