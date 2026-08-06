"""果仁行业估值数据层。

移植自 monitor_drawdown.py L37-39、L483-572。本模块仅负责取数 + 解析 + 归档:

- ``fetch_guorn_meta_payload``: 拉 guorn ``/stock/query/meta``(``GUORN_COOKIE`` 鉴权,
  ``alerts.run_with_retry`` 自动重试),校验 status==ok。
- ``extract_guorn_latest_date`` / ``extract_guorn_industry_valuation_rows``: 从 payload
  取数据日期(归一化为 ``YYYY-MM-DD``)与行业估值行(pepb.industry)。
- ``archive_guorn_meta_snapshot``: 按 latest_date 归档到
  ``storage.ARCHIVE_DIR/guorn_meta/{date}.json``(content_hash 去重,内容不变跳过)。
- ``fetch_industry_valuation``: 串联 fetch+archive+extract 的便捷入口,失败抛异常,
  由调用方(run.py)决定报警(``alerts.notify_alert``)或静默跳过。

渲染片段(``_render_guorn_industry_valuation_email_section``)归 ``valuation.render.py``;
原 ``build_guorn_failure_webhook_payload`` 退役,改由 ``common.alerts.notify_alert`` 报警。

归档格式保持与旧仓库一致:写原始 payload(顶层 data/status),不包 content_hash 信封,
以兼容既有 9 份历史快照;去重改用 ``storage.content_hash`` 对对象计算,与序列化缩进无关。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ..common import alerts, storage
from .metrics import parse_optional_date

GUORN_META_URL = "https://guorn.com/stock/query/meta"
DEFAULT_GUORN_TIMEOUT = 30


@dataclass(frozen=True)
class GuornSnapshot:
    """果仁行业估值一次成功取数的结果。"""

    latest_date: str
    industry_rows: List[Dict[str, Any]]


def build_guorn_meta_headers(cookie: str) -> Dict[str, str]:
    token = str(cookie or "").strip()
    if not token:
        raise RuntimeError("缺少环境变量 GUORN_COOKIE")

    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://guorn.com/stock/query/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": token,
    }


def fetch_guorn_meta_payload(cookie: str, request_ts: Optional[int] = None) -> Dict[str, Any]:
    ts = request_ts if request_ts is not None else int(time.time() * 1000)
    response = alerts.run_with_retry(
        "guorn_meta",
        lambda: requests.get(
            GUORN_META_URL,
            params={"_": ts},
            headers=build_guorn_meta_headers(cookie),
            timeout=DEFAULT_GUORN_TIMEOUT,
        ),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Guorn meta payload must be an object")
    if str(payload.get("status") or "").strip().lower() != "ok":
        raise ValueError(f"Guorn meta status not ok: {payload.get('status')}")
    return payload


def extract_guorn_latest_date(payload: Dict[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Guorn meta payload missing data")
    latest_date = parse_optional_date(data.get("latest_date"))
    if latest_date is None:
        raise ValueError("Guorn latest_date missing")
    return latest_date.strftime("%Y-%m-%d")


def extract_guorn_industry_valuation_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Guorn meta payload missing data")
    pepb = data.get("pepb")
    if not isinstance(pepb, dict):
        raise ValueError("Guorn meta payload missing pepb")
    rows = pepb.get("industry")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Guorn industry valuation rows missing")
    return [row for row in rows if isinstance(row, dict)]


def archive_guorn_meta_snapshot(
    payload: Dict[str, Any],
    archive_root: Path = storage.ARCHIVE_DIR,
) -> Dict[str, Any]:
    snapshot_date = extract_guorn_latest_date(payload)
    output_path = archive_root / "guorn_meta" / f"{snapshot_date}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    new_hash = storage.content_hash(payload)
    existed_before = output_path.exists()
    if existed_before:
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and storage.content_hash(existing) == new_hash:
                print(f"[INFO] Guorn 快照无变化,跳过覆盖: {output_path}")
                return {"snapshot_date": snapshot_date, "path": output_path, "status": "unchanged"}
        except (json.JSONDecodeError, OSError):
            pass  # 损坏文件,落下面覆盖

    normalized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output_path.write_text(normalized, encoding="utf-8")
    status = "updated" if existed_before else "created"
    print(f"[INFO] Guorn 快照已归档: {output_path}")
    return {"snapshot_date": snapshot_date, "path": output_path, "status": status}


def fetch_industry_valuation(
    cookie: str, *, archive_root: Path = storage.ARCHIVE_DIR
) -> GuornSnapshot:
    """便捷入口:fetch -> archive -> extract,返回 ``GuornSnapshot``。

    任何环节失败均抛异常(由调用方决定 ``notify_alert`` 报警或静默跳过)。
    """
    payload = fetch_guorn_meta_payload(cookie)
    archive_guorn_meta_snapshot(payload, archive_root=archive_root)
    snapshot = GuornSnapshot(
        latest_date=extract_guorn_latest_date(payload),
        industry_rows=extract_guorn_industry_valuation_rows(payload),
    )
    print(f"[INFO] Guorn 行业估值已加载: {snapshot.latest_date}, {len(snapshot.industry_rows)} 行")
    return snapshot
