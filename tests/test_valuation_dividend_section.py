"""高股息 section 编排层测试(不触网)。

回归 P0-3:``build_section`` 移植时遗漏了二次筛选,主表展示集思录原始返回、
TTM 列恒空、规则文案缺失。此处断言 ``filter_dividend_rows_by_secondary_rules``
被调用且在 ``prepare_dividend_email_data`` 之前。
"""
from __future__ import annotations

from pathlib import Path


def test_build_section_applies_secondary_filter_before_prepare(monkeypatch):
    from src.valuation.dividend import render as div_render

    order: list[str] = []

    def fake_filter(data, **kwargs):
        order.append("filter")
        return data

    def fake_prepare(data, *, cookie=None):
        order.append("prepare")
        return data

    monkeypatch.setattr(div_render, "get_cookie", lambda: "c")
    monkeypatch.setattr(div_render, "fetch_data", lambda *, cookie: {"rows": [], "raw_returned_count": 0})
    monkeypatch.setattr(div_render, "filter_dividend_rows_by_secondary_rules", fake_filter)
    monkeypatch.setattr(div_render, "prepare_dividend_email_data", fake_prepare)
    monkeypatch.setattr(div_render, "build_dividend_email_html", lambda data: ["<div>x</div>"])

    result = div_render.build_section(Path("/tmp"))
    assert result is not None
    assert order == ["filter", "prepare"]  # filter 必须在 prepare 之前
