"""可转债低价债筛选 section 入口。

用法:
    python -m src.convertible.screening.run            # 抓数 + 刷新归档 + 发邮件
    python -m src.convertible.screening.run --preview  # 抓数 + 生成预览 HTML
    build_section(work_dir) 供转债行情板块 run.py 聚合;结果附带 rows 供 IRM 复用。
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ...common import alerts, email
from . import archive, render, strategy

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "convertible_screening.html"


def build_section(work_dir: Path, mid_html: str = "") -> Dict[str, Any]:
    """登录 -> 抓 cb_list + 市场概览 -> 过滤排序 -> 刷新下修归档 -> 渲染。

    始终返回结果(screening 是主 section,无数据时展示"暂无")。
    结果附带 rows / index_quote,供板块内 IRM section 复用,避免重复抓取。
    失败抛异常,由调用方(run_send / 板块 run.py)捕获并告警。
    """
    config = strategy.load_config()
    session = strategy.login()
    try:
        filtered, index_quote = strategy.fetch_and_filter(session, config)
        archive_map = archive.refresh_cb_adjust_archives(filtered, session)
    finally:
        session.close()

    html = render.build_section_html(filtered, index_quote, archive_map, config, mid_html=mid_html)
    return {
        "html": html,
        "inline_images": {},
        "as_of_date": strategy.now_in_beijing().strftime("%Y-%m-%d"),
        "rows": filtered,
        "index_quote": index_quote,
    }


def run_send() -> int:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            section = build_section(Path(tmpdir))
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("转债筛选板块运行失败", str(exc))
        raise
    subject = f"集思录可转债筛选日报 {section['as_of_date']}"
    email.send_email(subject, email.compose_sections([section["html"]]))
    return 0


def run_preview(output_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        section = build_section(Path(tmpdir))
    body = email.compose_sections([section["html"]])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>集思录可转债筛选预览 {section['as_of_date']}</title></head>
<body style="margin:0;padding:20px;background:#f5f6f7">
{body}
</body></html>
"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {out}")
    return out


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="可转债低价债筛选")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML(不发信)")
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW_PATH, help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
