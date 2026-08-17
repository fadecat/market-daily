"""估值/国债/汇率/转债指数 归档刷新(无声后台任务,失败才报警)。

移植自旧仓 ``refresh_data_archive.py`` + ``refresh_cb_index_history.yml``:
- 指数三数据集(index_eod / index_dividend_ratio / index_valuation_percentile):
  复用 ``fetch.build_index_*_url`` + ``fetch.fetch_json_response``,按 ``trdDt`` 去重合并写盘。
- 10Y 国债: ``ak.bond_zh_us_rate`` 原始记录,按 ``日期`` 合并到 ``bond_10y/china_10y.json``。
- 汇率: ``ak.forex_hist_em`` 原始记录(含 ``最新价``),按 ``日期`` 合并到 ``fx/usd_cnh.json``。
- 转债等权指数: 委托 ``convertible.index_chart.refresh``。
写盘统一走 ``storage.merge_archive``(内容不变跳过);单步失败 ``alerts.notify_alert`` 后继续。
"""
from __future__ import annotations

import argparse
import re
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd
import yaml

from ..common import alerts, storage
from ..convertible.index_chart import refresh as cb_index_refresh
from . import fetch

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "valuation.yaml"

# 指数归档三数据集:(dataset, URL 构造器)
_INDEX_DATASETS: List[Tuple[str, Callable[[str], str]]] = [
    ("index_eod", fetch.build_index_eod_price_url),
    ("index_dividend_ratio", fetch.build_index_dividend_yield_url),
    ("index_valuation_percentile", fetch.build_index_valuation_percentile_url),
]
STYLE_ROTATION_SPECIAL_INDEX_CODES = ["399376", "399373"]


def load_targets(config_path: str | Path = DEFAULT_CONFIG_PATH) -> List[Dict]:
    """读取 valuation 配置,返回 type=valuation 的标的。"""
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    targets = data.get("targets", []) if isinstance(data, dict) else data
    return [t for t in targets if str(t.get("type", "")).strip().lower() == "valuation"]


def resolve_index_code(target: Dict) -> str:
    """从 ``index_detail_url`` 提取 ``indexCode``(与归档文件命名一致),失败回退 ``code``。"""
    url = str(target.get("index_detail_url") or "").strip()
    match = re.search(r"indexCode=(\d+)", url)
    if match:
        return match.group(1)
    return str(target.get("code") or "").strip()


def resolve_index_codes(targets: List[Dict]) -> List[str]:
    seen: set[str] = set()
    codes: List[str] = []
    for target in targets:
        code = resolve_index_code(target)
        if code and code not in seen:
            codes.append(code)
            seen.add(code)
    return codes


def _df_to_records(df: pd.DataFrame) -> List[Dict]:
    cleaned = df.astype(object).where(pd.notna(df), None)
    return cleaned.to_dict(orient="records")


def refresh_index_dataset(dataset: str, builder: Callable[[str], str], index_code: str, updated_at: str) -> List[Path]:
    url = builder(index_code)
    # cdn.efunds.com.cn 边缘节点偶发不健康(海外机房尤甚),拉长超时与重试窗口
    payload = fetch.fetch_json_response(dataset, url, timeout=30, retries=4)
    if not isinstance(payload, list):
        raise ValueError(f"{dataset} 响应非列表: {type(payload).__name__}")
    records = [row for row in payload if isinstance(row, dict)]
    path = storage.merge_archive(
        dataset,
        {"index_code": index_code},
        records,
        merge_key="trdDt",
        source=url,
        updated_at=updated_at,
    )
    return [path] if path else []


def refresh_style_rotation_special_index_dataset(index_code: str, updated_at: str) -> List[Path]:
    end_date = fetch.now_in_beijing().strftime("%Y%m%d")
    start_date = (fetch.now_in_beijing() - timedelta(days=365 * 10)).strftime("%Y%m%d")
    frame = fetch.fetch_style_rotation_special_index_history(index_code, start_date, end_date)
    if frame is None or getattr(frame, "empty", True):
        return []

    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized = normalized.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    if normalized.empty:
        return []

    records = [
        {"trdDt": row["date"].strftime("%Y-%m-%d"), "pxClose": float(row["close"])}
        for row in normalized.to_dict(orient="records")
    ]
    path = storage.merge_archive(
        "index_eod",
        {"index_code": index_code},
        records,
        merge_key="trdDt",
        source="akshare.stock_zh_a_hist_tx",
        updated_at=updated_at,
    )
    return [path] if path else []


