from __future__ import annotations

import argparse
import json
import math
from html import escape
from pathlib import Path
from typing import Any, Sequence

from .dividend_observation_chart import DEFAULT_OUTPUT_PATH as DEFAULT_INPUT_PATH
from .dividend_observation_config import (
    DEFAULT_CONFIG_PATH,
    load_dividend_observation_window_config,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREVIEW_PATH = REPO_ROOT / "preview" / "dividend_observation_930955.html"
STATE_LABELS = {
    "failed_recovery": "修复失败",
    "temporary_recovery": "临时修复",
    "confirmed_recovery": "确认修复",
}


def _fmt_pct(value: float | None, *, scale_100: bool = True) -> str:
    if value is None:
        return "-"
    number = value * 100 if scale_100 else value
    return f"{number:.1f}%"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _state_label(value: str | None) -> str:
    if not value:
        return "-"
    return STATE_LABELS.get(value, value)


def _window_years(meta: dict[str, Any], key: str, fallback: int) -> int:
    try:
        value = int(meta.get(key))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _date_axis_config() -> str:
    return """{
          type: "category",
          data: dates,
          axisLabel: {
            showMaxLabel: true,
            hideOverlap: true
          }
        }"""


def _estimated_endpoint_options(meta: dict[str, Any], series: dict[str, Any]) -> str:
    latest_estimate = meta.get("latest_estimate")
    dates = list(series.get("dates") or [])
    if not isinstance(latest_estimate, dict) or not dates:
        return "const estimateEndpointOptions = {};\n    const estimateEndpointLabelGridRight = 56;"
    if latest_estimate.get("date") != dates[-1]:
        return "const estimateEndpointOptions = {};\n    const estimateEndpointLabelGridRight = 56;"

    label_specs = (
        ("pe_ttm", "pe_ttm_percentile", "PE", ""),
        ("pb_lf", "pb_lf_percentile", "PB", ""),
        ("dividend_yield_spread", "dividend_yield_spread_percentile", "股息率差", "%"),
        ("earnings_yield_spread", "earnings_yield_spread_percentile", "盈利收益率差", "%"),
    )
    formatted_values: list[tuple[str, str, str, str]] = []
    for key, percentile_key, label, suffix in label_specs:
        try:
            value = float(latest_estimate[key])
            percentile = float(series[percentile_key][-1])
        except (IndexError, KeyError, TypeError, ValueError):
            return "const estimateEndpointOptions = {};\n    const estimateEndpointLabelGridRight = 56;"
        if not math.isfinite(value) or not math.isfinite(percentile):
            return "const estimateEndpointOptions = {};\n    const estimateEndpointLabelGridRight = 56;"
        formatted_values.append((key, label, _fmt_num(value), suffix))

    options = ",\n".join(
        f'''          {key}: {{
            endLabel: {{
              show: true,
              formatter: function(params) {{
                return "{label} {value}{suffix}，分位 " + Number(params.value).toFixed(1) + "%（预估）";
              }}
            }},
            labelLayout: {{ moveOverlap: "shiftY" }}
          }}'''
        for key, label, value, suffix in formatted_values
    )
    return (
        f"const estimateEndpointOptions = {{\n{options}\n        }};\n"
        "    const estimateEndpointLabelGridRight = 188;"
    )


def build_display_payload(
    payload: dict[str, Any],
    *,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    display_window_days: int | None = None,
) -> dict[str, Any]:
    series = payload.get("series") or {}
    dates = list(series.get("dates") or [])
    meta = dict(payload.get("meta") or {})
    window_config = load_dividend_observation_window_config(config_path)
    if display_window_days is None:
        display_years = _window_years(
            meta,
            "display_window_years",
            window_config["display_window_years"],
        )
        display_window_days = display_years * 252
    if display_window_days <= 0 or len(dates) <= display_window_days:
        clipped_series = {
            key: list(value) if isinstance(value, list) else value
            for key, value in series.items()
        }
    else:
        start = len(dates) - display_window_days
        clipped_series = {}
        for key, value in series.items():
            if isinstance(value, list) and len(value) == len(dates):
                clipped_series[key] = value[start:]
            else:
                clipped_series[key] = value

    meta["display_window_days"] = min(display_window_days, len(dates)) if dates else display_window_days
    meta["display_window_years"] = _window_years(
        meta,
        "display_window_years",
        window_config["display_window_years"],
    )
    meta["analysis_window_years"] = _window_years(
        meta,
        "analysis_window_years",
        window_config["analysis_window_years"],
    )
    return {
        "meta": meta,
        "series": clipped_series,
        "latest": dict(payload.get("latest") or {}),
    }


def build_preview_html(
    payload: dict[str, Any],
    *,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> str:
    display_payload = build_display_payload(payload, config_path=config_path)
    meta = display_payload.get("meta", {})
    latest = display_payload.get("latest", {})
    index_name = str(meta.get("index_name") or "红利低波100")
    index_code = str(meta.get("index_code") or "930955")
    analysis_window_years = _window_years(meta, "analysis_window_years", 3)
    display_window_years = _window_years(meta, "display_window_years", 3)
    payload_json = json.dumps(display_payload, ensure_ascii=False)
    estimate_endpoint_options = _estimated_endpoint_options(
        meta,
        dict(display_payload.get("series") or {}),
    )

    template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>红利观察图</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <style>
    :root {
      --bg: #f3efe7;
      --paper: rgba(255,255,255,0.82);
      --ink: #17212b;
      --muted: #667085;
      --line: rgba(23,33,43,0.10);
      --shadow: 0 18px 40px rgba(23,33,43,0.10);
      --accent: #aa6a16;
      --danger: #b85042;
      --warn: #c48a1c;
      --ok: #3f7d4b;
      --style: #d9485f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: "Avenir Next", "Microsoft YaHei", "PingFang SC", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(170,106,22,0.10), transparent 30%),
        linear-gradient(180deg, #f7f2ea 0%, var(--bg) 100%);
    }
    .shell {
      max-width: 1360px;
      margin: 0 auto;
      padding: 32px 18px 56px;
    }
    .hero,
    .panel {
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .hero {
      padding: 28px;
      margin-bottom: 18px;
    }
    .eyebrow {
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 12px;
    }
    h1 {
      margin: 0;
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1.05;
      font-family: "Georgia", "STSong", serif;
    }
    .sub {
      margin: 12px 0 0;
      color: var(--muted);
      line-height: 1.7;
      max-width: 70ch;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }
    .card {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.72);
    }
    .card-label {
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 10px;
    }
    .card-value {
      font-size: 30px;
      line-height: 1.05;
      font-weight: 700;
    }
    .card-meta {
      margin-top: 8px;
      font-size: 13px;
      color: var(--muted);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 16px;
    }
    .span-12 { grid-column: span 12; }
    .panel {
      padding: 20px;
    }
    .section-title {
      margin: 0 0 12px;
      font-size: 15px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .section-note {
      margin: -4px 0 4px;
      font-size: 13px;
      line-height: 1.6;
      color: var(--muted);
    }
    .section-formula {
      margin: 0 0 12px;
      font-size: 12px;
      line-height: 1.6;
      color: var(--muted);
      font-family: "Consolas", "SFMono-Regular", "Microsoft YaHei", monospace;
    }
    .chart {
      width: 100%;
      height: 290px;
    }
    .stack-chart .chart {
      height: 290px;
    }
    @media (max-width: 1100px) {
      .cards,
      .grid {
        grid-template-columns: 1fr;
      }
      .span-12 {
        grid-column: span 1;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Observation Preview</div>
      <h1>红利观察图 · __INDEX_NAME__</h1>
      <p class="sub">这是一张围绕 __INDEX_NAME__(__INDEX_CODE__) 的本地研究观察页。它只展示价格位置、绝对定价、利率相对吸引力、风格挤压和修复状态，不输出买卖建议。</p>
      <div class="cards">
        <div class="card">
          <div class="card-label">最新日期</div>
          <div class="card-value">__LATEST_DATE__</div>
          <div class="card-meta">观察对象 __INDEX_CODE__</div>
        </div>
        <div class="card">
          <div class="card-label">近窗回撤</div>
        <div class="card-value">__LATEST_DRAWDOWN__</div>
          <div class="card-meta">最新收盘 __LATEST_CLOSE__</div>
        </div>
        <div class="card">
          <div class="card-label">绝对估值</div>
          <div class="card-value">PE __LATEST_PE__</div>
          <div class="card-meta">PB __LATEST_PB__</div>
        </div>
        <div class="card">
          <div class="card-label">当前状态</div>
          <div class="card-value">__LATEST_STATE__</div>
        <div class="card-meta">风格挤压 __LATEST_STYLE__</div>
        </div>
      </div>
    </section>

    <section class="grid">
      <div class="panel span-12">
        <div class="section-title">价格与回撤</div>
        <div class="section-note">看当前离近__DISPLAY_WINDOW_YEARS__年高点还有多远，以及回撤发生在什么价格位置。</div>
        <div class="section-formula">drawdown_peak = close / 近__ANALYSIS_WINDOW_YEARS__年滚动高点 - 1</div>
        <div id="price-chart" class="chart"></div>
      </div>
      <div class="panel span-12 stack-chart">
        <div class="section-title">利率相对吸引力</div>
        <div class="section-note">看红利相对10年国债是否更有吸引力。</div>
        <div class="section-formula">dividend_yield_spread = 股息率 - 10年国债收益率；earnings_yield_spread = 100 / PE - 10年国债收益率；两者均取近__ANALYSIS_WINDOW_YEARS__年百分位</div>
        <div id="spread-chart" class="chart"></div>
      </div>
      <div class="panel span-12 stack-chart">
        <div class="section-title">绝对定价</div>
        <div class="section-note">看红利现在在近__ANALYSIS_WINDOW_YEARS__年估值里偏贵还是偏便宜。</div>
        <div class="section-formula">pe_ttm_percentile / pb_lf_percentile = 近__ANALYSIS_WINDOW_YEARS__年历史百分位</div>
        <div id="valuation-chart" class="chart"></div>
      </div>
      <div class="panel span-12 stack-chart">
        <div class="section-title">风格挤压</div>
        <div class="section-note">看市场是否仍处在成长拥挤、红利受压的阶段。</div>
        <div class="section-formula">style_rotation_spread_percentile = 风格轮动收益率差值近__ANALYSIS_WINDOW_YEARS__年百分位</div>
        <div id="style-chart" class="chart"></div>
      </div>
    </section>
  </div>

  <script>
    const payload = __PAYLOAD__;
    const series = payload.series || {};
    const dates = series.dates || [];
    const analysisWindowYears = __ANALYSIS_WINDOW_YEARS__;
    const displayWindowYears = __DISPLAY_WINDOW_YEARS__;
    const dateAxis = __DATE_AXIS__;
    __ESTIMATE_ENDPOINT_OPTIONS__

    function lineOption(title, rows, formatter, rightGridMargin = 56) {
      return {
        animationDuration: 400,
        tooltip: {
          trigger: "axis",
          formatter: items => {
            const head = items[0] ? items[0].axisValue : "";
            const body = items.map(item => item.marker + item.seriesName + ": " + formatter(item.value)).join("<br>");
            return head + "<br>" + body;
          }
        },
        legend: { top: 0 },
        grid: { left: 56, right: rightGridMargin, top: 34, bottom: 42 },
        xAxis: dateAxis,
        yAxis: { type: "value" },
        series: rows.map(row => ({
          type: "line",
          name: row.name,
          data: row.data,
          smooth: false,
          showSymbol: false,
          ...(row.endpointOptions || {})
        }))
      };
    }

    if (!window.echarts) {
      ["price-chart", "valuation-chart", "spread-chart", "style-chart"].forEach(id => {
        document.getElementById(id).innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#667085">ECharts CDN 未加载</div>';
      });
    } else {
      const priceChart = echarts.init(document.getElementById("price-chart"));
      priceChart.group = "dividend-observation";
      priceChart.setOption({
        animationDuration: 400,
        tooltip: {
          trigger: "axis",
          formatter: items => {
            const head = items[0] ? items[0].axisValue : "";
            const body = items.map(item => {
              if (item.seriesName === "drawdown_peak") {
                return item.marker + item.seriesName + ": " + ((item.value || 0) * 100).toFixed(1) + "%";
              }
              return item.marker + item.seriesName + ": " + Number(item.value).toFixed(2);
            }).join("<br>");
            return head + "<br>" + body;
          }
        },
        legend: { top: 0 },
        grid: { left: 56, right: 56, top: 34, bottom: 42 },
        xAxis: dateAxis,
        yAxis: [
          {
            type: "value",
            name: "价格",
            scale: true
          },
          {
            type: "value",
            name: "回撤",
            min: function(value) {
              return Math.min(value.min, -0.02);
            },
            max: 0,
            axisLabel: {
              formatter: function(value) {
                return (value * 100).toFixed(0) + "%";
              }
            }
          }
        ],
        series: [
          {
            type: "line",
            name: "index_close",
            data: series.index_close || [],
            smooth: false,
            showSymbol: false,
            yAxisIndex: 0
          },
          {
            type: "line",
            name: "drawdown_peak",
            data: series.drawdown_peak || [],
            smooth: false,
            showSymbol: false,
            yAxisIndex: 1,
            areaStyle: { opacity: 0.12 }
          }
        ]
      });

      const valuationChart = echarts.init(document.getElementById("valuation-chart"));
      valuationChart.group = "dividend-observation";
      valuationChart.setOption(lineOption("绝对定价", [
        { name: "pe_ttm_percentile", data: series.pe_ttm_percentile || [], endpointOptions: estimateEndpointOptions.pe_ttm },
        { name: "pb_lf_percentile", data: series.pb_lf_percentile || [], endpointOptions: estimateEndpointOptions.pb_lf }
      ], value => value === null || value === undefined ? "-" : Number(value).toFixed(1) + "%", estimateEndpointLabelGridRight));

      const spreadChart = echarts.init(document.getElementById("spread-chart"));
      spreadChart.group = "dividend-observation";
      spreadChart.setOption(lineOption("利率相对吸引力", [
        { name: "dividend_yield_spread_percentile", data: series.dividend_yield_spread_percentile || [], endpointOptions: estimateEndpointOptions.dividend_yield_spread },
        { name: "earnings_yield_spread_percentile", data: series.earnings_yield_spread_percentile || [], endpointOptions: estimateEndpointOptions.earnings_yield_spread }
      ], value => value === null || value === undefined ? "-" : Number(value).toFixed(1) + "%", estimateEndpointLabelGridRight));

      const styleChart = echarts.init(document.getElementById("style-chart"));
      styleChart.group = "dividend-observation";
      styleChart.setOption(lineOption("风格挤压", [
        { name: "style_rotation_spread_percentile", data: series.style_rotation_spread_percentile || [] }
      ], value => value === null || value === undefined ? "-" : Number(value).toFixed(1) + "%"));

      echarts.connect("dividend-observation");

      window.addEventListener("resize", () => {
        priceChart.resize();
        valuationChart.resize();
        spreadChart.resize();
        styleChart.resize();
      });
    }
  </script>
</body>
</html>
"""

    return (
        template.replace("__INDEX_NAME__", escape(index_name))
        .replace("__INDEX_CODE__", escape(index_code))
        .replace("__ANALYSIS_WINDOW_YEARS__", escape(str(analysis_window_years)))
        .replace("__DISPLAY_WINDOW_YEARS__", escape(str(display_window_years)))
        .replace("__LATEST_DATE__", escape(str(latest.get("date") or "-")))
        .replace("__LATEST_DRAWDOWN__", escape(_fmt_pct(latest.get("drawdown_peak"))))
        .replace("__LATEST_CLOSE__", escape(_fmt_num(latest.get("index_close"))))
        .replace("__LATEST_PE__", escape(_fmt_pct(latest.get("pe_ttm_percentile"), scale_100=False)))
        .replace("__LATEST_PB__", escape(_fmt_pct(latest.get("pb_lf_percentile"), scale_100=False)))
        .replace(
            "__LATEST_STYLE__",
            escape(_fmt_pct(latest.get("style_rotation_spread_percentile"), scale_100=False)),
        )
        .replace("__LATEST_STATE__", escape(_state_label(latest.get("event_state"))))
        .replace("__DATE_AXIS__", _date_axis_config())
        .replace("__ESTIMATE_ENDPOINT_OPTIONS__", estimate_endpoint_options)
        .replace("__PAYLOAD__", payload_json)
    )


def run_preview(
    input_path: Path = DEFAULT_INPUT_PATH,
    output_path: Path = DEFAULT_PREVIEW_PATH,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> Path:
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    html = build_preview_html(payload, config_path=config_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(f"[INFO] dividend observation preview generated: {output}")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate local ECharts preview for dividend observation payload.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    run_preview(args.input, args.output, args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
