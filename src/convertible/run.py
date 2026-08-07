"""转债行情板块编排:聚合 5 个 section(筛选/三低/指数图/董秘互动/日历)→ 发邮件 / 预览。

用法:
    python -m src.convertible.run            # 聚合 5 section + 发邮件
    python -m src.convertible.run --preview  # 生成预览 HTML(图 base64 内嵌,不发信)

编排约定:
- 筛选是主 section,始终返回(无数据展示"暂无"),并附带 rows 供 IRM 复用,避免重复抓集思录;
  筛选失败则整板告警中止(无主 section 不发空邮件)。
- 三低/指数图/董秘互动/日历 为辅 section:各自无数据返回 None 则略过;抛异常则告警但继续,
  不影响主邮件发出。所有 section 共用同一 work_dir(tempdir),图须存活到发信读图完成。
"""
from __future__ import annotations

import argparse
import base64
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from ..common import alerts, email
from .calendar import run as calendar_run
from .irm import run as irm_run
from .index_chart import run as index_chart_run
from .screening import run as screening_run
from .three_low import run as three_low_run

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "convertible.html"

# 辅 section:(名称, 构造函数)——顺序即邮件中呈现顺序
_SECONDARY_SECTIONS = [
    ("三低轮动", lambda work_dir, rows: three_low_run.build_section(work_dir)),
    ("董秘互动", lambda work_dir, rows: irm_run.build_section(work_dir, rows=rows)),
    ("日历提醒", lambda work_dir, rows: calendar_run.build_section(work_dir)),
]


def _build_sections(work_dir: Path) -> Dict[str, Any]:
    """聚合 5 section,返回 ``{fragments, inline_images, as_of_date}``。

    筛选失败抛异常(由调用方捕获告警);辅 section 失败告警后略过。
    """
    fragments: List[str] = []
    inline_images: Dict[str, str] = {}
    as_of_date = ""

    # 0. 转债指数图(注入筛选 section,置于概览后、筛选表前)
    chart_html = ""
    try:
        chart_section = index_chart_run.build_section(work_dir)
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("转债行情板块:转债指数图 section 异常", str(exc))
        chart_section = None
    if chart_section:
        chart_html = chart_section.get("html", "")
        inline_images.update(chart_section.get("inline_images") or {})

    # 1. 筛选(主 section)
    screening_section = screening_run.build_section(work_dir, mid_html=chart_html)  # 失败向上抛
    fragments.append(screening_section["html"])
    inline_images.update(screening_section.get("inline_images") or {})
    as_of_date = screening_section.get("as_of_date", "")
    rows = screening_section.get("rows")

    # 2~5. 辅 section
    for name, build_fn in _SECONDARY_SECTIONS:
        try:
            section = build_fn(work_dir, rows)
        except Exception as exc:  # noqa: BLE001
            alerts.notify_alert(f"转债行情板块:{name} section 异常", str(exc))
            continue
        if section is None:
            continue
        if section.get("html"):
            fragments.append(section["html"])
        inline_images.update(section.get("inline_images") or {})
        if not as_of_date and section.get("as_of_date"):
            as_of_date = section["as_of_date"]

    return {"fragments": fragments, "inline_images": inline_images, "as_of_date": as_of_date}


def _cid_to_data_uri(html: str, inline_images: Dict[str, str]) -> str:
    """把 HTML 里的 ``src="cid:xxx"`` 替换为 base64 data URI,供预览页离线显示。"""
    for cid, path in inline_images.items():
        ext = Path(path).suffix.lower().lstrip(".")
        mime = "image/png" if ext == "png" else ("image/jpeg" if ext in ("jpg", "jpeg") else "image/png")
        data = Path(path).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        html = html.replace(f'src="cid:{cid}"', f'src="data:{mime};base64,{b64}"')
    return html


def run_send() -> int:
    """聚合 5 section + 发邮件。

    tempdir 须存活到 send_email 读图完成,故 compose + send 均在 with 块内;
    发信失败同样走 except 告警(与 valuation/coal/commodity 一致)。
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = _build_sections(Path(tmpdir))
            subject = f"转债行情日报 {bundle['as_of_date']}".strip()
            html = email.compose_sections(bundle["fragments"])
            email.send_email(subject, html, inline_images=bundle["inline_images"] or None)
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("转债行情板块运行失败", str(exc))
        raise
    return 0


def run_preview(output_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = _build_sections(Path(tmpdir))
            body_html = _cid_to_data_uri(
                email.compose_sections(bundle["fragments"]), bundle["inline_images"]
            )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"转债行情板块预览生成失败: {exc}")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>转债行情日报预览 {bundle['as_of_date']}</title></head>
<body style="margin:0;padding:20px;background:#f5f6f7">
{body_html}
</body></html>
"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {out}")
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="转债行情板块(筛选+三低+指数图+董秘互动+日历)")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML(不发信)")
    parser.add_argument("--output", default=str(DEFAULT_PREVIEW_PATH), help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(Path(args.output))
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