def refresh_bond_dataset(updated_at: str, lookback_years: int = 11) -> List[Path]:
    start = (fetch.now_in_beijing() - timedelta(days=365 * lookback_years)).strftime("%Y%m%d")
    df = ak.bond_zh_us_rate(start_date=start)
    if df is None or getattr(df, "empty", True):
        return []
    records = _df_to_records(df)
    path = storage.merge_archive(
        "bond_10y",
        {"series": "china_10y"},
        records,
        merge_key="日期",
        source="akshare.bond_zh_us_rate",
        updated_at=updated_at,
        filename="china_10y.json",
    )
    return [path] if path else []


def refresh_fx_dataset(updated_at: str) -> List[Path]:
    df = fetch.fetch_fx_history_with_archive_fallback(symbol="USDCNH").copy()
    if df is None or getattr(df, "empty", True):
        return []
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df["最新价"] = pd.to_numeric(df.get("市场价"), errors="coerce")
    keep_columns = [column for column in ["日期", "最新价", "代码", "名称"] if column in df.columns]
    df = df[keep_columns]
    records = _df_to_records(df)
    path = storage.merge_archive(
        "fx",
        {"series": "usd_cnh"},
        records,
        merge_key="日期",
        source="akshare.forex_hist_em",
        updated_at=updated_at,
        filename="usd_cnh.json",
    )
    return [path] if path else []


def refresh_cb_index() -> List[Path]:
    changed = cb_index_refresh.refresh()
    return [cb_index_refresh.ARCHIVE_PATH] if changed else []


def _run_step(
    dataset: str,
    fn: Callable[[], List[Path]],
    *,
    code: str = "",
    target_name: str = "",
) -> Tuple[List[Path], bool]:
    try:
        return fn(), True
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] {dataset}:{code or target_name} 归档刷新失败: {exc}")
        alerts.notify_data_failure(
            dataset=dataset,
            error=exc,
            code=code,
            target_name=target_name,
            action="跳过本步并继续刷新其他归档",
        )
        return [], False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="刷新估值/国债/汇率/转债指数归档。")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args(argv)

    try:
        targets = load_targets(args.config)
        index_codes = resolve_index_codes(targets)
        target_names = {
            resolve_index_code(target): str(target.get("name") or resolve_index_code(target))
            for target in targets
            if resolve_index_code(target)
        }
        updated_at = fetch.now_in_beijing().isoformat()
        changed: List[Path] = []
        ok = 0
        failed: List[str] = []

        for code in index_codes:
            for dataset, builder in _INDEX_DATASETS:
                paths, success = _run_step(
                    dataset,
                    lambda d=dataset, b=builder, c=code: refresh_index_dataset(d, b, c, updated_at),
                    code=code,
                    target_name=target_names.get(code, code),
                )
                changed.extend(paths)
                if success:
                    ok += 1
                else:
                    failed.append(f"{target_names.get(code, code)}/{dataset}")

        for code in STYLE_ROTATION_SPECIAL_INDEX_CODES:
            paths, success = _run_step(
                "index_eod",
                lambda c=code: refresh_style_rotation_special_index_dataset(c, updated_at),
                code=code,
                target_name=f"风格指数{code}",
            )
            changed.extend(paths)
            if success:
                ok += 1
            else:
                failed.append(f"{code}/index_eod")

        bond_paths, bond_ok = _run_step(
            "bond_10y", lambda: refresh_bond_dataset(updated_at), target_name="10Y国债"
        )
        changed.extend(bond_paths)
        if bond_ok:
            ok += 1
        else:
            failed.append("bond_10y")

        fx_paths, fx_ok = _run_step("fx", lambda: refresh_fx_dataset(updated_at), target_name="汇率")
        changed.extend(fx_paths)
        if fx_ok:
            ok += 1
        else:
            failed.append("fx")

        cb_paths, cb_ok = _run_step("cb_index", refresh_cb_index, target_name="转债等权指数")
        changed.extend(cb_paths)
        if cb_ok:
            ok += 1
        else:
            failed.append("cb_index")

        if failed:
            print(f"[WARN] 归档刷新部分失败: {', '.join(failed)}")
        if ok == 0:
            print("[ERROR] 无任何归档刷新成功")
            return 1
        print(f"[INFO] 归档刷新完成: {ok} 成功, {len(failed)} 失败, {len(changed)} 文件变更")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}")
        alerts.notify_alert("归档刷新运行失败", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
