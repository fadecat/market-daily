"""高股息主表数据层:集思录 dividend_rate_list 拉取 + 关联转债/补充池编排。

移植自 jisilu_ggx/main.py L18-69(fetch_data)+ L785-805(build_dividend_email_data)
+ L1273-1316(prepare_dividend_email_data)。改动相对旧仓:
- 退役 ``JISILU_COOKIE`` / ``DEFAULT_COOKIE`` 硬编码 cookie;cookie 由调用方经
  ``common.jisilu.get_cookie()`` 账密登录后传入(``cookie=None`` 时本模块兜底登录)。
  生产路径应由 run.py 单次 ``get_cookie()`` 后复用同一 cookie 给 fetch_data 与
  prepare_dividend_email_data,避免重复登录;
- ``fetch_data`` 网络层包 ``run_with_retry``;
- ``prepare_dividend_email_data`` 的关联转债映射改调本仓 ``cb_reference`` 模块,
  补充池改调本仓 ``supplement`` 模块;失败告警 ``send_alert``(webhook 文本)改
  ``notify_alert(title, detail)``;
- ``normalize_stock_code`` 走 ``common.whitelist``,``ensure_dividend_report_meta``
  走 ``.filter``。

渲染(分组/表格/规则提示)归 ``valuation/render.py``(Task #19),本模块只管数据。
"""
from __future__ import annotations

import time
from typing import Any, Callable

import requests

from ...common.alerts import notify_alert, run_with_retry
from ...common.jisilu import get_cookie
from ...common.whitelist import normalize_stock_code
from .cb_reference import fetch_stock_to_listed_bonds_map
from .cninfo_cache import fetch_cached_or_live_ttm_parent_net_profit
from .filter import ensure_dividend_report_meta
from .supplement import (
    DIVIDEND_EMAIL_SUPPLEMENT_TITLE,
    DIVIDEND_EMAIL_SUPPLEMENT_XCID,
    build_dividend_email_supplement_failed_alert_text,
    fetch_dividend_email_supplement,
)


JISILU_DIVIDEND_URL = "https://www.jisilu.cn/data/stock/dividend_rate_list/"

DIVIDEND_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.jisilu.cn/data/stock/dividend_rate/",
    "Origin": "https://www.jisilu.cn",
}

# 集思录高股息筛选表单(服务端筛选):pe<=15、dividend_rate>=3、市值>=200亿 等。
DIVIDEND_FORM_DATA = {
    "market[]": ["sh", "sz"],
    "industry": "",
    "province": "",
    "pe": 15,
    "pb": "",
    "dividend_rate": 3,
    "roe": "",
    "pe_temperature": 40,
    "pb_temperature": 40,
    "aft_dividend": "",
    "roe_average": 5,
    "revenue_average": "",
    "profit_average": "",
    "eps_growth_ttm": "",
    "cashflow_average": "",
    "int_debt_rate": "",
    "total_value_a": "200",
    "total_value_b": "",
    "float_value_a": "",
    "float_value_b": "",
    "rp": 500,
}


def fetch_data(
    cookie: str | None = None,
    session: requests.Session | None = None,
    timestamp_ms: int | None = None,
) -> dict:
    """拉取集思录高股息列表(dividend_rate_list),返回带元数据的 data dict。

    cookie 为 None 时通过 ``common.jisilu.get_cookie()`` 账密登录获取。网络层包
    run_with_retry。
    """
    if cookie is None:
        cookie = get_cookie()
    client = session or requests.Session()
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    params = {"___jsl": f"LST___t={ts}"}
    headers = dict(DIVIDEND_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    def _do() -> dict:
        resp = client.post(
            JISILU_DIVIDEND_URL,
            params=params,
            data=DIVIDEND_FORM_DATA,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    payload = run_with_retry("dividend_rate_list", _do)
    return ensure_dividend_report_meta(payload)


def build_dividend_email_data(
    data,
    stock_to_bonds_map=None,
    linked_bonds_fetch_failed=False,
    email_supplement=None,
    email_supplement_error="",
):
    """把关联转债映射注入每行 cell,并挂上补充池数据/错误,供渲染层使用。"""
    stock_to_bonds_map = stock_to_bonds_map or {}
    copied_rows = []
    for row in data.get("rows", []):
        cell = dict(row["cell"])
        stock_id = normalize_stock_code(cell.get("stock_id"))
        cell["linked_bonds"] = list(stock_to_bonds_map.get(stock_id, []))
        cell["linked_bonds_fetch_failed"] = linked_bonds_fetch_failed
        copied_rows.append({**row, "cell": cell})
    return {
        **data,
        "rows": copied_rows,
        "email_supplement": email_supplement,
        "email_supplement_error": email_supplement_error,
    }


def prepare_dividend_email_data(
    data,
    *,
    cookie: str | None = None,
    ttm_fetcher=fetch_cached_or_live_ttm_parent_net_profit,
    supplement_fetcher=fetch_dividend_email_supplement,
    alert_sender: Callable[[str, str], Any] = notify_alert,
    cb_reference_fetcher=fetch_stock_to_listed_bonds_map,
):
    """高股息邮件数据编排:关联转债映射 + 东财补充池,失败均不阻断主表发送。

    cookie 为 None 时通过 ``get_cookie()`` 登录。关联转债失败 -> 该列显示「查询失败」;
    补充池失败 -> 该区块显示失败提示并 ``notify_alert`` 报警,主表照常发送。
    """
    if cookie is None:
        cookie = get_cookie()

    stock_to_bonds_map: dict[str, list[dict]] = {}
    linked_bonds_fetch_failed = False
    try:
        stock_to_bonds_map = cb_reference_fetcher(cookie)
    except Exception as e:  # noqa: BLE001
        print(f"关联转债映射获取失败: {e}")
        linked_bonds_fetch_failed = True

    email_supplement = None
    email_supplement_error = ""
    try:
        email_supplement = supplement_fetcher(
            stock_to_bonds_map=stock_to_bonds_map,
            linked_bonds_fetch_failed=linked_bonds_fetch_failed,
            ttm_fetcher=ttm_fetcher,
        )
    except Exception as e:  # noqa: BLE001
        email_supplement_error = f"{DIVIDEND_EMAIL_SUPPLEMENT_TITLE}获取失败: {e}"
        print(email_supplement_error)
        try:
            alert_sender(
                "高股息补充池获取失败",
                build_dividend_email_supplement_failed_alert_text(DIVIDEND_EMAIL_SUPPLEMENT_XCID, e),
            )
        except Exception as alert_error:  # noqa: BLE001
            print(f"东财补充池失败告警发送失败: {alert_error}")

    return build_dividend_email_data(
        data,
        stock_to_bonds_map=stock_to_bonds_map,
        linked_bonds_fetch_failed=linked_bonds_fetch_failed,
        email_supplement=email_supplement,
        email_supplement_error=email_supplement_error,
    )
