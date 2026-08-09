from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "data" / "research" / "dividend_observation_config.json"
DEFAULT_ANALYSIS_WINDOW_YEARS = 3
DEFAULT_DISPLAY_WINDOW_YEARS = 3
TRADING_DAYS_PER_YEAR = 252


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _int_or_default(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _years_to_days(years: int) -> int:
    return years * TRADING_DAYS_PER_YEAR


def load_dividend_observation_window_config(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> dict[str, int]:
    path = Path(config_path)
    payload: dict[str, Any] = {}
    if path.exists():
        raw = _load_json(path)
        if isinstance(raw, dict):
            payload = raw

    analysis_years = _int_or_default(payload.get("analysis_window_years"), DEFAULT_ANALYSIS_WINDOW_YEARS)
    display_years = _int_or_default(
        payload.get("display_window_years"),
        analysis_years or DEFAULT_DISPLAY_WINDOW_YEARS,
    )
    return {
        "analysis_window_years": analysis_years,
        "display_window_years": display_years,
        "drawdown_days": _years_to_days(analysis_years),
        "valuation_days": _years_to_days(analysis_years),
        "spread_days": _years_to_days(analysis_years),
        "style_days": _years_to_days(analysis_years),
        "display_window_days": _years_to_days(display_years),
    }
