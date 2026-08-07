"""商品极值板块编排层测试(不触网)。

回归 P0-4:``skip_if_no_today_data`` 守卫移植时丢失,akshare 全体返旧数据时会发
一封以旧日期为 subject 的"日报"。此处断言守卫生效(无今日数据不发、有今日数据发、
守卫关闭时总发)。
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace


def _result(latest_date, error=None):
    return SimpleNamespace(latest_date=latest_date, error=error)


def _patch_send(monkeypatch, email_mod):
    sent = {"n": 0}

    def fake_send(*a, **k):
        sent["n"] += 1
        return True

    monkeypatch.setattr(email_mod, "send_email", fake_send)
    return sent


def test_run_send_skips_when_no_today_data(monkeypatch):
    """skip_if_no_today_data=True 且无今日数据 -> 不发信。"""
    from src.commodity import run as commodity_run
    from src.common import email as email_mod

    monkeypatch.setattr(commodity_run, "_today_cn", lambda: datetime.date(2026, 8, 7))
    monkeypatch.setattr(
        commodity_run,
        "_scan",
        lambda: (
            SimpleNamespace(skip_if_no_today_data=True, max_stale_days=10),
            [_result(datetime.date(2026, 8, 6))],  # 只有 T-1
        ),
    )
    sent = _patch_send(monkeypatch, email_mod)
    assert commodity_run.run_send() == 0
    assert sent["n"] == 0  # 未发信


def test_run_send_sends_when_today_data_present(monkeypatch):
    """有今日数据 -> 正常发信。"""
    from src.commodity import reporting as commodity_reporting
    from src.commodity import run as commodity_run
    from src.common import email as email_mod

    monkeypatch.setattr(commodity_run, "_today_cn", lambda: datetime.date(2026, 8, 7))
    monkeypatch.setattr(
        commodity_run,
        "_scan",
        lambda: (
            SimpleNamespace(skip_if_no_today_data=True, max_stale_days=10),
            [_result(datetime.date(2026, 8, 7))],  # 有今日
        ),
    )
    monkeypatch.setattr(commodity_reporting, "build_email_html", lambda results, cfg, **k: (["<div>x</div>"], None))
    sent = _patch_send(monkeypatch, email_mod)
    assert commodity_run.run_send() == 0
    assert sent["n"] == 1


def test_run_send_sends_when_skip_disabled(monkeypatch):
    """skip_if_no_today_data=False -> 即使无今日数据也发信。"""
    from src.commodity import reporting as commodity_reporting
    from src.commodity import run as commodity_run
    from src.common import email as email_mod

    monkeypatch.setattr(commodity_run, "_today_cn", lambda: datetime.date(2026, 8, 7))
    monkeypatch.setattr(
        commodity_run,
        "_scan",
        lambda: (
            SimpleNamespace(skip_if_no_today_data=False, max_stale_days=10),
            [_result(datetime.date(2026, 8, 6))],  # 无今日但 skip 关闭
        ),
    )
    monkeypatch.setattr(commodity_reporting, "build_email_html", lambda results, cfg, **k: (["<div>x</div>"], None))
    sent = _patch_send(monkeypatch, email_mod)
    assert commodity_run.run_send() == 0
    assert sent["n"] == 1
