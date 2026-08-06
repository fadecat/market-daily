"""可转债低价债筛选 数据层。

日频拉集思录全市场转债(cb_list_new,价格≤max_price、评级 AAA~A-、已上市),
排除正股含 ST / 已公告强赎(O)/ 到期赎回(R)/ 短久期负收益债,
按双低值 + 剩余规模 两项排名得分求和降序排序。移植自 jisilu_ggx/cb_main.py 的数据逻辑:
去掉 ``from main import`` 与 webhook,集思录改 common.jisilu 账密登录 Session。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

from ...common import alerts, env, jisilu as jl


BEIJING_TZ = timezone(timedelta(hours=8))
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "cb_screening.yaml"

CB_URL = "https://www.jisilu.cn/data/cbnew/cb_list_new/"
CB_INDEX_QUOTE_URL = "https://www.jisilu.cn/webapi/cb/index_quote/"
CB_PAGE_SIZE = 1000
CB_ALLOWED_RATINGS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-"]
CB_ALLOWED_MARKETS = ["shmb", "shkc", "szmb", "szcy"]
SHORT_TERM_NEGATIVE_YTM_REASON = "剩余年限<1年且到期税前收益率<0"

CB_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://www.jisilu.cn/data/cbnew/",
    "Origin": "https://www.jisilu.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def now_in_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("max_price", 120)
    data.setdefault("max_show", 50)
    data.setdefault(
        "factors",
        [{"field": "dblow", "label": "双低"}, {"field": "curr_iss_amt", "label": "规模"}],
    )
    return data


def _build_form_data(max_price: int) -> Dict[str, Any]:
    return {
        "fprice": "", "tprice": max_price, "curr_iss_amt": "", "convert_amt_ratio": "",
        "premium_rt": "", "fyear_left": "", "tyear_left": "",
        "rating_cd[]": CB_ALLOWED_RATINGS,
        "is_search": "Y",
        "market_cd[]": CB_ALLOWED_MARKETS,
        "show_blocked": "N", "min_price_only": "N", "btype": "",
        "listed": "Y", "qflag": "N", "sw_cd": "", "bond_ids": "",
        "rp": CB_PAGE_SIZE,
    }


# ── 数据获取 ──────────────────────────────────────────────────────────────────
def fetch_cb_data(session: requests.Session, config: Dict[str, Any]) -> Dict[str, Any]:
    params = {"___jsl": f"LST___t={int(time.time() * 1000)}"}
    payload = _build_form_data(int(config["max_price"]))

    def _post() -> Dict[str, Any]:
        resp = session.post(CB_URL, params=params, data=payload, headers=CB_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()

    data = alerts.run_with_retry("cb_list_new", _post)
    rows = data.get("rows", [])
    total = data.get("total")
    if isinstance(total, int) and len(rows) < total:
        raise RuntimeError(f"可转债接口返回疑似未取全:rows={len(rows)}, total={total},排序可能失真")
    return data


def fetch_cb_index_quote(session: requests.Session) -> Dict[str, Any]:
    def _get() -> Dict[str, Any]:
        resp = session.get(CB_INDEX_QUOTE_URL, headers=CB_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()

    payload = alerts.run_with_retry("cb_index_quote", _get)
    if payload.get("code") != 200:
        raise RuntimeError(f"可转债概览接口返回异常: {payload}")
    return payload.get("data", {}) or {}


# ── 过滤 + 排序 ────────────────────────────────────────────────────────────────
def to_float(value: Any, default: float = float("inf")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_short_term_negative_ytm_cb(c: Dict[str, Any]) -> bool:
    year_left = to_float(c.get("year_left"), default=float("inf"))
    ytm_rt = to_float(c.get("ytm_rt"), default=float("inf"))
    if year_left == float("inf") or ytm_rt == float("inf"):
        return False
    return year_left < 1 and ytm_rt < 0


def get_cb_filter_reasons(c: Dict[str, Any]) -> List[str]:
    """返回命中的过滤原因;为空表示通过。"""
    reasons: List[str] = []
    icons = c.get("icons", {}) or {}
    if "O" in icons:
        reasons.append("已公告强赎(O)")
    if "R" in icons:
        reasons.append("到期赎回(R)")
    stock_nm = (c.get("stock_nm") or "").upper()
    if "ST" in stock_nm:
        reasons.append("正股含ST")
    if is_short_term_negative_ytm_cb(c):
        reasons.append(SHORT_TERM_NEGATIVE_YTM_REASON)
    return reasons


def is_force_redeem_triggered(c: Dict[str, Any]) -> bool:
    """判断是否触发强赎但未公告。"""
    icons = c.get("icons", {}) or {}
    if "O" in icons:
        return False
    sprice = float(c.get("sprice", 0) or 0)
    force_redeem_price = float(c.get("force_redeem_price", 999) or 999)
    return sprice >= force_redeem_price and force_redeem_price > 0


def get_numeric_value(row: Dict[str, Any], field_name: str) -> Optional[float]:
    value = row["cell"].get(field_name)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def assign_factor_scores(rows: List[Dict[str, Any]], field_name: str) -> int:
    ranked = []
    for index, row in enumerate(rows):
        value = get_numeric_value(row, field_name)
        if value is not None:
            ranked.append((index, row, value))
    ranked.sort(key=lambda item: (item[2], item[0]))
    total = len(ranked)
    rank_key = f"{field_name}_rank"
    score_key = f"{field_name}_score"
    for rank, (_, row, _) in enumerate(ranked, 1):
        row[rank_key] = rank
        row[score_key] = total - rank + 1
    for row in rows:
        row.setdefault(rank_key, None)
        row.setdefault(score_key, 0)
    return total


def sort_cb_rows(rows: List[Dict[str, Any]], factors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按各因子排名得分求和后降序排序;同分依次按双低/溢价/价格/规模升序。"""
    ranked_rows = list(rows)
    fields = [f["field"] for f in factors]
    for field_name in fields:
        assign_factor_scores(ranked_rows, field_name)
    for row in ranked_rows:
        row["total_score"] = sum(row.get(f"{f}_score", 0) for f in fields)
    return sorted(
        ranked_rows,
        key=lambda row: (
            -row["total_score"],
            to_float(row["cell"].get("dblow")),
            to_float(row["cell"].get("premium_rt")),
            to_float(row["cell"].get("price")),
            to_float(row["cell"].get("curr_iss_amt")),
        ),
    )


