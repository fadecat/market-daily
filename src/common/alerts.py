"""异常报警(webhook,仅报警)+ 重试装饰器。

新仓库的 webhook 只做异常报警,不再推送日报。板块跑挂时调 ``notify_alert()``
发企业微信 markdown 消息;网络层用 ``run_with_retry()`` 包裹,失败自动重试。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import requests

from . import env

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None

logger = logging.getLogger(__name__)


def is_retryable_error(exc: Exception) -> bool:
    """判断异常是否值得重试(网络/超时/5xx 等)。"""
    if isinstance(exc, requests.exceptions.RequestException):
        return True
    if httpx is not None and isinstance(exc, httpx.HTTPError):
        return True
    message = str(exc).lower()
    retry_keywords = [
        "timeout",
        "timed out",
        "connection",
        "connection aborted",
        "remote end closed",
        "max retries exceeded",
        "temporarily unavailable",
        "502",
        "503",
        "504",
    ]
    return any(keyword in message for keyword in retry_keywords)


def run_with_retry(name: str, fn: Callable[[], Any], retries: int = 3, base_sleep: float = 1.5) -> Any:
    """执行 fn,可重试异常自动退避重试;不可重试异常立即抛出。"""
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except TypeError:
            # 参数不匹配属于接口差异,不重试
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries and is_retryable_error(exc):
                wait = base_sleep * attempt
                print(f"[WARN] {name} 第 {attempt}/{retries} 次失败: {exc},{wait:.1f}s 后重试")
                time.sleep(wait)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"{name} 调用失败")


def notify_alert(title: str, detail: str = "", webhook: Optional[str] = None) -> bool:
    """发异常报警到企业微信 webhook(``ALERT_WEBHOOK``)。未配置则仅打日志。"""
    webhook = webhook or env.get("ALERT_WEBHOOK")
    content = f"# ⚠️ {title}\n{detail}".strip()
    if not webhook:
        logger.warning("[ALERT] 未配置 ALERT_WEBHOOK,仅日志:\n%s", content)
        return False
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ALERT] 报警发送失败: %s", exc)
        return False


def notify_data_failure(
    dataset: str,
    *,
    error: Exception,
    code: str = "",
    target_name: str = "",
    action: str = "",
    trace: str = "",
    partial: bool = False,
    webhook: Optional[str] = None,
) -> bool:
    """发送带业务影响范围和内部检索字段的数据失败报警。"""
    from .data_status import build_data_alert_title, format_data_failure_detail

    title = build_data_alert_title(
        dataset,
        code=code,
        target_name=target_name,
        partial=partial,
    )
    detail = format_data_failure_detail(
        dataset,
        error=error,
        code=code,
        target_name=target_name,
        action=action,
        trace=trace,
    )
    if webhook is None:
        return notify_alert(title, detail)
    return notify_alert(title, detail, webhook=webhook)
