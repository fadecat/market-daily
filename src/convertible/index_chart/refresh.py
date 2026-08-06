"""转债等权指数历史周期归档(无声后台任务,失败才报警)。

移植自 refresh_cb_index_history.py:去掉企业微信 webhook 通知,失败改走
common.alerts.notify_alert;写盘用 history.save_history(content_hash 去重)。
"""
from __future__ import annotations

from pathlib import Path

from ...common import alerts
from .history import ARCHIVE_PATH, build_merged_history, save_history


def refresh(path: Path | str = ARCHIVE_PATH) -> bool:
    try:
        merged, stats = build_merged_history(path)
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("转债指数历史归档失败", str(exc))
        raise
    changed = save_history(merged, path)
    print(
        f"[INFO] 转债指数归档: history={stats['history']} "
        f"updated={stats['updated']} added={stats['added']} changed={changed}"
    )
    return changed


def main() -> int:
    refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
