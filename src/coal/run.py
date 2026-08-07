"""煤炭日报板块编排:抓取 CCTDA 煤炭日报 -> 发邮件 / 生预览。

用法:
    python -m src.coal.run              # 抓取并发送日报邮件
    python -m src.coal.run --preview    # 仅生成 preview/coal.html(不发信)
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from src.common import alerts, email, storage
from src.coal import cctda

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREVIEW_PATH = REPO_ROOT / "preview" / "coal.html"


def run_send() -> int:
    """抓取最新 CCTDA 日报并发送邮件。已发过的(按 article_url 去重)跳过。"""
    try:
        state = storage.load_state(cctda.STATE_NAME, default={})
        latest = cctda.parse_latest_article_from_list(
            cctda.fetch_html(cctda.CCTDA_LIST_URL), cctda.CCTDA_LIST_URL
        )
        print(f"[INFO] 最新日报: {latest['article_title']} -> {latest['article_url']}")

        if cctda.should_skip_article(latest, state):
            print(f"[INFO] 最新日报已发送,跳过: {latest['article_url']}")
            return 0

        detail = cctda.parse_detail_content(
            cctda.fetch_html(latest["article_url"]), latest["article_url"]
        )
        print(f"[INFO] 内容类型: {detail['content_type']}")

        with tempfile.TemporaryDirectory(prefix="cctda_coal_daily_") as temp_dir:
            temp_root = Path(temp_dir)
            image_paths, content_hash = cctda.materialize_report_pages(detail, temp_root)
            fetched_at = cctda.now_in_beijing().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[INFO] 图片数量: {len(image_paths)}")

            subject = str(detail["article_title"])
            html = cctda.build_email_html(subject, latest["article_url"], fetched_at, len(image_paths))
            inline_images = {
                f"report_page_{index}": str(path)
                for index, path in enumerate(image_paths, start=1)
            }
            email.send_email(subject, html, inline_images=inline_images)

            storage.save_state(
                cctda.STATE_NAME,
                {
                    "article_url": latest["article_url"],
                    "article_title": str(detail["article_title"]),
                    "published_at": str(detail.get("published_at", "")),
                    "content_type": str(detail["content_type"]),
                    "image_count": len(image_paths),
                    "content_hash": content_hash,
                    "sent_at": fetched_at,
                },
            )
            print(f"[INFO] 状态已更新: {cctda.STATE_NAME}")
        return 0
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("煤炭日报板块运行失败", f"{type(exc).__name__}: {exc}")
        raise


def run_preview(output_path: str | Path | None = None) -> int:
    """抓取最新日报,生成预览 HTML(图片 base64 内嵌,不发信)。"""
    output_path = Path(output_path) if output_path else DEFAULT_PREVIEW_PATH
    latest = cctda.parse_latest_article_from_list(
        cctda.fetch_html(cctda.CCTDA_LIST_URL), cctda.CCTDA_LIST_URL
    )
    detail = cctda.parse_detail_content(
        cctda.fetch_html(latest["article_url"]), latest["article_url"]
    )
    fetched_at = cctda.now_in_beijing().strftime("%Y-%m-%d %H:%M:%S")

    with tempfile.TemporaryDirectory(prefix="cctda_preview_") as temp_dir:
        image_paths, _content_hash = cctda.materialize_report_pages(detail, Path(temp_dir))
        html = cctda.build_preview_html(
            str(detail["article_title"]), latest["article_url"], fetched_at, image_paths
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="煤炭日报板块:CCTDA 煤炭日报")
    parser.add_argument("--preview", action="store_true", help="仅生成预览 HTML,不发信")
    parser.add_argument("--output", default=None, help="预览输出路径(仅 --preview 生效)")
    args = parser.parse_args(argv)
    if args.preview:
        return run_preview(args.output)
    return run_send()


if __name__ == "__main__":
    sys.exit(main())
