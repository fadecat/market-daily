"""资产轮动板块编排:跑策略 -> 生成净值曲线 -> 发邮件 / 出预览。

用法:
    python -m src.rotation.run            # 跑策略并发邮件
    python -m src.rotation.run --preview  # 用已有 state 生成预览 HTML
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ..common import alerts, email
from . import charts, render, strategy

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "rotation.html"


def _build_subject(report: dict) -> str:
    return f"资产轮动日报 {report.get('as_of_date', '')}".strip()


def run_send() -> int:
    prev = strategy.load_state()
    try:
        state = strategy.run_strategy()
        if state is None:
            print("[WARN] 策略无数据，退出")
            alerts.notify_alert("资产轮动板块", "策略无数据，退出")  # 全数据源挂掉应告警
            return 1

        # 用 last_run_date 判断有无新交易日,避免重回填后 history 变短误判为"无新日"跳过邮件
        prev_date = (prev or {}).get("last_run_date")
        if state.get("last_run_date") == prev_date:
            print(f"[INFO] 无新交易日（last_run_date={state.get('last_run_date')}），跳过邮件")
            return 0

        config = strategy.load_strategy_config()
        report = strategy.build_report(state, config)

        with tempfile.TemporaryDirectory() as tmpdir:
            chart_path = Path(tmpdir) / "nav_chart.png"
            inline_images: dict[str, str] = {}
            try:
                charts.generate_nav_chart(report["history"], chart_path)
                inline_images[charts.NAV_CHART_CID] = str(chart_path)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] 净值曲线生成失败: {exc}")

            html = render.build_email_html(report, charts.NAV_CHART_CID)
            subject = _build_subject(report)
            email.send_email(subject, html, inline_images=inline_images or None)
        return 0
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("资产轮动板块运行失败", str(exc))
        raise


def run_preview(output_path: str = str(DEFAULT_PREVIEW_PATH)) -> Path:
    """生成预览 HTML(用已有 state,不跑策略/不发信)。无 state 抛错。"""
    state = strategy.load_state()
    if state is None:
        raise RuntimeError("无策略状态，请先运行 run_send 或 strategy.run_strategy()")
    config = strategy.load_strategy_config()
    report = strategy.build_report(state, config)

    with tempfile.TemporaryDirectory() as tmpdir:
        chart_path = Path(tmpdir) / "nav_chart.png"
        try:
            charts.generate_nav_chart(report["history"], chart_path)
            html = render.build_preview_html(report, chart_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 净值曲线生成失败,预览不含图: {exc}")
            html = render.build_preview_html(report, None)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="资产轮动板块(ETF 20 日轮动)")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML 而非发邮件")
    parser.add_argument("--output", default=str(DEFAULT_PREVIEW_PATH), help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
