"""可转债三低轮动 section 入口。

两种用法:
1. 板块聚合:转债行情 run.py 调 ``build_section(work_dir)`` 拿
   {html, inline_images, as_of_date},与其他 section 一起 compose_sections + 一次性发信。
2. 独立运行:``python -m src.convertible.three_low.run --preview`` 或发信,便于调试。
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ...common import alerts, email
from . import charts, render, strategy

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "convertible_three_low.html"


def _enrich_report(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """build_report + 基准对比 + 最大回撤区间,补 benchmark_return/excess_return。"""
    report = strategy.build_report(state, config)
    benchmark_series = render.load_benchmark_series()
    aligned = strategy.align_benchmark(report["history"], benchmark_series) if benchmark_series else []
    comparison = strategy.compute_benchmark_comparison(report["history"], benchmark_series or [])
    report["benchmark_return"] = comparison["benchmark_return"]
    report["excess_return"] = comparison["excess_return"]
    report["_aligned"] = aligned
    report["_drawdown"] = strategy.find_max_drawdown_window(
        report["history"], float(config["strategy"]["initial_nav"])
    )
    return report


def build_section(work_dir: Path) -> Optional[Dict[str, Any]]:
    """跑策略并返回 section 片段供板块聚合。无新交易日返回 None。

    work_dir:净值图落盘目录(由调用方管理生命周期),返回 inline_images 指向其中的 png。
    """
    prev = strategy.load_state()
    try:
        state = strategy.run_strategy()
    except Exception as exc:
        alerts.notify_alert("转债三低轮动运行失败", str(exc))
        raise
    if state is None:
        print("[WARN] 三低轮动策略无数据")
        return None
    if not render.history_updated(prev, state):
        print(f"[INFO] 三低轮动无新交易日(last_run_date={state.get('last_run_date')}),跳过")
        return None

    config = strategy.load_strategy_config()
    report = _enrich_report(state, config)

    chart_path = Path(work_dir) / f"{charts.NAV_CHART_CID}.png"
    inline_images: Dict[str, str] = {}
    try:
        charts.generate_nav_chart(
            report["history"], chart_path,
            benchmark=report["_aligned"] or None, drawdown=report["_drawdown"],
        )
        inline_images[charts.NAV_CHART_CID] = str(chart_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 三低轮动净值曲线生成失败: {exc}")

    html = render.build_email_html(report, charts.NAV_CHART_CID)
    return {
        "html": html,
        "inline_images": inline_images,
        "as_of_date": report.get("as_of_date"),
    }


def run_send() -> int:
    """独立发信(单 section 整封邮件)。返回 0 成功 / 1 无数据。

    tempdir 须存活到 send_email 读图完成,故发信在 with 块内完成。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        section = build_section(Path(tmpdir))
        if section is None:
            return 1
        subject = f"可转债三低轮动日报 {section.get('as_of_date', '')}"
        ok = email.send_email(subject, section["html"], inline_images=section["inline_images"] or None)
        return 0 if ok else 1


def run_preview(output_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    """基于已存状态生成预览页(不联网跑策略,便于离线查看)。"""
    state = strategy.load_state()
    if state is None:
        raise RuntimeError("无三低轮动状态,请先运行策略")
    config = strategy.load_strategy_config()
    report = _enrich_report(state, config)
    with tempfile.TemporaryDirectory() as tmpdir:
        chart_path = Path(tmpdir) / "nav_chart.png"
        try:
            charts.generate_nav_chart(
                report["history"], chart_path,
                benchmark=report["_aligned"] or None, drawdown=report["_drawdown"],
            )
            html = render.build_preview_html(report, chart_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 净值曲线生成失败: {exc}")
            html = render.build_preview_html(report, None)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {out}")
    return out


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="可转债三低轮动 模拟盘")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML(不发信)")
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW_PATH, help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
