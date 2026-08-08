"""可转债指数图 section 入口。

用法:
    python -m src.convertible.index_chart.run            # 拉指数历史 + 生成图 + 发邮件(单 section)
    python -m src.convertible.index_chart.run --preview  # 生成预览 HTML(图 base64 内嵌)
    build_section(work_dir) 供转债行情板块 run.py 聚合。
"""
from __future__ import annotations

import argparse
import base64
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ...common import alerts, email
from . import charts, history

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "convertible_index_chart.html"
CB_INDEX_CHART_CID = "cb_index_chart"


def build_section(work_dir: Path) -> Optional[Dict[str, Any]]:
    """抓转债等权指数历史 -> 生成价格中位数/平均收益率图 -> 返回 section 片段。

    数据不足(<30 个有效交易日)返回 None。cb_index 页面公开,无需登录。
    work_dir:图落盘目录(由调用方管理生命周期)。
    """
    try:
        merged, _stats = history.build_merged_history()
    except Exception as exc:
        alerts.notify_alert("转债指数历史抓取失败", str(exc))
        raise
    as_of_date = (merged[-1].get("date") or "") if merged else ""

    chart_path = Path(work_dir) / f"{CB_INDEX_CHART_CID}.png"
    result = charts.generate_cb_index_chart(chart_path, records=merged)
    if result is None:
        print("[INFO] 转债指数图数据不足(<30),跳过 section")
        return None

    html = (
        f'<div style="margin:8px 0;color:#687386;font-size:12px">'
        f"转债等权指数：A股收盘后更新，当前数据截至 {as_of_date or '暂无'}"
        f"</div>"
        f'<div style="margin:8px 0;text-align:center">'
        f'<img src="cid:{CB_INDEX_CHART_CID}" alt="可转债价格中位数与平均收益率" '
        f'style="max-width:100%;height:auto"></div>'
    )
    return {
        "html": html,
        "inline_images": {CB_INDEX_CHART_CID: str(chart_path)},
        "as_of_date": as_of_date,
    }


def _embed_image_data_uri(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def run_send() -> int:
    """独立发信(单 section 整封邮件)。返回 0 成功 / 1 无数据。

    tempdir 须存活到 send_email 读图完成,故发信在 with 块内完成。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        section = build_section(Path(tmpdir))
        if section is None:
            return 1
        subject = f"可转债指数图 {section.get('as_of_date', '')}"
        html = email.compose_sections([section["html"]])
        ok = email.send_email(subject, html, inline_images=section["inline_images"] or None)
        return 0 if ok else 1


def run_preview(output_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        chart_path = Path(tmpdir) / f"{CB_INDEX_CHART_CID}.png"
        try:
            merged, _ = history.build_merged_history()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"转债指数历史抓取失败: {exc}")
        as_of_date = (merged[-1].get("date") or "") if merged else ""
        result = charts.generate_cb_index_chart(chart_path, records=merged)
        if result is None:
            raise RuntimeError("转债指数图数据不足(<30),无法生成预览")
        img_src = _embed_image_data_uri(chart_path)

    body = (
        f'<div style="margin:8px 0;color:#687386;font-size:12px">'
        f"转债等权指数：A股收盘后更新，当前数据截至 {as_of_date or '暂无'}"
        f"</div>"
        f'<div style="margin:8px 0;text-align:center">'
        f'<img src="{img_src}" alt="可转债价格中位数与平均收益率" '
        f'style="max-width:100%;height:auto"></div>'
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>可转债指数图预览 {as_of_date}</title></head>
<body style="margin:0;padding:20px;background:#f5f6f7">
{email.compose_sections([body])}
</body></html>
"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {out}")
    return out


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="可转债指数图(价格中位数 + 平均收益率)")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML(不发信)")
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW_PATH, help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
