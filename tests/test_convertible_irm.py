"""董秘互动 测试(纯函数 + monkeypatch,不触网)。"""
from __future__ import annotations

from datetime import timedelta

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible.irm import query, render  # noqa: E402


# ── _parse_sse_date ───────────────────────────────────────────────────────────
def test_parse_sse_date_absolute():
    dt = query._parse_sse_date("2026年03月04日 14:08")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 3 and dt.day == 4


def test_parse_sse_date_relative():
    today = query.now_in_beijing()
    assert (query._parse_sse_date("昨天 15:36").date() - (today - timedelta(days=1)).date()).days == 0
    assert (query._parse_sse_date("前天").date() - (today - timedelta(days=2)).date()).days == 0
    assert (query._parse_sse_date("今天").date() - today.date()).days == 0
    assert (query._parse_sse_date("12:34").date() - today.date()).days == 0


def test_parse_sse_date_invalid():
    assert query._parse_sse_date("garbage") is None
    assert query._parse_sse_date("") is None


# ── query_irm 路由 ────────────────────────────────────────────────────────────
def test_query_irm_routing(monkeypatch):
    calls = {"cninfo": [], "sse": []}
    monkeypatch.setattr(query, "_query_cninfo", lambda name: calls["cninfo"].append(name) or ["cn"])
    monkeypatch.setattr(query, "_query_sse", lambda name: calls["sse"].append(name) or ["sse"])

    assert query.query_irm("深股", "000001") == ["cn"]
    assert query.query_irm("创", "300001") == ["cn"]
    assert query.query_irm("沪", "600001") == ["sse"]
    assert query.query_irm("北", "880001") == []
    assert calls["cninfo"] == ["深股", "创"]
    assert calls["sse"] == ["沪"]


def test_query_irm_swallows_exception(monkeypatch):
    def _boom(_name):
        raise RuntimeError("网络炸了")
    monkeypatch.setattr(query, "_query_cninfo", _boom)
    # 外层 try/except -> 返回 [],不抛
    assert query.query_irm("名", "000001") == []


# ── collect_irm_for_rows ──────────────────────────────────────────────────────
def _rows(*stocks):
    return [{"cell": {"stock_nm": nm, "stock_id": sid}} for nm, sid in stocks]


def test_collect_dedup_and_trim(monkeypatch):
    # 同一 stock_id 只查一次
    fake = {
        "000001": [
            {"question": "Q" * 100, "answer": "A" * 200, "url": "http://x/1"},  # 超长截断
            {"question": "短问", "answer": "短答", "url": ""},  # 不截断
            {"question": "第3条", "answer": "第3答", "url": ""},
            {"question": "第4条", "answer": "第4答", "url": ""},  # 超过 max_qas_per_stock=3 丢弃
        ],
    }
    monkeypatch.setattr(query, "query_irm", lambda nm, sid: fake.get(sid, []))

    rows = _rows(("股A", "000001"), ("股A重复", "000001"), ("股B无互动", "000002"))
    results = query.collect_irm_for_rows(rows, max_qas_per_stock=3, question_max=80, answer_max=150)

    # 000001 去重为1条;000002 无互动被略过
    assert len(results) == 1
    item = results[0]
    assert item["stock_nm"] == "股A" and item["stock_id"] == "000001"
    assert len(item["qas"]) == 3  # 最多3条
    # 第1条截断:question 80 + "...",answer 150 + "..."
    assert item["qas"][0]["question"].endswith("...") and len(item["qas"][0]["question"]) == 83
    assert item["qas"][0]["answer"].endswith("...") and len(item["qas"][0]["answer"]) == 153
    assert item["qas"][0]["url"] == "http://x/1"
    # 第2条不截断
    assert item["qas"][1]["question"] == "短问" and "..." not in item["qas"][1]["answer"]


def test_collect_empty_rows(monkeypatch):
    monkeypatch.setattr(query, "query_irm", lambda nm, sid: [{"question": "q", "answer": "a", "url": ""}])
    assert query.collect_irm_for_rows([]) == []


# ── render.build_section_html ─────────────────────────────────────────────────
def test_build_section_html_empty():
    assert render.build_section_html([]) == ""


def test_build_section_html_with_data():
    stock_qas = [
        {
            "stock_nm": "测试股", "stock_id": "000001",
            "qas": [
                {"question": "业绩如何?", "answer": "稳步增长。", "url": "http://x/1"},
                {"question": "分红?", "answer": "持续分红。", "url": ""},
            ],
        },
    ]
    html = render.build_section_html(stock_qas)
    assert "正股董秘互动" in html
    assert "测试股" in html and "000001" in html
    assert "业绩如何" in html and "稳步增长" in html
    assert "查看详情" in html  # 有 url -> 链接
