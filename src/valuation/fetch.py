"""市场估值板块数据层:指数估值/股息率/分位/EOD/国债/汇率/PE 历史 + 风格轮动指数行情。

移植自 monitor_drawdown.py(仅取数 + 解析 + 归档回退,不含渲染):

- URL 构造 + 符号解析: ``build_index_*_url`` / ``extract_index_digits`` /
  ``build_tickflow_index_symbols`` / ``build_em_index_symbols`` / ``build_numeric_index_symbols``
  (L140-227、L455-480)
- 通用 JSON 取数: ``fetch_json_response`` (L501)
- 归档读取/新鲜度/元信息: ``load_archive_records`` / ``is_archive_fresh`` /
  ``_get_latest_record_date`` / ``_build_archive_meta`` / ``_combine_archive_meta`` (L257-322)
- 指数 detail / dividend_yield / eod_price / valuation_percentile 解析+取数+归档回退
  (L574-831)
- ``resolve_target_index_code`` (L834);``fetch_target_index_metrics`` /
  ``fetch_target_index_dividend_yield`` (L1099-1134)
- 10Y 国债(收益率/历史/归档回退) (L848-905);汇率(归档回退) (L908-960);
  PE 历史(归档回退) (L963-1001)
- ``fetch_index_data`` 风格轮动用指数日线(tickflow + akshare 多源) (L1190-1264) +
  tickflow 客户端/klines (L343-408)

迁移要点:
- ``run_with_retry`` -> ``alerts.run_with_retry``;``ARCHIVE_ROOT`` -> ``storage.ARCHIVE_DIR``。
- 归档读取改用 ``storage.load_existing_records``(新仓库 {source,...,records} 格式,与旧
  data_archive 文件兼容),数据集特定文件名(bond_10y/china_10y.json、fx/usd_cnh.json、
  其余 {index_code}.json)由 ``_archive_path`` 保留。
- ``parse_float`` / ``parse_optional_date`` 自 ``metrics`` 导入(本板块共享)。

退役:旧回撤监控的 jisilu 指数 ETF 补丁(``patch_index_dataframe_with_jisilu`` 等)、drawdown
计算;归档 HTML/文本后缀(``_archive_html_suffix``/``_archive_suffix``)归 ``render.py``;
``compute_equity_bond_spread_percentiles``/``attach_equity_bond_*``/``get_index_valuation_metric``
已在 ``metrics.py``。
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd
import requests

from ..common import alerts, env, storage
from .metrics import parse_float, parse_optional_date

try:  # 可选数据源,未安装时降级 akshare
    from tickflow import TickFlow  # type: ignore
except ImportError:  # pragma: no cover
    TickFlow = None  # type: ignore


# ---------- 常量 ----------

BEIJING_TZ = timezone(timedelta(hours=8))

DEFAULT_TICKFLOW_FREE_BASE_URL = "https://free-api.tickflow.org"
DEFAULT_TICKFLOW_DAILY_COUNT = 500
DEFAULT_INDEX_DETAIL_URL_TEMPLATE = "https://www.etf.com.cn/api/etf-api-service/index/detail?indexCode={index_code}"
DEFAULT_INDEX_DIVIDEND_YIELD_URL_TEMPLATE = (
    "https://cdn.efunds.com.cn/etf-net/index_dividend_ratio_{index_code}.json"
)
DEFAULT_INDEX_EOD_PRICE_URL_TEMPLATE = "https://cdn.efunds.com.cn/etf-net/index_eod_price_{index_code}.json"
# 备用当日行情源（尚未接入）：
# https://www.etf.com.cn/api/etf-api-service/index-quotes/quote?symbol={index_code}&lastTimestamp=
# 用于获取指数当日价格与涨跌，不作为 EOD 归档或估值计算的数据源。
DEFAULT_INDEX_VALUATION_PERCENTILE_URL_TEMPLATE = (
    "https://cdn.efunds.com.cn/etf-net/index_valuation_percentile_{index_code}.json"
)
STYLE_ROTATION_SPECIAL_INDEX_CODES = {"399376", "399373"}

_CN_10Y_BOND_YIELD_COL = "中国国债收益率10年"

INDEX_VALUATION_METRIC_FIELDS: Dict[str, Dict[str, Any]] = {
    "PE(TTM)": {
        "current": "pETtm",
        "percentiles": {
            "3M": "pETtm3M",
            "6M": "pETtm6M",
            "1Y": "pETtm1Y",
            "2Y": "pETtm2Y",
            "3Y": "pETtm3Y",
            "5Y": "pETtm5Y",
            "10Y": "pETtm10Y",
            "今年以来": "pETtmTY",
            "成立以来": "pETtmBgn",
        },
    },
    "PB(LF)": {
        "current": "pBLf",
        "percentiles": {
            "3M": "pBLf3M",
            "6M": "pBLf6M",
            "1Y": "pBLf1Y",
            "2Y": "pBLf2Y",
            "3Y": "pBLf3Y",
            "5Y": "pBLf5Y",
            "10Y": "pBLf10Y",
            "今年以来": "pBLfTY",
            "成立以来": "pBLfBgn",
        },
    },
    "PS(TTM)": {
        "current": "pSTtm",
        "percentiles": {
            "3M": "pSTtm3M",
            "6M": "pSTtm6M",
            "1Y": "pSTtm1Y",
            "2Y": "pSTtm2Y",
            "3Y": "pSTtm3Y",
            "5Y": "pSTtm5Y",
            "10Y": "pSTtm10Y",
            "今年以来": "pSTtmTY",
            "成立以来": "pSTtmBgn",
        },
    },
}


def now_in_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


# ---------- 符号 / DataFrame 小工具 ----------


def dedupe_keep_order(items: List[str]) -> List[str]:
    seen: set = set()
    deduped: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def extract_index_digits(code: str) -> str:
    raw = code.strip().lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def build_tickflow_index_symbols(code: str) -> List[str]:
    raw = code.strip().lower()
    digits = extract_index_digits(raw)
    has_sz_hint = raw.startswith("sz") or raw.endswith(".sz")
    has_sh_hint = raw.startswith("sh") or raw.endswith(".sh")

    candidates: List[str] = []

    if raw.endswith(".sz") and len(digits) == 6:
        candidates.append(f"{digits}.SZ")
    if raw.endswith(".sh") and len(digits) == 6:
        candidates.append(f"{digits}.SH")

    if len(digits) == 6:
        if has_sz_hint:
            candidates.extend([f"{digits}.SZ", f"{digits}.SH"])
        elif has_sh_hint:
            candidates.extend([f"{digits}.SH", f"{digits}.SZ"])
        elif digits.startswith(("39", "98")):
            candidates.extend([f"{digits}.SZ", f"{digits}.SH"])
        else:
            candidates.extend([f"{digits}.SH", f"{digits}.SZ"])

    return dedupe_keep_order(candidates)


def normalize_index_symbol_for_em(code: str) -> str:
    code = code.strip().lower()
    if code.startswith(("csi", "sh", "sz")):
        return code
    if len(code) == 6 and code.isdigit():
        return f"csi{code}"
    return code


def build_em_index_symbols(code: str) -> List[str]:
    raw = code.strip().lower()
    digits = extract_index_digits(raw)

    has_sz_hint = raw.startswith("sz") or raw.endswith(".sz")
    has_sh_hint = raw.startswith("sh") or raw.endswith(".sh")
    has_csi_hint = raw.startswith("csi")

    candidates: List[str] = []

    # 显式带交易所后缀时优先转换为 EM 支持格式,如 980081.sz -> sz980081
    if raw.endswith(".sz") and len(digits) == 6:
        candidates.append(f"sz{digits}")
    if raw.endswith(".sh") and len(digits) == 6:
        candidates.append(f"sh{digits}")

    if raw and "." not in raw:
        candidates.append(raw)

    if len(digits) == 6:
        if has_sz_hint:
            candidates.extend([f"sz{digits}", digits, f"sh{digits}", f"csi{digits}"])
        elif has_sh_hint:
            candidates.extend([f"sh{digits}", digits, f"sz{digits}", f"csi{digits}"])
        elif has_csi_hint:
            candidates.extend([f"csi{digits}", digits, f"sz{digits}", f"sh{digits}"])
        else:
            # 无前缀时根据常见规则给优先级
            if digits.startswith(("39", "98")):
                candidates.extend([f"sz{digits}", f"csi{digits}", f"sh{digits}", digits])
            elif digits.startswith(("93",)):
                candidates.extend([f"csi{digits}", f"sz{digits}", f"sh{digits}", digits])
            elif digits.startswith(("00", "88", "99")):
                candidates.extend([f"sh{digits}", f"csi{digits}", f"sz{digits}", digits])
            else:
                candidates.extend([f"csi{digits}", f"sz{digits}", f"sh{digits}", digits])

    return dedupe_keep_order(candidates)


def build_numeric_index_symbols(code: str) -> List[str]:
    raw = code.strip()
    digits = extract_index_digits(raw)
    candidates = [digits, raw]
    return dedupe_keep_order([item.strip() for item in candidates])


def is_style_rotation_special_index(code: str) -> bool:
    return extract_index_digits(code) in STYLE_ROTATION_SPECIAL_INDEX_CODES


def _to_tencent_index_symbol(code: str) -> str:
    digits = extract_index_digits(code)
    if digits.startswith("399"):
        return f"sz{digits}"
    return f"sh{digits}"


def clip_dataframe_by_date(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if df.empty:
        return df

    start_ts = pd.to_datetime(start_date, format="%Y%m%d")
    end_ts = pd.to_datetime(end_date, format="%Y%m%d")
    return df[(df["date"] >= start_ts) & (df["date"] <= end_ts)].copy()


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "close"])

    renamed = df.copy()
    renamed.columns = [str(col).strip().lower() for col in renamed.columns]

    date_col_candidates = ["日期", "date", "trade_date", "交易日期", "净值日期"]
    close_col_candidates = [
        "收盘",
        "收盘价",
        "close",
        "close_price",
        "closeprice",
        "单位净值",
        "累计净值",
    ]
    date_col = next((c for c in date_col_candidates if c in renamed.columns), None)
    close_col = next((c for c in close_col_candidates if c in renamed.columns), None)

    if date_col is None or close_col is None:
        raise ValueError(f"无法识别日期/收盘列,现有列: {list(renamed.columns)}")

    out = renamed[[date_col, close_col]].copy()
    out.columns = ["date", "close"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    return out


def build_tickflow_client() -> Optional["TickFlow"]:
    if TickFlow is None:
        return None

    timeout = float(env.get("TICKFLOW_TIMEOUT", "15") or "15")
    api_key = env.get("TICKFLOW_API_KEY")

    if api_key:
        return TickFlow(
            api_key=api_key,
            base_url=env.get("TICKFLOW_BASE_URL") or None,
            timeout=timeout,
        )

    return TickFlow(
        api_key=None,
        base_url=env.get("TICKFLOW_FREE_BASE_URL", DEFAULT_TICKFLOW_FREE_BASE_URL) or None,
        timeout=timeout,
    )


def fetch_tickflow_klines(
    symbols: List[str],
    start_date: str,
    end_date: str,
    daily_count: int = DEFAULT_TICKFLOW_DAILY_COUNT,
) -> pd.DataFrame:
    client = build_tickflow_client()
    if client is None:
        raise RuntimeError("tickflow 未安装")

    errors: List[str] = []
    try:
        for symbol in symbols:
            try:
                raw = alerts.run_with_retry(
                    "tickflow.klines.get",
                    lambda symbol=symbol: client.klines.get(
                        symbol,
                        period="1d",
                        count=daily_count,
                        adjust="none",
                        as_dataframe=True,
                    ),
                )
                normalized = clip_dataframe_by_date(normalize_dataframe(raw), start_date, end_date)
                if not normalized.empty:
                    return normalized
                errors.append(f"tickflow.klines.get({symbol}): empty result")
                print(f"[WARN] TickFlow 返回空数据,尝试下一个符号: {symbol}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tickflow.klines.get({symbol}): {exc}")
                print(f"[WARN] TickFlow 数据源失败,尝试下一个符号: {symbol} -> {exc}")
    finally:
        client.close()

    raise RuntimeError("; ".join(errors) if errors else "TickFlow 未返回有效数据")


# ---------- 归档读取 / 新鲜度 / 元信息 ----------


def _archive_path(
    dataset_name: str,
    index_code: Optional[str] = None,
    archive_root: Path = storage.ARCHIVE_DIR,
) -> Path:
    if dataset_name == "bond_10y":
        return archive_root / dataset_name / "china_10y.json"
    if dataset_name == "fx":
        return archive_root / dataset_name / "usd_cnh.json"
    resolved_index_code = str(index_code or "").strip()
    if not resolved_index_code:
        raise ValueError(f"dataset {dataset_name} requires index_code")
    return archive_root / dataset_name / f"{resolved_index_code}.json"


def load_archive_records(
    dataset_name: str,
    index_code: Optional[str] = None,
    archive_root: Path = storage.ARCHIVE_DIR,
) -> List[Dict]:
    """读取归档 records 列表(复用 ``storage.load_existing_records``,兼容旧 data_archive 文件)。"""
    return storage.load_existing_records(_archive_path(dataset_name, index_code, archive_root))


def is_archive_fresh(
    latest_date: object,
    max_age_days: int = 7,
    now: Optional[datetime] = None,
) -> bool:
    parsed = parse_optional_date(latest_date)
    if parsed is None:
        return False
    current_date = (now or now_in_beijing()).astimezone(BEIJING_TZ).date()
    return (current_date - parsed.date()).days <= max_age_days


def _get_latest_record_date(records: List[Dict], date_fields: Tuple[str, ...]) -> Optional[str]:
    latest: Optional[pd.Timestamp] = None
    for record in records:
        for field in date_fields:
            parsed = parse_optional_date(record.get(field))
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
    return latest.strftime("%Y-%m-%d") if latest is not None else None


def _build_archive_meta(data_source: str, archive_latest_date: Optional[str]) -> Dict[str, Optional[str]]:
    return {
        "data_source": data_source,
        "archive_latest_date": archive_latest_date if data_source == "archive" else None,
    }


def _combine_archive_meta(*metas: Optional[Dict[str, Optional[str]]]) -> Dict[str, Optional[str]]:
    archive_dates = [
        str(meta.get("archive_latest_date") or "").strip()
        for meta in metas
        if isinstance(meta, dict) and str(meta.get("data_source") or "").strip() == "archive"
    ]
    if archive_dates:
        return {"data_source": "archive", "archive_latest_date": max(archive_dates)}
    return {"data_source": "live", "archive_latest_date": None}


# ---------- 通用 JSON 取数 ----------


def fetch_json_response(name: str, url: str) -> object:
    response = alerts.run_with_retry(name, lambda: requests.get(url, timeout=15))
    response.raise_for_status()
    return response.json()


# ---------- 指数详情 ----------


def build_index_detail_url(index_code: str) -> str:
    digits = extract_index_digits(index_code)
    if not digits:
        raise ValueError(f"无法识别追踪指数代码: {index_code}")
    return DEFAULT_INDEX_DETAIL_URL_TEMPLATE.format(index_code=digits)


def parse_index_detail_response(payload: object, fallback_index_code: str = "") -> Dict:
    if not isinstance(payload, dict):
        raise ValueError("追踪指数详情接口返回格式异常")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("追踪指数详情接口缺少 data")

    return {
        "index_code": str(data.get("trdCode") or fallback_index_code).strip(),
        "index_name": str(data.get("indexName") or "").strip(),
        "index_short_name": str(data.get("indexSht") or "").strip(),
        "index_type": str(data.get("indexType") or "").strip(),
        "index_detail_url": "",
        "index_dividend_yield_url": str(data.get("dividendRatioJson") or "").strip(),
        "index_valuation_percentile_url": str(data.get("valuationPercentileJson") or "").strip(),
    }


def fetch_index_detail(index_code: str, url: str = "") -> Dict:
    source_url = url.strip() if url else build_index_detail_url(index_code)
    result = parse_index_detail_response(
        fetch_json_response("index_detail", source_url), fallback_index_code=index_code
    )
    result["index_detail_url"] = source_url
    return result


# ---------- 指数股息率 ----------


def build_index_dividend_yield_url(index_code: str) -> str:
    digits = extract_index_digits(index_code)
    if not digits:
        raise ValueError(f"无法识别追踪指数代码: {index_code}")
    return DEFAULT_INDEX_DIVIDEND_YIELD_URL_TEMPLATE.format(index_code=digits)


def parse_index_dividend_yield_rows(rows: object, fallback_index_code: str = "") -> Dict:
    if not isinstance(rows, list):
        raise ValueError("追踪指数股息率接口返回格式异常")

    records: List[Dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dividend_yield = parse_float(row.get("dividendYield"))
        trade_date = parse_optional_date(row.get("trdDt"))
        if dividend_yield is None or trade_date is None:
            continue
        records.append({
            "date": pd.Timestamp(trade_date),
            "yield": dividend_yield,
            "code": str(row.get("trdCode") or fallback_index_code).strip(),
        })

    if not records:
        raise ValueError("追踪指数股息率接口未返回有效数据")

    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    latest = df.iloc[-1]
    result: Dict = {
        "index_code": str(latest["code"] or fallback_index_code).strip(),
        "index_dividend_yield": float(latest["yield"]),
        "index_dividend_yield_date": latest["date"].strftime("%Y-%m-%d"),
    }

    latest_date = latest["date"]
    percentiles: Dict[str, float] = {}
    for label, years in [("1Y", 1), ("3Y", 3), ("5Y", 5), ("10Y", 10)]:
        cutoff = latest_date - pd.DateOffset(years=years)
        window = df.loc[df["date"] >= cutoff, "yield"]
        if len(window) >= 20:
            percentiles[label] = round(float((window <= latest["yield"]).mean() * 100), 2)
    if percentiles:
        result["index_dividend_yield_percentiles"] = percentiles

    avg_window = df.loc[df["date"] >= latest_date - pd.DateOffset(years=5), "yield"]
    if not avg_window.empty:
        result["index_dividend_yield_average_5y"] = round(float(avg_window.mean()), 4)

    return result


def fetch_index_dividend_yield(index_code: str, url: str = "") -> Dict:
    source_url = url.strip() if url else build_index_dividend_yield_url(index_code)
    result = parse_index_dividend_yield_rows(
        fetch_json_response("index_dividend_ratio", source_url),
        fallback_index_code=index_code,
    )
    result["index_dividend_yield_source"] = source_url
    return result


def fetch_index_dividend_yield_with_archive_fallback(
    index_code: str,
    url: str = "",
    archive_root: Path = storage.ARCHIVE_DIR,
    now: Optional[datetime] = None,
) -> Dict:
    try:
        result = fetch_index_dividend_yield(index_code, url=url)
        result["data_source"] = "live"
        result["archive_latest_date"] = None
        return result
    except Exception as live_exc:  # noqa: BLE001
        records = load_archive_records("index_dividend_ratio", index_code=index_code, archive_root=archive_root)
        latest_date = _get_latest_record_date(records, ("trdDt", "date"))
        if not latest_date or not is_archive_fresh(latest_date, now=now):
            raise live_exc
        result = parse_index_dividend_yield_rows(records, fallback_index_code=index_code)
        result["index_dividend_yield_source"] = str(archive_root / "index_dividend_ratio" / f"{index_code}.json")
        result["data_source"] = "archive"
        result["archive_latest_date"] = latest_date
        print(f"[WARN] 指数股息率实时接口失败,已回退归档: {index_code} -> {live_exc}")
        return result


# ---------- 指数 EOD 价格 ----------


def build_index_eod_price_url(index_code: str) -> str:
    digits = extract_index_digits(index_code)
    if not digits:
        raise ValueError(f"无法识别追踪指数代码: {index_code}")
    return DEFAULT_INDEX_EOD_PRICE_URL_TEMPLATE.format(index_code=digits)


def parse_index_eod_price_rows(rows: object) -> pd.DataFrame:
    if not isinstance(rows, list):
        raise ValueError("指数 EOD 价格接口返回格式异常")

    records: List[Dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade_date = parse_optional_date(row.get("trdDt"))
        close_price = parse_float(row.get("pxClose"))
        if trade_date is None or close_price is None:
            continue
        records.append({"date": trade_date, "close": close_price})

    if not records:
        raise ValueError("指数 EOD 价格接口未返回有效数据")

    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def fetch_index_eod_price_data(code: str, start_date: str, end_date: str, url: str = "") -> pd.DataFrame:
    source_url = url.strip() if url else build_index_eod_price_url(code)
    rows = fetch_json_response("index_eod_price", source_url)
    normalized = parse_index_eod_price_rows(rows)
    clipped = clip_dataframe_by_date(normalized, start_date, end_date)
    if clipped.empty:
        raise ValueError(f"指数 EOD 价格在区间 {start_date}-{end_date} 内无数据")
    return clipped


# ---------- 指数估值分位 ----------


def build_index_valuation_percentile_url(index_code: str) -> str:
    digits = extract_index_digits(index_code)
    if not digits:
        raise ValueError(f"无法识别追踪指数代码: {index_code}")
    return DEFAULT_INDEX_VALUATION_PERCENTILE_URL_TEMPLATE.format(index_code=digits)


def parse_index_valuation_percentile_rows(rows: object, fallback_index_code: str = "") -> Dict:
    if not isinstance(rows, list):
        raise ValueError("追踪指数估值分位接口返回格式异常")

    latest_row: Optional[Dict] = None
    latest_date: Optional[pd.Timestamp] = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade_date = parse_optional_date(row.get("trdDt"))
        if trade_date is None:
            continue
        if latest_date is None or trade_date > latest_date:
            latest_row = row
            latest_date = trade_date

    if latest_row is None or latest_date is None:
        raise ValueError("追踪指数估值分位接口未返回有效数据")

    metrics: Dict[str, Dict] = {}
    for metric_name, fields in INDEX_VALUATION_METRIC_FIELDS.items():
        current = parse_float(latest_row.get(fields["current"]))
        percentiles = {
            label: parse_float(latest_row.get(source_field))
            for label, source_field in fields["percentiles"].items()
        }
        if current is not None or any(value is not None for value in percentiles.values()):
            metrics[metric_name] = {
                "current": current,
                "percentiles": percentiles,
            }

    if not metrics:
        raise ValueError("追踪指数估值分位接口未返回有效估值字段")

    return {
        "index_code": str(latest_row.get("trdCode") or fallback_index_code).strip(),
        "index_valuation_date": latest_date.strftime("%Y-%m-%d"),
        "index_valuation_metrics": metrics,
    }


def fetch_index_valuation_percentile(index_code: str, url: str = "") -> Dict:
    source_url = url.strip() if url else build_index_valuation_percentile_url(index_code)
    result = parse_index_valuation_percentile_rows(
        fetch_json_response("index_valuation_percentile", source_url),
        fallback_index_code=index_code,
    )
    result["index_valuation_percentile_source"] = source_url
    return result


def fetch_index_valuation_percentile_with_archive_fallback(
    index_code: str,
    url: str = "",
    archive_root: Path = storage.ARCHIVE_DIR,
    now: Optional[datetime] = None,
) -> Dict:
    try:
        result = fetch_index_valuation_percentile(index_code, url=url)
        result["data_source"] = "live"
        result["archive_latest_date"] = None
        return result
    except Exception as live_exc:  # noqa: BLE001
        records = load_archive_records(
            "index_valuation_percentile", index_code=index_code, archive_root=archive_root
        )
        latest_date = _get_latest_record_date(records, ("trdDt", "date"))
        if not latest_date or not is_archive_fresh(latest_date, now=now):
            raise live_exc
        result = parse_index_valuation_percentile_rows(records, fallback_index_code=index_code)
        result["index_valuation_percentile_source"] = str(
            archive_root / "index_valuation_percentile" / f"{index_code}.json"
        )
        result["data_source"] = "archive"
        result["archive_latest_date"] = latest_date
        print(f"[WARN] 指数估值分位实时接口失败,已回退归档: {index_code} -> {live_exc}")
        return result


def resolve_target_index_code(target: Dict) -> str:
    target_type = str(target.get("type", "")).strip().lower()
    index_code = str(target.get("tracking_index_code") or target.get("index_code") or "").strip()
    code = str(target.get("code") or "").strip()
    if index_code:
        return index_code
    detail_url = str(target.get("index_detail_url") or "").strip()
    match = re.search(r"(?:[?&])indexCode=(\d+)", detail_url, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    if target_type == "index":
        return code
    return ""


# ---------- 10Y 国债 ----------


_CHINABOND_CURVE_NAME = "中债国债收益率曲线"

# 模块级缓存:按天 key 缓存中债网当日备份结果,run.py 的 yield/history 两次调用共享一次请求。
# 进程级,run 结束即清(不跨 run 残留);失败不缓存,保证同进程可重试。
_CHINABOND_CACHE: Dict[str, Tuple[Optional[str], Optional[float]]] = {}


def _bond_date_str(value: Any) -> Optional[str]:
    """归一化国债日期为 'YYYY-MM-DD' 字符串;无法解析返回 None。"""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return ts.strftime("%Y-%m-%d") if pd.notna(ts) else None


def fetch_chinabond_10y_latest(
    lookback_days: int = 15,
    now: Optional[datetime] = None,
) -> Tuple[Optional[str], Optional[float]]:
    """中债网(chinabond)10Y 国债当日备份源。

    返回最近一条 ``(date_str 'YYYY-MM-DD', yield_pct)``;接口失败或无数据返回 ``(None, None)``。
    仅作为东方财富滞后时的当日补充,不写入归档。
    """
    try:
        moment = now or datetime.now(BEIJING_TZ)
        key = moment.strftime("%Y-%m-%d")
        if key in _CHINABOND_CACHE:
            return _CHINABOND_CACHE[key]
        end = moment.strftime("%Y%m%d")
        start = (moment - timedelta(days=lookback_days)).strftime("%Y%m%d")
        df = ak.bond_china_yield(start_date=start, end_date=end)
        if getattr(df, "empty", True):
            return None, None
        if "曲线名称" in df.columns:
            df = df[df["曲线名称"] == _CHINABOND_CURVE_NAME]
        if df.empty or "10年" not in df.columns or "日期" not in df.columns:
            return None, None
        df = df[["日期", "10年"]].dropna().sort_values("日期")
        if df.empty:
            return None, None
        last = df.iloc[-1]
        result = (_bond_date_str(last["日期"]), float(last["10年"]))
        # 仅请求成功(日期、收益率均非 None)才缓存;失败不缓存以便同进程重试
        if result[0] is not None and result[1] is not None:
            _CHINABOND_CACHE[key] = result
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 中债网10Y国债备份获取失败: {exc}")
        return None, None


def fetch_cn_10y_bond_yield(now: Optional[datetime] = None) -> Optional[float]:
    """东方财富 10Y 国债收益率;当其最新日期早于今天时,用中债网当日备份补足。"""
    moment = now or datetime.now(BEIJING_TZ)
    today = moment.strftime("%Y-%m-%d")
    em_date: Optional[str] = None
    em_yield: Optional[float] = None
    try:
        start = (moment - timedelta(days=30)).strftime("%Y%m%d")
        df = ak.bond_zh_us_rate(start_date=start)
        if _CN_10Y_BOND_YIELD_COL in df.columns and "日期" in df.columns:
            # 同行对齐 dropna + 按日期排序,避免"日期列有今天、10Y 列还是 NaN(部分发布)"
            # 时取到昨天的收益率配上今天的日期,从而误判已新鲜、跳过中债网备份。
            clean = df.dropna(subset=[_CN_10Y_BOND_YIELD_COL, "日期"]).sort_values("日期")
            if not clean.empty:
                last = clean.iloc[-1]
                em_yield = float(last[_CN_10Y_BOND_YIELD_COL])
                em_date = _bond_date_str(last["日期"])
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 10年期国债收益率获取失败: {exc}")
    if em_date is not None and em_date >= today:
        return em_yield
    cb_date, cb_yield = fetch_chinabond_10y_latest(now=moment)
    if cb_yield is not None and cb_date is not None and (em_date is None or cb_date > em_date):
        return cb_yield
    return em_yield


def fetch_cn_10y_bond_history(lookback_years: int = 11) -> pd.DataFrame:
    start = (datetime.now(BEIJING_TZ) - timedelta(days=365 * lookback_years)).strftime("%Y%m%d")
    df = ak.bond_zh_us_rate(start_date=start)
    date_col = "日期"
    if _CN_10Y_BOND_YIELD_COL not in df.columns or date_col not in df.columns:
        return pd.DataFrame(columns=["date", "yield_pct"])
    result = df[[date_col, _CN_10Y_BOND_YIELD_COL]].copy()
    result.columns = ["date", "yield_pct"]
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["yield_pct"] = pd.to_numeric(result["yield_pct"], errors="coerce")
    return result.dropna().sort_values("date").reset_index(drop=True)


def _append_chinabond_backup(
    history: pd.DataFrame, now: datetime
) -> Tuple[pd.DataFrame, Optional[str]]:
    """若历史最新日期早于今天,用中债网当日备份补一行 10Y 国债;返回 (历史, 备份日期)。"""
    if history.empty:
        return history, None
    latest_ts = history["date"].iloc[-1]
    if pd.isna(latest_ts):
        return history, None
    latest_str = pd.Timestamp(latest_ts).strftime("%Y-%m-%d")
    if latest_str >= now.strftime("%Y-%m-%d"):
        return history, None
    cb_date, cb_yield = fetch_chinabond_10y_latest(now=now)
    if cb_yield is None or cb_date is None or cb_date <= latest_str:
        return history, None
    new_row = pd.DataFrame({"date": [pd.Timestamp(cb_date)], "yield_pct": [cb_yield]})
    history = pd.concat([history, new_row], ignore_index=True)
    return history.sort_values("date").reset_index(drop=True), cb_date


def fetch_cn_10y_bond_history_with_archive_fallback(
    lookback_years: int = 11,
    archive_root: Path = storage.ARCHIVE_DIR,
    now: Optional[datetime] = None,
) -> Tuple[pd.DataFrame, Dict[str, Optional[str]]]:
    moment = now or datetime.now(BEIJING_TZ)
    try:
        live_df = (
            fetch_cn_10y_bond_history()
            if lookback_years == 11
            else fetch_cn_10y_bond_history(lookback_years=lookback_years)
        )
        result = live_df
        meta: Dict[str, Optional[str]] = {"data_source": "live", "archive_latest_date": None}
    except Exception as live_exc:  # noqa: BLE001
        records = load_archive_records("bond_10y", archive_root=archive_root)
        latest_date = _get_latest_record_date(records, ("日期", "date"))
        if not latest_date or not is_archive_fresh(latest_date, now=moment):
            raise live_exc
        df = pd.DataFrame(records)
        if _CN_10Y_BOND_YIELD_COL not in df.columns or "日期" not in df.columns:
            raise live_exc
        result = df[["日期", _CN_10Y_BOND_YIELD_COL]].copy()
        result.columns = ["date", "yield_pct"]
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        result["yield_pct"] = pd.to_numeric(result["yield_pct"], errors="coerce")
        result = result.dropna().sort_values("date").reset_index(drop=True)
        if result.empty:
            raise live_exc
        print(f"[WARN] 10年期国债历史实时接口失败,已回退归档 -> {live_exc}")
        meta = {"data_source": "archive", "archive_latest_date": latest_date}
    result, backup_date = _append_chinabond_backup(result, moment)
    meta["bond_backup_date"] = backup_date
    return result, meta


# ---------- 汇率 ----------


def fetch_fx_history_with_archive_fallback(
    symbol: str = "USDCNH",
    archive_root: Path = storage.ARCHIVE_DIR,
    now: Optional[datetime] = None,
) -> pd.DataFrame:
    errors: List[str] = []

    # Tier 1: SAFE (国家外汇管理局) - 最稳定,无代理问题
    try:
        raw = alerts.run_with_retry("fx_safe", ak.currency_boc_safe)
        if raw is not None and not getattr(raw, "empty", True):
            df = raw.copy()
            date_col = df.columns[0]
            usd_col = df.columns[1]
            df["日期"] = pd.to_datetime(df[date_col], errors="coerce")
            df["市场价"] = pd.to_numeric(df[usd_col], errors="coerce") / 100
            df["代码"] = symbol
            df["名称"] = symbol
            result = (
                df[["日期", "代码", "名称", "市场价"]].dropna().sort_values("日期").reset_index(drop=True)
            )
            if not result.empty:
                print(f"[INFO] 汇率数据来源: SAFE 中间价 ({len(result)} 条)")
                return result
    except Exception as exc:  # noqa: BLE001
        errors.append(f"SAFE: {exc}")

    # Tier 2: AKShare eastmoney 市场价
    try:
        df = alerts.run_with_retry("fx_usdcnh", lambda: ak.forex_hist_em(symbol=symbol).copy())
        df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        df["市场价"] = pd.to_numeric(df["最新价"], errors="coerce")
        result = df[["日期", "代码", "名称", "市场价"]].dropna().sort_values("日期").reset_index(drop=True)
        if not result.empty:
            print(f"[INFO] 汇率数据来源: eastmoney 市场价 ({len(result)} 条)")
            return result
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney: {exc}")

    # Tier 3: Archive fallback
    try:
        records = load_archive_records("fx", archive_root=archive_root)
        latest_date = _get_latest_record_date(records, ("日期",))
        if latest_date and is_archive_fresh(latest_date, now=now):
            df = pd.DataFrame(records)
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df["市场价"] = pd.to_numeric(df["最新价"], errors="coerce")
            result = df[["日期", "代码", "名称", "市场价"]].dropna().sort_values("日期").reset_index(drop=True)
            if not result.empty:
                print(f"[INFO] 汇率数据来源: archive 归档 ({len(result)} 条, 最新 {latest_date})")
                return result
    except Exception as exc:  # noqa: BLE001
        errors.append(f"archive: {exc}")

    raise RuntimeError("; ".join(errors) if errors else "汇率数据获取失败")


# ---------- 指数 PE 历史 ----------


def fetch_index_pe_history(index_code: str, url: str = "") -> pd.DataFrame:
    url = url or DEFAULT_INDEX_VALUATION_PERCENTILE_URL_TEMPLATE.format(index_code=index_code)
    resp = alerts.run_with_retry("index_pe_history", lambda: requests.get(url, timeout=15))
    resp.raise_for_status()
    records = []
    for row in resp.json():
        dt = str(row.get("trdDt") or "").strip()
        pe = parse_float(row.get("pETtm"))
        if dt and pe is not None and pe > 0:
            records.append({"date": pd.to_datetime(dt), "pe": pe})
    if not records:
        return pd.DataFrame(columns=["date", "pe"])
    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def fetch_index_pe_history_with_archive_fallback(
    index_code: str,
    url: str = "",
    archive_root: Path = storage.ARCHIVE_DIR,
    now: Optional[datetime] = None,
) -> Tuple[pd.DataFrame, Dict[str, Optional[str]]]:
    try:
        return (
            fetch_index_pe_history(index_code, url=url),
            {"data_source": "live", "archive_latest_date": None},
        )
    except Exception as live_exc:  # noqa: BLE001
        records = load_archive_records(
            "index_valuation_percentile", index_code=index_code, archive_root=archive_root
        )
        latest_date = _get_latest_record_date(records, ("trdDt", "date"))
        if not latest_date or not is_archive_fresh(latest_date, now=now):
            raise live_exc
        pe_records = []
        for row in records:
            trade_date = parse_optional_date(row.get("trdDt"))
            pe = parse_float(row.get("pETtm"))
            if trade_date is not None and pe is not None and pe > 0:
                pe_records.append({"date": trade_date, "pe": pe})
        pe_df = (
            pd.DataFrame(pe_records).sort_values("date").reset_index(drop=True)
            if pe_records
            else pd.DataFrame(columns=["date", "pe"])
        )
        if pe_df.empty:
            raise live_exc
        print(f"[WARN] 指数PE历史实时接口失败,已回退归档: {index_code} -> {live_exc}")
        return pe_df, {"data_source": "archive", "archive_latest_date": latest_date}


# ---------- 目标估值指标聚合 ----------


def fetch_target_index_metrics(target: Dict) -> Optional[Dict]:
    index_code = resolve_target_index_code(target)
    detail_url = str(target.get("index_detail_url") or "").strip()
    dividend_url = str(target.get("index_dividend_yield_url") or "").strip()
    valuation_url = str(target.get("index_valuation_percentile_url") or "").strip()
    if not index_code and not detail_url and not dividend_url and not valuation_url:
        return None

    result: Dict = {}
    if index_code or detail_url:
        try:
            detail = fetch_index_detail(index_code, url=detail_url)
            result.update({key: value for key, value in detail.items() if value not in {"", None}})
            index_code = str(result.get("index_code") or index_code).strip()
        except Exception as exc:  # noqa: BLE001
            # detail 接口失败不中止整个标的:保留已有 index_code,继续走股息率/分位归档回退
            print(f"[WARN] 指数 detail 接口失败,改走股息率/分位归档回退: {index_code} -> {exc}")

    dividend_url = dividend_url or str(result.get("index_dividend_yield_url") or "").strip()
    if index_code or dividend_url:
        dividend_result = fetch_index_dividend_yield_with_archive_fallback(index_code, url=dividend_url)
        result.update(dividend_result)
        result["index_dividend_yield_data_source"] = dividend_result.get("data_source")
        result["index_dividend_yield_archive_latest_date"] = dividend_result.get("archive_latest_date")

    valuation_url = valuation_url or str(result.get("index_valuation_percentile_url") or "").strip()
    if index_code or valuation_url:
        valuation_result = fetch_index_valuation_percentile_with_archive_fallback(index_code, url=valuation_url)
        result.update(valuation_result)
        result["index_valuation_data_source"] = valuation_result.get("data_source")
        result["index_valuation_archive_latest_date"] = valuation_result.get("archive_latest_date")

    return result or None


def fetch_target_index_dividend_yield(target: Dict) -> Optional[Dict]:
    metrics = fetch_target_index_metrics(target)
    if not metrics or "index_dividend_yield" not in metrics:
        return None
    return metrics


# ---------- 风格轮动:指数日线(多源) ----------


def fetch_style_rotation_special_index_history(
    code: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    raw = alerts.run_with_retry(
        "stock_zh_a_hist_tx",
        lambda: ak.stock_zh_a_hist_tx(
            symbol=_to_tencent_index_symbol(code),
            start_date=f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}",
            end_date=f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}",
            adjust="",
        ),
    )
    normalized = normalize_dataframe(raw)
    return clip_dataframe_by_date(normalized, start_date, end_date)


def _load_index_eod_archive_frame(
    code: str,
    start_date: str,
    end_date: str,
    archive_root: Path = storage.ARCHIVE_DIR,
) -> pd.DataFrame:
    records = load_archive_records("index_eod", index_code=extract_index_digits(code), archive_root=archive_root)
    if not records:
        return pd.DataFrame(columns=["date", "close"])

    frame = pd.DataFrame(
        [
            {"date": row.get("trdDt"), "close": row.get("pxClose")}
            for row in records
            if row.get("trdDt") is not None
        ]
    )
    if frame.empty:
        return pd.DataFrame(columns=["date", "close"])
    normalized = normalize_dataframe(frame)
    return clip_dataframe_by_date(normalized, start_date, end_date)


def fetch_index_data(
    code: str,
    start_date: str,
    end_date: str,
    tickflow_daily_count: int = DEFAULT_TICKFLOW_DAILY_COUNT,
    archive_root: Path = storage.ARCHIVE_DIR,
) -> pd.DataFrame:
    if is_style_rotation_special_index(code):
        archived = _load_index_eod_archive_frame(code, start_date, end_date, archive_root=archive_root)
        if not archived.empty:
            print(f"[INFO] 风格指数数据来源: archive ({extract_index_digits(code)})")
            return archived

        live = fetch_style_rotation_special_index_history(code, start_date, end_date)
        if not live.empty:
            print(f"[INFO] 风格指数数据来源: tencent live ({extract_index_digits(code)})")
            return live

    errors: List[str] = []

    tickflow_symbols = build_tickflow_index_symbols(code)
    if tickflow_symbols:
        try:
            return fetch_tickflow_klines(
                tickflow_symbols,
                start_date,
                end_date,
                daily_count=tickflow_daily_count,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tickflow({tickflow_symbols}): {exc}")
            print(f"[WARN] 指数 TickFlow 数据源失败,准备尝试 AkShare: {exc}")

    # 主接口: 东方财富指数日线(对 csi930955 实测更稳定)
    if hasattr(ak, "stock_zh_index_daily_em"):
        for symbol_em in build_em_index_symbols(code):
            try:
                raw = alerts.run_with_retry(
                    "stock_zh_index_daily_em",
                    lambda: ak.stock_zh_index_daily_em(symbol=symbol_em, start_date=start_date, end_date=end_date),
                )
                normalized = normalize_dataframe(raw)
                if not normalized.empty:
                    return normalized
                errors.append(f"stock_zh_index_daily_em({symbol_em}): empty result")
                print(f"[WARN] 指数主数据源返回空数据,尝试下一个符号: {symbol_em}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"stock_zh_index_daily_em({symbol_em}): {exc}")
                print(f"[WARN] 指数主数据源失败,尝试下一个符号: {symbol_em} -> {exc}")

    # 备源1: 东方财富另一接口
    if hasattr(ak, "index_zh_a_hist"):
        for symbol_hist in build_numeric_index_symbols(code):
            try:
                raw = alerts.run_with_retry(
                    "index_zh_a_hist",
                    lambda: ak.index_zh_a_hist(
                        symbol=symbol_hist, period="daily", start_date=start_date, end_date=end_date
                    ),
                )
                normalized = normalize_dataframe(raw)
                if not normalized.empty:
                    return normalized
                errors.append(f"index_zh_a_hist({symbol_hist}): empty result")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"index_zh_a_hist({symbol_hist}): {exc}")
                print(f"[WARN] 指数备源1失败,准备尝试备源2: index_zh_a_hist({symbol_hist}) -> {exc}")

    # 备源2: 中证官网指数历史
    if hasattr(ak, "stock_zh_index_hist_csindex"):
        for symbol_csindex in build_numeric_index_symbols(code):
            try:
                raw = alerts.run_with_retry(
                    "stock_zh_index_hist_csindex",
                    lambda: ak.stock_zh_index_hist_csindex(
                        symbol=symbol_csindex, start_date=start_date, end_date=end_date
                    ),
                )
                normalized = normalize_dataframe(raw)
                if not normalized.empty:
                    return normalized
                errors.append(f"stock_zh_index_hist_csindex({symbol_csindex}): empty result")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"stock_zh_index_hist_csindex({symbol_csindex}): {exc}")

    error_message = "; ".join(errors) if errors else "未找到可用指数接口"
    raise RuntimeError(f"指数数据获取失败: {error_message}")
