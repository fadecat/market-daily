"""巨潮财报快照缓存:content_hash 去重 + manifest + latest.json + 日期文件。

移植自 jisilu_ggx/financial_snapshot_cache.py:保留 bundle 级重试(巨潮偶发返回不完整
数据,需整体重抓)、content_hash 去重、manifest 索引、latest/日期双写结构。改动:
``from cninfo_finance_probe import`` 改相对导入 ``from .cninfo import``;``os.environ.get``
改 ``env.get``;``root_dir`` 默认指向新仓库根(落 ``data/cninfo/``)。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ...common import env
from .cninfo import (
    compute_ttm_from_cumulative_values,
    fetch_financial_bundle,
    get_statement_row,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIRNAME = "data"
CNINFO_DIRNAME = "cninfo"
DEFAULT_TIMEZONE = "Asia/Shanghai"

# 实时抓取(缓存未命中时)的最大尝试次数与线性退避基数。
# CI 运行在美区 runner,巨潮资讯对境外/高频请求偶发返回不完整数据,
# 这里对「抓取 bundle + 派生指标」整体重试,每次都会重新校验归母净利润行。
CNINFO_FETCH_MAX_ATTEMPTS = max(1, int(env.get("CNINFO_FETCH_MAX_RETRIES", "3") or "3"))
# 默认 10s(线性 10/20s):巨潮对高频 IP 软限流(200 + 空 records),2s 级退避必撞墙
CNINFO_FETCH_BACKOFF_SECONDS = max(0.0, float(env.get("CNINFO_FETCH_BACKOFF_SECONDS", "10") or "10"))


def _normalize_stock_code(stock_code: str) -> str:
    digits = "".join(ch for ch in str(stock_code or "").strip() if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)


def stable_json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def compute_bundle_hash(bundle: dict[str, Any]) -> str:
    digest = hashlib.sha256(stable_json_dumps(bundle).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _fetched_at_to_filename(fetched_at: str) -> str:
    dt = datetime.fromisoformat(fetched_at)
    return dt.strftime("%Y-%m-%dT%H%M%S%z")


def _default_fetched_at(tz_name: str = DEFAULT_TIMEZONE) -> str:
    return datetime.now(ZoneInfo(tz_name)).isoformat(timespec="seconds")


def derive_metrics_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    income_statement = bundle["income_statement"]
    row = get_statement_row(income_statement, "归属母公司净利润")
    if row is None:
        raise RuntimeError(f"Income statement row not found: 归属母公司净利润 ({bundle['company']['stock_code']})")
    ttm = compute_ttm_from_cumulative_values(row["values"])
    derived = {
        "ttm_parent_net_profit_wan": round(ttm["ttm_value"], 2),
        "ttm_parent_net_profit_yi": round(ttm["ttm_value"] / 10000, 2),
        "ttm_parent_net_profit_latest_period": ttm["latest_period"],
        "ttm_parent_net_profit_basis": ttm["basis"],
        "ttm_parent_net_profit_components": ttm["components"],
    }
    latest_main_report = (((bundle.get("main_indicators") or {}).get("latest_report")) or {})
    latest_by_label = latest_main_report.get("by_label") or {}
    book_value_per_share = ((latest_by_label.get("每股净资产") or {}).get("value"))
    roe_value = ((latest_by_label.get("ROE") or {}).get("value"))
    if book_value_per_share is not None:
        derived["latest_book_value_per_share"] = book_value_per_share
    if roe_value is not None:
        derived["latest_roe"] = roe_value
    if latest_main_report.get("report_date"):
        derived["latest_main_indicators_report_date"] = latest_main_report["report_date"]
    return derived


def build_financial_snapshot_payload(bundle: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    company = bundle["company"]
    report_date = bundle["head_strip"]["report_date"]
    derived = derive_metrics_from_bundle(bundle)
    return {
        "stock_code": company["stock_code"],
        "stock_name": company["sec_name"],
        "report_date": report_date,
        "fetched_at": fetched_at,
        "content_hash": compute_bundle_hash(bundle),
        "bundle": bundle,
        "derived": derived,
    }


def _load_manifest(base_dir: Path) -> dict[str, Any]:
    manifest_path = base_dir / "manifest.json"
    return load_json(manifest_path, default={"stocks": {}})


def _save_manifest(base_dir: Path, manifest: dict[str, Any]) -> None:
    write_json(base_dir / "manifest.json", manifest)


def archive_financial_snapshot(
    root_dir: Path | str, snapshot_payload: dict[str, Any]
) -> dict[str, Any]:
    root = Path(root_dir)
    base_dir = root / DATA_DIRNAME / CNINFO_DIRNAME
    stock_code = _normalize_stock_code(snapshot_payload["stock_code"])
    stock_dir = base_dir / stock_code
    latest_path = stock_dir / "latest.json"
    manifest = _load_manifest(base_dir)
    existing_latest = load_json(latest_path)
    fetched_at = snapshot_payload["fetched_at"]

    if existing_latest and existing_latest.get("content_hash") == snapshot_payload["content_hash"]:
        manifest["stocks"][stock_code] = {
            "stock_name": snapshot_payload["stock_name"],
            "report_date": snapshot_payload["report_date"],
            "latest_content_hash": snapshot_payload["content_hash"],
            "latest_path": str(latest_path.relative_to(root)),
            "last_checked_at": fetched_at,
            "last_changed_at": existing_latest.get("fetched_at", fetched_at),
        }
        _save_manifest(base_dir, manifest)
        return {
            "status": "unchanged",
            "latest_path": str(latest_path),
        }

    report_date = snapshot_payload["report_date"] or fetched_at
    report_key = str(report_date)[:10]
    dated_path = stock_dir / f"{report_key}.json"
    status = "created"
    if dated_path.exists():
        dated_path = stock_dir / f"{_fetched_at_to_filename(fetched_at)}.json"
        status = "updated"

    write_json(dated_path, snapshot_payload)
    write_json(latest_path, snapshot_payload)
    manifest["stocks"][stock_code] = {
        "stock_name": snapshot_payload["stock_name"],
        "report_date": snapshot_payload["report_date"],
        "latest_content_hash": snapshot_payload["content_hash"],
        "latest_path": str(latest_path.relative_to(root)),
        "last_checked_at": fetched_at,
        "last_changed_at": fetched_at,
    }
    _save_manifest(base_dir, manifest)
    return {
        "status": status,
        "dated_path": str(dated_path),
        "latest_path": str(latest_path),
    }


def load_cached_financial_snapshot(
    stock_code: str, root_dir: Path | str = _REPO_ROOT
) -> dict[str, Any] | None:
    normalized_code = _normalize_stock_code(stock_code)
    if not normalized_code:
        return None

    latest_path = Path(root_dir) / DATA_DIRNAME / CNINFO_DIRNAME / normalized_code / "latest.json"
    snapshot = load_json(latest_path)
    if not snapshot:
        return None

    derived = snapshot.get("derived") or {}
    required_fields = (
        "ttm_parent_net_profit_wan",
        "ttm_parent_net_profit_yi",
        "ttm_parent_net_profit_latest_period",
        "ttm_parent_net_profit_basis",
    )
    if any(field not in derived for field in required_fields):
        return None
    return snapshot


def get_or_fetch_financial_snapshot(
    stock_code: str,
    root_dir: Path | str = _REPO_ROOT,
    bundle_fetcher=fetch_financial_bundle,
    fetched_at: str | None = None,
    max_attempts: int = CNINFO_FETCH_MAX_ATTEMPTS,
    backoff_seconds: float = CNINFO_FETCH_BACKOFF_SECONDS,
    force_refresh: bool = False,
) -> dict[str, Any]:
    normalized_code = _normalize_stock_code(stock_code)
    if not normalized_code:
        raise ValueError(f"无效股票代码: {stock_code}")

    if not force_refresh:
        cached = load_cached_financial_snapshot(normalized_code, root_dir=root_dir)
        if cached is not None:
            return cached

    fetched_at_value = fetched_at or _default_fetched_at()
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            bundle = bundle_fetcher(normalized_code)
            snapshot_payload = build_financial_snapshot_payload(bundle, fetched_at_value)
            archive_result = archive_financial_snapshot(root_dir, snapshot_payload)
            snapshot_payload["archive_status"] = archive_result["status"]
            return snapshot_payload
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt >= max_attempts:
                break
            sleep_for = backoff_seconds * attempt
            print(
                f"[财报抓取重试] {normalized_code} 第 {attempt}/{max_attempts} 次失败: {e}"
                f",{sleep_for:g}s 后重试"
            )
            time.sleep(sleep_for)
    assert last_error is not None
    raise last_error


def fetch_cached_or_live_ttm_parent_net_profit(
    stock_code: str,
    root_dir: Path | str = _REPO_ROOT,
    bundle_fetcher=fetch_financial_bundle,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    snapshot = get_or_fetch_financial_snapshot(
        stock_code,
        root_dir=root_dir,
        bundle_fetcher=bundle_fetcher,
        fetched_at=fetched_at,
    )
    derived = snapshot["derived"]
    return {
        "stock_code": _normalize_stock_code(stock_code),
        "row_name": "归属母公司净利润",
        "unit": "万元",
        "ttm_value_wan": derived["ttm_parent_net_profit_wan"],
        "ttm_value_yi": derived["ttm_parent_net_profit_yi"],
        "latest_period": derived["ttm_parent_net_profit_latest_period"],
        "basis": derived["ttm_parent_net_profit_basis"],
        "components": derived.get("ttm_parent_net_profit_components"),
        "report_date": snapshot.get("report_date"),
    }
