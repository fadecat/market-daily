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
