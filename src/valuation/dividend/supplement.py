"""东财条件选股(xuangu)高股息补充池:xcid 条件拉取 + 本地二次过滤 + 行业分组 + 邮件渲染。

移植自 jisilu_ggx/eastmoney_xuangu_probe.py + eastmoney_xuangu_client.py + main.py
L80-159/L763-1366。邮件路径只走 xcid 流程(从东财服务器读取已保存条件),不走
condition_template.json —— search_template.py 与 condition_template.json 整体退役
(旧仓 grep 确认 condition_template.json 不被任何 .py 引用)。

改动相对旧仓:
- ``os.environ.get`` / 模块级 ``XUANGU_COOKIE`` 改 ``env.get`` 懒读(cookie 在
  ``fetch_all_results_by_xcid`` 入口解析,便于测试注入);
- 网络调用(``fetch_xuangu_detail`` GET、``_post_search_payload`` POST)包
  ``run_with_retry``,旧仓无重试;
- 失败告警 ``send_alert``(webhook 文本)改 ``notify_alert``;
- ``normalize_stock_code`` 走 ``common.whitelist``,``render_markdown``/``render_table``
  走 ``common.email``;
- 退役 CLI(``main``/``run_probe``)、``compact_rows``、probe 版 ``build_search_payload``/
  ``fetch_xuangu_results``(邮件路径不用)、页结果里的 ``sample_rows``/``raw_result``/
  ``request_payload``(下游不读)。

关联转债映射(cb_reference)尚未迁移:补充池通过 ``stock_to_bonds_map`` 参数接收映射,
未提供时该列显示 "-"(``linked_bonds_fetch_failed=True`` 时显示 "查询失败"),待
Task #18 主表迁移 cb_reference 后由 ``prepare_dividend_email_data`` 注入。
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from ...common import env
from ...common.alerts import notify_alert, run_with_retry
from ...common.email import render_markdown, render_table
from ...common.whitelist import normalize_stock_code
from .cninfo_cache import fetch_cached_or_live_ttm_parent_net_profit


# ---------- 常量 ----------

DETAIL_URL = "https://np-tjxg-b.eastmoney.com/api/smart-tag/stock/v3/getXcIdDetail"
SEARCH_URL = "https://np-tjxg-b.eastmoney.com/api/smart-tag/stock/v3/pw/search-code"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://xuangu.eastmoney.com/",
    "Origin": "https://xuangu.eastmoney.com",
}

SEARCH_HEADERS = {
    **BASE_HEADERS,
    "Content-Type": "application/json",
    "curPage": "stockResult",
    "jumpSource": "edit_way",
    "actionMode": "edit_way",
}

DIVIDEND_EMAIL_SUPPLEMENT_XCID = env.get("DIVIDEND_EMAIL_SUPPLEMENT_XCID") or "xc12fd39e81b0700714b"
DIVIDEND_EMAIL_SUPPLEMENT_TITLE = "东财条件补充池"
DIVIDEND_SUPPLEMENT_INDUSTRY_FIELD_LABELS = {
    "INDUSTRY_LV1": "东财一级行业",
    "INDUSTRY": "东财二级行业",
    "INDUSTRY_LV3": "东财三级行业",
}
# 补充池本地行业排除名单:(字段, 关键字) 子串匹配,命中即剔除。
# 与主表 EXCLUDED_DIVIDEND_INDUSTRIES(仅 "基建市政工程")是两份不同名单,勿合并。
DIVIDEND_SUPPLEMENT_EXCLUDED_INDUSTRY_RULES = (
    ("INDUSTRY_LV3", "基建市政工程"),
    ("INDUSTRY", "工程机械"),
    ("INDUSTRY_LV3", "工程机械器件"),
    ("INDUSTRY_LV3", "工程机械整机"),
    ("INDUSTRY", "工程咨询服务Ⅱ"),
    ("INDUSTRY_LV3", "工程咨询服务Ⅲ"),
    ("INDUSTRY_LV3", "国际工程"),
    ("INDUSTRY_LV3", "化学工程"),
    ("INDUSTRY_LV3", "其他专业工程"),
    ("INDUSTRY_LV3", "通信工程及服务"),
    ("INDUSTRY_LV3", "园林工程"),
    ("INDUSTRY", "油服工程"),
    ("INDUSTRY_LV3", "油气及炼化工程"),
    ("INDUSTRY", "专业工程"),
    ("INDUSTRY_LV3", "培训教育"),
    ("INDUSTRY", "旅游及景区"),
    ("INDUSTRY_LV3", "旅游综合"),
    ("INDUSTRY_LV3", "旅游零售Ⅲ"),
    ("INDUSTRY", "旅游零售Ⅱ"),
    ("INDUSTRY", "出版"),
    ("INDUSTRY_LV3", "大众出版"),
    ("INDUSTRY_LV3", "教育出版"),
    ("INDUSTRY", "游戏Ⅱ"),
    ("INDUSTRY_LV3", "游戏Ⅲ"),
    ("INDUSTRY_LV3", "广告媒体"),
    ("INDUSTRY_LV3", "其他数字媒体"),
    ("INDUSTRY", "数字媒体"),
    ("INDUSTRY_LV3", "视频媒体"),
    ("INDUSTRY_LV3", "图片媒体"),
    ("INDUSTRY_LV3", "文字媒体"),
    ("INDUSTRY", "电视广播Ⅱ"),
    ("INDUSTRY_LV3", "电视广播Ⅲ"),
    ("INDUSTRY", "化妆品"),
    ("INDUSTRY_LV3", "化妆品制造及其他"),
    ("INDUSTRY_LV3", "品牌化妆品"),
    ("INDUSTRY_LV1", "传媒"),
    ("INDUSTRY", "广告营销"),
    ("INDUSTRY_LV3", "营销代理"),
    ("INDUSTRY_LV3", "门户网站"),
    ("INDUSTRY_LV3", "影视动漫制作"),
    ("INDUSTRY_LV3", "院线"),
    ("INDUSTRY", "房地产开发"),
    ("INDUSTRY_LV3", "产业地产"),
    ("INDUSTRY_LV3", "住宅开发"),
    ("INDUSTRY_LV3", "房地产综合服务"),
    ("INDUSTRY_LV3", "房产租赁经纪"),
    ("INDUSTRY", "房地产服务"),
    ("INDUSTRY_LV1", "房地产"),
    ("INDUSTRY_LV3", "信托"),
    ("INDUSTRY_LV3", "期货"),
    ("INDUSTRY_LV3", "金融信息服务"),
    ("INDUSTRY_LV3", "金融控股"),
    ("INDUSTRY", "多元金融"),
    ("INDUSTRY_LV3", "资产管理"),
    ("INDUSTRY_LV1", "国防军工"),
    ("INDUSTRY", "地面兵装Ⅱ"),
    ("INDUSTRY_LV3", "地面兵装Ⅲ"),
    ("INDUSTRY", "航海装备Ⅱ"),
    ("INDUSTRY_LV3", "航海装备Ⅲ"),
    ("INDUSTRY", "航空装备Ⅱ"),
    ("INDUSTRY_LV3", "航空装备Ⅲ"),
    ("INDUSTRY", "航天装备Ⅱ"),
    ("INDUSTRY_LV3", "航天装备Ⅲ"),
    ("INDUSTRY", "军工电子Ⅱ"),
    ("INDUSTRY_LV3", "军工电子Ⅲ"),
    ("INDUSTRY_LV1", "环保"),
    ("INDUSTRY", "环保设备Ⅱ"),
    ("INDUSTRY_LV3", "环保设备Ⅲ"),
    ("INDUSTRY", "环境治理"),
    ("INDUSTRY_LV3", "大气治理"),
    ("INDUSTRY_LV3", "固废治理"),
)
DIVIDEND_SUPPLEMENT_PE_TTM_MAX = float(env.get("DIVIDEND_SUPPLEMENT_PE_TTM_MAX", "15") or "15")
DIVIDEND_SUPPLEMENT_EMAIL_COLUMN_STYLES = {
    10: "white-space:normal;word-break:break-word;min-width:180px",
}
LINKED_BONDS_FETCH_FAILED_TEXT = "查询失败"


def parse_float(value, default=None):
    """宽松解析为 float:去掉 ``%`` 后取值,失败返回 default。

    与 ``valuation.metrics.parse_float`` 语义不同(后者无 default、去逗号、``-`` 视为
    None);补充池排序依赖 ``default=float("inf")`` / ``0.0`` 兜底,且东财字段直接传入
    (NEWEST_PRICE/PB 等),故本模块保留旧仓 main.py 的实现。
    """
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError, AttributeError):
        return default


# ---------- 东财 xuangu 客户端(低层 HTTP + 解析) ----------


def extract_xc_id(value: str) -> str:
    """从 xcId 原文 / 分享链接 / 含 xc 串的文本中提取 xcId。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("缺少 xcId 或分享链接")
    if text.startswith("xc"):
        return text

    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        query = parse_qs(parsed.query)
        xc_id = (query.get("id") or [""])[0].strip()
        if xc_id.startswith("xc"):
            return xc_id

    match = re.search(r"\bxc[a-zA-Z0-9]+\b", text)
    if match:
        return match.group(0)
    raise ValueError(f"无法从输入中提取 xcId: {value}")


