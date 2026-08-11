"""src/valuation/run.py 单测:配置加载 / 估值核心取数 / 辅 section 收集 / 聚合 / 静默守卫 / 预览。

所有外部取数(指数估值/国债/图表/高股息/果仁/风格轮动/汇率)用 monkeypatch mock,
不触网。静默守卫用 storage.load_state/save_state mock 验证。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.valuation import run


# ---------- load_valuation_config ----------


def test_load_valuation_config_returns_valuation_targets():
    targets = run.load_valuation_config()
    assert len(targets) == 8
    assert all(t.get("type") == "valuation" for t in targets)
    codes = {t["code"] for t in targets}
    assert "000300" in codes and "930955" in codes


def test_load_valuation_config_filters_non_valuation(tmp_path):
    cfg = tmp_path / "valuation.yaml"
    cfg.write_text(
        "targets:\n"
        '  - {name: "A", code: "1", type: "valuation", index_detail_url: "u1"}\n'
        '  - {name: "B", code: "2", type: "etf"}\n'
        '  - {name: "C", code: "3", type: "valuation", index_detail_url: "u3"}\n',
        encoding="utf-8",
    )
    targets = run.load_valuation_config(str(cfg))
    assert [t["code"] for t in targets] == ["1", "3"]


def test_load_valuation_config_empty_raises(tmp_path):
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("targets: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="type=valuation"):
        run.load_valuation_config(str(cfg))


# ---------- _fetch_valuation_items ----------


def _fake_metrics(code, date="2024-05-10"):
    return {
        "index_code": code,
        "index_name": f"指数{code}",
        "index_valuation_date": date,
        "index_valuation_metrics": {
            "PE(TTM)": {"current": 12.0, "percentiles": {"1Y": 50.0}},
            "PB(LF)": {"current": 1.2, "percentiles": {"1Y": 50.0}},
        },
    }


def _fake_bond_history():
    """模拟非空国债历史 DataFrame(_fetch_valuation_items 检查 .empty)。"""
    class _DF:
        empty = False
    return _DF()


def test_fetch_valuation_items_happy_path(monkeypatch, tmp_path):
    targets = [{"name": "A", "code": "000300", "type": "valuation"},
               {"name": "B", "code": "000905", "type": "valuation"}]
    monkeypatch.setattr(run.fetch, "fetch_cn_10y_bond_yield", lambda: 2.5)
    monkeypatch.setattr(
        run.fetch, "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda *a, **k: (_fake_bond_history(), {"data_source": "live", "archive_latest_date": None}),
    )
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda *a, **k: None)
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda *a, **k: None)
    monkeypatch.setattr(
        run.fetch, "fetch_target_index_metrics",
        lambda t: _fake_metrics(t["code"]),
    )
    fake_png = tmp_path / "chart.png"
    fake_png.write_bytes(b"PNG")
    monkeypatch.setattr(run.charts, "generate_valuation_percentile_chart", lambda item, d, **k: fake_png)

    items, chart_paths = run._fetch_valuation_items(targets, tmp_path)
    assert len(items) == 2
    assert items[0]["index_code"] == "000300"
    assert items[0]["name"] == "A"  # 来自 target
    assert set(chart_paths.keys()) == {"000300", "000905"}
    assert chart_paths["000300"] == fake_png


def test_fetch_valuation_items_skips_failed_and_empty(monkeypatch, tmp_path):
    targets = [{"name": "A", "code": "000300", "type": "valuation"},
               {"name": "B", "code": "FAIL", "type": "valuation"},
               {"name": "C", "code": "EMPTY", "type": "valuation"}]
    monkeypatch.setattr(run.fetch, "fetch_cn_10y_bond_yield", lambda: 2.5)
    monkeypatch.setattr(
        run.fetch, "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda *a, **k: (_fake_bond_history(), {"data_source": "live", "archive_latest_date": None}),
    )
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda *a, **k: None)
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda *a, **k: None)

    def fake_fetch(t):
        if t["code"] == "FAIL":
            raise RuntimeError("network")
        if t["code"] == "EMPTY":
            return None
        return _fake_metrics(t["code"])

    monkeypatch.setattr(run.fetch, "fetch_target_index_metrics", fake_fetch)
    monkeypatch.setattr(run.charts, "generate_valuation_percentile_chart", lambda item, d, **k: None)

    items, chart_paths = run._fetch_valuation_items(targets, tmp_path)
    assert len(items) == 1
    assert items[0]["index_code"] == "000300"
    assert chart_paths == {}


def test_fetch_valuation_items_bond_failure_continues(monkeypatch, tmp_path):
    targets = [{"name": "A", "code": "000300", "type": "valuation"}]
    monkeypatch.setattr(
        run.fetch, "fetch_cn_10y_bond_yield",
        lambda: (_ for _ in ()).throw(RuntimeError("bond fail")),
    )
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda *a, **k: None)
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda *a, **k: None)
    monkeypatch.setattr(run.fetch, "fetch_target_index_metrics", lambda t: _fake_metrics(t["code"]))
    monkeypatch.setattr(run.charts, "generate_valuation_percentile_chart", lambda item, d, **k: None)
    items, _ = run._fetch_valuation_items(targets, tmp_path)
    assert len(items) == 1  # 国债失败不中止


def test_fetch_valuation_items_uses_actual_index_code_for_etf_estimate_overlay(monkeypatch, tmp_path):
    """ETF 的配置代码不能替代其已解析出的底层指数代码。"""
    bond_history = _fake_bond_history()
    overlay = SimpleNamespace(item=_fake_metrics("931052"), pe_history="estimated-pe-history")
    calls = {"latest": [], "refresh": [], "load": [], "apply": [], "charts": []}
    monkeypatch.setattr(run.fetch, "fetch_cn_10y_bond_yield", lambda: 2.5)
    monkeypatch.setattr(
        run.fetch, "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda *a, **k: (bond_history, {"data_source": "live", "archive_latest_date": None}),
    )
    monkeypatch.setattr(
        run.fetch, "fetch_target_index_metrics", lambda target: _fake_metrics("931052", "2024-05-10")
    )
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda *a, **k: pytest.fail("overlay 应跳过常规股债比"))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda *a, **k: pytest.fail("overlay 应跳过常规股债收益差"))
    monkeypatch.setattr(
        run,
        "estimate_ledger",
        SimpleNamespace(
            DEFAULT_OUTPUT_ROOT=tmp_path / "ledger",
            refresh_estimate_ledger=lambda code, **kwargs: calls["refresh"].append((code, kwargs)),
            load_estimate_record=lambda code, estimate_date, **kwargs: calls["load"].append((code, estimate_date, kwargs)) or {"estimate_date": estimate_date},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        run,
        "estimate_overlay",
        SimpleNamespace(
            latest_price_date=lambda code, archive_root: calls["latest"].append((code, archive_root)) or "2024-05-10",
            apply_from_archives=lambda item, **kwargs: calls["apply"].append((item, kwargs)) or overlay,
        ),
        raising=False,
    )
    fake_png = tmp_path / "chart.png"
    fake_png.write_bytes(b"PNG")
    monkeypatch.setattr(
        run.charts,
        "generate_valuation_percentile_chart",
        lambda item, directory, **kwargs: calls["charts"].append((item, kwargs)) or fake_png,
    )

    items, chart_paths = run._fetch_valuation_items(
        [{"name": "ETF", "code": "512040", "type": "valuation"}], tmp_path
    )

    assert items[0]["index_code"] == "931052"
    assert calls["latest"][0][0] == "931052"
    assert calls["refresh"] == [("931052", {"bond_history": bond_history})]
    assert calls["load"] == [("931052", "2024-05-10", {"output_root": tmp_path / "ledger"})]
    assert calls["apply"][0][1]["price_date"] == "2024-05-10"
    assert calls["apply"][0][1]["bond_history"] is bond_history
    assert calls["charts"] == [(items[0], {"pe_history": "estimated-pe-history"})]
    assert chart_paths == {"931052": fake_png}


def test_fetch_valuation_items_uses_estimated_item_for_email_and_chart(monkeypatch, tmp_path):
    bond_history = _fake_bond_history()
    estimated_item = _fake_metrics("931052", "2026-08-10")
    estimated_item["estimate_meta"] = {"date": "2026-08-10", "status": "estimated"}
    overlay = SimpleNamespace(item=estimated_item, pe_history="estimated-pe-history")
    chart_items = []
    monkeypatch.setattr(run.fetch, "fetch_cn_10y_bond_yield", lambda: 2.5)
    monkeypatch.setattr(
        run.fetch, "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda *a, **k: (bond_history, {"data_source": "live", "archive_latest_date": None}),
    )
    monkeypatch.setattr(run.fetch, "fetch_target_index_metrics", lambda target: _fake_metrics("931052"))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda *a, **k: pytest.fail("must use overlay"))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda *a, **k: pytest.fail("must use overlay"))
    monkeypatch.setattr(
        run.estimate_ledger, "refresh_estimate_ledger", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        run.estimate_ledger, "load_estimate_record", lambda *a, **k: {"estimate_date": "2026-08-10"},
    )
    monkeypatch.setattr(run.estimate_overlay, "latest_price_date", lambda *a, **k: "2026-08-10")
    monkeypatch.setattr(run.estimate_overlay, "apply_from_archives", lambda *a, **k: overlay)
    monkeypatch.setattr(
        run.charts,
        "generate_valuation_percentile_chart",
        lambda item, directory, **kwargs: chart_items.append((item, kwargs)) or None,
    )

    items, _ = run._fetch_valuation_items(
        [{"name": "ETF", "code": "512040", "type": "valuation"}], tmp_path
    )

    assert items == [estimated_item]
    assert chart_items == [(estimated_item, {"pe_history": "estimated-pe-history"})]


def test_fetch_valuation_items_skips_estimate_when_official_values_match_latest_close(monkeypatch, tmp_path):
    bond_history = _fake_bond_history()
    official_item = _fake_metrics("930955", "2026-08-10")
    official_item["index_dividend_yield_date"] = "2026-08-10"
    monkeypatch.setattr(run.fetch, "fetch_cn_10y_bond_yield", lambda: 2.5)
    monkeypatch.setattr(
        run.fetch,
        "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda *a, **k: (bond_history, {"data_source": "live", "archive_latest_date": None}),
    )
    monkeypatch.setattr(run.fetch, "fetch_target_index_metrics", lambda target: official_item)
    monkeypatch.setattr(run.estimate_overlay, "latest_price_date", lambda *a, **k: "2026-08-10")
    monkeypatch.setattr(run.estimate_ledger, "refresh_estimate_ledger", lambda *a, **k: pytest.fail("official data must not refresh an estimate"))
    monkeypatch.setattr(run.estimate_ledger, "load_estimate_record", lambda *a, **k: pytest.fail("official data must not load an estimate"))
    monkeypatch.setattr(run.estimate_overlay, "apply_from_archives", lambda *a, **k: pytest.fail("official data must not apply an estimate"))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda *a, **k: None)
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda *a, **k: None)
    monkeypatch.setattr(run.charts, "generate_valuation_percentile_chart", lambda *a, **k: None)

    items, _ = run._fetch_valuation_items(
        [{"name": "红利低波", "code": "930955", "type": "valuation"}], tmp_path
    )

    assert items == [{"name": "红利低波", "code": "930955", **official_item}]


def test_fetch_valuation_items_estimate_failure_keeps_item_and_continues(monkeypatch, tmp_path, capsys):
    """单项估算失败只回退该项，后续指数仍可使用覆盖结果。"""
    bond_history = _fake_bond_history()
    overlay = SimpleNamespace(item=_fake_metrics("000905"), pe_history="second-overlay-history")
    attached = []
    chart_histories = []
    monkeypatch.setattr(run.fetch, "fetch_cn_10y_bond_yield", lambda: 2.5)
    monkeypatch.setattr(
        run.fetch, "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda *a, **k: (bond_history, {"data_source": "live", "archive_latest_date": None}),
    )
    monkeypatch.setattr(run.fetch, "fetch_target_index_metrics", lambda target: _fake_metrics(target["code"]))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda item, *a, **k: attached.append(("ratio", item["index_code"])))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda item, *a, **k: attached.append(("spread", item["index_code"])))
    monkeypatch.setattr(
        run,
        "estimate_ledger",
        SimpleNamespace(
            DEFAULT_OUTPUT_ROOT=tmp_path / "ledger",
            refresh_estimate_ledger=lambda *a, **k: None,
            load_estimate_record=lambda code, estimate_date, **kwargs: {"estimate_date": estimate_date},
        ),
        raising=False,
    )
    monkeypatch.setattr(
        run,
        "estimate_overlay",
        SimpleNamespace(
            latest_price_date=lambda code, archive_root: "2024-05-10",
            apply_from_archives=lambda item, **kwargs: (_ for _ in ()).throw(RuntimeError("overlay unavailable"))
            if item["index_code"] == "000300" else overlay,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        run.charts,
        "generate_valuation_percentile_chart",
        lambda item, directory, **kwargs: chart_histories.append((item["index_code"], kwargs.get("pe_history"))) or None,
    )

    items, _ = run._fetch_valuation_items(
        [{"name": "失败项", "code": "000300"}, {"name": "成功项", "code": "000905"}], tmp_path
    )

    assert [item["index_code"] for item in items] == ["000300", "000905"]
    assert attached == [("ratio", "000300"), ("spread", "000300")]
    assert chart_histories == [("000300", None), ("000905", "second-overlay-history")]
    assert "[WARN] 000300 估算覆盖失败" in capsys.readouterr().out


def test_fetch_valuation_items_without_estimate_record_preserves_normal_path(monkeypatch, tmp_path):
    """账本尚无同日记录时，维持原有股债字段和默认图表取数。"""
    bond_history = _fake_bond_history()
    attached = []
    chart_histories = []
    monkeypatch.setattr(run.fetch, "fetch_cn_10y_bond_yield", lambda: 2.5)
    monkeypatch.setattr(
        run.fetch, "fetch_cn_10y_bond_history_with_archive_fallback",
        lambda *a, **k: (bond_history, {"data_source": "live", "archive_latest_date": None}),
    )
    monkeypatch.setattr(run.fetch, "fetch_target_index_metrics", lambda target: _fake_metrics(target["code"]))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_ratio", lambda item, *a, **k: attached.append("ratio"))
    monkeypatch.setattr(run.metrics, "attach_equity_bond_spread", lambda item, *a, **k: attached.append("spread"))
    monkeypatch.setattr(
        run,
        "estimate_ledger",
        SimpleNamespace(
            DEFAULT_OUTPUT_ROOT=tmp_path / "ledger",
            refresh_estimate_ledger=lambda *a, **k: None,
            load_estimate_record=lambda *a, **k: None,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        run,
        "estimate_overlay",
        SimpleNamespace(latest_price_date=lambda *a, **k: "2024-05-10", apply_from_archives=lambda *a, **k: pytest.fail("无账本记录不应应用覆盖")),
        raising=False,
    )
    monkeypatch.setattr(
        run.charts,
        "generate_valuation_percentile_chart",
        lambda item, directory, **kwargs: chart_histories.append(kwargs.get("pe_history")) or None,
    )

    items, _ = run._fetch_valuation_items([{"name": "常规项", "code": "000300"}], tmp_path)

    assert len(items) == 1
    assert attached == ["ratio", "spread"]
    assert chart_histories == [None]


# ---------- _build_extra_sections ----------


def test_build_extra_sections_order_and_merge(monkeypatch, tmp_path):
    monkeypatch.setattr(run.env, "get", lambda name, default="": "guorn_cookie" if name == "GUORN_COOKIE" else default)
    monkeypatch.setattr(run.dividend_render, "build_section",
                        lambda wd: {"html": "<tr>DIV</tr>", "inline_images": {"div_cid": "/d.png"}, "as_of_date": "x"})
    monkeypatch.setattr(run.guorn, "fetch_industry_valuation",
                        lambda cookie: run.guorn.GuornSnapshot(latest_date="2024-05-10", industry_rows=[{"ticker": "1"}]))
    monkeypatch.setattr(run.render, "render_guorn_section", lambda **kw: "<tr>GUORN</tr>")
    monkeypatch.setattr(run.style_rotation, "build_section",
                        lambda wd: {"html": "<tr>SR</tr>", "inline_images": {"style_rotation_chart": "/s.png"}, "as_of_date": "y"})
    monkeypatch.setattr(run.charts, "generate_fx_chart", lambda wd, **k: Path("/fx.png"))
    monkeypatch.setattr(run.render, "render_fx_chart_section", lambda path: "<tr>FX</tr>")

    sections, imgs = run._build_extra_sections(tmp_path)
    # 顺序:风格轮动 -> 汇率图 -> 高股息 -> 果仁
    assert sections == ["<tr>SR</tr>", "<tr>FX</tr>", "<tr>DIV</tr>", "<tr>GUORN</tr>"]
    assert imgs == {"div_cid": "/d.png", "style_rotation_chart": "/s.png", run.render.FX_CHART_CID: str(Path("/fx.png"))}
def test_build_extra_sections_dividend_none_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(run.env, "get", lambda name, default="": "" if name == "GUORN_COOKIE" else default)
    monkeypatch.setattr(run.dividend_render, "build_section", lambda wd: None)
    monkeypatch.setattr(run.style_rotation, "build_section", lambda wd: None)
    monkeypatch.setattr(run.charts, "generate_fx_chart", lambda wd, **k: None)
    sections, imgs = run._build_extra_sections(tmp_path)
    assert sections == []  # 全 skip(guorn 无 cookie 也 skip)
    assert imgs == {}


def test_build_extra_sections_guorn_failure_alerts_and_error_section(monkeypatch, tmp_path):
    monkeypatch.setattr(run.env, "get", lambda name, default="": "bad_cookie" if name == "GUORN_COOKIE" else default)
    monkeypatch.setattr(run.dividend_render, "build_section", lambda wd: None)
    monkeypatch.setattr(run.style_rotation, "build_section", lambda wd: None)
    monkeypatch.setattr(run.charts, "generate_fx_chart", lambda wd, **k: None)
    monkeypatch.setattr(run.guorn, "fetch_industry_valuation",
                        lambda cookie: (_ for _ in ()).throw(RuntimeError("guorn boom")))
    alerted = []
    monkeypatch.setattr(run.alerts, "notify_alert", lambda title, detail="": alerted.append((title, detail)))
    sections, imgs = run._build_extra_sections(tmp_path)
    assert len(sections) == 1  # guorn 错误 section
    assert "果仁行业估值" in sections[0] and "guorn boom" in sections[0]
    assert alerted and alerted[0][0] == "果仁行业估值获取失败"


# ---------- _build_bundle ----------


def test_build_bundle_extracts_valuation_date_and_assembles(monkeypatch, tmp_path):
    items = [{"index_code": "000300", "index_valuation_date": "2024-05-10"}]
    monkeypatch.setattr(run, "load_valuation_config", lambda: [{"code": "000300"}])
    monkeypatch.setattr(run, "_fetch_valuation_items",
                        lambda targets, wd: (items, {"000300": tmp_path / "p.png"}))
    monkeypatch.setattr(run, "_build_extra_sections",
                        lambda wd: (["<tr>EXTRA</tr>"], {"fx": "/fx.png"}))
    assembled = ("<html>ASSEMBLED</html>", {"equity_bond_000300": "/p.png"})
    monkeypatch.setattr(run.render, "assemble_email_html",
                        lambda **kw: (assembled[0], dict(assembled[1])))
    bundle = run._build_bundle(tmp_path)
    assert bundle["html"] == "<html>ASSEMBLED</html>"
    assert bundle["valuation_date"] == "2024-05-10"
    # inline_images 合并:辅 section + 核心
    assert bundle["inline_images"] == {"fx": "/fx.png", "equity_bond_000300": "/p.png"}


def test_build_bundle_valuation_date_empty_when_no_items(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "load_valuation_config", lambda: [])
    monkeypatch.setattr(run, "_fetch_valuation_items", lambda targets, wd: ([], {}))
    monkeypatch.setattr(run, "_build_extra_sections", lambda wd: ([], {}))
    monkeypatch.setattr(run.render, "assemble_email_html", lambda **kw: ("<html></html>", {}))
    bundle = run._build_bundle(tmp_path)
    assert bundle["valuation_date"] == ""


# ---------- _build_subject / _cid_to_data_uri ----------


def test_build_subject_uses_valuation_date(monkeypatch):
    monkeypatch.setattr(run.fetch, "now_in_beijing", lambda: __import__("datetime").datetime(2024, 5, 11))
    assert run._build_subject({"valuation_date": "2024-05-10"}) == "市场估值日报 2024-05-10"


def test_build_subject_falls_back_to_today(monkeypatch):
    import datetime
    monkeypatch.setattr(run.fetch, "now_in_beijing", lambda: datetime.datetime(2024, 5, 11))
    assert run._build_subject({"valuation_date": ""}) == "市场估值日报 2024-05-11"


def test_cid_to_data_uri_replaces_cids(tmp_path):
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n")
    html = '<img src="cid:foo">'
    out = run._cid_to_data_uri(html, {"foo": str(png)})
    assert "data:image/png;base64," in out and "cid:foo" not in out


# ---------- run_send(静默守卫) ----------


def _mock_bundle(monkeypatch, valuation_date, html="<html></html>", imgs=None):
    monkeypatch.setattr(run, "_build_bundle",
                        lambda wd: {"html": html, "inline_images": imgs or {}, "valuation_date": valuation_date})


def _mock_today(monkeypatch, date_str):
    """固定 fetch.now_in_beijing() 返回的日期(守卫按发送日期判断)。"""
    import datetime
    y, m, d = (int(x) for x in date_str.split("-"))
    monkeypatch.setattr(run.fetch, "now_in_beijing", lambda: datetime.datetime(y, m, d))


def test_run_send_same_day_silent_exit(monkeypatch, tmp_path):
    _mock_bundle(monkeypatch, "2024-05-10")
    _mock_today(monkeypatch, "2024-05-10")
    monkeypatch.setattr(run.storage, "load_state", lambda name, default=None: {"last_send_date": "2024-05-10"})
    sent = []
    monkeypatch.setattr(run.email, "send_email", lambda *a, **k: sent.append(k) or True)
    saved = []
    monkeypatch.setattr(run.storage, "save_state", lambda name, obj: saved.append((name, obj)))
    assert run.run_send() == 0
    assert sent == []  # 今日已发,未发信
    assert saved == []  # 未存 state


def test_run_send_new_day_sends_and_saves(monkeypatch, tmp_path):
    _mock_bundle(monkeypatch, "2024-05-10", imgs={"cid": "/x.png"})
    _mock_today(monkeypatch, "2024-05-10")
    monkeypatch.setattr(run.storage, "load_state", lambda name, default=None: {"last_send_date": "2024-05-09"})
    sent = []
    monkeypatch.setattr(run.email, "send_email", lambda subject, html, **k: sent.append(subject) or True)
    saved = []
    monkeypatch.setattr(run.storage, "save_state", lambda name, obj: saved.append((name, obj)))
    assert run.run_send() == 0
    assert sent == ["市场估值日报 2024-05-10"]  # 标题仍用估值基准日
    assert saved == [("valuation", {"last_send_date": "2024-05-10"})]


def test_run_send_first_run_no_state_sends(monkeypatch, tmp_path):
    _mock_bundle(monkeypatch, "2024-05-10")
    _mock_today(monkeypatch, "2024-05-10")
    monkeypatch.setattr(run.storage, "load_state", lambda name, default=None: default or {})
    sent = []
    monkeypatch.setattr(run.email, "send_email", lambda *a, **k: sent.append(1) or True)
    saved = []
    monkeypatch.setattr(run.storage, "save_state", lambda name, obj: saved.append(obj))
    assert run.run_send() == 0
    assert len(sent) == 1
    assert saved == [{"last_send_date": "2024-05-10"}]


def test_run_send_no_valuation_date_still_sends_and_saves(monkeypatch, tmp_path):
    # 估值核心失败(无 valuation_date)-> 守卫不看 valuation_date,照常发信并存 today
    _mock_bundle(monkeypatch, "")
    _mock_today(monkeypatch, "2024-05-10")
    monkeypatch.setattr(run.storage, "load_state", lambda name, default=None: {"last_send_date": "2024-05-09"})
    sent = []
    monkeypatch.setattr(run.email, "send_email", lambda *a, **k: sent.append(1) or True)
    saved = []
    monkeypatch.setattr(run.storage, "save_state", lambda name, obj: saved.append(obj))
    assert run.run_send() == 0
    assert len(sent) == 1
    assert saved == [{"last_send_date": "2024-05-10"}]  # 无 valuation_date 也存 today


def test_run_send_send_failure_returns_1(monkeypatch, tmp_path):
    _mock_bundle(monkeypatch, "2024-05-10")
    _mock_today(monkeypatch, "2024-05-10")
    monkeypatch.setattr(run.storage, "load_state", lambda name, default=None: {})
    monkeypatch.setattr(run.email, "send_email", lambda *a, **k: False)
    saved = []
    monkeypatch.setattr(run.storage, "save_state", lambda name, obj: saved.append(obj))
    assert run.run_send() == 1
    assert saved == []  # 发信失败不存 state


# ---------- run_preview ----------


def test_run_preview_writes_data_uri_html(monkeypatch, tmp_path):
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(run, "_build_bundle",
                        lambda wd: {"html": '<!doctype html><img src="cid:foo">',
                                    "inline_images": {"foo": str(png)},
                                    "valuation_date": "2024-05-10"})
    out_path = tmp_path / "preview.html"
    result = run.run_preview(out_path)
    assert result == out_path
    content = out_path.read_text(encoding="utf-8")
    assert "data:image/png;base64," in content and "cid:foo" not in content


def test_main_preview_flag(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(run, "run_preview", lambda path: called.append(path) or path)
    rc = run.main(["--preview", "--output", str(tmp_path / "p.html")])
    assert rc == 0
    assert called == [tmp_path / "p.html"]


def test_main_default_runs_send(monkeypatch):
    monkeypatch.setattr(run, "run_send", lambda: 42)
    assert run.main([]) == 42
