"""巨潮资讯(cninfo)结构化财报抓取。

移植自 jisilu_ggx/cninfo_finance_probe.py:保留全部数据抓取/解析函数,去掉 CLI ``main``。
``_get_json`` 保持裸 ``requests.get``--重试由上层 ``cninfo_cache`` 的 bundle 级重试承担
(巨潮偶发返回不完整数据,需整体重抓而非单接口重试),避免双重重试。字段字典 json 随模块
同目录(``cninfo_financial_field_dictionary.json``)。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests


DATA20_BASE_URL = "https://www.cninfo.com.cn/data20"
HEAD_STRIP_URL = f"{DATA20_BASE_URL}/companyOverview/getHeadStripData"
COMPANY_INFO_URL = f"{DATA20_BASE_URL}/companyOverview/getCompanyInfo"
FIELD_DICTIONARY_PATH = Path(__file__).with_name("cninfo_financial_field_dictionary.json")
# 巨潮对 python-requests 默认 UA + 数据中心 IP 更敏感,带浏览器 UA 降低软限流概率
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cninfo.com.cn/",
}
PERIOD_SUFFIXES = {
    "year": "-12-31",
    "three": "-09-30",
    "middle": "-06-30",
    "one": "-03-31",
}


@lru_cache(maxsize=1)
def load_field_dictionary() -> dict[str, Any]:
    return json.loads(FIELD_DICTIONARY_PATH.read_text(encoding="utf-8"))


FIELD_DICTIONARY = load_field_dictionary()
HEAD_STRIP_FIELDS = FIELD_DICTIONARY["head_strip"]["fields"]
EXPECTED_HEAD_STRIP_FIELDS = set(HEAD_STRIP_FIELDS)
MAIN_INDICATORS_CONFIG = FIELD_DICTIONARY["main_indicators"]
MAIN_INDICATORS_FIELDS = MAIN_INDICATORS_CONFIG["fields"]
MAIN_INDICATORS_URL = f"{DATA20_BASE_URL}{MAIN_INDICATORS_CONFIG['endpoint_path']}"
STATEMENT_CONFIG = {
    section: {
        **meta,
        "url": f"{DATA20_BASE_URL}{meta['endpoint_path']}",
    }
    for section, meta in FIELD_DICTIONARY["statements"].items()
}


def _get_json(url: str, params: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise RuntimeError(f"CNINFO returned unexpected code for {url}: {payload.get('code')}")
    return payload


def _extract_first_record(payload: dict[str, Any], label: str) -> dict[str, Any]:
    records = (((payload.get("data") or {}).get("records")) or [])
    if not records:
        raise RuntimeError(f"CNINFO returned no {label} records")
    return records[0]


def fetch_company_info_data(stock_code: str, timeout: int = 30) -> dict[str, Any]:
    return _get_json(COMPANY_INFO_URL, {"scode": stock_code}, timeout=timeout)


def fetch_company_info_record(stock_code: str, timeout: int = 30) -> dict[str, Any]:
    payload = fetch_company_info_data(stock_code, timeout=timeout)
    return _extract_first_record(payload, f"company info for {stock_code}")


def fetch_company_sign(stock_code: str, timeout: int = 30) -> Any:
    return fetch_company_info_record(stock_code, timeout=timeout).get("F002N")


def fetch_head_strip_data(stock_code: str, timeout: int = 30) -> dict[str, Any]:
    return _get_json(HEAD_STRIP_URL, {"scode": stock_code}, timeout=timeout)


def fetch_head_strip_record(stock_code: str, timeout: int = 30) -> dict[str, Any]:
    payload = fetch_head_strip_data(stock_code, timeout=timeout)
    return _extract_first_record(payload, f"head-strip for {stock_code}")


def fetch_main_indicators_data(stock_code: str, sign: Any, timeout: int = 30) -> dict[str, Any]:
    return _get_json(MAIN_INDICATORS_URL, {"scode": stock_code, "sign": sign}, timeout=timeout)


def fetch_main_indicators_record(stock_code: str, sign: Any, timeout: int = 30) -> dict[str, Any]:
    payload = fetch_main_indicators_data(stock_code, sign, timeout=timeout)
    return _extract_first_record(payload, f"main indicators for {stock_code}")


def map_head_strip_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        meta["label"]: record.get(field)
        for field, meta in HEAD_STRIP_FIELDS.items()
        if field in record
    }


def build_head_strip(record: dict[str, Any]) -> dict[str, Any]:
    fields = []
    by_label = {}
    for field, meta in HEAD_STRIP_FIELDS.items():
        if field not in record:
            continue
        item = {
            "field": field,
            "label": meta["label"],
            "unit": meta["unit"],
            "value": record[field],
        }
        fields.append(item)
        by_label[meta["label"]] = item
    return {
        "report_date": record.get("ENDDATE"),
        "fields": fields,
        "by_label": by_label,
        "raw": record,
    }


def build_main_indicators(record: dict[str, Any]) -> dict[str, Any]:
    entries = []
    periods: list[str] = []
    for bucket in ("year", "three", "middle", "one"):
        for row in record.get(bucket) or []:
            fields = []
            by_label = {}
            for field, meta in MAIN_INDICATORS_FIELDS.items():
                if field not in row:
                    continue
                item = {
                    "field": field,
                    "label": meta["label"],
                    "unit": meta["unit"],
                    "value": row.get(field),
                }
                fields.append(item)
                by_label[meta["label"]] = item
            report_date = row.get("ENDDATE")
            if report_date:
                periods.append(report_date)
            entries.append(
                {
                    "report_date": report_date,
                    "bucket": bucket,
                    "fields": fields,
                    "by_label": by_label,
                    "raw": row,
                }
            )

    entries.sort(key=lambda item: item.get("report_date") or "", reverse=True)
    return {
        "label": MAIN_INDICATORS_CONFIG["label"],
        "periods": sorted(set(periods), reverse=True),
        "entries": entries,
        "latest_report": entries[0] if entries else None,
        "raw": record,
    }


def fetch_statement_data(stock_code: str, sign: Any, section: str, timeout: int = 30) -> dict[str, Any]:
    if section not in STATEMENT_CONFIG:
        raise KeyError(f"Unknown statement section: {section}")
    config = STATEMENT_CONFIG[section]
    return _get_json(config["url"], {"scode": stock_code, "sign": sign}, timeout=timeout)


def fetch_statement_record(stock_code: str, sign: Any, section: str, timeout: int = 30) -> dict[str, Any]:
    payload = fetch_statement_data(stock_code, sign, section, timeout=timeout)
    return _extract_first_record(payload, f"{section} for {stock_code}")


def _statement_period_key(bucket: str, year_key: str) -> str:
    suffix = PERIOD_SUFFIXES[bucket]
    return f"{year_key}{suffix}"


def build_statement(section: str, record: dict[str, Any]) -> dict[str, Any]:
    config = STATEMENT_CONFIG[section]
    periods: list[str] = []
    rows_by_name: dict[str, dict[str, Any]] = {}

    for bucket in ("year", "three", "middle", "one"):
        for row in record.get(bucket) or []:
            row_name = row.get("index")
            if not row_name:
                continue
            normalized = rows_by_name.setdefault(
                row_name,
                {
                    "name": row_name,
                    "is_section": all(value is None for key, value in row.items() if key != "index"),
                    "values": {},
                },
            )
            for key, value in row.items():
                if key == "index":
                    continue
                period = _statement_period_key(bucket, str(key))
                normalized["values"][period] = value
                periods.append(period)

    unique_periods = sorted(set(periods), reverse=True)
    rows = list(rows_by_name.values())
    return {
        "label": config["label"],
        "unit": config["unit"],
        "cumulative": config["cumulative"],
        "periods": unique_periods,
        "rows": rows,
        "raw": record,
    }


def get_statement_row(statement: dict[str, Any], row_name: str) -> dict[str, Any] | None:
    for row in statement.get("rows", []):
        if row.get("name") == row_name:
            return row
    return None


def compute_ttm_from_cumulative_values(values: dict[str, Any]) -> dict[str, Any]:
    available_periods = sorted(
        period for period, value in values.items()
        if value is not None
    )
    if not available_periods:
        raise ValueError("No available periods for TTM calculation")

    latest_period = available_periods[-1]
    latest_value = values[latest_period]
    month_day = latest_period[5:]
    if month_day == "12-31":
        return {
            "latest_period": latest_period,
            "ttm_value": latest_value,
            "basis": "annual",
            "components": {
                "annual_period": latest_period,
            },
        }

    current_year = int(latest_period[:4])
    prior_annual_period = f"{current_year - 1}-12-31"
    prior_same_period = f"{current_year - 1}-{month_day}"
    if prior_annual_period not in values or values[prior_annual_period] is None:
        raise ValueError(f"Missing annual period for TTM calculation: {prior_annual_period}")
    if prior_same_period not in values or values[prior_same_period] is None:
        raise ValueError(f"Missing same period for TTM calculation: {prior_same_period}")

    ttm_value = values[prior_annual_period] + latest_value - values[prior_same_period]
    return {
        "latest_period": latest_period,
        "ttm_value": ttm_value,
        "basis": "rolling",
        "components": {
            "annual_period": prior_annual_period,
            "current_period": latest_period,
            "prior_same_period": prior_same_period,
        },
    }


def fetch_ttm_income_metric(stock_code: str, row_name: str, timeout: int = 30) -> dict[str, Any]:
    sign = fetch_company_sign(stock_code, timeout=timeout)
    if sign is None:
        raise RuntimeError(f"CNINFO returned no sign for {stock_code}")

    record = fetch_statement_record(stock_code, sign, "income_statement", timeout=timeout)
    statement = build_statement("income_statement", record)
    row = get_statement_row(statement, row_name)
    if row is None:
        raise RuntimeError(f"Income statement row not found for {stock_code}: {row_name}")

    ttm = compute_ttm_from_cumulative_values(row["values"])
    return {
        "stock_code": stock_code,
        "row_name": row_name,
        "unit": statement["unit"],
        "ttm_value_wan": ttm["ttm_value"],
        "ttm_value_yi": round(ttm["ttm_value"] / 10000, 2),
        "latest_period": ttm["latest_period"],
        "basis": ttm["basis"],
        "components": ttm["components"],
    }


def fetch_ttm_parent_net_profit(stock_code: str, timeout: int = 30) -> dict[str, Any]:
    return fetch_ttm_income_metric(stock_code, "归属母公司净利润", timeout=timeout)


def fetch_financial_bundle(stock_code: str, timeout: int = 30) -> dict[str, Any]:
    company_record = fetch_company_info_record(stock_code, timeout=timeout)
    sign = company_record.get("F002N")
    if sign is None:
        raise RuntimeError(f"CNINFO returned no sign for {stock_code}")

    head_record = fetch_head_strip_record(stock_code, timeout=timeout)
    main_indicators_record = fetch_main_indicators_record(stock_code, sign, timeout=timeout)
    bundle = {
        "company": {
            "stock_code": stock_code,
            "sec_name": company_record.get("SECNAME"),
            "org_name": company_record.get("ORGNAME"),
            "org_short_name": company_record.get("F001V"),
            "market_code": company_record.get("F004V"),
            "sign": sign,
        },
        "head_strip": build_head_strip(head_record),
        "main_indicators": build_main_indicators(main_indicators_record),
    }
    for section in STATEMENT_CONFIG:
        statement_record = fetch_statement_record(stock_code, sign, section, timeout=timeout)
        bundle[section] = build_statement(section, statement_record)
    return bundle
