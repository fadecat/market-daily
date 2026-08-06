"""转债等权指数历史:抓取集思录 cb_index 页面 + 与本地归档合并。

合并自两仓库的 cb_index_history.py:
- 字段映射采用 jisilu_ggx 的干净名(median_price/avg_ytm/...),
- 并保留 monitor_drawdown 的 price->index_value 别名与 build_runtime_index_series()
  (供三低轮动基准对比)。
- 归档原 market_temperature_history.json(命名陷阱,实为转债等权指数)已重命名为
  data/cb_index_history.json。
- load_history 会对旧记录的原始字段名做一次性归一(mid_price->median_price 等),
  无需单独迁移脚本;首次 refresh 后文件即收敛为干净名。

cb_index 页面无需登录,裸 GET。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from ...common import alerts, storage

_REPO_ROOT = Path(__file__).resolve().parents[3]
ARCHIVE_PATH = _REPO_ROOT / "data" / "cb_index_history.json"
JISILU_CB_INDEX_URL = "https://www.jisilu.cn/data/cbnew/cb_index/"
REQUEST_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 集思录原始字段名 -> 干净字段名(含 price->index_value 供三低基准)
JISILU_FIELD_MAP: Dict[str, str] = {
    "price": "index_value",
    "mid_price": "median_price",
    "avg_ytm_rt": "avg_ytm",
    "avg_price": "avg_price",
    "mid_convert_value": "median_convert_value",
    "avg_dblow": "avg_dblow",
    "avg_premium_rt": "avg_premium",
    "mid_premium_rt": "median_premium",
    "turnover_rt": "turnover_rate",
    "count": "count",
    "temperature": "temperature",
    "idx_price": "idx_price",
    "idx_increase_rt": "idx_increase_rt",
}


def fetch_page(url: str = JISILU_CB_INDEX_URL) -> str:
    resp = alerts.run_with_retry(
        "cb_index.fetch_page",
        lambda: requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT),
    )
    resp.raise_for_status()
    return resp.text


def parse_jisilu(body: str) -> List[Dict[str, str]]:
    m_date = re.search(r"var __date\s*=\s*(\[[^\]]*\]);", body)
    if not m_date:
        raise RuntimeError("未找到 var __date 变量")
    dates = re.findall(r"'([^']*)'", m_date.group(1))

    m_data = re.search(r"var __data\s*=\s*\{([\s\S]*?)\};", body)
    if not m_data:
        raise RuntimeError("未找到 var __data 变量")
    pairs = re.findall(r"'([a-zA-Z_]+)'\s*:\s*\[([^\]]*)\]", m_data.group(1))
    series: Dict[str, List[str]] = {}
    for key, values in pairs:
        series[key] = [v.strip() for v in values.split(",") if v.strip()]

    records: List[Dict[str, str]] = []
    for idx, date in enumerate(dates):
        record: Dict[str, str] = {"date": date}
        for jisilu_key, target_key in JISILU_FIELD_MAP.items():
            values = series.get(jisilu_key)
            if not values or idx >= len(values):
                continue
            record[target_key] = values[idx]
        records.append(record)
    return records


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """把旧记录里的原始字段名归一为干净名(如 mid_price->median_price, price->index_value)。

    已经是干净名的字段不在 JISILU_FIELD_MAP 的 key 集合里,原样保留。
    """
    out: Dict[str, Any] = {}
    for key, value in record.items():
        out[JISILU_FIELD_MAP.get(key, key)] = value
    return out


def load_history(path: Path | str = ARCHIVE_PATH) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return [
        _normalize_record(record)
        for record in raw
        if isinstance(record, dict) and record.get("date")
    ]


def merge_records(
    history: List[Dict[str, Any]], live_records: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """按 date 合并;重叠日以线上为准(精度更高),新日期追加。返回(合并列表, 统计)。"""
    by_date: Dict[str, Dict[str, Any]] = {
        str(record.get("date")): dict(record) for record in history if record.get("date")
    }
    stats = {"history": len(by_date), "updated": 0, "added": 0}
    for record in live_records:
        row_date = str(record.get("date") or "").strip()
        if not row_date:
            raise ValueError("live record missing date")
        if row_date in by_date:
            by_date[row_date].update({k: v for k, v in record.items() if k != "date"})
            stats["updated"] += 1
        else:
            by_date[row_date] = dict(record)
            stats["added"] += 1
    merged = [by_date[d] for d in sorted(by_date)]
    return merged, stats


def build_merged_history(
    path: Path | str = ARCHIVE_PATH,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    live_records = parse_jisilu(fetch_page())
    history = load_history(path)
    return merge_records(history, live_records)


def build_runtime_merged_history(path: Path | str = ARCHIVE_PATH) -> List[Dict[str, Any]]:
    merged, _ = build_merged_history(path)
    return merged


def build_runtime_index_series(
    records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """提取 [{date, value}] 供三低轮动基准对比。records 为 None 时抓取+合并。"""
    if records is None:
        records = build_runtime_merged_history()
    series: List[Dict[str, Any]] = []
    for record in records:
        row_date = str(record.get("date") or "").strip()
        raw_value = record.get("index_value")
        if not row_date or raw_value in (None, ""):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        series.append({"date": row_date, "value": value})
    if not series:
        raise ValueError("empty cb index runtime series")
    return series


def save_history(
    records: List[Dict[str, Any]], path: Path | str = ARCHIVE_PATH
) -> bool:
    """写归档(plain list)。内容不变(content_hash)则不写,返回是否实际写入。"""
    path = Path(path)
    new_hash = storage.content_hash(records)
    if path.exists():
        try:
            existing = load_history(path)
            if storage.content_hash(existing) == new_hash:
                return False
        except Exception:  # noqa: BLE001
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
