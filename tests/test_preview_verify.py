"""src/preview/verify.py 的单元测试。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.preview import verify

TODAY = date(2026, 8, 6)


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------ check_state ------------------------------


def test_state_missing():
    assert verify.check_state("valuation", "市场估值", ("last_send_date",), None) == [
        "市场估值(valuation) 状态缺失"
    ]


def test_state_valuation_ok():
    state = {"last_send_date": "2026-08-05"}
    assert verify.check_state("valuation", "市场估值", ("last_send_date",), state) == []


def test_state_valuation_missing_key():
    state = {}
    issues = verify.check_state("valuation", "市场估值", ("last_send_date",), state)
    assert any("缺少 last_send_date" in i for i in issues)


def test_state_rotation_empty_history():
    state = {"holdings_history": [], "portfolio_nav": 1.0, "next_holding": "159915"}
    issues = verify.check_state(
        "etf_rotation_20d", "资产轮动ETF", ("holdings_history", "portfolio_nav", "next_holding"), state
    )
    assert any("holdings_history 为空" in i for i in issues)


def test_state_rotation_ok():
    state = {
        "holdings_history": [{"date": "2026-08-05", "nav": 1.0}],
        "portfolio_nav": 1.0,
        "next_holding": "159915",
    }
    assert verify.check_state(
        "etf_rotation_20d", "资产轮动ETF", ("holdings_history", "portfolio_nav", "next_holding"), state
    ) == []


# ------------------------ check_holdings_continuity ------------------------


def test_continuity_none_returns_empty():
    assert verify.check_holdings_continuity("资产轮动ETF", None) == []


def test_continuity_empty_history_returns_empty():
    assert verify.check_holdings_continuity("资产轮动ETF", {"holdings_history": []}) == []


def test_continuity_ok():
    state = {
        "holdings_history": [
            {"date": "2026-08-04", "nav": 1.0},
            {"date": "2026-08-05", "nav": 1.01},
        ],
        "last_run_date": "2026-08-05",
    }
    assert verify.check_holdings_continuity("资产轮动ETF", state) == []


def test_continuity_non_increasing_date():
    state = {
        "holdings_history": [
            {"date": "2026-08-05", "nav": 1.0},
            {"date": "2026-08-04", "nav": 1.01},
        ]
    }
    issues = verify.check_holdings_continuity("资产轮动ETF", state)
    assert any("非严格递增" in i for i in issues)


def test_continuity_duplicate_date():
    state = {
        "holdings_history": [
            {"date": "2026-08-05", "nav": 1.0},
            {"date": "2026-08-05", "nav": 1.01},
        ]
    }
    issues = verify.check_holdings_continuity("资产轮动ETF", state)
    assert any("非严格递增" in i for i in issues)


def test_continuity_missing_nav():
    state = {"holdings_history": [{"date": "2026-08-05"}]}
    issues = verify.check_holdings_continuity("转债三低轮动", state)
    assert any("nav 为空" in i for i in issues)


def test_continuity_last_date_mismatch():
    state = {
        "holdings_history": [{"date": "2026-08-05", "nav": 1.0}],
        "last_run_date": "2026-08-04",
    }
    issues = verify.check_holdings_continuity("资产轮动ETF", state)
    assert any("!= last_run_date" in i for i in issues)


# -------------------------- check_archive_dates --------------------------


def test_archive_empty():
    assert verify.check_archive_dates("ds", [], "trdDt", TODAY) == ["ds 无记录"]


def test_archive_ok():
    recs = [{"trdDt": "2026-08-04"}, {"trdDt": "2026-08-05"}]
    assert verify.check_archive_dates("ds", recs, "trdDt", TODAY) == []


def test_archive_duplicate_dates():
    recs = [{"trdDt": "2026-08-05"}, {"trdDt": "2026-08-05"}]
    issues = verify.check_archive_dates("ds", recs, "trdDt", TODAY)
    assert any("重复日期" in i for i in issues)


def test_archive_unsorted():
    recs = [{"trdDt": "2026-08-05"}, {"trdDt": "2026-08-04"}]
    issues = verify.check_archive_dates("ds", recs, "trdDt", TODAY)
    assert any("未升序" in i for i in issues)


def test_archive_stale():
    recs = [{"trdDt": "2026-07-20"}]  # 17 天前
    issues = verify.check_archive_dates("ds", recs, "trdDt", TODAY, max_stale_days=10)
    assert any("过期" in i for i in issues)


def test_archive_stale_within_threshold():
    recs = [{"trdDt": "2026-07-30"}]  # 7 天前
    assert verify.check_archive_dates("ds", recs, "trdDt", TODAY, max_stale_days=10) == []


def test_archive_unparseable_last():
    recs = [{"trdDt": "not-a-date"}]
    issues = verify.check_archive_dates("ds", recs, "trdDt", TODAY)
    assert any("无法解析" in i for i in issues)


def test_archive_missing_date_key():
    recs = [{"trdDt": "2026-08-05"}, {"foo": "bar"}]
    issues = verify.check_archive_dates("ds", recs, "trdDt", TODAY)
    assert any("缺 trdDt" in i for i in issues)


# ------------------------ check_guorn_meta_dates ------------------------


def test_guorn_empty():
    assert verify.check_guorn_meta_dates([], TODAY) == ["guorn_meta 无快照"]


def test_guorn_ok():
    assert verify.check_guorn_meta_dates(["2026-08-04", "2026-08-05"], TODAY) == []


def test_guorn_stale():
    issues = verify.check_guorn_meta_dates(["2026-07-20"], TODAY, max_stale_days=10)
    assert any("过期" in i for i in issues)


def test_guorn_duplicate():
    issues = verify.check_guorn_meta_dates(["2026-08-05", "2026-08-05"], TODAY)
    assert any("重复" in i for i in issues)


# -------------------------- check_preview_html --------------------------


def test_preview_empty():
    assert verify.check_preview_html("valuation", "") == ["valuation 为空或缺失"]


def test_preview_leftover_cid():
    issues = verify.check_preview_html("valuation", '<img src="cid:chart1">')
    assert any("残留未解析 cid" in i for i in issues)


def test_preview_ok():
    assert verify.check_preview_html("valuation", '<img src="data:image/png;base64,xxx">') == []


# ------------------------------- run_all -------------------------------


def _make_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    state = tmp_path / "state"
    archive = tmp_path / "archive"
    preview = tmp_path / "preview"

    _write(state / "valuation.json", {"last_send_date": "2026-08-05"})
    _write(
        state / "etf_rotation_20d.json",
        {
            "holdings_history": [{"date": "2026-08-04", "nav": 1.0}, {"date": "2026-08-05", "nav": 1.01}],
            "portfolio_nav": 1.01,
            "next_holding": "159915",
            "last_run_date": "2026-08-05",
        },
    )
    _write(
        state / "cb_three_low.json",
        {
            "holdings_history": [{"date": "2026-08-05", "nav": 1.0}],
            "next_holding": "113001",
        },
    )
    _write(state / "cctda_coal_daily.json", {"article_url": "http://x", "sent_at": "2026-08-05 10:00:00"})

    _write(
        archive / "index_eod" / "000300.json",
        {"records": [{"trdDt": "2026-08-04"}, {"trdDt": "2026-08-05"}]},
    )
    for ds in ("index_dividend_ratio", "index_valuation_percentile"):
        _write(
            archive / ds / "000300.json",
            {"records": [{"trdDt": "2026-08-04"}, {"trdDt": "2026-08-05"}]},
        )
    _write(
        archive / "bond_10y" / "china_10y.json",
        {"records": [{"日期": "2026-08-05"}]},
    )
    _write(
        archive / "fx" / "usd_cnh.json",
        {"records": [{"日期": "2026-08-05"}]},
    )
    _write(archive / "guorn_meta" / "2026-08-05.json", {"x": 1})

    (preview / "valuation.html").parent.mkdir(parents=True, exist_ok=True)
    (preview / "valuation.html").write_text("<html>ok</html>", encoding="utf-8")
    return state, archive, preview


def test_run_all_pass(tmp_path):
    state, archive, preview = _make_tree(tmp_path)
    results = verify.run_all(
        today=TODAY, state_dir=state, archive_dir=archive, preview_dir=preview, max_stale_days=10
    )
    fails = [r for r in results if not r.ok]
    assert not fails, [f"{r.name}: {r.issues}" for r in fails]
    # 应覆盖 4 个状态 + 2 个连续性 + 归档 + 预览
    sections = {r.section for r in results}
    assert {"状态快照", "净值/持仓连续性", "归档日期连续性", "预览HTML"} <= sections


def test_run_all_missing_state(tmp_path):
    state, archive, preview = _make_tree(tmp_path)
    (state / "valuation.json").unlink()
    results = verify.run_all(today=TODAY, state_dir=state, archive_dir=archive, preview_dir=preview)
    val = next(r for r in results if r.name.startswith("市场估值"))
    assert not val.ok
    assert any("状态缺失" in i for i in val.issues)


def test_run_all_stale_archive(tmp_path):
    state, archive, preview = _make_tree(tmp_path)
    _write(archive / "bond_10y" / "china_10y.json", {"records": [{"日期": "2026-07-20"}]})
    results = verify.run_all(
        today=TODAY, state_dir=state, archive_dir=archive, preview_dir=preview, max_stale_days=10
    )
    bond = next(r for r in results if r.name == "bond_10y")
    assert not bond.ok
    assert any("过期" in i for i in bond.issues)


def test_run_all_leftover_cid(tmp_path):
    state, archive, preview = _make_tree(tmp_path)
    (preview / "valuation.html").write_text('<img src="cid:chart1">', encoding="utf-8")
    results = verify.run_all(today=TODAY, state_dir=state, archive_dir=archive, preview_dir=preview)
    pv = next(r for r in results if r.name == "valuation" and r.section == "预览HTML")
    assert not pv.ok


def test_run_all_missing_archive_dir(tmp_path):
    state, archive, preview = _make_tree(tmp_path)
    # 删掉整个 index_valuation_percentile 目录
    import shutil

    shutil.rmtree(archive / "index_valuation_percentile")
    results = verify.run_all(today=TODAY, state_dir=state, archive_dir=archive, preview_dir=preview)
    ivp = next(
        r for r in results if r.name == "index_valuation_percentile" and r.section == "归档日期连续性"
    )
    assert not ivp.ok
    assert any("目录缺失" in i for i in ivp.issues)


# ------------------------------ build_report ------------------------------


def test_build_report_all_pass():
    results = [
        verify.CheckResult("状态快照", "市场估值(valuation)", True, "last_send_date=2026-08-05"),
        verify.CheckResult("归档日期连续性", "bond_10y", False, "", ["bond_10y 末尾日期 2026-07-20 过期"]),
    ]
    md = verify.build_report(results, TODAY)
    assert "# 数据校验报告" in md
    assert "1 项问题" in md
    assert "市场估值" in md
    assert "过期" in md


def test_build_report_no_fails():
    results = [verify.CheckResult("状态快照", "x", True, "OK")]
    md = verify.build_report(results, TODAY)
    assert "全部通过" in md


# --------------------------------- main ---------------------------------


def test_main_writes_report_and_returns_zero(tmp_path, monkeypatch):
    state, archive, preview = _make_tree(tmp_path)
    report_path = tmp_path / "verify_report.md"
    monkeypatch.setattr(verify, "_STATE_DIR", state)
    monkeypatch.setattr(verify, "_ARCHIVE_DIR", archive)
    monkeypatch.setattr(verify, "_PREVIEW_DIR", preview)
    monkeypatch.setattr(verify, "_today", lambda: TODAY)
    import sys as _sys

    monkeypatch.setattr(_sys, "argv", ["verify", "--output", str(report_path)])
    rc = verify.main()
    assert rc == 0
    assert report_path.exists()
    assert "数据校验报告" in report_path.read_text(encoding="utf-8")


def test_main_returns_one_on_failure(tmp_path, monkeypatch):
    state, archive, preview = _make_tree(tmp_path)
    (state / "valuation.json").unlink()  # 制造一个失败
    report_path = tmp_path / "verify_report.md"
    monkeypatch.setattr(verify, "_STATE_DIR", state)
    monkeypatch.setattr(verify, "_ARCHIVE_DIR", archive)
    monkeypatch.setattr(verify, "_PREVIEW_DIR", preview)
    monkeypatch.setattr(verify, "_today", lambda: TODAY)
    import sys as _sys

    monkeypatch.setattr(_sys, "argv", ["verify", "--output", str(report_path)])
    rc = verify.main()
    assert rc == 1
