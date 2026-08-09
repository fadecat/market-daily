from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from ..research import dividend_observation_chart as research_chart
from ..research import dividend_observation_chart_preview as research_preview
from ..valuation import refresh_archive
from . import data, run as email_run

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESEARCH_PREVIEW_PATH = REPO_ROOT / "preview" / "dividend_observation_930955.html"
DEFAULT_EMAIL_PREVIEW_PATH = REPO_ROOT / "preview" / "dividend_observation_email.html"


def refresh_local_preview(
    *,
    research_output_path: Path | str = DEFAULT_RESEARCH_PREVIEW_PATH,
    email_output_path: Path | str = DEFAULT_EMAIL_PREVIEW_PATH,
    skip_archive: bool = False,
) -> int:
    if not skip_archive:
        archive_code = refresh_archive.main(
            ["--config", str(refresh_archive.DEFAULT_CONFIG_PATH)]
        )
        if archive_code != 0:
            return archive_code

    raw_payload = data.build_or_load_payload(
        force_refresh=True,
        force_refresh_style_rotation=True,
    )
    display_payload = data.prepare_display_payload(raw_payload)
    research_preview.run_preview(
        input_path=research_chart.DEFAULT_OUTPUT_PATH,
        output_path=Path(research_output_path),
    )
    email_run.run_preview(
        output_path=Path(email_output_path),
        payload=display_payload,
        force_refresh_style_rotation=False,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新本地红利观察数据并重建两个预览。")
    parser.add_argument(
        "--research-output",
        type=Path,
        default=DEFAULT_RESEARCH_PREVIEW_PATH,
        help="研究版 HTML 预览输出路径",
    )
    parser.add_argument(
        "--email-output",
        type=Path,
        default=DEFAULT_EMAIL_PREVIEW_PATH,
        help="邮件版 HTML 预览输出路径",
    )
    parser.add_argument(
        "--skip-archive",
        action="store_true",
        help="跳过归档刷新，仅重建 payload 与预览",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    return refresh_local_preview(
        research_output_path=args.research_output,
        email_output_path=args.email_output,
        skip_archive=args.skip_archive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
