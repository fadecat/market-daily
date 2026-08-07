"""商品极值板块编排:akshare 扫描 ~75 个品种分位数 -> 发邮件 / 生预览。

用法:
    python -m src.commodity.run              # 扫描并发送极值日报邮件
    python -m src.commodity.run --preview    # 仅生成 preview/commodity.html(不发信)

扫描 ~75 个品种、每品种间隔 2-4s,全程约 3-5 分钟。preview 与发送均跑全量扫描。
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..common import alerts, email
from . import config as commodity_config
from . import core as commodity_core
from . import reporting as commodity_reporting

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "commodity.yaml"
DEFAULT_PREVIEW_PATH = REPO_ROOT / "preview" / "commodity.html"


def _scan() -> tuple[commodity_config.MonitorConfig, list[commodity_core.SymbolResult]]:
    cfg = commodity_config.load_config(CONFIG_PATH)
    results = commodity_core.run_scan(cfg)
    return cfg, results


def _today_cn() -> date:
    """北京时间今日(供 skip_if_no_today_data 守卫,可被测试 patch)。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def run_send() -> int:
    """扫描全部品种并发送商品极值日报邮件。

    ``skip_if_no_today_data`` 为真且无品种拉到今日数据时(长假/akshare 全体返旧数据)
    跳过发信,避免发一封以旧日期为 subject 的"日报"(移植自源仓库 has_today_data 守卫)。
    """
    try:
        cfg, results = _scan()
        today = _today_cn()
        if cfg.skip_if_no_today_data and not any(
            r.error is None and r.latest_date is not None and r.latest_date == today
            for r in results
        ):
            print(f"[INFO] 无今日({today})品种数据,跳过发信(skip_if_no_today_data)")
            return 0
        html_parts, _summary = commodity_reporting.build_email_html(results, cfg)
        latest_dates = [r.latest_date for r in results if r.latest_date is not None]
        date_tag = max(latest_dates).isoformat() if latest_dates else ""
        subject = f"商品极值监控日报 {date_tag}".strip()
        html = email.compose_sections(html_parts)
        ok = email.send_email(subject, html)
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("商品极值板块运行失败", f"{type(exc).__name__}: {exc}")
        return 1


def run_preview(output_path: str | Path | None = None) -> Path:
    """扫描全部品种,生成预览 HTML(不发信)。"""
    output_path = Path(output_path) if output_path else DEFAULT_PREVIEW_PATH
    try:
        cfg, results = _scan()
        html_parts, _summary = commodity_reporting.build_email_html(results, cfg)
        body_html = email.compose_sections(html_parts)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"商品极值板块预览生成失败: {exc}") from exc
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>商品极值监控日报预览</title></head>
<body style="margin:0;padding:20px;background:#f5f6f7">
{body_html}
</body></html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {output_path}")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="商品极值板块:商品分位数极值监控日报")
    parser.add_argument("--preview", action="store_true", help="仅生成预览 HTML,不发信")
    parser.add_argument("--output", default=None, help="预览输出路径(仅 --preview 生效)")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
