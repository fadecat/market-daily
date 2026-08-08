"""面向普通用户的数据集状态、影响范围和失败文案。"""
from __future__ import annotations

import traceback
from typing import Any, Dict

import requests

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None


DATASET_STATUS: Dict[str, Dict[str, str]] = {
    "index_eod": {
        "label": "指数收盘价",
        "scope": "市场估值历史价格归档与归档校验",
        "timing": "A股收盘后更新",
    },
    "index_dividend_ratio": {
        "label": "指数股息率",
        "scope": "市场估值股息率及其实时失败后的归档回退",
        "timing": "收盘后或次日更新",
    },
    "index_valuation_percentile": {
        "label": "指数估值分位",
        "scope": "市场估值PE/PB分位及PE历史回退",
        "timing": "收盘后或次日更新",
    },
    "bond_10y": {
        "label": "10Y国债",
        "scope": "市场估值股债收益差和股债比值",
        "timing": "交易日更新",
    },
    "fx": {
        "label": "汇率",
        "scope": "市场估值汇率图及汇率归档回退",
        "timing": "实时源可用，归档为行情快照",
    },
    "cb_index": {
        "label": "转债等权指数",
        "scope": "转债行情指数图和三低轮动基准对比",
        "timing": "A股收盘后更新",
    },
    "cninfo": {
        "label": "巨潮财报",
        "scope": "高股息TTM归母净利润和PE-TTM",
        "timing": "财报披露后更新，优先使用本地缓存",
    },
}

_MARKET_VALUATION_DATASETS = {
    "index_eod",
    "index_dividend_ratio",
    "index_valuation_percentile",
    "bond_10y",
    "fx",
}


def dataset_status(dataset: str) -> Dict[str, str]:
    """返回数据集元信息；未知数据集也返回可读的安全默认值。"""
    known = DATASET_STATUS.get(dataset)
    if known:
        return dict(known)
    return {
        "label": dataset or "未知数据",
        "scope": "相关数据展示和归档任务",
        "timing": "按数据源更新",
    }


def build_data_alert_title(
    dataset: str,
    *,
    code: str = "",
    target_name: str = "",
    partial: bool = False,
) -> str:
    status = dataset_status(dataset)
    state = "部分缺失" if partial else "刷新失败"
    if dataset in _MARKET_VALUATION_DATASETS:
        subject = target_name or code or status["label"]
        return f"市场估值数据{state}：{subject}"
    if dataset == "cninfo":
        return f"高股息数据{state}：{status['label']}"
    if dataset == "cb_index":
        return f"转债指数数据{state}"
    return f"{status['label']}数据{state}"


def classify_failure(error: Exception) -> str:
    """把常见底层异常压缩为用户能理解、也便于搜索的类别。"""
    if isinstance(error, requests.exceptions.RequestException):
        return "网络抓取失败"
    if httpx is not None and isinstance(error, httpx.HTTPError):
        return "网络抓取失败"
    text = str(error).lower()
    network_words = (
        "timeout",
        "timed out",
        "connection",
        "proxyerror",
        "remote end closed",
        "max retries exceeded",
        "502",
        "503",
        "504",
        "网络",
        "抓取",
    )
    if any(word in text for word in network_words):
        return "网络抓取失败"
    field_words = (
        "missing",
        "not found",
        "column",
        "field",
        "row",
        "字段",
        "列",
        "归属母公司净利润",
    )
    if any(word in text for word in field_words):
        return "字段解析失败"
    response_words = ("response", "empty", "非列表", "响应", "json", "返回")
    if any(word in text for word in response_words):
        return "返回数据异常"
    if isinstance(error, (OSError, IOError)):
        return "归档写入失败"
    return "程序异常"


def format_data_failure_detail(
    dataset: str,
    *,
    error: Exception,
    code: str = "",
    target_name: str = "",
    action: str = "",
    trace: str = "",
) -> str:
    status = dataset_status(dataset)
    lines = [
        f"影响范围：{status['scope']}",
        f"数据类型：{status['label']}",
        f"数据时效：{status['timing']}",
        f"原因分类：{classify_failure(error)}",
        f"内部任务：{dataset}",
    ]
    if target_name:
        lines.append(f"内部名称：{target_name}")
    if code:
        lines.append(f"内部代码：{code}")
    if action:
        lines.append(f"处理动作：{action}")
    lines.append(f"原始错误：{error}")
    lines.append(f"异常类型：{type(error).__name__}")
    if trace:
        trace_lines = [line.strip() for line in trace.splitlines() if line.strip()]
    else:
        trace_lines = traceback.format_exception_only(type(error), error)
        trace_lines = [line.strip() for line in trace_lines if line.strip()]
    if trace_lines:
        lines.append("简短堆栈：" + " | ".join(trace_lines[-4:]))
    return "\n".join(lines)
