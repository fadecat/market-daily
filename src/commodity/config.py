"""商品极值板块:配置加载(YAML)。

移植自 commodity-monitor-days 的 config.py,精简:去掉 WeChat/Degrade 配置
(本板块只发邮件,不做企业微信推送与运行时降级)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DelayConfig:
    min_seconds: float
    max_seconds: float


@dataclass(frozen=True)
class ThresholdConfig:
    high_percentile: float
    low_percentile: float


@dataclass(frozen=True)
class SymbolConfig:
    code: str
    name: str
    market: str
    enabled: bool = True


@dataclass(frozen=True)
class MonitorConfig:
    delay: DelayConfig
    thresholds: ThresholdConfig
    windows: dict[str, int]
    symbols: list[SymbolConfig]
    max_stale_days: int
    skip_if_no_today_data: bool


def _required_dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Missing or invalid config section: {key}")
    return value


def _parse_symbols(raw_symbols: Any) -> list[SymbolConfig]:
    if not isinstance(raw_symbols, list):
        raise ValueError("symbols must be a list")
    symbols: list[SymbolConfig] = []
    for item in raw_symbols:
        if not isinstance(item, dict):
            continue
        symbols.append(
            SymbolConfig(
                code=str(item["code"]).strip().upper(),
                name=str(item.get("name", item["code"])).strip(),
                market=str(item.get("market", "domestic")).strip().lower(),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return symbols


def load_config(path: Path) -> MonitorConfig:
    """从 YAML 读取并校验监控配置。"""
    import yaml

    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(content, dict):
        raise ValueError("Config root must be a mapping")

    scan = _required_dict(content, "scan")
    thresholds = _required_dict(content, "thresholds")
    windows = _required_dict(content, "windows")

    delay = DelayConfig(
        min_seconds=float(scan.get("delay_min_seconds", 2.0)),
        max_seconds=float(scan.get("delay_max_seconds", 4.0)),
    )
    if delay.min_seconds < 0 or delay.max_seconds < delay.min_seconds:
        raise ValueError("Invalid delay range in scan section")

    threshold_cfg = ThresholdConfig(
        high_percentile=float(thresholds.get("high_percentile", 85)),
        low_percentile=float(thresholds.get("low_percentile", 30)),
    )
    if not (0 <= threshold_cfg.low_percentile <= 100):
        raise ValueError("low_percentile must be in [0, 100]")
    if not (0 <= threshold_cfg.high_percentile <= 100):
        raise ValueError("high_percentile must be in [0, 100]")
    if threshold_cfg.low_percentile >= threshold_cfg.high_percentile:
        raise ValueError("low_percentile must be smaller than high_percentile")

    normalized_windows = {
        str(name): int(days)
        for name, days in windows.items()
        if isinstance(name, str) and int(days) > 0
    }
    if not normalized_windows:
        raise ValueError("windows section cannot be empty")

    symbols = _parse_symbols(content.get("symbols", []))
    if not symbols:
        raise ValueError("No valid symbols in config")

    return MonitorConfig(
        delay=delay,
        thresholds=threshold_cfg,
        windows=normalized_windows,
        symbols=symbols,
        max_stale_days=int(scan.get("max_stale_days", 10)),
        skip_if_no_today_data=bool(scan.get("skip_if_no_today_data", True)),
    )
