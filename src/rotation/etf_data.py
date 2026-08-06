"""ETF 日线行情数据层(akshare 主,tickflow 可选备源)。

从 monitor_drawdown.fetch_etf_data 抽离,使资产轮动板块不再依赖 117KB 的
monitor_drawdown.py。返回标准化的 {date, close} DataFrame,或 {date_str: close} 映射。

数据源优先级:tickflow(可选,需装 tickflow 包)-> akshare fund_etf_hist_em
-> akshare fund_etf_fund_info_em(净值)。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

from ..common import alerts

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None

try:
    from tickflow import TickFlow
except ImportError:  # pragma: no cover
    TickFlow = None


BEIJING_TZ = timezone(timedelta(hours=8))
DEFAULT_TICKFLOW_DAILY_COUNT = 500
DEFAULT_TICKFLOW_FREE_BASE_URL = "https://free-api.tickflow.org"
EASTMONEY_LOOKBACK_CALENDAR_DAYS = 90


def now_in_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


# ----------------------------- 标准化与符号工具 --------------------------- #
def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _extract_index_digits(code: str) -> str:
    raw = code.strip().lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def _add_exchange_prefix_if_needed(code: str) -> str:
    code = code.strip()
    lower = code.lower()
    if lower.startswith("sh") or lower.startswith("sz"):
        return code
    if len(code) == 6 and code.isdigit():
        return f"sh{code}" if code[0] in {"5", "6", "9"} else f"sz{code}"
    return code


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
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
        raise ValueError(f"无法识别日期/收盘列，现有列: {list(renamed.columns)}")

    out = renamed[[date_col, close_col]].copy()
    out.columns = ["date", "close"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    return out


def _build_tickflow_etf_symbols(code: str) -> List[str]:
    raw = code.strip().lower()
    digits = _extract_index_digits(raw)
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
        else:
            primary = "SH" if digits[0] in {"5", "6", "9"} else "SZ"
            secondary = "SZ" if primary == "SH" else "SH"
            candidates.extend([f"{digits}.{primary}", f"{digits}.{secondary}"])
    return _dedupe_keep_order(candidates)


def _clip_dataframe_by_date(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if df.empty:
        return df
    start_ts = pd.to_datetime(start_date, format="%Y%m%d")
    end_ts = pd.to_datetime(end_date, format="%Y%m%d")
    return df[(df["date"] >= start_ts) & (df["date"] <= end_ts)].copy()


# ----------------------------- tickflow 可选 ------------------------------ #
def _build_tickflow_client() -> Optional["TickFlow"]:
    if TickFlow is None:
        return None
    timeout = float(os.getenv("TICKFLOW_TIMEOUT", "15"))
    api_key = os.getenv("TICKFLOW_API_KEY")
    if api_key:
        return TickFlow(api_key=api_key, base_url=os.getenv("TICKFLOW_BASE_URL"), timeout=timeout)
    return TickFlow(
        api_key=None,
        base_url=os.getenv("TICKFLOW_FREE_BASE_URL", DEFAULT_TICKFLOW_FREE_BASE_URL),
        timeout=timeout,
    )


def _fetch_tickflow_klines(
    symbols: List[str],
    start_date: str,
    end_date: str,
    daily_count: int = DEFAULT_TICKFLOW_DAILY_COUNT,
) -> pd.DataFrame:
    client = _build_tickflow_client()
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
                normalized = _clip_dataframe_by_date(_normalize_dataframe(raw), start_date, end_date)
                if not normalized.empty:
                    return normalized
                errors.append(f"tickflow.klines.get({symbol}): empty result")
                print(f"[WARN] TickFlow 返回空数据，尝试下一个符号: {symbol}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tickflow.klines.get({symbol}): {exc}")
                print(f"[WARN] TickFlow 数据源失败，尝试下一个符号: {symbol} -> {exc}")
    finally:
        client.close()

    raise RuntimeError("; ".join(errors) if errors else "TickFlow 未返回有效数据")


# ----------------------------- 主接口 ------------------------------------ #
def fetch_etf_daily(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """取 ETF 日线,返回标准化 {date, close}。

    优先级:tickflow(可选)-> akshare fund_etf_hist_em -> fund_etf_fund_info_em。
    """
    errors: List[str] = []

    tickflow_symbols = _build_tickflow_etf_symbols(code)
    if tickflow_symbols:
        try:
            return _fetch_tickflow_klines(tickflow_symbols, start_date, end_date)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tickflow({tickflow_symbols}): {exc}")
            print(f"[WARN] ETF TickFlow 数据源失败，准备尝试 AkShare: {exc}")

    if ak is not None and hasattr(ak, "fund_etf_hist_em"):
        for symbol in [code, _add_exchange_prefix_if_needed(code)]:
            try:
                raw = alerts.run_with_retry(
                    "fund_etf_hist_em",
                    lambda symbol=symbol: ak.fund_etf_hist_em(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust="",
                    ),
                )
                normalized = _normalize_dataframe(raw)
                if not normalized.empty:
                    return normalized
            except Exception as exc:  # noqa: BLE001
                errors.append(f"fund_etf_hist_em({symbol}): {exc}")
                print(f"[WARN] ETF 主数据源失败，准备尝试备源: fund_etf_hist_em({symbol}) -> {exc}")

    if ak is not None and hasattr(ak, "fund_etf_fund_info_em"):
        try:
            raw = alerts.run_with_retry(
                "fund_etf_fund_info_em",
                lambda: ak.fund_etf_fund_info_em(
                    fund=code,
                    start_date=start_date,
                    end_date=end_date,
                ),
            )
            normalized = _normalize_dataframe(raw)
            if not normalized.empty:
                return normalized
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fund_etf_fund_info_em({code}): {exc}")

    raise RuntimeError(
        f"ETF 数据获取失败: {'; '.join(errors) if errors else '未找到可用 ETF 接口'}"
    )


def fetch_close_series(
    code: str, calendar_days: int = EASTMONEY_LOOKBACK_CALENDAR_DAYS
) -> Dict[str, float]:
    """取近 calendar_days 个自然日的收盘价映射 {YYYY-MM-DD: close};失败返回 {}。

    替代旧版 ``fetch_eastmoney_series``(原来 ``import monitor_drawdown``)。
    """
    end = now_in_beijing().date()
    start = end - timedelta(days=calendar_days)
    try:
        df = fetch_etf_daily(code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] eastmoney 获取 {code} 失败: {exc}")
        return {}
    mapping: Dict[str, float] = {}
    for _, row in df.iterrows():
        d = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        try:
            value = float(row["close"])
        except (TypeError, ValueError):
            continue
        mapping[d] = value
    return mapping
