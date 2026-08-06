"""集思录日历提醒 section 入口。

用法:
    python -m src.convertible.calendar.run            # 拉日历 + 发邮件(单 section)
    python -m src.convertible.calendar.run --preview  # 拉日历 + 生成预览 HTML
    build_section(work_dir) 供转债行情板块 run.py 聚合。
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ...common import alerts, email
from . import calendar, render

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "cb_calendar.yaml"
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "convertible_calendar.html"


def build_section(work_dir: Path) -> Optional[Dict[str, Any]]:
    """遍历所有规则 -> 拉日历(共享缓存) -> 关键词过滤 -> 聚合为一个 section。

    无命中事件返回 None。日历接口公开,无需登录。
    """
    rules = calendar.load_calendar_rules(str(DEFAULT_CONFIG_PATH))
    if not rules:
        print("[WARN] 未配置 calendar_monitors")
        return None

    current_time = calendar.now_in_beijing()
    cache: Dict[tuple, list] = {}
    matched_rules: list = []
    for rule in rules:
        name = str(rule.get("name", "")).strip()
        qtype = str(rule.get("qtype", "CNV")).strip() or "CNV"
        window = str(rule.get("window", "next_month")).strip() or "next_month"
        lookahead_days = int(rule.get("lookahead_days", 45))
        keywords = rule.get("title_keywords", [])
        if not name or not isinstance(keywords, list):
            print(f"[ERROR] calendar_monitors 配置不完整,已跳过: {rule}")
            continue
        cache_key = (qtype, window, lookahead_days)
        if cache_key not in cache:
            try:
                cache[cache_key] = calendar.fetch_calendar_events(
                    qtype, window=window, lookahead_days=lookahead_days, current_time=current_time,
                )
            except Exception as exc:  # noqa: BLE001
                alerts.notify_alert("集思录日历拉取失败", f"{name}: {exc}")
                cache[cache_key] = []
        matched = calendar.filter_events_by_keywords(cache[cache_key], keywords)
        if matched:
            matched_rules.append({"rule_name": name, "events": matched})

    if not matched_rules:
        print("[INFO] 日历无命中事件,跳过 section")
        return None

    html = render.build_section_html(matched_rules, current_time=current_time)
    return {"html": html, "inline_images": {}, "as_of_date": current_time.strftime("%Y-%m-%d")}


def run_send() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        section = build_section(Path(tmpdir))
        if section is None:
            return 1
        subject = f"集思录日历提醒 {section['as_of_date']}"
        html = email.compose_sections([section["html"]])
        ok = email.send_email(subject, html)
        return 0 if ok else 1


def run_preview(output_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        section = build_section(Path(tmpdir))
    if section is None:
        raise RuntimeError("日历无命中事件,无法生成预览")
    body = email.compose_sections([section["html"]])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>集思录日历提醒预览 {section['as_of_date']}</title></head>
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
    parser = argparse.ArgumentParser(description="集思录日历下修提醒")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML(不发信)")
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW_PATH, help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
