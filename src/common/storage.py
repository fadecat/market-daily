"""数据备份与状态持久化(统一层)。

合并自旧仓库两套机制:
- ``financial_snapshot_cache`` 的 content_hash 去重(内容不变不写)
- ``refresh_data_archive`` 的 archive 合并(按 key 合并历史记录)

三类存储:
- ``data/state/*.json``      运行状态(持仓/净值/去重),板块用 load_state/save_state
- ``data/archive/<ds>/*.json`` 历史归档(估值/国债/汇率),用 merge_archive
- 任意路径 snapshot            巨潮财报等带 content_hash 的快照,用 save_snapshot
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# src/common/storage.py -> parents[2] = 仓库根目录
_REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = _REPO_ROOT / "data" / "state"
ARCHIVE_DIR = _REPO_ROOT / "data" / "archive"


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default) + "\n"


def _write_text(path: Path, text: str) -> None:
    """统一 LF 写盘:Windows 文本模式会把 \\n 翻译成 \\r\\n,污染归档 diff。"""
    path.write_text(text, encoding="utf-8", newline="\n")


def content_hash(obj: Any) -> str:
    """对对象算稳定 hash(排序键 + ISO 日期),用于内容去重。"""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=_json_default).encode("utf-8")
    ).hexdigest()


# ---------- 运行状态 data/state/*.json ----------

def state_path(name: str) -> Path:
    return STATE_DIR / f"{name}.json"


def load_state(name: str, default: Any = None) -> Any:
    path = state_path(name)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(name: str, obj: Any) -> None:
    path = state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, _dumps(obj))


# ---------- 带 content_hash 的快照 ----------

def save_snapshot(path: str | Path, obj: Any, *, meta: Optional[Dict[str, Any]] = None) -> bool:
    """写带 content_hash 的快照。内容不变(同 hash)则不写,返回是否实际写入。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    h = content_hash(obj)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("content_hash") == h:
                return False
        except Exception:  # noqa: BLE001
            pass
    payload: Dict[str, Any] = {"content_hash": h, "fetched_at": datetime.now().isoformat()}
    if meta:
        payload.update(meta)
    payload["data"] = obj
    _write_text(path, _dumps(payload))
    return True


def load_snapshot(path: str | Path) -> Optional[dict]:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- 历史归档 data/archive/<dataset>/<code>.json ----------

def _record_key(record: Dict, key: str) -> str:
    if key not in record:
        return ""
    value = record[key]
    if value is None:
        return ""
    # datetime/date/pd.Timestamp 统一走 isoformat,与 _json_default 落盘口径一致:
    # 否则内存里的 Timestamp(str 得 "2026-08-05 00:00:00" 空格)与落盘回读的字符串
    # ("2026-08-05T00:00:00" T 分隔)key 不一致,会让 merge_archive 同日翻倍。
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def merge_records_by_key(existing: List[Dict], incoming: List[Dict], key: str) -> List[Dict]:
    """按 key 合并历史记录,重叠日以 incoming 覆盖,结果按 key 排序。"""
    merged: Dict[str, Dict] = {}
    for record in existing:
        rk = _record_key(record, key)
        if rk:
            merged[rk] = record
    for record in incoming:
        rk = _record_key(record, key)
        if rk:
            merged[rk] = record
    return [merged[rk] for rk in sorted(merged)]


def load_existing_records(output_path: str | Path) -> List[Dict]:
    """读取归档文件的 records 列表(格式: {source, ..., updated_at, records: [...]})."""
    output_path = Path(output_path)
    if not output_path.exists():
        return []
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "records" not in payload:
        raise ValueError(f"无效归档文件 {output_path}:缺少 records")
    records = payload["records"]
    if not isinstance(records, list):
        raise ValueError(f"无效归档文件 {output_path}:records 必须是列表")
    return [r for r in records if isinstance(r, dict)]


def write_archive_file(
    output_path: str | Path,
    source: str,
    identity: Dict[str, str],
    records: List[Dict],
    updated_at: str,
) -> bool:
    """写归档文件。内容相同则不写,返回是否实际写入。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": source, **(identity or {}), "updated_at": updated_at, "records": records}
    serialized = _dumps(payload)
    if output_path.exists() and output_path.read_text(encoding="utf-8") == serialized:
        return False
    _write_text(output_path, serialized)
    return True


def merge_archive(
    dataset: str,
    identity: Dict[str, str],
    incoming: List[Dict],
    *,
    merge_key: str,
    source: str,
    updated_at: str,
    filename: str = "",
) -> Optional[Path]:
    """合并 incoming 到 ``data/archive/<dataset>/<filename>.json``,返回变更的路径(无变更 None)。

    filename 默认用 identity 的第一个值 + .json;无 identity 时用 ``_default.json``。
    """
    name = filename or (f"{next(iter(identity.values()))}.json" if identity else "_default.json")
    output_path = ARCHIVE_DIR / dataset / name
    existing = load_existing_records(output_path)
    merged = merge_records_by_key(existing, incoming, key=merge_key)
    if merged == existing:
        return None
    changed = write_archive_file(output_path, source=source, identity=identity, records=merged, updated_at=updated_at)
    return output_path if changed else None
