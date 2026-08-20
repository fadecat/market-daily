"""巨潮(cninfo)财报缓存预热/重试 + 高股息股票池快照备份。

移植自旧仓 ``dividend_financial_backup.py``:复用 market-daily 的
``cninfo_cache``(归档/缓存)、``cninfo``(财报抓取)、``supplement``(东财补充池)、
``filter``/``fetch``(高股息数据)、``common.whitelist``(国资白名单);
webhook 通知统一改走 ``common.alerts.notify_alert``(仅报警)。

三种模式:
- 默认(backup):抓高股息池,写 ``data/dividend_universe`` 快照,逐只归档 cninfo 财报。
- ``--warmup``:对「高股息池 ∩ 国资白名单 + 东财补充池」逐只 force_refresh,按分片限流。
- ``--retry``:仅重试当日尚未成功抓取的标的。
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ...common import alerts, env
from ...common.whitelist import (
    STATE_OWNED_WHITELIST_XLSX,
    load_stock_code_whitelist_from_xlsx,
    normalize_stock_code,
)
from .cninfo import fetch_financial_bundle
from .cninfo_cache import (
    CNINFO_FETCH_BACKOFF_SECONDS,
    CNINFO_FETCH_MAX_ATTEMPTS,
    archive_financial_snapshot,
    build_financial_snapshot_payload,
    get_or_fetch_financial_snapshot,
    load_cached_financial_snapshot,
    write_json,
)
from .fetch import DIVIDEND_FORM_DATA, fetch_data
from .filter import (
    TTM_PARENT_NET_PROFIT_MIN_YI,
    filter_dividend_rows_by_secondary_rules,
)
from .supplement import (
    DIVIDEND_EMAIL_SUPPLEMENT_XCID,
    fetch_all_results_by_xcid,
    filter_dividend_email_supplement_rows,
)

DEFAULT_TIMEZONE = "Asia/Shanghai"
DATA_DIRNAME = "data"
DIVIDEND_UNIVERSE_DIRNAME = "dividend_universe"
# 全量预热:每只之间间隔多少秒,低速率避免巨潮限流。
CNINFO_WARMUP_DELAY_SECONDS = max(0.0, float(env.get("CNINFO_WARMUP_DELAY_SECONDS", "4") or "4"))
# 单轮抓取的墙钟预算(秒):超过后停止开新标的,干净退出、剩余留待下轮 --retry,
# 避免个别卡死标的把整轮拖到 workflow timeout(30min)被强杀、已抓成果也丢。
CNINFO_TIME_BUDGET_SECONDS = max(0.0, float(env.get("CNINFO_TIME_BUDGET_SECONDS", "1500") or "1500"))

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _now_in_timezone(tz_name: str = DEFAULT_TIMEZONE) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def write_dividend_universe_snapshot(root_dir: Path | str, snapshot: dict[str, Any]) -> dict[str, str]:
    root = Path(root_dir)
    base_dir = root / DATA_DIRNAME / DIVIDEND_UNIVERSE_DIRNAME
    dated_path = base_dir / f"{snapshot['date']}.json"
    latest_path = base_dir / "latest.json"
    write_json(dated_path, snapshot)
    write_json(latest_path, snapshot)
    return {"dated_path": str(dated_path), "latest_path": str(latest_path)}


def build_backup_summary(result: dict[str, Any]) -> str:
    lines = [
        "财报备份结果",
        f"状态 {result.get('status', 'unknown')}",
        f"日期 {result['date']}",
        f"股票池 {result['universe_count']} 只",
        f"成功 {result['archived_count']} 只",
        f"新增 {result['created_count']} 只",
        f"更新 {result['updated_count']} 只",
        f"未变化 {result['unchanged_count']} 只",
        f"失败 {result['failed_count']} 只",
    ]
    if result.get("error"):
        lines.append(f"错误: {result['error']}")
    if result.get("failed_codes"):
        lines.append("失败代码: " + ", ".join(result["failed_codes"][:10]))
    return "\n".join(lines)


def build_dividend_universe_snapshot(date_str: str, fetched_at: str, filtered_data: dict[str, Any]) -> dict[str, Any]:
    stocks = []
    for row in filtered_data.get("rows", []):
        cell = row["cell"]
        stocks.append(
            {
                "stock_code": cell["stock_id"],
                "stock_name": cell["stock_nm"],
                "industry": cell.get("industry_nm"),
                "dividend_rate": cell.get("dividend_rate"),
                "pe": cell.get("pe"),
                "pb": cell.get("pb"),
                "roe": cell.get("roe"),
                "ttm_parent_net_profit_yi": cell.get("ttm_parent_net_profit_yi"),
            }
        )
    return {
        "date": date_str,
        "fetched_at": fetched_at,
        "count": len(stocks),
        "filters": {
            "market": DIVIDEND_FORM_DATA["market[]"],
            "pe_max": DIVIDEND_FORM_DATA["pe"],
            "pb_max": DIVIDEND_FORM_DATA["pb"],
            "dividend_rate_min": DIVIDEND_FORM_DATA["dividend_rate"],
            "roe_min": DIVIDEND_FORM_DATA["roe"],
            "ttm_parent_net_profit_min_yi": TTM_PARENT_NET_PROFIT_MIN_YI,
            "state_owned_whitelist_file": STATE_OWNED_WHITELIST_XLSX,
        },
        "stocks": stocks,
    }


def fetch_dividend_email_supplement_universe(
    supplement_xcid: str = DIVIDEND_EMAIL_SUPPLEMENT_XCID,
    supplement_fetcher=None,
) -> list[dict[str, str]]:
    supplement_xcid = str(supplement_xcid or "").strip()
    if not supplement_xcid:
        return []

    supplement_fetcher = supplement_fetcher or fetch_all_results_by_xcid
    result = supplement_fetcher(supplement_xcid)
    rows, _excluded_rows = filter_dividend_email_supplement_rows(result.get("rows") or [])

    universe = []
    seen_codes: set[str] = set()
    for row in rows:
        stock_code = normalize_stock_code(row.get("SECURITY_CODE"))
        if not stock_code or stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        universe.append(
            {
                "stock_code": stock_code,
                "stock_name": str(row.get("SECURITY_SHORT_NAME", "")).strip(),
            }
        )
    return universe


def run_backup(
    root_dir: Path | str = _REPO_ROOT,
    tz_name: str = DEFAULT_TIMEZONE,
    data_fetcher=None,
    filter_func=None,
    bundle_fetcher=None,
) -> dict[str, Any]:
    data_fetcher = data_fetcher or fetch_data
    filter_func = filter_func or filter_dividend_rows_by_secondary_rules
    bundle_fetcher = bundle_fetcher or fetch_financial_bundle
    now = _now_in_timezone(tz_name)
    fetched_at = now.isoformat(timespec="seconds")
    date_str = now.strftime("%Y-%m-%d")

    raw_data = data_fetcher()
    filtered_data = filter_func(raw_data)
    universe_snapshot = build_dividend_universe_snapshot(date_str, fetched_at, filtered_data)
    write_dividend_universe_snapshot(root_dir, universe_snapshot)

    created_count = 0
    updated_count = 0
    unchanged_count = 0
    failed_codes: list[str] = []
    archived_count = 0

    for row in filtered_data.get("rows", []):
        stock_code = row["cell"]["stock_id"]
        try:
            bundle = bundle_fetcher(stock_code)
            payload = build_financial_snapshot_payload(bundle, fetched_at)
            archived = archive_financial_snapshot(root_dir, payload)
            archived_count += 1
            if archived["status"] == "created":
                created_count += 1
            elif archived["status"] == "updated":
                updated_count += 1
            else:
                unchanged_count += 1
        except Exception:  # noqa: BLE001
            failed_codes.append(stock_code)

    return {
        "status": "partial_failed" if failed_codes else "success",
        "date": date_str,
        "fetched_at": fetched_at,
        "universe_count": universe_snapshot["count"],
        "archived_count": archived_count,
        "created_count": created_count,
        "updated_count": updated_count,
        "unchanged_count": unchanged_count,
        "failed_count": len(failed_codes),
        "failed_codes": failed_codes,
    }


def build_warmup_summary(result: dict[str, Any]) -> str:
    action = "重试" if result.get("mode") == "retry" else "预热"
    work_count = result.get("work_count", 0)
    total_work_count = result.get("total_work_count", 0) or work_count
    work_line = f"待抓取 {work_count} 只"
    if total_work_count != work_count:
        work_line = f"待抓取 {work_count} / 全量 {total_work_count} 只"
    lines = [
        f"财报缓存{action}结果",
        f"状态 {result.get('status', 'unknown')}",
        f"日期 {result['date']}",
        f"批次 {result.get('slot', '')}",
        f"开始 {result.get('started_at', '')}",
        f"结束 {result.get('finished_at', '')}",
        f"耗时 {result.get('elapsed_seconds', 0):g} 秒",
        f"候选池 {result.get('universe_count', 0)} 只",
        work_line,
        f"本批 {result.get('selected_count', 0)} 只",
        f"成功 {result.get('success_count', 0)} 只",
        f"失败 {result.get('failed_count', 0)} 只",
    ]
    skipped_count = int(result.get("skipped_count", 0) or 0)
    if skipped_count:
        lines.append(f"预算收摊,留待下轮 {skipped_count} 只")
    shard_label = str(result.get("shard_label") or "").strip()
    if shard_label:
        lines.insert(5, f"分片 {shard_label}")
    if result.get("error"):
        lines.append(f"错误: {result['error']}")
    warnings = result.get("warnings") or []
    successes = result.get("successes") or []
    failures = result.get("failures") or []
    if warnings:
        lines.append("警告:")
        lines.extend(str(item) for item in warnings[:8])
    if successes:
        lines.append("成功:")
        lines.extend(
            f"{item['stock_code']} {item['stock_name']} {item.get('snapshot_status', '')} {item['elapsed_seconds']:g}秒"
            for item in successes[:8]
        )
    if failures:
        lines.append("失败:")
        lines.extend(
            f"{item['stock_code']} {item['stock_name']} {item.get('reason', '')} {item['elapsed_seconds']:g}秒"
            for item in failures[:8]
        )
    return "\n".join(lines)


def should_notify_warmup_result(result: dict[str, Any]) -> bool:
    return int(result.get("failed_count", 0) or 0) > 0 or bool(result.get("warnings"))


def run_incremental_warmup(
    root_dir: Path | str = _REPO_ROOT,
    data_fetcher=None,
    stock_code_whitelist=None,
    whitelist_path=STATE_OWNED_WHITELIST_XLSX,
    supplement_xcid: str = DIVIDEND_EMAIL_SUPPLEMENT_XCID,
    supplement_fetcher=None,
    bundle_fetcher=None,
    max_per_run: int | None = None,
    delay_seconds: float = CNINFO_WARMUP_DELAY_SECONDS,
    max_attempts: int = CNINFO_FETCH_MAX_ATTEMPTS,
    backoff_seconds: float = CNINFO_FETCH_BACKOFF_SECONDS,
    time_budget_seconds: float = CNINFO_TIME_BUDGET_SECONDS,
    fetched_at: str | None = None,
    only_not_checked_today: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> dict[str, Any]:
    """按「高股息池 ∩ 国资白名单 + 东财补充池」做每日预热(默认全量;only_not_checked_today 仅重试当日未成功标的)。

    每天对整个交集逐只 force_refresh 巨潮财报;content_hash 自动去重无变化快照。
    """
    data_fetcher = data_fetcher or fetch_data
    supplement_fetcher = supplement_fetcher or fetch_all_results_by_xcid
    bundle_fetcher = bundle_fetcher or fetch_financial_bundle
    now = _now_in_timezone()
    started_at = fetched_at or now.isoformat(timespec="seconds")
    started_dt = datetime.fromisoformat(started_at)
    date_str = started_dt.strftime("%Y-%m-%d")
    slot = started_dt.strftime("%M")

    raw = data_fetcher()
    whitelist = stock_code_whitelist
    if whitelist is None:
        whitelist = load_stock_code_whitelist_from_xlsx(whitelist_path)

    universe: list[dict[str, str]] = []
    warnings: list[str] = []
    seen_codes: set[str] = set()
    for row in raw.get("rows", []):
        cell = row.get("cell", {})
        stock_code = normalize_stock_code(cell.get("stock_id"))
        if not stock_code or stock_code not in whitelist or stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        universe.append(
            {
                "stock_code": stock_code,
                "stock_name": str(cell.get("stock_nm", "")).strip(),
            }
        )

    try:
        supplement_universe = fetch_dividend_email_supplement_universe(
            supplement_xcid=supplement_xcid,
            supplement_fetcher=supplement_fetcher,
        )
    except Exception as e:  # noqa: BLE001
        warning_text = f"东财补充池候选获取失败:xcid: {supplement_xcid};错误信息: {e}"
        warnings.append(warning_text)
        print(f"[东财补充池预热失败] {supplement_xcid}: {e}")
        supplement_universe = []

    for item in supplement_universe:
        stock_code = item["stock_code"]
        if stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        universe.append(item)

    if not universe:
        raw_row_count = len(raw.get("rows", []) or [])
        if not raw_row_count:
            raise RuntimeError("集思录返回空数据,候选池为空(Cookie 可能已过期)")
        raise RuntimeError(
            f"候选池 ∩ 国资白名单 交集为空(集思录返回 {raw_row_count} 行,"
            f"白名单 {len(whitelist)} 只),请检查白名单文件与集思录数据"
        )

    shard_label = ""
    if only_not_checked_today:
        today = started_dt.strftime("%Y-%m-%d")
        work = []
        for item in universe:
            snapshot = load_cached_financial_snapshot(item["stock_code"], root_dir=root_dir)
            last_checked = str((snapshot or {}).get("fetched_at") or "")[:10]
            if not last_checked or last_checked < today:
                work.append(item)
    else:
        work = universe
    total_work_count = len(work)
    if not only_not_checked_today and shard_count is not None:
        if shard_count <= 0:
            raise RuntimeError(f"warmup 分片总数必须为正整数,实际为 {shard_count}")
        if shard_index is None:
            raise RuntimeError("warmup 分片缺少 shard_index")
        if shard_index < 0 or shard_index >= shard_count:
            raise RuntimeError(f"warmup 分片索引越界:shard_index={shard_index},shard_count={shard_count}")
        start = total_work_count * shard_index // shard_count
        end = total_work_count * (shard_index + 1) // shard_count
        work = work[start:end]
        shard_label = f"{shard_index + 1}/{shard_count}"
        print(f"[预热分片] {shard_label}: 全量待抓 {total_work_count} 只,本片 {len(work)} 只")
    selected = work if not max_per_run else work[:max_per_run]

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    batch_start_perf = time.perf_counter()
    for index, item in enumerate(selected, 1):
        if time_budget_seconds and index > 1:
            elapsed = time.perf_counter() - batch_start_perf
            if elapsed >= time_budget_seconds:
                skipped = selected[index - 1:]
                print(
                    f"[预算收摊] 已耗时 {elapsed:g}s >= 预算 {time_budget_seconds:g}s,"
                    f"剩余 {len(skipped)} 只留待下轮重试"
                )
                break
        item_start_perf = time.perf_counter()
        stock_code = item["stock_code"]
        stock_name = item["stock_name"]
        try:
            snapshot = get_or_fetch_financial_snapshot(
                stock_code,
                root_dir=root_dir,
                bundle_fetcher=bundle_fetcher,
                fetched_at=started_at,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
                force_refresh=True,
            )
            successes.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name or snapshot.get("stock_name", ""),
                    "elapsed_seconds": round(time.perf_counter() - item_start_perf, 1),
                    "snapshot_status": snapshot.get("archive_status", "unknown"),
                }
            )
        except Exception as e:  # noqa: BLE001
            failures.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "reason": str(e),
                    "elapsed_seconds": round(time.perf_counter() - item_start_perf, 1),
                }
            )
            print(f"[预热失败] {stock_code} {stock_name}: {e}")
        if delay_seconds and index < len(selected):
            time.sleep(delay_seconds)

    finished_at = _now_in_timezone().isoformat(timespec="seconds")
    elapsed_seconds = round(time.perf_counter() - batch_start_perf, 1)
    status = "success"
    if failures and successes:
        status = "partial_failed"
    elif failures:
        status = "failed"
    return {
        "status": status,
        "mode": "retry" if only_not_checked_today else "warmup",
        "date": date_str,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed_seconds,
        "slot": slot,
        "fetched_at": started_at,
        "universe_count": len(universe),
        "total_work_count": total_work_count,
        "work_count": len(work),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "shard_label": shard_label,
        "batch_size": max_per_run if max_per_run else len(work),
        "selected_count": len(selected),
        "remaining_count": max(0, len(work) - len(successes) - len(failures)),
        "success_count": len(successes),
        "failed_count": len(failures),
        "skipped_count": len(skipped),
        "warnings": warnings,
        "selected": selected,
        "successes": successes,
        "failures": failures,
    }


def _failed_warmup_result(error: str, mode: str) -> dict[str, Any]:
    now = _now_in_timezone()
    return {
        "status": "failed",
        "mode": mode,
        "date": now.strftime("%Y-%m-%d"),
        "started_at": now.isoformat(timespec="seconds"),
        "finished_at": now.isoformat(timespec="seconds"),
        "elapsed_seconds": 0,
        "slot": now.strftime("%M"),
        "fetched_at": now.isoformat(timespec="seconds"),
        "universe_count": 0,
        "total_work_count": 0,
        "work_count": 0,
        "selected_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "warnings": [],
        "successes": [],
        "failures": [],
        "error": error,
    }


def _failed_backup_result(error: str) -> dict[str, Any]:
    now = _now_in_timezone()
    return {
        "status": "failed",
        "date": now.strftime("%Y-%m-%d"),
        "fetched_at": now.isoformat(timespec="seconds"),
        "universe_count": 0,
        "archived_count": 0,
        "created_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "failed_count": 0,
        "failed_codes": [],
        "error": error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="巨潮财报缓存预热/重试 + 高股息股票池快照备份。")
    parser.add_argument("--root-dir", default=str(_REPO_ROOT))
    parser.add_argument("--summary-path", default="")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--warmup", action="store_true", help="全量预热财报缓存(对候选池∩白名单逐只抓取)")
    mode_group.add_argument("--retry", action="store_true", help="仅重试当日预热未成功的标的")
    parser.add_argument("--shard-index", type=int, default=None, help="warmup 分片索引(从 0 开始)")
    parser.add_argument("--shard-count", type=int, default=None, help="warmup 分片总数")
    args = parser.parse_args(argv)

    summary_path = Path(args.summary_path) if args.summary_path else None
    root_dir = Path(args.root_dir)

    if args.warmup or args.retry:
        mode = "retry" if args.retry else "warmup"
        mode_cn = "重试" if args.retry else "预热"
        try:
            result = run_incremental_warmup(
                root_dir,
                only_not_checked_today=args.retry,
                shard_index=args.shard_index,
                shard_count=args.shard_count,
            )
        except Exception as e:  # noqa: BLE001
            result = _failed_warmup_result(str(e), mode)
            print(build_warmup_summary(result))
            if summary_path:
                write_json(summary_path, result)
            alerts.notify_alert(f"财报缓存{mode_cn}失败", build_warmup_summary(result))
            return 1

        print(build_warmup_summary(result))
        if summary_path:
            write_json(summary_path, result)
        if should_notify_warmup_result(result):
            alerts.notify_alert(f"财报缓存{mode_cn}结果", build_warmup_summary(result))
        return 0  # best-effort:部分失败不退出非 0,避免阻塞日报

    try:
        result = run_backup(root_dir)
    except Exception as e:  # noqa: BLE001
        result = _failed_backup_result(str(e))
        summary = build_backup_summary(result)
        print(summary)
        if summary_path:
            write_json(summary_path, result)
        alerts.notify_alert("财报备份失败", summary)
        return 1

    summary = build_backup_summary(result)
    print(summary)
    if summary_path:
        write_json(summary_path, result)
    if result["status"] != "success":
        alerts.notify_alert("财报备份部分失败", summary)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
