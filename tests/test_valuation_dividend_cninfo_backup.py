"""src/valuation/dividend/cninfo_backup.py 单测:预热/重试/备份逻辑,全 mock 不触网。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.valuation.dividend import cninfo_backup as cb


# ---------- write_dividend_universe_snapshot ----------


def test_write_dividend_universe_snapshot(tmp_path):
    snapshot = {"date": "2024-05-10", "count": 1, "stocks": []}
    paths = cb.write_dividend_universe_snapshot(tmp_path, snapshot)
    dated = tmp_path / "data" / "dividend_universe" / "2024-05-10.json"
    latest = tmp_path / "data" / "dividend_universe" / "latest.json"
    assert Path(paths["dated_path"]) == dated
    assert Path(paths["latest_path"]) == latest
    assert dated.exists() and latest.exists()
    assert json.loads(dated.read_text(encoding="utf-8"))["date"] == "2024-05-10"


# ---------- build_backup_summary / build_dividend_universe_snapshot ----------


def test_build_backup_summary():
    result = {"status": "success", "date": "2024-05-10", "universe_count": 5, "archived_count": 5,
              "created_count": 2, "updated_count": 1, "unchanged_count": 2, "failed_count": 0}
    text = cb.build_backup_summary(result)
    assert "财报备份结果" in text and "成功 5 只" in text and "新增 2 只" in text and "失败 0 只" in text


def test_build_dividend_universe_snapshot():
    filtered = {"rows": [{"cell": {"stock_id": "000001", "stock_nm": "X", "industry_nm": "银行",
                                   "dividend_rate": 5.0, "pe": 4, "pb": 0.5, "roe": 12, "ttm_parent_net_profit_yi": 100}}]}
    snap = cb.build_dividend_universe_snapshot("2024-05-10", "2024-05-10T15:30:00+08:00", filtered)
    assert snap["date"] == "2024-05-10" and snap["count"] == 1
    assert snap["stocks"][0]["stock_code"] == "000001"
    assert snap["filters"]["pe_max"] == cb.DIVIDEND_FORM_DATA["pe"]
    assert snap["filters"]["dividend_rate_min"] == cb.DIVIDEND_FORM_DATA["dividend_rate"]
    assert "state_owned_whitelist_file" in snap["filters"]


# ---------- fetch_dividend_email_supplement_universe ----------


def test_supplement_universe_empty_xcid():
    assert cb.fetch_dividend_email_supplement_universe(supplement_xcid="") == []


def test_supplement_universe_dedup(monkeypatch):
    def fake_fetcher(xcid):
        return {"rows": [
            {"SECURITY_CODE": "000001", "SECURITY_SHORT_NAME": "X"},
            {"SECURITY_CODE": "sz000001", "SECURITY_SHORT_NAME": "X2"},  # 归一后重复
            {"SECURITY_CODE": "000002", "SECURITY_SHORT_NAME": "Y"},
        ]}
    monkeypatch.setattr(cb, "filter_dividend_email_supplement_rows", lambda rows: (rows, []))
    universe = cb.fetch_dividend_email_supplement_universe(supplement_xcid="xc1", supplement_fetcher=fake_fetcher)
    codes = [u["stock_code"] for u in universe]
    assert codes == ["000001", "000002"]


# ---------- run_backup ----------


def test_run_backup_success(monkeypatch, tmp_path):
    raw = {"rows": [{"cell": {"stock_id": "000001", "stock_nm": "X"}}, {"cell": {"stock_id": "000002", "stock_nm": "Y"}}]}
    monkeypatch.setattr(cb, "fetch_data", lambda *a, **k: raw)
    monkeypatch.setattr(cb, "filter_dividend_rows_by_secondary_rules", lambda data: data)
    monkeypatch.setattr(cb, "fetch_financial_bundle", lambda code: {"bundle": code})
    monkeypatch.setattr(cb, "build_financial_snapshot_payload", lambda bundle, fetched_at: {"p": True})
    monkeypatch.setattr(cb, "archive_financial_snapshot", lambda root_dir, payload: {"status": "created"})
    result = cb.run_backup(root_dir=tmp_path)
    assert result["status"] == "success"
    assert result["universe_count"] == 2 and result["archived_count"] == 2 and result["created_count"] == 2
    # dividend_universe 快照已写
    assert (tmp_path / "data" / "dividend_universe" / "latest.json").exists()


def test_run_backup_partial_failure(monkeypatch, tmp_path):
    raw = {"rows": [{"cell": {"stock_id": "000001", "stock_nm": "X"}}, {"cell": {"stock_id": "000002", "stock_nm": "Y"}}]}
    monkeypatch.setattr(cb, "fetch_data", lambda *a, **k: raw)
    monkeypatch.setattr(cb, "filter_dividend_rows_by_secondary_rules", lambda data: data)
    def bundle(code):
        if code == "000002":
            raise RuntimeError("cninfo boom")
        return {"bundle": code}
    monkeypatch.setattr(cb, "fetch_financial_bundle", bundle)
    monkeypatch.setattr(cb, "build_financial_snapshot_payload", lambda b, f: {})
    monkeypatch.setattr(cb, "archive_financial_snapshot", lambda rd, p: {"status": "created"})
    result = cb.run_backup(root_dir=tmp_path)
    assert result["status"] == "partial_failed"
    assert result["failed_count"] == 1 and result["failed_codes"] == ["000002"]


# ---------- build_warmup_summary / should_notify ----------


def test_build_warmup_summary_retry_with_shard():
    result = {"mode": "retry", "status": "partial_failed", "date": "2024-05-10", "slot": "07",
              "started_at": "s", "finished_at": "f", "elapsed_seconds": 12.3,
              "universe_count": 10, "work_count": 3, "total_work_count": 3, "selected_count": 3,
              "success_count": 2, "failed_count": 1, "shard_label": "1/5",
              "successes": [{"stock_code": "000001", "stock_name": "X", "snapshot_status": "created", "elapsed_seconds": 1.0}],
              "failures": [{"stock_code": "000002", "stock_name": "Y", "reason": "boom", "elapsed_seconds": 2.0}]}
    text = cb.build_warmup_summary(result)
    assert "财报缓存重试结果" in text and "分片 1/5" in text and "成功 2 只" in text and "失败 1 只" in text
    assert "000001" in text and "000002" in text


def test_should_notify_warmup_result():
    assert cb.should_notify_warmup_result({"failed_count": 1}) is True
    assert cb.should_notify_warmup_result({"failed_count": 0, "warnings": ["w"]}) is True
    assert cb.should_notify_warmup_result({"failed_count": 0, "warnings": []}) is False


# ---------- run_incremental_warmup ----------


def _raw_rows():
    return {"rows": [{"cell": {"stock_id": "000001", "stock_nm": "X"}}, {"cell": {"stock_id": "000002", "stock_nm": "Y"}}]}


def _stub_warmup(monkeypatch, *, get_or_fetch=None, load_cached=None, supplement_rows=None):
    monkeypatch.setattr(cb, "fetch_data", lambda *a, **k: _raw_rows())
    monkeypatch.setattr(cb, "fetch_all_results_by_xcid", lambda xcid: {"rows": supplement_rows or []})
    monkeypatch.setattr(cb, "filter_dividend_email_supplement_rows", lambda rows: (rows, []))
    monkeypatch.setattr(cb, "load_stock_code_whitelist_from_xlsx", lambda p: {"000001", "000002"})
    monkeypatch.setattr(cb, "get_or_fetch_financial_snapshot",
                        get_or_fetch or (lambda *a, **k: {"archive_status": "created", "stock_name": "X"}))
    if load_cached is not None:
        monkeypatch.setattr(cb, "load_cached_financial_snapshot", load_cached)


def test_warmup_full_success(monkeypatch):
    _stub_warmup(monkeypatch)
    result = cb.run_incremental_warmup(
        stock_code_whitelist={"000001", "000002"}, delay_seconds=0, fetched_at="2024-05-10T15:30:00+08:00")
    assert result["status"] == "success"
    assert result["universe_count"] == 2 and result["selected_count"] == 2 and result["success_count"] == 2
    assert result["mode"] == "warmup" and result["shard_label"] == ""


def test_warmup_shard(monkeypatch):
    _stub_warmup(monkeypatch)
    result = cb.run_incremental_warmup(
        stock_code_whitelist={"000001", "000002"}, delay_seconds=0,
        shard_count=2, shard_index=0, fetched_at="2024-05-10T15:30:00+08:00")
    assert result["shard_label"] == "1/2"
    assert result["total_work_count"] == 2 and result["selected_count"] == 1  # 本片 1 只


def test_warmup_max_per_run(monkeypatch):
    _stub_warmup(monkeypatch)
    result = cb.run_incremental_warmup(
        stock_code_whitelist={"000001", "000002"}, delay_seconds=0, max_per_run=1,
        fetched_at="2024-05-10T15:30:00+08:00")
    assert result["selected_count"] == 1 and result["remaining_count"] == 1


def test_warmup_time_budget_stops_early(monkeypatch):
    _stub_warmup(monkeypatch)
    result = cb.run_incremental_warmup(
        stock_code_whitelist={"000001", "000002"}, delay_seconds=0, time_budget_seconds=1e-9,
        fetched_at="2024-05-10T15:30:00+08:00")
    # 预算即刻用尽:只抓第 1 只,第 2 只留待下轮
    assert result["success_count"] == 1 and result["skipped_count"] == 1
    assert result["remaining_count"] == 1
    assert "留待下轮 1 只" in cb.build_warmup_summary(result)


def test_warmup_all_fail(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cninfo down")
    _stub_warmup(monkeypatch, get_or_fetch=boom)
    result = cb.run_incremental_warmup(
        stock_code_whitelist={"000001", "000002"}, delay_seconds=0, fetched_at="2024-05-10T15:30:00+08:00")
    assert result["status"] == "failed" and result["success_count"] == 0 and result["failed_count"] == 2
    assert cb.should_notify_warmup_result(result) is True


def test_warmup_retry_only_not_checked_today(monkeypatch):
    # 000001 今日已抓(跳过),000002 未抓(进 work)
    def load_cached(code, root_dir=None):
        if code == "000001":
            return {"fetched_at": "2024-05-10T08:00:00+08:00"}
        return None
    _stub_warmup(monkeypatch, load_cached=load_cached)
    result = cb.run_incremental_warmup(
        stock_code_whitelist={"000001", "000002"}, delay_seconds=0, only_not_checked_today=True,
        fetched_at="2024-05-10T15:30:00+08:00")
    assert result["mode"] == "retry"
    assert result["selected_count"] == 1  # 仅 000002


def test_warmup_empty_universe_raises(monkeypatch):
    monkeypatch.setattr(cb, "fetch_data", lambda *a, **k: {"rows": []})
    monkeypatch.setattr(cb, "fetch_all_results_by_xcid", lambda xcid: {"rows": []})
    monkeypatch.setattr(cb, "filter_dividend_email_supplement_rows", lambda rows: (rows, []))
    with pytest.raises(RuntimeError, match="集思录返回空数据"):
        cb.run_incremental_warmup(stock_code_whitelist=set(), delay_seconds=0,
                                  fetched_at="2024-05-10T15:30:00+08:00")


def test_warmup_shard_index_out_of_range(monkeypatch):
    _stub_warmup(monkeypatch)
    with pytest.raises(RuntimeError, match="分片索引越界"):
        cb.run_incremental_warmup(stock_code_whitelist={"000001"}, delay_seconds=0,
                                  shard_count=2, shard_index=5, fetched_at="2024-05-10T15:30:00+08:00")


def test_warmup_supplement_failure_warning(monkeypatch):
    def boom_fetcher(xcid):
        raise RuntimeError("eastmoney down")
    monkeypatch.setattr(cb, "fetch_data", lambda *a, **k: _raw_rows())
    monkeypatch.setattr(cb, "fetch_all_results_by_xcid", boom_fetcher)
    monkeypatch.setattr(cb, "load_stock_code_whitelist_from_xlsx", lambda p: {"000001", "000002"})
    monkeypatch.setattr(cb, "get_or_fetch_financial_snapshot", lambda *a, **k: {"archive_status": "created"})
    result = cb.run_incremental_warmup(stock_code_whitelist={"000001", "000002"}, delay_seconds=0,
                                       fetched_at="2024-05-10T15:30:00+08:00")
    assert result["warnings"] and "东财补充池" in result["warnings"][0]
    assert cb.should_notify_warmup_result(result) is True  # warnings 触发通知


# ---------- main ----------


def test_main_warmup_success_no_alert(monkeypatch):
    monkeypatch.setattr(cb, "run_incremental_warmup", lambda *a, **k: {"status": "success", "mode": "warmup",
                                                                       "date": "d", "slot": "1", "started_at": "s", "finished_at": "f",
                                                                       "elapsed_seconds": 1, "universe_count": 1, "work_count": 1,
                                                                       "total_work_count": 1, "selected_count": 1, "success_count": 1,
                                                                       "failed_count": 0, "warnings": [], "successes": [], "failures": []})
    alerted = []
    monkeypatch.setattr(cb.alerts, "notify_alert", lambda title, detail="": alerted.append(title))
    assert cb.main(["--warmup"]) == 0
    assert alerted == []  # 无失败不报警


def test_main_warmup_partial_alerts_best_effort(monkeypatch):
    monkeypatch.setattr(cb, "run_incremental_warmup", lambda *a, **k: {"status": "partial_failed", "mode": "warmup",
                                                                       "date": "d", "slot": "1", "started_at": "s", "finished_at": "f",
                                                                       "elapsed_seconds": 1, "universe_count": 2, "work_count": 2,
                                                                       "total_work_count": 2, "selected_count": 2, "success_count": 1,
                                                                       "failed_count": 1, "warnings": [], "successes": [], "failures": [{"stock_code": "x", "stock_name": "X", "reason": "boom", "elapsed_seconds": 1.0}]})
    alerted = []
    monkeypatch.setattr(cb.alerts, "notify_alert", lambda title, detail="": alerted.append(title))
    assert cb.main(["--warmup"]) == 0  # best-effort 不退出非 0
    assert alerted and "预热结果" in alerted[0]


def test_main_warmup_exception_returns_1(monkeypatch):
    monkeypatch.setattr(cb, "run_incremental_warmup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    alerted = []
    monkeypatch.setattr(cb.alerts, "notify_alert", lambda title, detail="": alerted.append(title))
    assert cb.main(["--warmup"]) == 1
    assert alerted and "预热失败" in alerted[0]


def test_main_backup_success(monkeypatch):
    monkeypatch.setattr(cb, "run_backup", lambda root_dir: {"status": "success", "date": "d", "universe_count": 1,
                                                            "archived_count": 1, "created_count": 1, "updated_count": 0,
                                                            "unchanged_count": 0, "failed_count": 0, "failed_codes": []})
    alerted = []
    monkeypatch.setattr(cb.alerts, "notify_alert", lambda title, detail="": alerted.append(title))
    assert cb.main([]) == 0
    assert alerted == []


def test_main_backup_partial_returns_1(monkeypatch):
    monkeypatch.setattr(cb, "run_backup", lambda root_dir: {"status": "partial_failed", "date": "d", "universe_count": 2,
                                                            "archived_count": 1, "created_count": 1, "updated_count": 0,
                                                            "unchanged_count": 0, "failed_count": 1, "failed_codes": ["x"]})
    alerted = []
    monkeypatch.setattr(cb.alerts, "notify_alert", lambda title, detail="": alerted.append(title))
    assert cb.main([]) == 1
    assert alerted and "部分失败" in alerted[0]


def test_main_backup_exception_returns_1(monkeypatch):
    monkeypatch.setattr(cb, "run_backup", lambda root_dir: (_ for _ in ()).throw(RuntimeError("boom")))
    alerted = []
    monkeypatch.setattr(cb.alerts, "notify_alert", lambda title, detail="": alerted.append(title))
    assert cb.main([]) == 1
    assert alerted and "备份失败" in alerted[0]


def test_main_warmup_writes_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "run_incremental_warmup", lambda *a, **k: {"status": "success", "mode": "warmup",
                                                                       "date": "2024-05-10", "slot": "1", "started_at": "s", "finished_at": "f",
                                                                       "elapsed_seconds": 1, "universe_count": 1, "work_count": 1,
                                                                       "total_work_count": 1, "selected_count": 1, "success_count": 1,
                                                                       "failed_count": 0, "warnings": [], "successes": [], "failures": []})
    monkeypatch.setattr(cb.alerts, "notify_alert", lambda *a, **k: None)
    summary = tmp_path / "summary.json"
    assert cb.main(["--warmup", "--summary-path", str(summary)]) == 0
    assert json.loads(summary.read_text(encoding="utf-8"))["date"] == "2024-05-10"
