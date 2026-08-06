"""股票 -> 转债 关联映射(集思录 cb_list_new 已上市 + pre_list 待发)。

移植自 jisilu_ggx/cb_reference.py:拉取集思录转债列表,按 stock_id 聚合每只股票
对应的已上市/待发转债,供高股息日报「关联转债」列展示。改动相对旧仓:
- 去掉 ``base_headers`` 入参,本模块自带 ``CB_HEADERS``(与 ``common.jisilu`` 一样
  各数据源自带请求头,解耦);
- ``normalize_security_code`` 改用 ``common.whitelist.normalize_stock_code``(同口径);
- 网络调用(``fetch_listed_cb_rows`` / ``fetch_pending_cb_rows``)包 ``run_with_retry``;
- cookie 由调用方(``prepare_dividend_email_data``)经 ``common.jisilu.get_cookie()``
  统一登录后传入,本模块不再读全局 cookie。
"""
from __future__ import annotations

import re
import time
from typing import Any

import requests

from ...common.alerts import run_with_retry
from ...common.whitelist import normalize_stock_code


CB_REFERENCE_URL = "https://www.jisilu.cn/data/cbnew/cb_list_new/"
CB_PRE_REFERENCE_URL = "https://www.jisilu.cn/data/cbnew/pre_list/"
CB_REFERENCE_PAGE_SIZE = 1000
CB_REFERENCE_MARKETS = ["shmb", "shkc", "szmb", "szcy"]

CB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.jisilu.cn",
    "Referer": "https://www.jisilu.cn/data/cbnew/",
}

LISTED_CB_FORM_DATA = {
    "fprice": "",
    "tprice": "",
    "curr_iss_amt": "",
    "convert_amt_ratio": "",
    "premium_rt": "",
    "fyear_left": "",
    "tyear_left": "",
    "rating_cd[]": [],
    "is_search": "Y",
    "market_cd[]": CB_REFERENCE_MARKETS,
    "show_blocked": "Y",
    "min_price_only": "N",
    "btype": "",
    "listed": "Y",
    "qflag": "N",
    "sw_cd": "",
    "bond_ids": "",
    "rp": CB_REFERENCE_PAGE_SIZE,
}


def fetch_listed_cb_rows(cookie: str, session: requests.Session | None = None, timestamp_ms: int | None = None) -> list[dict]:
    """拉取已上市转债行(集思录 cb_list_new)。网络层包 run_with_retry。"""
    client = session or requests.Session()
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    params = {"___jsl": f"LST___t={ts}"}
    headers = dict(CB_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    def _do() -> dict:
        resp = client.post(
            CB_REFERENCE_URL,
            params=params,
            data=LISTED_CB_FORM_DATA,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    payload = run_with_retry("cb_listed_reference", _do)
    return payload.get("rows", [])


def fetch_pending_cb_rows(cookie: str, session: requests.Session | None = None, timestamp_ms: int | None = None) -> list[dict]:
    """拉取待发转债行(集思录 pre_list)。网络层包 run_with_retry。"""
    client = session or requests.Session()
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    params = {"___jsl": f"LST___t={ts}"}
    headers = dict(CB_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    def _do() -> dict:
        resp = client.post(
            CB_PRE_REFERENCE_URL,
            params=params,
            data={},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    payload = run_with_retry("cb_pending_reference", _do)
    return payload.get("rows", [])


def normalize_progress_text(value) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.split())


def pending_bond_display_name(cell: dict) -> str:
    bond_nm = str(cell.get("bond_nm", "") or "").strip()
    if bond_nm:
        return bond_nm

    stock_nm = str(cell.get("stock_nm", "") or "").strip()
    if stock_nm:
        return f"{stock_nm}转债"
    return ""


def bond_dedupe_key(bond: dict) -> str:
    bond_id = normalize_stock_code(bond.get("bond_id"))
    if bond_id:
        return f"id:{bond_id}"
    bond_nm = str(bond.get("bond_nm", "") or "").strip()
    if bond_nm:
        return f"name:{bond_nm}"
    return ""


def build_stock_to_bonds_map(rows: list, bond_source: str = "listed") -> dict[str, list[dict]]:
    """把集思录转债行按 stock_id 聚合为 {stock_id: [bond_entry, ...]}。"""
    mapping: dict[str, list[dict]] = {}
    seen: dict[str, set] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        cell = row.get("cell", {})
        if not isinstance(cell, dict):
            continue

        stock_id = normalize_stock_code(cell.get("stock_id"))
        bond_id = normalize_stock_code(cell.get("bond_id"))
        bond_nm = str(cell.get("bond_nm", "") or "").strip()
        if bond_source == "pending":
            bond_nm = pending_bond_display_name(cell)
        if not stock_id:
            continue
        if bond_source == "listed" and not (bond_id and bond_nm):
            continue
        if bond_source == "pending" and not (bond_id or bond_nm):
            continue

        stock_seen = seen.setdefault(stock_id, set())
        dedupe_key = bond_dedupe_key({"bond_id": bond_id, "bond_nm": bond_nm})
        if dedupe_key in stock_seen:
            continue

        bond_entry: dict[str, Any] = {
            "bond_id": bond_id,
            "bond_nm": bond_nm,
            "bond_source": bond_source,
        }
        if bond_source == "pending":
            bond_entry["progress_nm"] = normalize_progress_text(cell.get("progress_nm"))
            bond_entry["progress_dt"] = str(cell.get("progress_dt") or "").strip()
            bond_entry["list_date"] = str(cell.get("list_date") or "").strip()
        mapping.setdefault(stock_id, []).append(bond_entry)
        stock_seen.add(dedupe_key)

    for bonds in mapping.values():
        if bond_source == "pending":
            bonds.sort(key=lambda item: (item.get("progress_dt", ""), item["bond_id"], item["bond_nm"]))
        else:
            bonds.sort(key=lambda item: (item["bond_id"], item["bond_nm"]))

    return mapping


def merge_stock_to_bonds_maps(*maps: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """合并多个 stock->bonds 映射,按 bond 去重(已上市优先,因其先传入)。"""
    merged: dict[str, list[dict]] = {}
    seen: dict[str, set] = {}

    for stock_map in maps:
        for stock_id, bonds in stock_map.items():
            merged.setdefault(stock_id, [])
            stock_seen = seen.setdefault(stock_id, set())
            for bond in bonds:
                dedupe_key = bond_dedupe_key(bond)
                if not dedupe_key or dedupe_key in stock_seen:
                    continue
                merged[stock_id].append(dict(bond))
                stock_seen.add(dedupe_key)

    return merged


def fetch_stock_to_listed_bonds_map(cookie: str, session: requests.Session | None = None) -> dict[str, list[dict]]:
    """拉取已上市 + 待发转债,合并为 {stock_id: [bond_entry, ...]}(供高股息关联转债列)。"""
    listed_rows = fetch_listed_cb_rows(cookie, session=session)
    pending_rows = fetch_pending_cb_rows(cookie, session=session)
    listed_map = build_stock_to_bonds_map(listed_rows, bond_source="listed")
    pending_map = build_stock_to_bonds_map(pending_rows, bond_source="pending")
    return merge_stock_to_bonds_maps(listed_map, pending_map)
