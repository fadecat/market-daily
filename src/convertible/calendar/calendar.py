"""集思录日历下修提醒 数据层。

按规则(qtype + 时间窗)拉集思录日历(公开接口,无需登录),归一化、去重,
按 title_keywords 过滤命中事件。另含「同意注册转债」数据源:复用
valuation.dividend.cb_reference.fetch_pending_cb_rows 拉集思录 pre_list(待发转债,
需登录),过滤 progress_nm 含"同意注册"的行,归一化为同构事件。移植自
monitor_jisilu_calendar.py 的数据逻辑部分:去掉 webhook 推送与 os 依赖,
fetch 包 common.alerts.run_with_retry。
"""
from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
import yaml

from ...common import alerts
from ...valuation.dividend import cb_reference
from .. import industry


BEIJING_TZ = timezone(timedelta(hours=8))
CALENDAR_URL = "https://www.jisilu.cn/data/calendar/get_calendar_data/"
CALENDAR_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.jisilu.cn/data/calendar/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def now_in_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def load_calendar_rules(config_path: str) -> List[Dict]:
    with open(config_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    rules = data.get("calendar_monitors", [])
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise ValueError("config 中 calendar_monitors 必须是列表")
    return rules


def first_day_of_month(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=BEIJING_TZ)


def shift_month(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return datetime(year, month, 1, tzinfo=BEIJING_TZ)


def build_calendar_time_window(
    window: str = "next_month",
    lookahead_days: int = 45,
    current_time: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    now = current_time or now_in_beijing()
    current_month_start = first_day_of_month(now)

    if window == "next_month":
        start_dt = shift_month(current_month_start, 1)
        end_dt = shift_month(current_month_start, 2)
        return start_dt, end_dt

    if window == "current_to_lookahead":
        start_dt = datetime.combine(now.date(), dt_time.min, tzinfo=BEIJING_TZ)
        end_dt = start_dt + timedelta(days=max(1, int(lookahead_days)))
        return start_dt, end_dt

    raise ValueError(f"不支持的 calendar window: {window}")


def build_calendar_request_params(
    qtype: str,
    lookahead_days: int,
    window: str = "next_month",
    current_time: Optional[datetime] = None,
) -> Dict[str, str]:
    now = current_time or now_in_beijing()
    start_dt, end_dt = build_calendar_time_window(
        window=window, lookahead_days=lookahead_days, current_time=now,
    )
    return {
        "qtype": qtype,
        "start": str(int(start_dt.timestamp())),
        "end": str(int(end_dt.timestamp())),
        "_": str(int(now.timestamp() * 1000)),
    }


def extract_event_records(payload: object) -> List[Dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("rows", "data", "result", "calendar", "events"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            records = extract_event_records(nested)
            if records:
                return records

    for value in payload.values():
        if isinstance(value, dict):
            records = extract_event_records(value)
            if records:
                return records

    return []


def pick_first_text(source: Dict, keys: Sequence[str]) -> str:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def parse_event_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.astimezone(BEIJING_TZ) if value.tzinfo else value.replace(tzinfo=BEIJING_TZ)

    text = str(value).strip()
    if not text:
        return None

    try:
        numeric = float(text)
    except ValueError:
        numeric = None

    if numeric is not None:
        timestamp = numeric / 1000 if numeric > 1_000_000_000_000 else numeric
        return datetime.fromtimestamp(timestamp, tz=BEIJING_TZ)

    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
    return None


def normalize_calendar_event(record: Dict) -> Optional[Dict]:
    source = record.get("cell") if isinstance(record.get("cell"), dict) else record
    if not isinstance(source, dict):
        return None

    title = pick_first_text(source, ("title", "event_title", "name", "summary"))
    if not title:
        return None

    event_time = None
    for key in ("start", "date", "day", "event_date", "meeting_date", "mtg_dt"):
        event_time = parse_event_datetime(source.get(key))
        if event_time is not None:
            break

    return {
        "id": pick_first_text(source, ("id",)),
        "code": pick_first_text(source, ("code",)),
        "title": title,
        "event_time": event_time,
        "description": pick_first_text(source, ("description",)),
        "url": pick_first_text(source, ("url",)),
        "raw": record,
    }


def dedupe_events(events: Iterable[Dict]) -> List[Dict]:
    seen = set()
    deduped: List[Dict] = []
    for event in events:
        event_time = event.get("event_time")
        event_time_text = event_time.isoformat() if isinstance(event_time, datetime) else ""
        key = (event.get("title", ""), event_time_text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def filter_events_by_keywords(events: Iterable[Dict], keywords: Sequence[str]) -> List[Dict]:
    normalized_keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    if not normalized_keywords:
        return []

    matched = []
    for event in events:
        title = str(event.get("title", "")).strip()
        if title and any(keyword in title for keyword in normalized_keywords):
            matched.append(event)
    return dedupe_events(matched)


def fetch_calendar_events(
    qtype: str,
    lookahead_days: int,
    window: str = "next_month",
    current_time: Optional[datetime] = None,
    session: Optional[requests.Session] = None,
) -> List[Dict]:
    params = build_calendar_request_params(
        qtype, window=window, lookahead_days=lookahead_days, current_time=current_time,
    )
    request_session = session or requests.Session()

    def _do_get() -> object:
        response = request_session.get(
            CALENDAR_URL, headers=CALENDAR_HEADERS, params=params, timeout=15,
        )
        response.raise_for_status()
        return response.json()

    try:
        payload = alerts.run_with_retry("calendar_events", _do_get)
    finally:
        if session is None:
            request_session.close()

    events: List[Dict] = []
    for record in extract_event_records(payload):
        normalized = normalize_calendar_event(record)
        if normalized is not None:
            events.append(normalized)
    return dedupe_events(events)


def format_event_time(event_time: Optional[datetime]) -> str:
    if event_time is None:
        return "日期未知"
    return event_time.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")


# —— 同意注册转债(集思录 pre_list 待发转债,需登录) ——

REGISTERED_PROGRESS_KEYWORD = "同意注册"


def load_registered_monitor(config_path: str) -> Dict:
    """读取 yaml 中 registered_cb_monitor 配置块;缺省返回空 dict(不启用)。"""
    with open(config_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    monitor = data.get("registered_cb_monitor") or {}
    return monitor if isinstance(monitor, dict) else {}


def fetch_registered_cb_events(cookie: str, session: Optional[requests.Session] = None) -> List[Dict]:
    """拉 pre_list,过滤 progress_nm 含「同意注册」的行,归一化为日历事件结构。

    同意注册阶段尚无 bond_nm/bond_id,故 title 用「正股名 同意注册」,code 用正股代码。
    附加工业(查待发行业缓存,缺失时增量 backfill 详情页)与正股价格(pre_list.price)。
    """
    rows = cb_reference.fetch_pending_cb_rows(cookie, session=session)
    # 增量补行业缓存(首次全抓,之后只抓新出现的 stock_id)
    stock_ids = [str(r.get("cell", {}).get("stock_id") or "").strip() for r in rows]
    stock_ids = [sid for sid in stock_ids if sid]
    try:
        industry.backfill_pending_industries(cookie, stock_ids, session=session)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 待发转债行业增量补抓失败,按缓存兜底: {exc}")

    events: List[Dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cell = row.get("cell") if isinstance(row.get("cell"), dict) else row
        progress = cb_reference.normalize_progress_text(cell.get("progress_nm"))
        if REGISTERED_PROGRESS_KEYWORD not in progress:
            continue
        stock_nm = str(cell.get("stock_nm") or "").strip()
        stock_id = str(cell.get("stock_id") or "").strip()
        if not stock_nm:
            continue
        pending_ind = industry.pending_industry_of(stock_id)
        events.append(
            {
                "id": stock_id,
                "code": stock_id,
                "title": f"{stock_nm} {REGISTERED_PROGRESS_KEYWORD}",
                "event_time": parse_event_datetime(cell.get("progress_dt")),
                "description": (
                    cb_reference.normalize_progress_text(cell.get("progress_full")) or progress
                ),
                "industry": (pending_ind or {}).get("l1_name") or "未分类",
                "stock_price": str(cell.get("price") or "").strip(),
                "url": "",
                "raw": row,
            }
        )
    return dedupe_events(events)