def parse_cookie_string(cookie_text: str) -> dict[str, str]:
    cookie_map: dict[str, str] = {}
    for part in str(cookie_text or "").split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookie_map[key.strip()] = value.strip()
    return cookie_map


def infer_fingerprint(cookie_text: str) -> str:
    """指纹:优先取 cookie 里的 qgqp_b_id,否则用 cookie+小时桶的 md5 兜底。"""
    cookie_map = parse_cookie_string(cookie_text)
    if cookie_map.get("qgqp_b_id"):
        return cookie_map["qgqp_b_id"]
    seed = f"{cookie_text}|{int(time.time() // 3600)}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def _current_timestamp_micros() -> str:
    return str(time.time_ns() // 1000)


def _build_request_id() -> str:
    return f"{uuid.uuid4().hex}{int(time.time() * 1000)}"


def _require_code_100(payload: dict[str, Any], label: str) -> dict[str, Any]:
    # 东财返回 code 是字符串 "100"(非 int 100)。
    if str(payload.get("code")) != "100":
        raise RuntimeError(f"{label} 返回异常: code={payload.get('code')} msg={payload.get('msg')}")
    return payload


def fetch_xuangu_detail(
    xc_id: str,
    cookie_text: str = "",
    timeout: int = 30,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """GET 详情接口,返回 ``data`` 字段(已保存条件定义)。网络层包 run_with_retry。"""
    client = session or requests.Session()
    headers = dict(BASE_HEADERS)
    if cookie_text:
        headers["Cookie"] = cookie_text

    def _do() -> dict[str, Any]:
        response = client.get(DETAIL_URL, params={"xcId": xc_id}, headers=headers, timeout=timeout)
        response.raise_for_status()
        return _require_code_100(response.json(), "getXcIdDetail")

    payload = run_with_retry("xuangu_detail", _do)
    return payload.get("data") or {}


def build_condition_from_detail(detail_data: dict[str, Any]) -> dict[str, Any]:
    """从详情 data 抽出搜索所需的四元组(detail 形状 -> condition 形状)。"""
    keyword_info = detail_data.get("keywordInfoNew") or {}
    return {
        "keyWordNew": detail_data.get("contentNew") or detail_data.get("content") or "",
        "customDataNew": detail_data.get("customDataNew") or detail_data.get("customData") or "[]",
        "dxInfoNew": keyword_info.get("dxInfo") or [],
        "senInfoNew": keyword_info.get("senInfo") or [],
    }


def build_search_payload_from_condition(
    condition: dict[str, Any],
    fingerprint: str,
    page_no: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """由 condition 四元组组装 search-code POST body。

    timestamp/requestId 在 condition 里缺失时为 None 并被过滤掉(旧仓行为),邮件路径
    走的就是这份不含 timestamp/requestId 的 body。
    """
    payload = {
        "needAmbiguousSuggest": True,
        "pageSize": page_size,
        "pageNo": page_no,
        "fingerprint": fingerprint,
        "matchWord": str(condition.get("matchWord", "")),
        "shareToGuba": bool(condition.get("shareToGuba", False)),
        "timestamp": str(condition.get("timestamp", "")) or None,
        "requestId": str(condition.get("requestId", "")) or None,
        "removedConditionIdList": list(condition.get("removedConditionIdList", [])),
        "ownSelectAll": bool(condition.get("ownSelectAll", False)),
        "needCorrect": bool(condition.get("needCorrect", True)),
        "client": str(condition.get("client", "WEB")),
        "product": str(condition.get("product", "")),
        "needShowStockNum": bool(condition.get("needShowStockNum", False)),
        "biz": str(condition.get("biz", "web_ai_select_stocks")),
        "gids": list(condition.get("gids", [])),
        "dxInfoNew": list(condition.get("dxInfoNew", [])),
        "keyWordNew": str(condition.get("keyWordNew", "")),
        "customDataNew": condition.get("customDataNew", "[]"),
        "senInfoNew": list(condition.get("senInfoNew", [])),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _post_search_payload(
    payload: dict[str, Any],
    cookie_text: str = "",
    timeout: int = 30,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """POST search-code,返回校验过 code==100 的完整响应。网络层包 run_with_retry。"""
    client = session or requests.Session()
    headers = dict(SEARCH_HEADERS)
    if cookie_text:
        headers["Cookie"] = cookie_text

    def _do() -> dict[str, Any]:
        response = client.post(SEARCH_URL, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
        return _require_code_100(response.json(), "search-code")

    return run_with_retry("xuangu_search", _do)


def _is_column_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) for item in value)
        and any("title" in item and "key" in item for item in value)
    )


def _is_result_row_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) for item in value)
        and any("SECURITY_CODE" in item or "SECURITY_SHORT_NAME" in item for item in value)
    )


