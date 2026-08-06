"""可转债下修触发归档。

逐债抓集思录详情页 + detail_hist,解析下修条款与触发天数,落本地归档供筛选邮件
"下修天计数"列展示。移植自 jisilu_ggx/cb_adjust_archive.py + cb_main.py 的
refresh_cb_adjust_archives:get 请求改走已登录 Session,fetch 包 run_with_retry,
失败告警改 common.alerts.notify_alert,归档根目录落 storage.ARCHIVE_DIR/cb_bonds。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ...common import alerts, storage
from .strategy import CB_HEADERS, now_in_beijing


CB_BOND_DATA_DIR = storage.ARCHIVE_DIR / "cb_bonds"
DETAIL_URL = "https://www.jisilu.cn/data/convert_bond_detail/{bond_id}"
DETAIL_HIST_URL = "https://www.jisilu.cn/data/cbnew/detail_hist/{bond_id}"
DETAIL_FIELD_LABELS = {
    "bond_nm": "转债名称",
    "stock_id": "正股代码",
    "stock_nm": "正股名称",
    "convert_price": "转股价",
    "adjust_trigger_price": "下修触发价",
    "adjust_clause_text": "下修条款",
}
TITLE_RE = re.compile(r"<title>\s*([^<]+?)\s*-\s*\d+\s*-\s*集思录\s*</title>", flags=re.S)
STOCK_ANCHOR_RE = re.compile(
    r'<div class="stock_nm">.*?href="/data/stock/(?P<stock_id>\d{6})">'
    r"<span[^>]*>(?P<stock_nm>[^<]*)",
    flags=re.S,
)
TAG_RE = re.compile(r"<[^>]+>")
ADJUST_TIPS_RE = re.compile(r"下修\s*转股价由([0-9.]+)调整为([0-9.]+)")
ADJUST_CLAUSE_RE = re.compile(
    r"(?:任意)?连续(?P<window>[0-9零一二三四五六七八九十百两]+)个交易日中"
    r".*?(?:至少)?(?:有)?(?P<required>[0-9零一二三四五六七八九十百两]+)个交易日",
    flags=re.S,
)
CHINESE_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _clean_html_text(value: str) -> str:
    text = TAG_RE.sub("", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_labeled_cell(html: str, label: str) -> str:
    pattern = re.compile(
        rf"<td[^>]*class=\"jisilu_title\"[^>]*>\s*{re.escape(label)}\s*</td>\s*"
        r"<td[^>]*>\s*(.*?)\s*</td>",
        flags=re.S,
    )
    match = pattern.search(html or "")
    if not match:
        return ""
    return _clean_html_text(match.group(1))


def _iso_today() -> str:
    return now_in_beijing().date().isoformat()


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_chinese_number(token: str) -> Optional[int]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total = 0
    current = 0
    for char in token:
        if char in CHINESE_DIGITS:
            current = CHINESE_DIGITS[char]
            continue
        if char == "十":
            current = 1 if current == 0 else current
            total += current * 10
            current = 0
            continue
        if char == "百":
            current = 1 if current == 0 else current
            total += current * 100
            current = 0
            continue
        return None
    return total + current if total + current > 0 else None


def _parse_adjust_clause_days(clause_text: str) -> tuple:
    match = ADJUST_CLAUSE_RE.search(clause_text or "")
    if not match:
        return None, None
    return _parse_chinese_number(match.group("window")), _parse_chinese_number(match.group("required"))


# ── 数据获取(已登录 Session)──────────────────────────────────────────────────
def fetch_cb_detail_page(bond_id: str, session: requests.Session) -> str:
    url = DETAIL_URL.format(bond_id=bond_id)

    def _get() -> str:
        resp = session.get(url, headers=CB_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.text

    return alerts.run_with_retry(f"cb_detail_page:{bond_id}", _get)


def fetch_cb_detail_hist(bond_id: str, session: requests.Session) -> Dict[str, Any]:
    url = DETAIL_HIST_URL.format(bond_id=bond_id)
    params = {"___jsl": f"LST___t={int(time.time() * 1000)}"}
    headers = {**CB_HEADERS, "Referer": DETAIL_URL.format(bond_id=bond_id)}

    def _get() -> Dict[str, Any]:
        resp = session.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    payload = alerts.run_with_retry(f"cb_detail_hist:{bond_id}", _get)
    payload["bond_id"] = str(bond_id)
    payload["fetched_at"] = _iso_today()
    return payload


def parse_cb_detail_adjust_info(html: str, bond_id: str) -> Dict[str, Any]:
    detail_fields = {key: _extract_labeled_cell(html, label) for key, label in DETAIL_FIELD_LABELS.items()}
    detail: Dict[str, Any] = {"bond_id": str(bond_id)}
    title_match = TITLE_RE.search(html or "")
    stock_match = STOCK_ANCHOR_RE.search(html or "")
    detail["bond_nm"] = _clean_html_text(title_match.group(1)) if title_match else detail_fields["bond_nm"]
    detail["stock_id"] = stock_match.group("stock_id") if stock_match else detail_fields["stock_id"]
    detail["stock_nm"] = _clean_html_text(stock_match.group("stock_nm")) if stock_match else detail_fields["stock_nm"]
    for key in ("convert_price", "adjust_trigger_price", "adjust_clause_text"):
        detail[key] = detail_fields[key]
    detail["source_url"] = DETAIL_URL.format(bond_id=bond_id)
    detail["updated_at"] = _iso_today()
    return detail


def _find_latest_adjust(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    for row in rows:
        cell = row.get("cell", {})
        tips = cell.get("convert_price_tips") or ""
        match = ADJUST_TIPS_RE.search(str(tips))
        if match:
            return {
                "last_adjust_date": cell.get("last_chg_dt", ""),
                "last_adjust_from_price": match.group(1),
                "last_adjust_to_price": match.group(2),
            }
    return {"last_adjust_date": "", "last_adjust_from_price": "", "last_adjust_to_price": ""}


def derive_cb_adjust_metrics(
    detail_data: Dict[str, Any], hist_payload: Dict[str, Any], as_of_date: Optional[str] = None
) -> Dict[str, Any]:
    rows = hist_payload.get("rows", []) or []
    latest_adjust = _find_latest_adjust(rows)
    trigger_price = _to_float(detail_data.get("adjust_trigger_price"))
    trigger_window_days, trigger_required_days = _parse_adjust_clause_days(
        detail_data.get("adjust_clause_text", "")
    )

    window_rows = []
    for row in rows:
        cell = row.get("cell", {})
        trade_date = cell.get("last_chg_dt", "")
        if latest_adjust["last_adjust_date"] and trade_date < latest_adjust["last_adjust_date"]:
            continue
        window_rows.append(row)

    hit_days = 0
    for row in window_rows:
        sprice = _to_float(row.get("cell", {}).get("sprice"))
        if trigger_price is not None and sprice is not None and sprice < trigger_price:
            hit_days += 1

    window_dates = [
        row.get("cell", {}).get("last_chg_dt", "")
        for row in window_rows
        if row.get("cell", {}).get("last_chg_dt")
    ]
    observed_days = len(window_rows)
    if (
        trigger_price is not None and observed_days
        and trigger_required_days is not None and trigger_window_days is not None
    ):
        display_hit_days = min(hit_days, trigger_required_days)
        display_text = f"{display_hit_days}/{trigger_required_days} | {trigger_window_days}"
    elif trigger_price is not None and observed_days:
        display_text = f"{hit_days}/-- | --"
    else:
        display_text = "--"

    return {
        "bond_id": detail_data.get("bond_id", ""),
        "bond_nm": detail_data.get("bond_nm", ""),
        "trigger_price": detail_data.get("adjust_trigger_price", ""),
        "last_adjust_date": latest_adjust["last_adjust_date"],
        "last_adjust_from_price": latest_adjust["last_adjust_from_price"],
        "last_adjust_to_price": latest_adjust["last_adjust_to_price"],
        "window_start_date": window_dates[-1] if window_dates else "",
        "window_end_date": window_dates[0] if window_dates else "",
        "trigger_hit_days_30": hit_days,
        "trigger_required_days": trigger_required_days,
        "trigger_window_days": trigger_window_days,
        "trigger_observed_days": observed_days,
        "trigger_total_days_30": observed_days,
        "display_text": display_text,
        "updated_at": as_of_date or _iso_today(),
    }


def _build_snapshot_payload(
    detail_data: Dict[str, Any], derived_data: Dict[str, Any], as_of_date: str
) -> Dict[str, Any]:
    return {
        "date": as_of_date,
        "bond_id": detail_data.get("bond_id", ""),
        "bond_nm": detail_data.get("bond_nm", ""),
        "stock_id": detail_data.get("stock_id", ""),
        "stock_nm": detail_data.get("stock_nm", ""),
        "convert_price": detail_data.get("convert_price", ""),
        "adjust_clause_text": detail_data.get("adjust_clause_text", ""),
        "adjust_trigger_price": detail_data.get("adjust_trigger_price", ""),
        "last_adjust_date": derived_data.get("last_adjust_date", ""),
        "trigger_hit_days_30": derived_data.get("trigger_hit_days_30", 0),
        "trigger_required_days": derived_data.get("trigger_required_days"),
        "trigger_window_days": derived_data.get("trigger_window_days"),
        "trigger_observed_days": derived_data.get("trigger_observed_days", 0),
        "trigger_total_days_30": derived_data.get("trigger_total_days_30", 0),
        "display_text": derived_data.get("display_text", "--"),
        "source_updated_at": derived_data.get("updated_at", as_of_date),
    }


def write_cb_archive_files(
    bond_id: str,
    detail_data: Dict[str, Any],
    hist_payload: Dict[str, Any],
    derived_data: Dict[str, Any],
    as_of_date: str,
    base_dir: Optional[Path] = None,
) -> None:
    root = Path(base_dir) if base_dir else CB_BOND_DATA_DIR
    bond_dir = root / str(bond_id)
    snapshot_dir = bond_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (bond_dir / "detail.latest.json").write_text(
        json.dumps(detail_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (bond_dir / "detail_hist.latest.json").write_text(
        json.dumps(hist_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (bond_dir / "derived.latest.json").write_text(
        json.dumps(derived_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (snapshot_dir / f"{as_of_date}.json").write_text(
        json.dumps(_build_snapshot_payload(detail_data, derived_data, as_of_date), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_cb_adjust_metric(bond_id: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(base_dir) if base_dir else CB_BOND_DATA_DIR
    derived_path = root / str(bond_id) / "derived.latest.json"
    if not derived_path.exists():
        return {}
    return json.loads(derived_path.read_text(encoding="utf-8"))


def get_cb_adjust_days_text(c: Dict[str, Any], archive_map: Optional[Dict[str, Any]] = None) -> str:
    bond_id = str(c.get("bond_id", "")).strip()
    if not bond_id:
        return "--"
    if archive_map and bond_id in archive_map:
        return archive_map[bond_id].get("display_text") or "--"
    cached = load_cb_adjust_metric(bond_id)
    return cached.get("display_text") or "--"


def build_cb_adjust_archive_failed_alert_text(
    failed_list: List[Dict[str, Any]], max_show: int = 10, reason_limit: int = 60
) -> str:
    lines = [f"ℹ️ 可转债日报:{len(failed_list)} 只转债下修数据刷新失败,已回退本地缓存"]
    for item in failed_list[:max_show]:
        reason = str(item.get("reason", "")).replace("\n", " ")
        if len(reason) > reason_limit:
            reason = reason[:reason_limit] + "…"
        lines.append(f"• {item['bond_id']} {item['bond_nm']}:{reason}")
    if len(failed_list) > max_show:
        lines.append(f"• …另有 {len(failed_list) - max_show} 只")
    lines.append("(主邮件流程不中断,失败原因详见 CI 日志)")
    return "\n".join(lines)


def refresh_cb_adjust_archives(
    rows: List[Dict[str, Any]], session: requests.Session, as_of_date: Optional[str] = None
) -> Dict[str, Any]:
    """逐债抓详情页 + detail_hist,算下修天数,写归档。失败回退本地缓存并告警。"""
    archive_map: Dict[str, Any] = {}
    run_date = as_of_date or _iso_today()
    seen = set()
    failed_list: List[Dict[str, Any]] = []
    for row in rows:
        c = row["cell"]
        bond_id = str(c.get("bond_id", "")).strip()
        if not bond_id or bond_id in seen:
            continue
        seen.add(bond_id)
        try:
            html = fetch_cb_detail_page(bond_id, session)
            detail = parse_cb_detail_adjust_info(html, bond_id)
            hist_payload = fetch_cb_detail_hist(bond_id, session)
            derived = derive_cb_adjust_metrics(detail, hist_payload, as_of_date=run_date)
            write_cb_archive_files(bond_id, detail, hist_payload, derived, as_of_date=run_date)
            archive_map[bond_id] = derived
        except Exception as e:  # noqa: BLE001
            print(f"可转债下修归档失败 {bond_id}: {e}")
            reason = str(e)
            cached_metric: Dict[str, Any] = {}
            try:
                cached_metric = load_cb_adjust_metric(bond_id)
            except Exception as cache_error:  # noqa: BLE001
                reason = f"{reason}; cache fallback failed: {cache_error}"
            failed_list.append({
                "bond_id": bond_id,
                "bond_nm": str(c.get("bond_nm", "")).strip() or "--",
                "reason": reason,
            })
            archive_map[bond_id] = cached_metric
    if failed_list:
        alerts.notify_alert("可转债下修数据刷新失败", build_cb_adjust_archive_failed_alert_text(failed_list))
    return archive_map
