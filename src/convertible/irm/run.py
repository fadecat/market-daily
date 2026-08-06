"""董秘互动 section 入口。

用法:
    python -m src.convertible.irm.run            # 拉筛选转债 + 查董秘互动 + 发邮件(单 section)
    python -m src.convertible.irm.run --preview  # 生成预览 HTML
    build_section(work_dir, rows=None) 供转债行情板块 run.py 聚合;rows 由筛选 section 复用,
    避免重复抓集思录。rows 为 None 时自行 login+fetch_and_filter 取 rows。
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ...common import alerts, email
from ..screening import strategy as screening
from . import query, render

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "cb_irm.yaml"
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "convertible_irm.html"


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("max_qas_per_stock", 3)
    data.setdefault("question_max", 80)
    data.setdefault("answer_max", 150)
    return data


def _fetch_rows() -> List[Dict[str, Any]]:
    """rows 为 None 时:登录集思录 -> 拉全市场 -> 过滤排序,返回筛选后的 rows。"""
    config = screening.load_config()
    session = screening.login()
    try:
        filtered, _index_quote = screening.fetch_and_filter(session, config)
    finally:
        session.close()
    return filtered


def build_section(
    work_dir: Path, rows: Optional[List[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """对筛选转债正股查董秘互动,渲染 section。

    rows 由筛选 section 复用;为 None 时自行抓取。无互动数据返回 None(板块略过)。
    """
    if rows is None:
        rows = _fetch_rows()
    if not rows:
        print("[INFO] 无筛选转债 rows,跳过 IRM section")
        return None

    config = load_config()
    stock_qas = query.collect_irm_for_rows(
        rows,
        max_qas_per_stock=int(config["max_qas_per_stock"]),
        question_max=int(config["question_max"]),
        answer_max=int(config["answer_max"]),
    )
    if not stock_qas:
        print("[INFO] 近一周无董秘互动,跳过 IRM section")
        return None

    html = render.build_section_html(stock_qas)
    as_of_date = query.now_in_beijing().strftime("%Y-%m-%d")
    return {"html": html, "inline_images": {}, "as_of_date": as_of_date}


def run_send() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        section = build_section(Path(tmpdir))
        if section is None:
            return 1
        subject = f"正股董秘互动 {section['as_of_date']}"
        html = email.compose_sections([section["html"]])
        ok = email.send_email(subject, html)
        return 0 if ok else 1


def run_preview(output_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        section = build_section(Path(tmpdir))
    if section is None:
        raise RuntimeError("近一周无董秘互动,无法生成预览")
    body = email.compose_sections([section["html"]])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>正股董秘互动预览 {section['as_of_date']}</title></head>
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
    parser = argparse.ArgumentParser(description="正股董秘互动(深交所互动易 + 上证 e 互动)")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML(不发信)")
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW_PATH, help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
