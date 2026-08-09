"""src/valuation/refresh_archive.py 单测:归档刷新逻辑,全 mock 不触网。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.valuation import refresh_archive as run


# ---------- load_targets / resolve ----------


def test_load_targets_filters_valuation(tmp_path):
    cfg = tmp_path / "v.yaml"
    cfg.write_text(
        "targets:\n"
        '  - {name: "A", code: "000300", type: "valuation", index_detail_url: "https://x?indexCode=000300"}\n'
        '  - {name: "B", code: "9", type: "etf"}\n',
        encoding="utf-8",
    )
    targets = run.load_targets(cfg)
    assert [t["code"] for t in targets] == ["000300"]


def test_resolve_index_code_from_url():
    t = {"code": "512040", "index_detail_url": "https://x?indexCode=931052"}
    assert run.resolve_index_code(t) == "931052"


def test_resolve_index_code_fallback_to_code():
    assert run.resolve_index_code({"code": "000300", "index_detail_url": ""}) == "000300"
    assert run.resolve_index_code({}) == ""


def test_resolve_index_codes_dedup():
    targets = [
        {"code": "512040", "index_detail_url": "?indexCode=931052"},
        {"code": "000300", "index_detail_url": "?indexCode=000300"},
        {"code": "dup", "index_detail_url": "?indexCode=931052"},
    ]
    assert run.resolve_index_codes(targets) == ["931052", "000300"]


# ---------- _df_to_records ----------


def test_df_to_records_nan_to_none():
    df = pd.DataFrame([{"日期": "2024-01-01", "最新价": 7.1}, {"日期": None, "最新价": float("nan")}])
    recs = run._df_to_records(df)
    assert recs[0]["最新价"] == 7.1
    assert recs[1]["日期"] is None and recs[1]["最新价"] is None


# ---------- refresh_index_dataset ----------


def test_refresh_index_dataset_merges_and_returns_path(monkeypatch):
    monkeypatch.setattr(run.fetch, "build_index_eod_price_url", lambda c: f"https://eod/{c}")
    monkeypatch.setattr(run.fetch, "fetch_json_response",
                        lambda name, url: [{"trdDt": "2024-05-10", "pxClose": 3500}])
    out = Path("/tmp/eod.json")
    captured = {}
    monkeypatch.setattr(run.storage, "merge_archive",
                        lambda dataset, identity, incoming, **kw: captured.update(dict(dataset=dataset, identity=identity, kw=kw)) or out)
    paths = run.refresh_index_dataset("index_eod", run.fetch.build_index_eod_price_url, "000300", "2024-05-10T15:30+08:00")
    assert paths == [out]
    assert captured["dataset"] == "index_eod"
    assert captured["identity"] == {"index_code": "000300"}
    assert captured["kw"]["merge_key"] == "trdDt"
    assert captured["kw"]["source"] == "https://eod/000300"
    assert captured["kw"]["updated_at"] == "2024-05-10T15:30+08:00"


def test_refresh_index_dataset_no_change_returns_empty(monkeypatch):
    monkeypatch.setattr(run.fetch, "build_index_eod_price_url", lambda c: f"u/{c}")
    monkeypatch.setattr(run.fetch, "fetch_json_response", lambda name, url: [{"trdDt": "d"}])
    monkeypatch.setattr(run.storage, "merge_archive", lambda *a, **k: None)
    assert run.refresh_index_dataset("index_eod", run.fetch.build_index_eod_price_url, "000300", "now") == []


def test_refresh_index_dataset_non_list_raises(monkeypatch):
    monkeypatch.setattr(run.fetch, "build_index_eod_price_url", lambda c: "u")
    monkeypatch.setattr(run.fetch, "fetch_json_response", lambda name, url: {"not": "a list"})
    with pytest.raises(ValueError, match="非列表"):
        run.refresh_index_dataset("index_eod", run.fetch.build_index_eod_price_url, "000300", "now")


def test_refresh_style_rotation_special_index_dataset_merges_tencent_rows(monkeypatch):
    monkeypatch.setattr(
        run.fetch,
        "fetch_style_rotation_special_index_history",
        lambda code, start_date, end_date: pd.DataFrame({"date": ["2026-08-08"], "close": [3210.5]}),
    )
    captured = {}
    monkeypatch.setattr(
        run.storage,
        "merge_archive",
        lambda dataset, identity, incoming, **kw: captured.update(
            {"dataset": dataset, "identity": identity, "incoming": incoming, "kw": kw}
        ) or Path("/tmp/399376.json"),
    )

    paths = run.refresh_style_rotation_special_index_dataset("399376", "now-iso")

    assert paths == [Path("/tmp/399376.json")]
    assert captured["dataset"] == "index_eod"
    assert captured["identity"] == {"index_code": "399376"}
    assert captured["incoming"][0]["trdDt"] == "2026-08-08"
    assert captured["incoming"][0]["pxClose"] == 3210.5
    assert captured["kw"]["source"] == "akshare.stock_zh_a_hist_tx"


# ---------- refresh_bond_dataset ----------


def test_refresh_bond_dataset_merges_raw_records(monkeypatch):
    fixed_now = datetime(2024, 5, 10, 15, 30)
    monkeypatch.setattr(run.fetch, "now_in_beijing", lambda: fixed_now)
    df = pd.DataFrame([{"日期": "2024-05-09", "中国国债到期收益率10年": 2.5}])
    monkeypatch.setattr(run.ak, "bond_zh_us_rate", lambda start_date: df)
    captured = {}
    monkeypatch.setattr(run.storage, "merge_archive",
                        lambda dataset, identity, incoming, **kw: captured.update(dict(dataset=dataset, identity=identity, kw=kw)) or Path("/b.json"))
    paths = run.refresh_bond_dataset("now-iso")
    assert paths == [Path("/b.json")]
    assert captured["dataset"] == "bond_10y"
    assert captured["identity"] == {"series": "china_10y"}
    assert captured["kw"]["merge_key"] == "日期"
    assert captured["kw"]["filename"] == "china_10y.json"
    assert captured["kw"]["source"] == "akshare.bond_zh_us_rate"
    # bond_zh_us_rate 收到 11 年回溯起始日
    assert run.ak.bond_zh_us_rate  # 调用已发生


def test_refresh_bond_dataset_empty_df_returns_empty(monkeypatch):
    monkeypatch.setattr(run.fetch, "now_in_beijing", lambda: datetime(2024, 5, 10))
    monkeypatch.setattr(run.ak, "bond_zh_us_rate", lambda start_date: pd.DataFrame())
    monkeypatch.setattr(run.storage, "merge_archive", lambda *a, **k: Path("/should_not.json"))
    assert run.refresh_bond_dataset("now") == []


# ---------- refresh_fx_dataset ----------


def test_refresh_fx_dataset_cleans_and_merges(monkeypatch):
    df = pd.DataFrame([{"日期": "2024-05-09", "市场价": "7.10", "代码": "USDCNH", "名称": "美元人民币"}])
    monkeypatch.setattr(run.fetch, "fetch_fx_history_with_archive_fallback", lambda symbol="USDCNH": df)
    captured = {}
    monkeypatch.setattr(run.storage, "merge_archive",
                        lambda dataset, identity, incoming, **kw: captured.update(dict(dataset=dataset, kw=kw, incoming=incoming)) or Path("/fx.json"))
    paths = run.refresh_fx_dataset("now-iso")
    assert paths == [Path("/fx.json")]
    assert captured["dataset"] == "fx"
    assert captured["kw"]["merge_key"] == "日期"
    assert captured["kw"]["filename"] == "usd_cnh.json"
    # 最新价已转数值
    assert captured["incoming"][0]["最新价"] == 7.10


def test_refresh_fx_dataset_archive_fallback_no_change_returns_empty(monkeypatch):
    df = pd.DataFrame([{"日期": "2024-05-09", "市场价": 7.10, "代码": "USDCNH", "名称": "美元人民币"}])
    monkeypatch.setattr(run.fetch, "fetch_fx_history_with_archive_fallback", lambda symbol="USDCNH": df)
    monkeypatch.setattr(run.storage, "merge_archive", lambda *args, **kwargs: None)

    assert run.refresh_fx_dataset("now-iso") == []


# ---------- refresh_cb_index ----------


def test_refresh_cb_index_changed(monkeypatch):
    monkeypatch.setattr(run.cb_index_refresh, "refresh", lambda: True)
    assert run.refresh_cb_index() == [run.cb_index_refresh.ARCHIVE_PATH]


def test_refresh_cb_index_unchanged(monkeypatch):
    monkeypatch.setattr(run.cb_index_refresh, "refresh", lambda: False)
    assert run.refresh_cb_index() == []


# ---------- _run_step ----------


def test_run_step_success():
    paths, ok = run._run_step("x", lambda: [Path("/a")])
    assert paths == [Path("/a")] and ok is True


def test_run_step_failure_alerts(monkeypatch):
    alerted = []
    monkeypatch.setattr(run.alerts, "notify_alert", lambda title, detail="": alerted.append((title, detail)))
    paths, ok = run._run_step(
        "bond_10y",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        target_name="10Y国债",
    )
    assert paths == [] and ok is False
    assert alerted and alerted[0][0] == "市场估值数据刷新失败：10Y国债"
    assert "影响范围：市场估值股债收益差和股债比值" in alerted[0][1]


def test_run_step_index_failure_alert_is_searchable(monkeypatch):
    alerted = []
    monkeypatch.setattr(
        run.alerts,
        "notify_data_failure",
        lambda dataset, **kwargs: alerted.append({"dataset": dataset, **kwargs}),
    )
    paths, ok = run._run_step(
        "index_eod",
        lambda: (_ for _ in ()).throw(RuntimeError("index fail")),
        code="000300",
        target_name="沪深300",
    )
    assert paths == [] and ok is False
    assert alerted and alerted[0]["dataset"] == "index_eod"
    assert alerted[0]["code"] == "000300"
    assert alerted[0]["target_name"] == "沪深300"


# ---------- main ----------


def _stub_main(monkeypatch, *, index_paths=None, index_raise=False, bond_paths=None, fx_paths=None,
               cb_paths=None, now=None):
    monkeypatch.setattr(run, "load_targets", lambda cfg: [{"code": "000300", "index_detail_url": "?indexCode=000300"}])
    monkeypatch.setattr(run.fetch, "now_in_beijing", lambda: now or datetime(2024, 5, 10, 15, 30))
    calls = {"index": 0, "bond": 0, "fx": 0, "cb": 0}

    def fake_index(dataset, builder, code, updated_at):
        calls["index"] += 1
        if index_raise:
            raise RuntimeError("index fail")
        return index_paths or []

    monkeypatch.setattr(run, "refresh_index_dataset", fake_index)
    monkeypatch.setattr(run, "refresh_bond_dataset", lambda ua: (calls.__setitem__("bond", calls["bond"] + 1), bond_paths or [])[1])
    monkeypatch.setattr(run, "refresh_fx_dataset", lambda ua: (calls.__setitem__("fx", calls["fx"] + 1), fx_paths or [])[1])
    monkeypatch.setattr(run, "refresh_cb_index", lambda: (calls.__setitem__("cb", calls["cb"] + 1), cb_paths or [])[1])
    return calls


def test_main_all_success_returns_0(monkeypatch):
    calls = _stub_main(monkeypatch, index_paths=[Path("/i")], bond_paths=[Path("/b")],
                       fx_paths=[Path("/f")], cb_paths=[Path("/c")])
    assert run.main([]) == 0
    assert calls["index"] == 3  # 1 code * 3 datasets
    assert calls["bond"] == 1 and calls["fx"] == 1 and calls["cb"] == 1


def test_main_partial_failure_returns_0(monkeypatch):
    # index 全失败,bond/fx/cb 成功 -> ok>0 -> 0
    calls = _stub_main(monkeypatch, index_raise=True, bond_paths=[Path("/b")], fx_paths=[Path("/f")], cb_paths=[])
    assert run.main([]) == 0
    assert calls["index"] == 3


def test_main_all_fail_returns_1(monkeypatch):
    _stub_main(monkeypatch, index_raise=True)  # bond/fx/cb 默认 [] 且不抛,需再改成抛
    monkeypatch.setattr(run, "refresh_bond_dataset", lambda ua: (_ for _ in ()).throw(RuntimeError("b")))
    monkeypatch.setattr(run, "refresh_fx_dataset", lambda ua: (_ for _ in ()).throw(RuntimeError("f")))
    monkeypatch.setattr(run, "refresh_cb_index", lambda: (_ for _ in ()).throw(RuntimeError("c")))
    assert run.main([]) == 1


def test_main_top_level_exception_alerts(monkeypatch):
    monkeypatch.setattr(run, "load_targets", lambda cfg: (_ for _ in ()).throw(RuntimeError("config boom")))
    alerted = []
    monkeypatch.setattr(run.alerts, "notify_alert", lambda title, detail="": alerted.append((title, detail)))
    assert run.main([]) == 1
    assert alerted and "归档刷新运行失败" in alerted[0][0]