def filter_cb(rows: List[Dict[str, Any]], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """过滤可转债:排除正股含 ST、已公告强赎(O)、到期赎回(R)和短久期负收益债,再排序。"""
    result = [row for row in rows if not get_cb_filter_reasons(row["cell"])]
    return sort_cb_rows(result, config.get("factors", []))


def get_cb_excluded_details(rows: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], List[str]]]:
    return [(row["cell"], reasons) for row in rows if (reasons := get_cb_filter_reasons(row["cell"]))]


def log_cb_exclusions(rows: List[Dict[str, Any]]) -> None:
    excluded = get_cb_excluded_details(rows)
    if not excluded:
        return
    print(f"可转债排除数量: {len(excluded)}")
    for cell, reasons in excluded:
        print(f"排除 {cell.get('bond_nm', '--')}({cell.get('bond_id', '--')}) 原因: {', '.join(reasons)}")


# ── 企业性质(TODO: 2d 移植高股息时抽 common/whitelist.py 接回)─────────────────
def normalize_stock_code(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)


def load_enterprise_nature_map() -> Dict[str, str]:
    """加载 {正股代码: 企业性质} 映射。

    当前暂返回空映射(企业性质列留空)。白名单 Excel 加载逻辑与高股息板块共用,
    将在移植高股息时抽到 common/whitelist.py 后接回。
    """
    return {}


def get_enterprise_nature(c: Dict[str, Any], nature_map: Optional[Dict[str, str]] = None) -> str:
    if nature_map is None:
        nature_map = load_enterprise_nature_map()
    stock_code = normalize_stock_code(c.get("stock_id"))
    if not stock_code:
        return ""
    return nature_map.get(stock_code, "")


# ── 共享数据通路 ──────────────────────────────────────────────────────────────
def login() -> requests.Session:
    """集思录账密登录,返回已带 cookie 的 Session。"""
    username = env.require("JISILU_USERNAME")
    password = env.require("JISILU_PASSWORD")
    cookie = jl.login_jisilu(username, password)
    if not cookie:
        raise RuntimeError("集思录登录失败")
    session = requests.Session()
    jl.apply_cookie_string(session, cookie)
    return session


def fetch_and_filter(
    session: requests.Session, config: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """拉 cb_list + 市场概览 -> 过滤排序。返回 (filtered_rows, index_quote)。"""
    data = fetch_cb_data(session, config)
    index_quote = fetch_cb_index_quote(session)
    log_cb_exclusions(data.get("rows", []))
    filtered = filter_cb(data.get("rows", []), config)
    return filtered, index_quote
