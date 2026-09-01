"""转债正股申万行业查表模块。

数据源:config/sw_industry_2021.json(申万 2021 版,抓自东财掘金公开文档,
31 一级 + 134 二级 + 346 三级,含 l1 / by_code / l3_to_l1 三个索引)。

用法:
    l1_name_of("640107")          # -> "机械设备"(直接命中三级)
    l1_name_of("610101")          # -> "建筑材料"(三级缺行,回溯二级水泥 610100)
    l1_name_of("999999")          # -> "999999"(完全未知,返回原始代码兜底)

口径说明:集思录 cb_list_new 的 sw_cd 与个股详情页 industry-{sw_cd} 均为
申万 2021 三级代码,与本表一致。

待发转债行业:pre_list 不带 sw_cd,改抓正股详情页(https://www.jisilu.cn/data/stock/{code})
静态 HTML 中 ``行业 <a href="/data/stock/dividend_rate/industry-{sw_cd}">名称</a>``,
结果落盘 data/state/cb_pending_industry.json 缓存(行业静态,增量补抓)。
"""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import requests

from ..common import alerts

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP_PATH = _REPO_ROOT / "config" / "sw_industry_2021.json"
STOCK_DETAIL_URL = "https://www.jisilu.cn/data/stock/{stock_id}"
STOCK_DETAIL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.jisilu.cn/data/stock/",
}
# 详情页行业链接:行业 </span> <a href="/data/stock/dividend_rate/industry-{sw_cd}">三级名</a>
_DETAIL_INDUSTRY_RE = re.compile(
    r'行业</span>\s*<a href="[^"]*industry-(\d{6})"[^>]*>([^<]+)</a>'
)
PENDING_INDUSTRY_STATE_NAME = "cb_pending_industry"
PENDING_INDUSTRY_STATE_PATH = _REPO_ROOT / "data" / "state" / "cb_pending_industry.json"


@lru_cache(maxsize=1)
def load_industry_map(map_path: Optional[str] = None) -> Dict:
    """加载申万 2021 行业表(进程内缓存一次)。"""
    path = Path(map_path) if map_path else DEFAULT_MAP_PATH
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    return data


def l1_code_of(sw_cd, industry_map: Optional[Dict] = None) -> Optional[str]:
    """sw_cd(申万三级) -> 一级行业代码。未知返回 None。

    fallback 链:直接命中三级取 grand;否则回溯二级(前 4 位 + "00")取 parent;
    否则回溯一级形态(前 3 位 + "000")自身;仍未知返回 None。
    """
    code = str(sw_cd or "").strip()
    if not code:
        return None
    data = industry_map or load_industry_map()
    by_code = data.get("by_code", {})

    entry = by_code.get(code)
    if entry is not None:
        if entry.get("level") == 1:
            return code
        if entry.get("level") == 2:
            return entry.get("parent")
        return entry.get("grand")

    # 三级缺行(如东财文档缺失 610101):回溯二级
    if len(code) >= 6:
        l2 = by_code.get(code[:4] + "00")
        if l2 is not None and l2.get("level") == 2:
            return l2.get("parent")
        # 再回溯一级形态(注意 421xxx 之类是二级,需继续取 parent)
        l1 = by_code.get(code[:3] + "000")
        if l1 is not None:
            if l1.get("level") == 1:
                return code[:3] + "000"
            return l1.get("parent")
    return None


def l1_name_of(sw_cd, industry_map: Optional[Dict] = None) -> str:
    """sw_cd -> 一级行业名称;完全未知返回原始代码兜底。"""
    data = industry_map or load_industry_map()
    code = l1_code_of(sw_cd, data)
    if code is None:
        raw = str(sw_cd or "").strip()
        return raw or "未分类"
    return data.get("l1", {}).get(code) or code


# —— 待发转债行业(抓正股详情页,落盘缓存) ——


def fetch_stock_industry_from_detail(
    stock_id: str,
    cookie: str,
    session: Optional[requests.Session] = None,
) -> Optional[Dict]:
    """抓集思录正股详情页,解析行业链接 industry-{sw_cd},返回 {sw_cd, l3_name, l1_name}。

    页面为 JS 渲染,但行业链接是静态 HTML(实测有效);解析失败返回 None。
    """
    client = session or requests.Session()
    headers = dict(STOCK_DETAIL_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    def _do() -> str:
        resp = client.get(
            STOCK_DETAIL_URL.format(stock_id=stock_id), headers=headers, timeout=15,
        )
        resp.raise_for_status()
        return resp.text

    text = alerts.run_with_retry(f"stock_detail_industry_{stock_id}", _do)
    match = _DETAIL_INDUSTRY_RE.search(text)
    if match is None:
        return None
    sw_cd = match.group(1)
    l3_name = match.group(2).strip()
    return {
        "sw_cd": sw_cd,
        "l3_name": l3_name,
        "l1_name": l1_name_of(sw_cd),
    }


def load_pending_industry_cache() -> Dict:
    """读 data/state/cb_pending_industry.json,{stock_id: {sw_cd, l3_name, l1_name}}。"""
    if not PENDING_INDUSTRY_STATE_PATH.exists():
        return {}
    try:
        with open(PENDING_INDUSTRY_STATE_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_pending_industry_cache(cache: Dict) -> None:
    """写回 data/state/cb_pending_industry.json。"""
    PENDING_INDUSTRY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PENDING_INDUSTRY_STATE_PATH, "w", encoding="utf-8") as file:
        json.dump(cache, file, ensure_ascii=False, indent=1)


def pending_industry_of(stock_id: str) -> Optional[Dict]:
    """查待发转债行业缓存;未命中返回 None(不触网)。"""
    stock_id = str(stock_id or "").strip()
    if not stock_id:
        return None
    return load_pending_industry_cache().get(stock_id)


def backfill_pending_industries(
    cookie: str,
    stock_ids: List[str],
    session: Optional[requests.Session] = None,
    sleep_sec: float = 0.6,
) -> Dict:
    """批量抓取待发转债正股行业,只补缓存缺失的 stock_id,返回更新后的缓存。

    ``stock_ids`` 建议来自 pre_list 行;已缓存者跳过,控制对集思录的请求量。
    """
    cache = load_pending_industry_cache()
    missing = [sid for sid in stock_ids if str(sid).strip() and str(sid).strip() not in cache]
    if not missing:
        return cache
    client = session or requests.Session()
    for idx, stock_id in enumerate(missing, start=1):
        try:
            info = fetch_stock_industry_from_detail(stock_id, cookie, session=client)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 抓取正股行业失败 {stock_id}: {exc}")
            continue
        if info is not None:
            cache[stock_id] = info
            print(f"[INFO] 正股行业 {stock_id}: {info['l1_name']}/{info['l3_name']}")
        else:
            print(f"[WARN] 正股详情页未解析到行业 {stock_id}")
        if idx < len(missing) and sleep_sec > 0:
            time.sleep(sleep_sec)
    save_pending_industry_cache(cache)
    return cache
