"""董秘互动问答查询:深交所互动易(cninfo)+ 上证 e 互动(SSE)。

移植自 jisilu_ggx/irm_query.py:零集思录耦合,按正股代码前缀路由(00/30→深交所,
60→上证)。改动:``datetime.now()`` 统一改 ``now_in_beijing()`` 以适配 UTC CI;
三处 POST 包 ``common.alerts.run_with_retry`` 应对瞬时网络错误,保留 ``query_irm``
外层 try/except(单只正股查询失败不影响整体)。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from typing import Any, Dict, List

import requests

from ...common import alerts


BEIJING_TZ = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def now_in_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def seven_days_ago() -> datetime:
    return now_in_beijing() - timedelta(days=7)


# ── 深交所互动易 (cninfo) ──────────────────────────────────────

CNINFO_SEARCH_URL = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"
CNINFO_QA_URL = "https://irm.cninfo.com.cn/newircs/company/question"


def _query_cninfo(stock_name: str) -> List[Dict[str, Any]]:
    """查询深交所互动易问答。"""
    # Step 1: 搜索获取 stockCode + secid
    ts = int(time.time() * 1000)

    def _search() -> Dict[str, Any]:
        resp = requests.post(
            f"{CNINFO_SEARCH_URL}?_t={ts}",
            data={"keyWord": stock_name},
            headers={**HEADERS, "Origin": "https://irm.cninfo.com.cn"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    info = alerts.run_with_retry("irm_cninfo_search", _search)
    candidates = info.get("data") or []
    if not candidates:
        return []

    # 取第一个股票类型的结果
    match = next((c for c in candidates if c.get("stockType") == "S"), candidates[0])
    stock_code = match["stockCode"]
    org_id = match["secid"]

    # Step 2: 获取问答列表
    def _qa() -> Dict[str, Any]:
        resp = requests.post(
            CNINFO_QA_URL,
            data={
                "_t": int(time.time() * 1000),
                "stockcode": stock_code,
                "orgId": org_id,
                "pageSize": 20,
                "pageNum": 1,
                "keyWord": "",
                "startDay": "",
                "endDay": "",
            },
            headers={**HEADERS, "Origin": "https://irm.cninfo.com.cn"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    data = alerts.run_with_retry("irm_cninfo_qa", _qa)

    cutoff = seven_days_ago()
    results: List[Dict[str, Any]] = []
    for row in data.get("rows") or []:
        # contentType==11 表示已回复
        if row.get("contentType") != 11:
            continue
        if not row.get("attachedContent"):
            continue
        pub_ts = row.get("pubDate") or row.get("updateDate") or 0
        pub_dt = datetime.fromtimestamp(pub_ts / 1000, tz=BEIJING_TZ)
        if pub_dt < cutoff:
            continue
        index_id = row.get("indexId")
        url = (
            f"https://irm.cninfo.com.cn/ircs/question/questionDetail?questionId={index_id}"
            if index_id else ""
        )
        results.append({
            "question": row["mainContent"].strip(),
            "answer": row["attachedContent"].strip(),
            "date": pub_dt.strftime("%Y-%m-%d"),
            "url": url,
        })
    return results


# ── 上证 e 互动 (SSE) ────────────────────────────────────────────

SSE_URL = "https://sns.sseinfo.com/qasearchFullText.do"


def _parse_sse_date(text: str) -> Any:
    """解析上证 e 互动的日期文本,如 '2026年03月04日 14:08' 或 '昨天 15:36'。"""
    text = text.strip()
    m = re.match(r"(\d{4})年(\d{2})月(\d{2})日", text)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=BEIJING_TZ)
    today = now_in_beijing()
    if text.startswith("昨天"):
        return today - timedelta(days=1)
    if text.startswith("前天"):
        return today - timedelta(days=2)
    if text.startswith("今天") or re.match(r"\d{1,2}:\d{2}", text):
        return today
    return None


def _query_sse(stock_name: str) -> List[Dict[str, Any]]:
    """查询上证 e 互动问答。"""
    today = now_in_beijing()
    sdate = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    edate = today.strftime("%Y-%m-%d")

    def _post() -> str:
        resp = requests.post(
            SSE_URL,
            data={
                "page": 1,
                "keyword": stock_name,
                "sdate": sdate,
                "edate": edate,
            },
            headers={
                **HEADERS,
                "Origin": "https://sns.sseinfo.com",
                "Referer": f"https://sns.sseinfo.com/searchFullText.do?keyword={quote(stock_name)}",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.text

    html = alerts.run_with_retry("irm_sse_qa", _post)

    # 解析 HTML:按 m_feed_item 分块
    cutoff = seven_days_ago()
    results: List[Dict[str, Any]] = []
    items = re.split(r'<div[^>]*class="[^"]*m_feed_item[^"]*"', html)
    for item in items:
        if "answer_ico" not in item:
            continue
        # 提取所有 m_feed_txt 的内容(问+答各一个)
        texts = re.findall(
            r'<div[^>]*class="[^"]*m_feed_txt[^"]*"[^>]*>\s*(.*?)\s*</div>',
            item, re.DOTALL,
        )
        if len(texts) < 2:
            continue
        question = re.sub(r"<[^>]+>", "", texts[0]).strip().lstrip(":")
        answer = re.sub(r"<[^>]+>", "", texts[1]).strip()
        if not question or not answer:
            continue
        # 提取回复日期(取最后一个 m_feed_from 中的 span)
        date_spans = re.findall(
            r'<div[^>]*class="[^"]*m_feed_from[^"]*"[^>]*>.*?<span[^>]*>(.*?)</span>',
            item, re.DOTALL,
        )
        pub_dt = None
        if date_spans:
            # 用最后一个日期(回复日期)
            pub_dt = _parse_sse_date(date_spans[-1])
        if pub_dt and pub_dt < cutoff:
            continue
        results.append({
            "question": question,
            "answer": answer,
            "date": pub_dt.strftime("%Y-%m-%d") if pub_dt else "未知",
            "url": "",
        })
    return results


# ── 统一入口 ───────────────────────────────────────────────────

def query_irm(stock_name: str, stock_code: str) -> List[Dict[str, Any]]:
    """查询董秘互动问答,根据股票代码自动选择平台。

    Returns: ``[{question, answer, date, url}, ...]``
    """
    try:
        code = str(stock_code).zfill(6)
        if code.startswith(("00", "30")):
            return _query_cninfo(stock_name)
        if code.startswith(("60", "68")):  # 60 沪市主板 / 68 科创板(688/689),均走上证 e 互动
            return _query_sse(stock_name)
        return []
    except Exception as e:  # noqa: BLE001
        print(f"[irm_query] {stock_name}({stock_code}) 查询失败: {e}")
        return []


def collect_irm_for_rows(
    rows: List[Dict[str, Any]],
    *,
    max_qas_per_stock: int = 3,
    question_max: int = 80,
    answer_max: int = 150,
) -> List[Dict[str, Any]]:
    """对筛选出的转债正股去重查询董秘互动,返回每只正股的 QA 摘要。

    移植自 cb_main.build_irm_messages 的查询+摘要部分(去掉 wechat 分页)。
    Returns: ``[{"stock_nm", "stock_id", "qas": [{question, answer, url}]}, ...]``
    """
    seen = set()
    stocks: List[tuple] = []
    for row in rows:
        c = row.get("cell", row)
        stock_id = c.get("stock_id", "")
        if stock_id and stock_id not in seen:
            seen.add(stock_id)
            stocks.append((c.get("stock_nm", ""), stock_id))

    results: List[Dict[str, Any]] = []
    for stock_nm, stock_id in stocks:
        qas = query_irm(stock_nm, stock_id)
        time.sleep(0.5)  # 节流:每只正股 2 POST,最多 50 只,避免触发限流
        if not qas:
            continue
        trimmed = []
        for qa in qas[:max_qas_per_stock]:
            q = qa["question"][:question_max] + ("..." if len(qa["question"]) > question_max else "")
            a = qa["answer"][:answer_max] + ("..." if len(qa["answer"]) > answer_max else "")
            trimmed.append({"question": q, "answer": a, "url": qa.get("url", "")})
        results.append({"stock_nm": stock_nm, "stock_id": stock_id, "qas": trimmed})
    return results