def _find_nested_list(payload: Any, predicate) -> list[dict[str, Any]]:
    if predicate(payload):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            result = _find_nested_list(value, predicate)
            if result:
                return result
    if isinstance(payload, list):
        for value in payload:
            result = _find_nested_list(value, predicate)
            if result:
                return result
    return []


def find_result_columns(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _find_nested_list(payload, _is_column_list)


def find_result_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _find_nested_list(payload, _is_result_row_list)


def find_total_count(payload: dict[str, Any]) -> int | None:
    if isinstance(payload, dict):
        total = payload.get("total")
        if isinstance(total, int):
            return total
        if isinstance(total, str) and total.isdigit():
            return int(total)
        for value in payload.values():
            found = find_total_count(value)
            if found is not None:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = find_total_count(value)
            if found is not None:
                return found
    return None


def _build_page_result(
    raw_result: dict[str, Any],
    condition_text: str,
    page_no: int,
    page_size: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    columns = find_result_columns(raw_result)
    rows = find_result_rows(raw_result)
    result: dict[str, Any] = {
        "condition_text": condition_text,
        "page_no": page_no,
        "page_size": page_size,
        "column_count": len(columns),
        "total_count": find_total_count(raw_result),
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
    }
    if extra:
        result.update(extra)
    return result


def fetch_result_page_by_condition(
    condition: dict[str, Any],
    cookie_text: str = "",
    page_no: int = 1,
    page_size: int = 50,
    timeout: int = 30,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    payload = build_search_payload_from_condition(
        condition,
        fingerprint=infer_fingerprint(cookie_text),
        page_no=page_no,
        page_size=page_size,
    )
    raw_result = _post_search_payload(
        payload,
        cookie_text=cookie_text,
        timeout=timeout,
        session=session,
    )
    return _build_page_result(
        raw_result,
        condition_text=payload.get("keyWordNew", ""),
        page_no=page_no,
        page_size=page_size,
    )


def _dedupe_rows(rows: list[dict[str, Any]], dedupe_key: str = "SECURITY_CODE") -> list[dict[str, Any]]:
    seen = set()
    unique_rows = []
    for row in rows:
        key = row.get(dedupe_key)
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)
    return unique_rows


def fetch_all_results_by_condition(
    condition: dict[str, Any],
    cookie_text: str = "",
    page_size: int = 50,
    max_pages: int | None = None,
    sleep_seconds: float = 0.0,
    timeout: int = 30,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    first_page = fetch_result_page_by_condition(
        condition,
        cookie_text=cookie_text,
        page_no=1,
        page_size=page_size,
        timeout=timeout,
        session=session,
    )
    total_count = first_page.get("total_count") or 0
    total_pages = math.ceil(total_count / page_size) if total_count else 0
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    all_rows = list(first_page["rows"])
    for page_no in range(2, total_pages + 1):
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        page_result = fetch_result_page_by_condition(
            condition,
            cookie_text=cookie_text,
            page_no=page_no,
            page_size=page_size,
            timeout=timeout,
            session=session,
        )
        all_rows.extend(page_result["rows"])

    rows = _dedupe_rows(all_rows)
    return {
        **first_page,
        "page_no": 1,
        "page_size": page_size,
        "page_count": total_pages,
        "pages_fetched": total_pages,
        "row_count": len(rows),
        "rows": rows,
    }


def fetch_all_results_by_xcid(
    xc_id_or_url: str,
    cookie_text: str | None = None,
    page_size: int = 100,
    max_pages: int | None = None,
    sleep_seconds: float = 0.0,
    timeout: int = 30,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """按 xcid 拉取已保存条件的全部结果(分页 + 去重)。

    cookie_text 为 None 时从 ``EASTMONEY_XUANGU_COOKIE`` 懒读;显式传 ``""`` 可跳过。
    邮件路径用 page_size=100、不设 max_pages(翻完为止)。
    """
    if cookie_text is None:
        cookie_text = env.get("EASTMONEY_XUANGU_COOKIE")
    xc_id = extract_xc_id(xc_id_or_url)
    detail = fetch_xuangu_detail(xc_id, cookie_text=cookie_text, timeout=timeout, session=session)
    condition = build_condition_from_detail(detail)
    result = fetch_all_results_by_condition(
        condition,
        cookie_text=cookie_text,
        page_size=page_size,
        max_pages=max_pages,
        sleep_seconds=sleep_seconds,
        timeout=timeout,
        session=session,
    )
    result["xc_id"] = xc_id
    result["detail_data"] = detail
    return result


# ---------- 行字段提取与格式化 ----------


def _first_value_by_prefix(row, prefix):
    """东财字段带日期后缀(如 ``PETTMDEDUCTED{2026-08-01}``),按前缀取首个命中值。"""
    for key, value in row.items():
        key_text = str(key)
        if key_text == prefix or key_text.startswith(prefix):
            return value
    return ""


def _leading_metric_text(value):
    """取 ``|`` 分隔的首段并去掉 亿/% 单位,供数值解析。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split("|", 1)[0].replace("亿", "").replace("%", "").strip()


def _format_decimal_text(value, digits=2):
    number = parse_float(value)
    if number is None:
        return str(value or "")
    return f"{number:.{digits}f}"


def _format_percent_text(value):
    number = parse_float(value)
    if number is None:
        return str(value or "")
    return f"{number:.2f}%"


def _format_prefixed_decimal_text(row, prefix, digits=2):
    return _format_decimal_text(_leading_metric_text(_first_value_by_prefix(row, prefix)), digits=digits)


def _format_prefixed_percent_text(row, prefix):
    return _format_percent_text(_leading_metric_text(_first_value_by_prefix(row, prefix)))


def _parse_yi_amount_text(value, default=None):
    """解析 ``亿`` / ``万亿`` / 纯数字金额文本为亿元数值。"""
    text = str(value or "").strip()
    if not text:
        return default
    text = text.split("|", 1)[0].replace(",", "").strip()
    if text.endswith("万亿"):
        base = parse_float(text[:-2], None)
        return default if base is None else base * 10000
    if text.endswith("亿"):
        return parse_float(text[:-1], default)
    return parse_float(text, default)


# ---------- 补充池行级指标 ----------


def _supplement_industry_name_of(row):
    return (
        str(row.get("INDUSTRY_LV3") or row.get("INDUSTRY") or row.get("INDUSTRY_LV1") or "未分类").strip()
        or "未分类"
    )


def _supplement_pe_value(row):
    """PE 排序值:优先 PE_DYNAMIC,否则扣非 PETTMDEDUCTED;缺失返回 inf(排序沉底)。"""
    pe_dynamic = parse_float(row.get("PE_DYNAMIC"), None)
    if pe_dynamic is not None:
        return pe_dynamic
    return parse_float(_leading_metric_text(_first_value_by_prefix(row, "PETTMDEDUCTED")), float("inf"))


def _supplement_pe_text(row):
    pe_dynamic = parse_float(row.get("PE_DYNAMIC"), None)
    if pe_dynamic is not None:
        return f"{pe_dynamic:.2f}"
    return _format_prefixed_decimal_text(row, "PETTMDEDUCTED")


def _supplement_dividend_rate_value(row):
    return parse_float(_leading_metric_text(_first_value_by_prefix(row, "DIVIDEND_NEWRATIO_HYY")), 0.0)


def _supplement_roe_text(row):
    roe_weight_text = _format_prefixed_decimal_text(row, "ROE_WEIGHT")
    if roe_weight_text:
        return roe_weight_text
    for key, value in row.items():
        if "ROE" in str(key).upper():
            roe_text = _format_decimal_text(_leading_metric_text(value), digits=2)
            if roe_text:
                return roe_text
    return ""


def _supplement_market_value_yi(row):
    # 注意东财字段名是 TOAL_MARKET_VALUE(拼写如此,非 TOTAL),保留原拼写。
    return _parse_yi_amount_text(_first_value_by_prefix(row, "TOAL_MARKET_VALUE"), None)


def _supplement_ttm_metrics(row, stock_code, ttm_fetcher=fetch_cached_or_live_ttm_parent_net_profit):
    """本地 TTM 指标:取巨潮 TTM 归母净利润,用总市值/净利润算 PE-TTM。结果缓存到行内。"""
    cached_metrics = row.get("_ttm_metrics")
    if isinstance(cached_metrics, dict):
        return cached_metrics
    try:
        ttm_info = ttm_fetcher(stock_code)
    except Exception as e:  # noqa: BLE001
        print(f"[东财补充池TTM失败] {stock_code}: {e}")
        metrics = {"ttm_text": "", "ttm_value_yi": None, "pe_ttm_text": "", "error": str(e)}
        row["_ttm_metrics"] = metrics
        return metrics
    value = ttm_info.get("ttm_value_yi")
    if value is None:
        metrics = {"ttm_text": "", "ttm_value_yi": None, "pe_ttm_text": ""}
        row["_ttm_metrics"] = metrics
        return metrics

    ttm_value_yi = float(value)
    ttm_text = f"{ttm_value_yi:.2f}"
    pe_ttm_text = ""
    market_value_yi = _supplement_market_value_yi(row)
    if market_value_yi is not None and ttm_value_yi > 0:
        pe_ttm_text = f"{(market_value_yi / ttm_value_yi):.2f}"
    metrics = {
        "ttm_text": ttm_text,
        "ttm_value_yi": ttm_value_yi,
        "pe_ttm_text": pe_ttm_text,
    }
    row["_ttm_metrics"] = metrics
    return metrics


# ---------- ROE 动态列解析 ----------


def resolve_dividend_email_supplement_roe_column(columns, rows):
    """ROE 字段 key 带日期后缀且可变:先扫 columns 表头,再扫行 key 兜底。"""
    columns = columns or []
    rows = rows or []
    for column in columns:
        title = str(column.get("title") or "").strip()
        key = str(column.get("key") or "").strip()
        if ("ROE" in title.upper()) or ("ROE" in key.upper()) or ("净资产收益率" in title):
            header = "ROE" if title.upper() == "ROE" else (title or "ROE")
            return {"key": key, "header": header}

    for row in rows:
        for key in row.keys():
            key_text = str(key)
            if "ROE_WEIGHT" in key_text.upper():
                return {"key": key_text, "header": "ROE"}
            if "ROE" in key_text.upper():
                return {"key": key_text, "header": key_text}
    return {"key": "", "header": "ROE"}


def _supplement_roe_text_by_key(row, roe_key=""):
    roe_key = str(roe_key or "").strip()
    if roe_key:
        roe_text = _format_decimal_text(_leading_metric_text(row.get(roe_key)), digits=2)
        if roe_text:
            return roe_text
    return _supplement_roe_text(row)


# ---------- 表头 / 标题 / 条件摘要 ----------


def build_dividend_email_supplement_headers(roe_header="ROE"):
    return [
        "#",
        "行业",
        "名称",
        "代码",
        "价格",
        "股息率",
        "PE-TTM",
        "PB",
        roe_header,
        "TTM归母净利(亿)",
        "关联转债",
    ]


def build_dividend_email_supplement_title(xc_id):
    xc_id = str(xc_id or "").strip()
    if not xc_id:
        return DIVIDEND_EMAIL_SUPPLEMENT_TITLE
    return f"{DIVIDEND_EMAIL_SUPPLEMENT_TITLE}·{xc_id}"


def build_dividend_email_supplement_condition_lines(condition_text):
    """从东财条件的自然语言摘要文本解析人类可读口径行(纯串匹配,文本变化时优雅降级为 [])。"""
    text = str(condition_text or "").strip()
    if not text:
        return []

    lines = []

    primary_parts = []
    if "企业性质包含中央国有企业或地方国有企业" in text:
        primary_parts.append("国企")
    if "市盈率TTM(扣非)大于等于0倍小于等于20倍" in text:
        primary_parts.append("扣非PE 0~20")
    if "上年扣非净利润大于10亿" in text:
        primary_parts.append("上年扣非净利润 > 10亿")
    if "三年平均ROE＞5%" in text:
        primary_parts.append("三年平均ROE > 5%")
    elif "ROE_WEIGHT" in text or "ROE" in text:
        primary_parts.append("ROE 条件已启用")
    if primary_parts:
        lines.append("东财条件:" + "；".join(primary_parts))

    secondary_parts = []
    if "最新股息率>3%" in text:
        secondary_parts.append("最新股息率 > 3%")
    if "年度现金分红比例大于等于40%小于等于100%" in text:
        secondary_parts.append("年度现金分红比例 40%~100%")
    risk_parts = []
    if "不要ST股及不要退市股" in text:
        risk_parts.append("ST/退市")
    if "不要北交所" in text:
        risk_parts.append("北交所")
    if "剔除新规风险" in text:
        risk_parts.append("新规风险")
    if risk_parts:
        secondary_parts.append("剔除" + "/".join(risk_parts))
    if secondary_parts:
        lines.append("东财条件:" + "；".join(secondary_parts))

    if "不要东财三级行业包含基建市政工程" in text:
        lines.append("东财行业:剔除工程链/传媒/地产/金融/军工/环保等行业")

    if "股息率正序" in text or "股息率倒序" in text:
        lines.append("本地展示:三级行业分组;行业与组内按股息率降序")

    return lines


# ---------- 本地二次过滤 ----------


def print_dividend_email_supplement_exclusion(row, reason):
    stock_code = normalize_stock_code(row.get("SECURITY_CODE"))
    stock_name = str(row.get("SECURITY_SHORT_NAME", "")).strip()
    print(f"[过滤排除] {stock_code} {stock_name}: {reason}")
    return {**row, "_exclude_reason": reason}


def summarize_dividend_email_supplement_exclusions(excluded_rows):
    summary = {
        "industry_excluded_count": 0,
        "pe_ttm_excluded_count": 0,
    }
    for row in excluded_rows:
        reason = str(row.get("_exclude_reason", "") or "")
        if reason.startswith("东财补充池行业命中排除名单:"):
            summary["industry_excluded_count"] += 1
        elif reason.startswith("东财补充池PE-TTM命中排除条件:"):
            summary["pe_ttm_excluded_count"] += 1
    return summary


def filter_dividend_email_supplement_rows(
    rows,
    exclusion_rules=DIVIDEND_SUPPLEMENT_EXCLUDED_INDUSTRY_RULES,
    ttm_fetcher=fetch_cached_or_live_ttm_parent_net_profit,
    pe_ttm_max=DIVIDEND_SUPPLEMENT_PE_TTM_MAX,
):
    """两段过滤:① 行业排除名单(子串匹配);② 本地 PE-TTM > 上限剔除。

    PE-TTM 为空(无总市值或无 TTM 净利润)的行**保留**——只有明确 > 上限才剔除。
    """
    total_count = len(rows)
    filtered_rows = []
    excluded_rows = []
    for row in rows:
        normalized_row = dict(row)
        matched_reason = ""
        for field, keyword in exclusion_rules:
            value = str(normalized_row.get(field, "") or "").strip()
            if keyword and keyword in value:
                matched_reason = f"{DIVIDEND_SUPPLEMENT_INDUSTRY_FIELD_LABELS[field]}包含{keyword}"
                break
        if matched_reason:
            excluded_rows.append(
                print_dividend_email_supplement_exclusion(
                    normalized_row,
                    f"东财补充池行业命中排除名单: {matched_reason}",
                )
            )
            continue
        market_value_yi = _supplement_market_value_yi(normalized_row)
        if market_value_yi is not None:
            stock_code = normalize_stock_code(normalized_row.get("SECURITY_CODE"))
            ttm_metrics = _supplement_ttm_metrics(normalized_row, stock_code, ttm_fetcher=ttm_fetcher)
            pe_ttm_value = parse_float(ttm_metrics.get("pe_ttm_text"), None)
            if pe_ttm_value is not None and pe_ttm_value > pe_ttm_max:
                excluded_rows.append(
                    print_dividend_email_supplement_exclusion(
                        normalized_row,
                        f"东财补充池PE-TTM命中排除条件: PE-TTM {pe_ttm_value:.2f} > {pe_ttm_max:g}",
                    )
                )
                continue
        filtered_rows.append(normalized_row)
    exclusion_summary = summarize_dividend_email_supplement_exclusions(excluded_rows)
    print(
        f"[过滤汇总] 东财补充池本地二次过滤: 输入 {total_count} 只,"
        f"行业排除 {exclusion_summary['industry_excluded_count']} 只,"
        f"PE-TTM 排除 {exclusion_summary['pe_ttm_excluded_count']} 只,"
        f"剔除 {len(excluded_rows)} 只,剩余 {len(filtered_rows)} 只"
    )
    return filtered_rows, excluded_rows


# ---------- 行业分组 + 排序 ----------


def build_dividend_email_supplement_groups(rows):
    """按三级行业分组;组内按 (-股息率, PB, PE, 代码) 排序;组均值仅取组内前 2 名(龙头)。"""
    grouped_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        normalized = dict(row)
        grouped_rows.setdefault(_supplement_industry_name_of(normalized), []).append(normalized)

    groups = []
    for industry_name, industry_rows in grouped_rows.items():
        ranked_rows = []
        for row in industry_rows:
            pb_value = parse_float(row.get("PB"), float("inf"))
            pe_value = _supplement_pe_value(row)
            dividend_rate_value = _supplement_dividend_rate_value(row)
            ranked_rows.append(
                {
                    "industry_name": industry_name,
                    "row": row,
                    "pb_value": pb_value,
                    "pe_value": pe_value,
                    "dividend_rate_value": dividend_rate_value,
                }
            )

        ranked_rows.sort(
            key=lambda item: (
                -item["dividend_rate_value"],
                item["pb_value"],
                item["pe_value"],
                normalize_stock_code(item["row"].get("SECURITY_CODE")),
            )
        )
        leaders = ranked_rows[:2]
        groups.append(
            {
                "industry_name": industry_name,
                "industry_count": len(ranked_rows),
                "industry_avg_pb": sum(item["pb_value"] for item in leaders) / len(leaders),
                "industry_avg_pe": sum(item["pe_value"] for item in leaders) / len(leaders),
                "industry_avg_dividend_rate": sum(item["dividend_rate_value"] for item in leaders) / len(leaders),
                "rows": ranked_rows,
            }
        )

    groups.sort(
        key=lambda group: (
            -group["industry_avg_dividend_rate"],
            group["industry_avg_pb"],
            group["industry_avg_pe"],
            group["industry_name"],
        )
    )
    return groups


# ---------- 关联转债展示(主表与补充池共用,cb_reference 迁移后由主表注入映射) ----------


def format_linked_bonds_html_from_items(linked_bonds, linked_bonds_fetch_failed=False):
    if linked_bonds:
        formatted = []
        for item in linked_bonds:
            if item.get("bond_source") == "pending":
                progress_nm = str(item.get("progress_nm") or "").strip()
                bond_id = str(item.get("bond_id") or "").strip()
                if progress_nm:
                    if bond_id:
                        formatted.append(f'待发: {item["bond_nm"]}({bond_id}, {progress_nm})')
                    else:
                        formatted.append(f'待发: {item["bond_nm"]}({progress_nm})')
                else:
                    if bond_id:
                        formatted.append(f'待发: {item["bond_nm"]}({bond_id})')
                    else:
                        formatted.append(f'待发: {item["bond_nm"]}')
            else:
                formatted.append(f'{item["bond_nm"]}({item["bond_id"]})')
        return "<br>".join(formatted)
    if linked_bonds_fetch_failed:
        return LINKED_BONDS_FETCH_FAILED_TEXT
    return "-"


# ---------- 组装 ----------


def build_dividend_email_supplement(
    result,
    stock_to_bonds_map=None,
    linked_bonds_fetch_failed=False,
    ttm_fetcher=fetch_cached_or_live_ttm_parent_net_profit,
):
    stock_to_bonds_map = stock_to_bonds_map or {}
    source_rows = result.get("rows") or []
    columns = result.get("columns") or []
    xc_id = result.get("xc_id", "")
    rows, excluded_rows = filter_dividend_email_supplement_rows(source_rows, ttm_fetcher=ttm_fetcher)
    exclusion_summary = summarize_dividend_email_supplement_exclusions(excluded_rows)
    ttm_missing_count = sum(
        1
        for row in rows
        if isinstance(row.get("_ttm_metrics"), dict) and row["_ttm_metrics"].get("error")
    )
    roe_column = resolve_dividend_email_supplement_roe_column(columns, rows)
    groups = build_dividend_email_supplement_groups(rows)
    group_styles = ["background:#FBFCFE", "background:#F7FBF8"]
    row_specs = []
    idx = 1
    for group_index, group in enumerate(groups):
        base_style = group_styles[group_index % len(group_styles)]
        first_row_style = f"{base_style};border-top:2px solid #dfe5ec"
        for row_index, ranked_row in enumerate(group["rows"]):
            row = ranked_row["row"]
            stock_code = normalize_stock_code(row.get("SECURITY_CODE"))
            ttm_metrics = _supplement_ttm_metrics(row, stock_code, ttm_fetcher=ttm_fetcher)
            row_specs.append(
                {
                    "cells": [
                        str(idx),
                        group["industry_name"],
                        str(row.get("SECURITY_SHORT_NAME", "")),
                        stock_code,
                        _format_decimal_text(row.get("NEWEST_PRICE", "")),
                        _format_prefixed_percent_text(row, "DIVIDEND_NEWRATIO_HYY"),
                        ttm_metrics["pe_ttm_text"],
                        _format_decimal_text(row.get("PB", "")),
                        _supplement_roe_text_by_key(row, roe_column.get("key", "")),
                        ttm_metrics["ttm_text"],
                        format_linked_bonds_html_from_items(
                            stock_to_bonds_map.get(stock_code, []),
                            linked_bonds_fetch_failed=linked_bonds_fetch_failed,
                        ),
                    ],
                    "row_style": first_row_style if row_index == 0 else base_style,
                }
            )
            idx += 1
    summary_lines = [
        "补充区块,与集思录高股息主表分开展示",
        "本地展示:三级行业分组;行业与组内按股息率降序",
        f"本地二次过滤:行业排除名单 + PE-TTM <= {DIVIDEND_SUPPLEMENT_PE_TTM_MAX:g}(空值保留)",
        f"共 {len(groups)} 个三级行业,合计 {len(rows)} 只;组内按股息率降序",
        f"本地二次过滤剔除 {len(excluded_rows)} 只:"
        f"行业排除 {exclusion_summary['industry_excluded_count']} 只;"
        f"PE-TTM 排除 {exclusion_summary['pe_ttm_excluded_count']} 只",
    ]
    if ttm_missing_count:
        summary_lines.append(
            f"巨潮TTM部分缺失：{ttm_missing_count}只，已保留股票但TTM/PE-TTM为空"
        )
    summary_lines.append(
        "口径:国企;股息率/PB/ROE取东财字段;PE与净利润取本地TTM口径;关联转债复用本地映射"
    )
    return {
        "title": build_dividend_email_supplement_title(xc_id),
        "summary_lines": summary_lines,
        "headers": build_dividend_email_supplement_headers(roe_column.get("header", "ROE")),
        "rows": row_specs,
        "xc_id": xc_id,
        "condition_text": result.get("condition_text", ""),
        "excluded_rows": excluded_rows,
    }


def fetch_dividend_email_supplement(
    xc_id=DIVIDEND_EMAIL_SUPPLEMENT_XCID,
    stock_to_bonds_map=None,
    linked_bonds_fetch_failed=False,
    ttm_fetcher=fetch_cached_or_live_ttm_parent_net_profit,
):
    """拉取东财条件结果并组装补充池数据;xcid 为空则返回 None(不展示)。"""
    if not xc_id:
        return None
    result = fetch_all_results_by_xcid(xc_id, page_size=100)
    return build_dividend_email_supplement(
        result,
        stock_to_bonds_map=stock_to_bonds_map,
        linked_bonds_fetch_failed=linked_bonds_fetch_failed,
        ttm_fetcher=ttm_fetcher,
    )


def build_dividend_email_supplement_failed_alert_text(xc_id, error):
    xc_id = str(xc_id or "").strip() or DIVIDEND_EMAIL_SUPPLEMENT_XCID
    return "\n".join(
        [
            "⚠️ 高股息日报:东财条件补充池获取失败",
            f"xcid: {xc_id}",
            f"错误信息: {error}",
            "处理结果: 主表继续发送,补充区块显示失败提示",
        ]
    )


def build_dividend_email_supplement_html(data):
    """渲染补充池 HTML 片段列表(标题块 + 表格);失败/缺失返回对应提示或空列表。

    供 render.py 在主表之前拼接(supplement_parts 先于集思录主表)。
    """
    supplement = data.get("email_supplement")
    error_text = str(data.get("email_supplement_error") or "").strip()
    if error_text:
        return [render_markdown(f"**{DIVIDEND_EMAIL_SUPPLEMENT_TITLE}**\n> {error_text}")]
    if not supplement:
        return []

    title_lines = [f"**{supplement['title']}**"]
    for line in supplement.get("summary_lines", []):
        title_lines.append(f"> {line}")
    if supplement.get("xc_id"):
        title_lines.append(f"> xcid: {supplement['xc_id']}")

    return [
        render_markdown("\n".join(title_lines)),
        render_table(
            supplement.get("headers", build_dividend_email_supplement_headers()),
            supplement.get("rows", []),
            column_styles=DIVIDEND_SUPPLEMENT_EMAIL_COLUMN_STYLES,
        ),
    ]
