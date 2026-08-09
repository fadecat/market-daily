from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..research.dividend_observation_chart import (
    DEFAULT_OUTPUT_PATH,
    _default_style_rotation_fetcher,
    build_dividend_observation_payload,
)
from ..research.dividend_observation_chart_preview import build_display_payload
from ..research.dividend_observation_config import DEFAULT_CONFIG_PATH


def load_payload(path: Path | str = DEFAULT_OUTPUT_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dividend observation payload must be a mapping")
    return payload


def build_or_load_payload(
    *,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    force_refresh: bool = True,
    force_refresh_style_rotation: bool = False,
) -> dict[str, Any]:
    path = Path(output_path)
    if force_refresh:
        payload = build_dividend_observation_payload(
            style_rotation_fetcher=(
                _default_style_rotation_fetcher if force_refresh_style_rotation else None
            ),
            force_refresh_style_rotation_payload=force_refresh_style_rotation,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload
    if path.exists():
        return load_payload(path)
    payload = build_dividend_observation_payload(
        style_rotation_fetcher=(
            _default_style_rotation_fetcher if force_refresh_style_rotation else None
        ),
        force_refresh_style_rotation_payload=force_refresh_style_rotation,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def prepare_display_payload(
    payload: dict[str, Any],
    *,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    display_payload = build_display_payload(payload, config_path=config_path)
    if not isinstance(display_payload, dict):
        raise ValueError("dividend observation display payload must be a mapping")
    return display_payload
