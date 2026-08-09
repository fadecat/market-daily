from __future__ import annotations

import argparse
import base64
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ..common import alerts, email
from . import charts, data, render

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREVIEW_PATH = REPO_ROOT / "preview" / "dividend_observation_email.html"


def _build_subject() -> str:
    return f"红利观察日报 | {datetime.now().strftime('%Y-%m-%d')}"


def _preview_data_uri_map(
    chart_bundle: Mapping[str, charts.ChartResult],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, chart in chart_bundle.items():
        if not chart.image_path:
            continue
        encoded = base64.b64encode(Path(chart.image_path).read_bytes()).decode("ascii")
        result[name] = f"data:image/png;base64,{encoded}"
    return result


def _inline_images(
    chart_bundle: Mapping[str, charts.ChartResult],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for chart in chart_bundle.values():
        if not chart.image_path:
            continue
        result[chart.cid] = chart.image_path
    return result


def run_preview(
    output_path: Path | str = DEFAULT_PREVIEW_PATH,
    *,
    payload: dict[str, Any] | None = None,
    force_refresh_style_rotation: bool = False,
) -> Path:
    if payload is None:
        preview_payload = data.prepare_display_payload(
            data.build_or_load_payload(
                force_refresh=True,
                force_refresh_style_rotation=True,
            )
        )
    else:
        preview_payload = payload
    with tempfile.TemporaryDirectory(prefix="dividend_observation_preview_") as tmpdir:
        chart_bundle = charts.generate_chart_bundle(preview_payload, Path(tmpdir))
        html = render.build_preview_html(
            preview_payload,
            chart_bundle,
            _preview_data_uri_map(chart_bundle),
        )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(f"[INFO] dividend observation email preview generated: {output}")
    return output


def run_send() -> int:
    try:
        payload = data.prepare_display_payload(
            data.build_or_load_payload(
                force_refresh=True,
                force_refresh_style_rotation=True,
            )
        )
        with tempfile.TemporaryDirectory(prefix="dividend_observation_send_") as tmpdir:
            chart_bundle = charts.generate_chart_bundle(payload, Path(tmpdir))
            inline_images = _inline_images(chart_bundle)
            html = render.build_email_html(payload, chart_bundle)
            config = email.load_email_config(
                recipient_env_name="DIVIDEND_OBSERVATION_RECEIVER_EMAIL"
            )
            email.send_email(
                _build_subject(),
                html,
                inline_images=inline_images or None,
                config=config,
            )
        return 0
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("红利观察邮件运行失败", f"{type(exc).__name__}: {exc}")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="红利观察独立邮件")
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML 而非发信")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_PREVIEW_PATH),
        help="预览输出路径",
    )
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(args.output)
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
