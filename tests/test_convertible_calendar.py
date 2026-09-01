"""集思录日历提醒 测试(纯函数,不触网)。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible.calendar import calendar, render  # noqa: E402

BEIJING_TZ = calendar.BEIJING_TZ
FIXED_NOW = datetime(2026, 8, 6, 10, 0, tzinfo=BEIJING_TZ)


def test_build_calendar_time_window_next_month():
    start, end = calendar.build_calendar_time_window("next_month", current_time=FIXED_NOW)
    assert start.strftime("%Y-%m-%d") == "2026-09-01"
    assert end.strftime("%Y-%m-%d") == "2026-10-01"


def test_build_calendar_time_window_current_to_lookahead():
    start, end = calendar.build_calendar_time_window(
        "current_to_lookahead", lookahead_days=45, current_time=FIXED_NOW
    )
    assert start.strftime("%Y-%m-%d") == "2026-08-06"
    assert end.strftime("%Y-%m-%d") == "2026-09-20"  # 8/6 + 45 天


def test_build_calendar_request_params_keys():
    params = calendar.build_calendar_request_params("CNV", 45, window="next_month", current_time=FIXED_NOW)
    assert set(params) == {"qtype", "start", "end", "_"}
    assert params["qtype"] == "CNV"
    assert params["start"].isdigit() and params["end"].isdigit()


def test_extract_event_records_list_and_nested():
    assert calendar.extract_event_records([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]
    nested = calendar.extract_event_records({"data": {"rows": [{"cell": {"title": "x"}}]}})
    assert nested == [{"cell": {"title": "x"}}]
    assert calendar.extract_event_records({"foo": "bar"}) == []


def test_parse_event_datetime_variants():
    assert calendar.parse_event_datetime(None) is None
    assert calendar.parse_event_datetime("invalid") is None
    # 字符串日期
    dt = calendar.parse_event_datetime("2026-01-15")
    assert dt is not None and dt.strftime("%Y-%m-%d") == "2026-01-15"
    # 秒级时间戳
    dt2 = calendar.parse_event_datetime("1690000000")
    assert dt2 is not None and dt2.tzinfo == BEIJING_TZ
    # 毫秒级时间戳
    dt3 = calendar.parse_event_datetime("1690000000000")
    assert dt3 is not None and dt3 == dt2


def test_normalize_calendar_event_cell_and_missing_title():
    rec = {"cell": {"title": "某某下修股东会", "start": "2026-09-01", "code": "128001"}}
    ev = calendar.normalize_calendar_event(rec)
    assert ev is not None
    assert ev["title"] == "某某下修股东会"
    assert ev["code"] == "128001"
    assert ev["event_time"] is not None
    # 无 title -> None
    assert calendar.normalize_calendar_event({"cell": {"code": "x"}}) is None


def test_dedupe_events_by_title_and_time():
    t = datetime(2026, 9, 1, tzinfo=BEIJING_TZ)
    events = [
        {"title": "A", "event_time": t},
        {"title": "A", "event_time": t},  # 重复
        {"title": "B", "event_time": t},
    ]
    assert len(calendar.dedupe_events(events)) == 2


def test_filter_events_by_keywords():
    events = [
        {"title": "某某下修股东会", "event_time": None},
        {"title": "无关事件", "event_time": None},
    ]
    matched = calendar.filter_events_by_keywords(events, ["下修股东会"])
    assert len(matched) == 1 and matched[0]["title"] == "某某下修股东会"
    # 空关键词 -> []
    assert calendar.filter_events_by_keywords(events, []) == []


def test_format_event_time():
    assert calendar.format_event_time(None) == "日期未知"
    dt = datetime(2026, 9, 1, tzinfo=BEIJING_TZ)
    assert calendar.format_event_time(dt) == "2026-09-01"


def test_load_calendar_rules_reads_yaml():
    config_path = REPO_ROOT / "config" / "cb_calendar.yaml"
    rules = calendar.load_calendar_rules(str(config_path))
    assert len(rules) == 1
    assert rules[0]["name"] == "下修股东会提醒"
    assert "下修股东会" in rules[0]["title_keywords"]
    assert "webhook_env" not in rules[0]  # 已去掉 webhook_env
    # 窗口须为 current_to_lookahead:事件日期未过(含今天)持续显示,过了自动退出;
    # next_month 会漏掉本月内即将发生的事件
    assert rules[0]["window"] == "current_to_lookahead"


def test_build_section_html_renders_events():
    matched = [{
        "rule_name": "下修股东会提醒",
        "events": [{"title": "某某下修股东会", "event_time": datetime(2026, 9, 1, tzinfo=BEIJING_TZ),
                     "code": "128001"}],
    }]
    html = render.build_section_html(matched, current_time=FIXED_NOW)
    assert "集思录日历提醒" in html
    assert "某某下修股东会" in html
    assert "128001" in html
    # 企业微信方言 <font color="warning"> 应被 render_markdown 转成 span
    assert "color:#D93026" in html


def test_load_registered_monitor_reads_yaml():
    config_path = REPO_ROOT / "config" / "cb_calendar.yaml"
    monitor = calendar.load_registered_monitor(str(config_path))
    assert monitor.get("name") == "同意注册转债提醒"
    assert monitor.get("enabled") is True


def test_fetch_registered_cb_events_filters_and_normalizes(monkeypatch):
    def fake_rows(cookie, session=None, timestamp_ms=None):
        assert cookie == "test-cookie"
        return [
            {"cell": {"stock_id": "300727", "stock_nm": "润禾材料",
                      "progress_nm": "同意注册", "progress_dt": "2026-08-27",
                      "progress_full": "证监会同意注册", "bond_id": "", "bond_nm": None,
                      "price": "30.02"}},
            {"cell": {"stock_id": "600000", "stock_nm": "五洲交通",
                      "progress_nm": "同意注册", "progress_dt": "2026-08-21",
                      "bond_id": "", "bond_nm": None, "price": "9.5"}},
            # 非注册状态应被过滤
            {"cell": {"stock_id": "600001", "stock_nm": "某某股份",
                      "progress_nm": "交易所受理", "progress_dt": "2026-08-01"}},
            # 与第一条重复(同正股同时点)应去重
            {"cell": {"stock_id": "300727", "stock_nm": "润禾材料",
                      "progress_nm": "同意注册", "progress_dt": "2026-08-27",
                      "bond_id": "", "bond_nm": None, "price": "30.02"}},
        ]

    monkeypatch.setattr(calendar.cb_reference, "fetch_pending_cb_rows", fake_rows)
    monkeypatch.setattr(calendar.industry, "backfill_pending_industries", lambda *a, **k: {})
    monkeypatch.setattr(
        calendar.industry, "pending_industry_of",
        lambda sid: {"l1_name": "基础化工" if sid == "300727" else "交通运输"},
    )
    events = calendar.fetch_registered_cb_events("test-cookie")
    assert len(events) == 2
    assert events[0]["title"] == "润禾材料 同意注册"
    assert events[0]["code"] == "300727"
    assert events[0]["event_time"].strftime("%Y-%m-%d") == "2026-08-27"
    assert events[0]["description"] == "证监会同意注册"
    assert events[0]["industry"] == "基础化工"
    assert events[0]["stock_price"] == "30.02"
    assert events[1]["title"] == "五洲交通 同意注册"
    assert events[1]["industry"] == "交通运输"


def test_fetch_registered_cb_events_ignores_bad_rows(monkeypatch):
    def fake_rows(cookie, session=None, timestamp_ms=None):
        return [
            {"cell": {"stock_id": "", "stock_nm": "", "progress_nm": "同意注册"}},  # 无正股名 -> 丢弃
            {"not_cell": 1},  # 结构异常 -> 丢弃
        ]

    monkeypatch.setattr(calendar.cb_reference, "fetch_pending_cb_rows", fake_rows)
    monkeypatch.setattr(calendar.industry, "backfill_pending_industries", lambda *a, **k: {})
    assert calendar.fetch_registered_cb_events("test-cookie") == []


def test_build_section_includes_registered_events(monkeypatch, tmp_path):
    from src.convertible.calendar import run as calendar_run

    monkeypatch.setattr(calendar_run.calendar, "fetch_calendar_events", lambda *a, **k: [])
    monkeypatch.setattr(calendar_run.jl, "get_cookie", lambda *a, **k: "test-cookie")
    monkeypatch.setattr(
        calendar_run.calendar,
        "fetch_registered_cb_events",
        lambda cookie, **k: [
            {"title": "润禾材料 同意注册",
             "event_time": datetime(2026, 8, 27, tzinfo=BEIJING_TZ), "code": "300727"},
        ],
    )
    section = calendar_run.build_section(tmp_path)
    assert section is not None
    assert "同意注册转债提醒" in section["html"]
    assert "润禾材料 同意注册" in section["html"]


def test_build_section_skips_registered_when_disabled(monkeypatch, tmp_path):
    from src.convertible.calendar import run as calendar_run

    monkeypatch.setattr(calendar_run.calendar, "fetch_calendar_events", lambda *a, **k: [])
    monkeypatch.setattr(
        calendar_run.calendar, "load_registered_monitor",
        lambda *a, **k: {"name": "同意注册转债提醒", "enabled": False},
    )

    def should_not_call(*a, **k):
        raise AssertionError("enabled=false 时不应拉取同意注册数据")

    monkeypatch.setattr(calendar_run.jl, "get_cookie", should_not_call)
    monkeypatch.setattr(calendar_run.calendar, "fetch_registered_cb_events", should_not_call)
    assert calendar_run.build_section(tmp_path) is None
