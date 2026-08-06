"""股债收益差 / 股债收益比值 分位计算。

移植自 monitor_drawdown.py L1004-1097。``compute_equity_bond_spread_percentiles`` 为
纯 pandas 计算(可直接单测);``attach_equity_bond_*`` 把结果写回估值 item。

注意:``parse_float`` / ``get_index_valuation_metric`` 为本板块(fetch.py 也用)共享小工具,
放在此处;fetch.py 移植后从本模块导入。``attach_equity_bond_spread`` 触网(拉 PE 历史),
对 ``valuation.fetch`` 用懒导入,避免模块级循环依赖、并允许 metrics 先于 fetch 移植。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_optional_date(value: object) -> Optional[pd.Timestamp]:
    """宽松解析日期为归一化(零点)Timestamp,无法解析返回 None。移植自 monitor_drawdown。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.normalize()


def get_index_valuation_metric(item: Dict, metric_name: str) -> Dict:
    metrics = item.get("index_valuation_metrics")
    if not isinstance(metrics, dict):
        return {}
    metric = metrics.get(metric_name)
    return metric if isinstance(metric, dict) else {}


def compute_equity_bond_spread_percentiles(
    pe_df: pd.DataFrame, bond_df: pd.DataFrame
) -> Dict[str, Any]:
    """由 PE 历史 + 10Y 国债历史算股债收益差/比值的 1Y/3Y/5Y/10Y 分位与 5Y 均值。

    pe_df 需含 ``date``/``pe``;bond_df 需含 ``date``/``yield_pct``。样本不足 20 返回空 dict。
    """
    merged = pd.merge(pe_df, bond_df, on="date", how="inner").dropna()
    merged = merged[merged["pe"] > 0].sort_values("date").reset_index(drop=True)
    if len(merged) < 20:
        return {}
    merged["spread"] = (1.0 / merged["pe"]) * 100.0 - merged["yield_pct"]
    merged["ratio"] = pd.NA
    ratio_mask = merged["yield_pct"] > 0
    merged.loc[ratio_mask, "ratio"] = (
        100.0 / merged.loc[ratio_mask, "pe"]
    ) / merged.loc[ratio_mask, "yield_pct"]

    current_spread = float(merged.iloc[-1]["spread"])
    latest_ratio = merged.iloc[-1]["ratio"]
    current_ratio = float(latest_ratio) if pd.notna(latest_ratio) else None
    latest_date = merged.iloc[-1]["date"]

    percentiles: Dict[str, float] = {}
    ratio_percentiles: Dict[str, float] = {}
    for label, years in [("1Y", 1), ("3Y", 3), ("5Y", 5), ("10Y", 10)]:
        cutoff = latest_date - pd.DateOffset(years=years)
        window = merged[merged["date"] >= cutoff]
        if len(window) >= 20:
            percentiles[label] = round(float((window["spread"] < current_spread).mean() * 100), 2)
            if current_ratio is not None:
                rw = window.dropna(subset=["ratio"])
                if len(rw) >= 20:
                    ratio_percentiles[label] = round(float((rw["ratio"] < current_ratio).mean() * 100), 2)

    avg_5y_window = merged[merged["date"] >= latest_date - pd.DateOffset(years=5)]
    avg_5y = round(float(avg_5y_window["spread"].mean()), 4) if not avg_5y_window.empty else None
    ratio_avg_5y = None
    if current_ratio is not None:
        rw5 = avg_5y_window.dropna(subset=["ratio"])
        if not rw5.empty:
            ratio_avg_5y = round(float(rw5["ratio"].mean()), 4)

    result: Dict[str, Any] = {
        "current": round(current_spread, 4),
        "percentiles": percentiles,
        "average_5y": avg_5y,
    }
    if current_ratio is not None:
        result["ratio_current"] = round(current_ratio, 4)
        result["ratio_percentiles"] = ratio_percentiles
        result["ratio_average_5y"] = ratio_avg_5y
    return result


def attach_equity_bond_ratio(
    item: Dict,
    bond_yield: float,
    data_source: str = "live",
    archive_latest_date: Optional[str] = None,
) -> None:
    """用当前 PE 与 10Y 国债收益率算单点股债收益差,写回 item。"""
    pe_metric = get_index_valuation_metric(item, "PE(TTM)")
    if not pe_metric:
        return
    pe_current = parse_float(pe_metric.get("current"))
    if pe_current is None or pe_current <= 0:
        return
    item["equity_bond_ratio"] = round((1.0 / pe_current) * 100.0 - bond_yield, 4)
    item["cn_10y_bond_yield"] = bond_yield
    item["cn_10y_bond_yield_data_source"] = data_source
    item["cn_10y_bond_yield_archive_latest_date"] = archive_latest_date if data_source == "archive" else None


def attach_equity_bond_spread(item: Dict, bond_history: pd.DataFrame) -> None:
    """拉 PE 历史 -> 算股债收益差分位 -> 写回 item。失败仅告警不抛。"""
    index_code = str(item.get("index_code") or item.get("code") or "").strip()
    valuation_url = str(item.get("index_valuation_percentile_source") or "").strip()
    if not index_code and not valuation_url:
        return
    try:
        from .fetch import _combine_archive_meta, fetch_index_pe_history_with_archive_fallback

        pe_df, pe_meta = fetch_index_pe_history_with_archive_fallback(index_code, url=valuation_url)
        spread = compute_equity_bond_spread_percentiles(pe_df, bond_history)
        if spread:
            item["equity_bond_spread"] = spread
            combined_meta = _combine_archive_meta(
                pe_meta,
                {
                    "data_source": item.get("cn_10y_bond_yield_data_source"),
                    "archive_latest_date": item.get("cn_10y_bond_yield_archive_latest_date"),
                },
            )
            item.update(
                {
                    "equity_bond_spread_data_source": combined_meta.get("data_source"),
                    "equity_bond_spread_archive_latest_date": combined_meta.get("archive_latest_date"),
                }
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] {item.get('name', index_code)} 股债收益差分位计算失败: {exc}")
