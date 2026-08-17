"""src/valuation/fetch.py 单测。

不触网:网络层(fetch_json_response / akshare / requests)与归档回退均用 monkeypatch +
tmp_path 归档 fixture 覆盖。符号/解析/归档新鲜度/多源回退/聚合编排各路径均有用例。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

from src.valuation import fetch


@pytest.fixture(autouse=True)
def _clear_chinabond_cache():
    """模块级 _CHINABOND_CACHE 跨用例残留会假绿:每用例前后清空。"""
    fetch._CHINABOND_CACHE.clear()
    yield
    fetch._CHINABOND_CACHE.clear()


# ---------- 符号 / DataFrame 小工具 ----------


def test_extract_index_digits():
    assert fetch.extract_index_digits("csi930955") == "930955"
    assert fetch.extract_index_digits("sh000300") == "000300"
    assert fetch.extract_index_digits("980081.SZ") == "980081"
    assert fetch.extract_index_digits("abc") == ""


def test_build_tickflow_index_symbols_sh_prefix():
    syms = fetch.build_tickflow_index_symbols("sh000300")
    assert syms[0] == "000300.SH"


def test_build_tickflow_index_symbols_sz_prefix():
    syms = fetch.build_tickflow_index_symbols("sz399303")
    assert syms[0] == "000300.SH"[:0] + "399303.SZ"


def test_build_em_index_symbols_explicit_suffix():
    # 980081.sz -> sz980081 优先
    syms = fetch.build_em_index_symbols("980081.sz")
    assert syms[0] == "sz980081"


def test_build_numeric_index_symbols():
    syms = fetch.build_numeric_index_symbols("csi930955")
    assert "930955" in syms


def test_dedupe_keep_order():
    assert fetch.dedupe_keep_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_normalize_dataframe_detects_columns():
    df = pd.DataFrame({"日期": ["2026-08-01", "2026-08-02"], "收盘": [10.0, 11.0]})
    out = fetch.normalize_dataframe(df)
    assert list(out.columns) == ["date", "close"]
    assert len(out) == 2
    assert out["close"].iloc[-1] == 11.0


def test_normalize_dataframe_empty():
    out = fetch.normalize_dataframe(pd.DataFrame())
    assert out.empty
    assert list(out.columns) == ["date", "close"]


def test_normalize_dataframe_unknown_columns_raises():
    with pytest.raises(ValueError, match="无法识别"):
        fetch.normalize_dataframe(pd.DataFrame({"foo": [1], "bar": [2]}))


def test_clip_dataframe_by_date():
    df = pd.DataFrame({
        "date": pd.to_datetime(["20260101", "20260201", "20260301"]),
        "close": [1.0, 2.0, 3.0],
    })
    clipped = fetch.clip_dataframe_by_date(df, "20260115", "20260215")
    assert len(clipped) == 1
    assert clipped["close"].iloc[0] == 2.0


# ---------- URL 构造 ----------


def test_build_index_detail_url():
    assert fetch.build_index_detail_url("csi930955") == (
        "https://www.etf.com.cn/api/etf-api-service/index/detail?indexCode=930955"
    )


def test_build_index_detail_url_bad_code_raises():
    with pytest.raises(ValueError, match="无法识别"):
        fetch.build_index_detail_url("abc")


def test_build_index_dividend_yield_url():
    assert fetch.build_index_dividend_yield_url("000300") == (
        "https://cdn.efunds.com.cn/etf-net/index_dividend_ratio_000300.json"
    )


def test_build_index_eod_price_url():
    assert "index_eod_price_000300" in fetch.build_index_eod_price_url("000300")


def test_fetch_latest_index_close_prefers_realtime_quote(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_eod" / "931233.json",
        [{"trdDt": "2026-08-11", "pxClose": 1148.5547}],
    )
    monkeypatch.setattr(
        fetch.requests,
        "get",
        lambda *args, **kwargs: type(
            "Response",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: {
                    "data": {
                        "latestQuote": {
                            "date": 20260812,
                            "lastPrice": 1140.986,
                        }
                    }
                },
            },
        )(),
    )

    result = fetch.fetch_latest_index_close_with_archive_fallback(
        "931233", archive_root=tmp_path
    )

    assert result == {"date": "2026-08-12", "close": 1140.986, "data_source": "live"}


def test_fetch_latest_index_close_falls_back_to_archive_when_realtime_quote_fails(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_eod" / "931233.json",
        [{"trdDt": "2026-08-11", "pxClose": 1148.5547}],
    )
    monkeypatch.setattr(
        fetch.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.RequestException("quote down")),
    )

    result = fetch.fetch_latest_index_close_with_archive_fallback(
        "931233", archive_root=tmp_path
    )

    assert result == {"date": "2026-08-11", "close": 1148.5547, "data_source": "archive"}


def test_valuation_target_extracts_index_code_from_detail_url():
    target = {
        "type": "valuation",
        "code": "000300",
        "index_detail_url": "https://example.test/index/detail?indexCode=000300",
    }
    assert fetch.resolve_target_index_code(target) == "000300"


def test_build_index_valuation_percentile_url():
    assert "index_valuation_percentile_930955" in fetch.build_index_valuation_percentile_url("csi930955")


# ---------- parse_index_detail_response ----------


def test_parse_index_detail_response_ok():
    payload = {"data": {"trdCode": "000300", "indexName": "沪深300", "dividendRatioJson": "http://x/d.json"}}
    out = fetch.parse_index_detail_response(payload, fallback_index_code="000300")
    assert out["index_code"] == "000300"
    assert out["index_name"] == "沪深300"
    assert out["index_dividend_yield_url"] == "http://x/d.json"


def test_parse_index_detail_response_errors():
    with pytest.raises(ValueError, match="格式异常"):
        fetch.parse_index_detail_response(["not", "dict"])
    with pytest.raises(ValueError, match="缺少 data"):
        fetch.parse_index_detail_response({"data": []})


# ---------- parse_index_dividend_yield_rows ----------


def _div_rows() -> List[Dict]:
    return [
        {"trdDt": "2026-07-30", "dividendYield": 2.5, "trdCode": "000300"},
        {"trdDt": "2026-08-04", "dividendYield": 2.53, "trdCode": "000300"},
    ]


def test_parse_index_dividend_yield_rows_ok():
    out = fetch.parse_index_dividend_yield_rows(_div_rows(), fallback_index_code="000300")
    assert out["index_dividend_yield"] == 2.53
    assert out["index_dividend_yield_date"] == "2026-08-04"


def test_parse_index_dividend_yield_rows_empty_raises():
    with pytest.raises(ValueError, match="未返回有效数据"):
        fetch.parse_index_dividend_yield_rows([{"trdDt": "2026-08-04"}])  # 缺 dividendYield


def test_parse_index_dividend_yield_rows_non_list_raises():
    with pytest.raises(ValueError, match="格式异常"):
        fetch.parse_index_dividend_yield_rows({"not": "list"})


# ---------- parse_index_eod_price_rows ----------


def test_parse_index_eod_price_rows_ok():
    rows = [{"trdDt": "2026-08-01", "pxClose": 4000.0}, {"trdDt": "2026-08-04", "pxClose": 4050.0}]
    df = fetch.parse_index_eod_price_rows(rows)
    assert len(df) == 2
    assert df["close"].iloc[-1] == 4050.0


def test_parse_index_eod_price_rows_empty_raises():
    with pytest.raises(ValueError, match="未返回有效数据"):
        fetch.parse_index_eod_price_rows([{"trdDt": "2026-08-04"}])


# ---------- parse_index_valuation_percentile_rows ----------


def test_parse_index_valuation_percentile_rows_ok():
    rows = [
        {"trdDt": "2026-08-01", "pETtm": 12.0, "pETtm5Y": 30.0, "pBLf": 1.2, "pBLf5Y": 40.0},
        {"trdDt": "2026-08-04", "pETtm": 12.5, "pETtm5Y": 35.0, "pBLf": 1.3, "pBLf5Y": 45.0},
    ]
    out = fetch.parse_index_valuation_percentile_rows(rows, fallback_index_code="000300")
    assert out["index_valuation_date"] == "2026-08-04"
    assert "PE(TTM)" in out["index_valuation_metrics"]
    assert out["index_valuation_metrics"]["PE(TTM)"]["current"] == 12.5
    assert out["index_valuation_metrics"]["PE(TTM)"]["percentiles"]["5Y"] == 35.0


def test_parse_index_valuation_percentile_rows_no_metrics_raises():
    with pytest.raises(ValueError, match="未返回有效估值字段"):
        fetch.parse_index_valuation_percentile_rows([{"trdDt": "2026-08-04"}])


# ---------- resolve_target_index_code ----------


def test_resolve_target_index_code():
    assert fetch.resolve_target_index_code({"tracking_index_code": "000300"}) == "000300"
    assert fetch.resolve_target_index_code({"type": "index", "code": "930955"}) == "930955"
    assert fetch.resolve_target_index_code({"type": "etf", "code": "510300"}) == ""


# ---------- 归档读取 / 新鲜度 / 元信息 ----------


def _write_archive(path: Path, records: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "test", "records": records}, ensure_ascii=False), encoding="utf-8")


def test_archive_path_dataset_specific():
    assert fetch._archive_path("bond_10y").name == "china_10y.json"
    assert fetch._archive_path("fx").name == "usd_cnh.json"
    assert fetch._archive_path("index_dividend_ratio", index_code="000300").name == "000300.json"


def test_archive_path_requires_index_code():
    with pytest.raises(ValueError, match="requires index_code"):
        fetch._archive_path("index_dividend_ratio")


def test_load_archive_records_reads_records(tmp_path):
    path = tmp_path / "index_dividend_ratio" / "000300.json"
    _write_archive(path, [{"trdDt": "2026-08-04", "dividendYield": 2.5}, {"junk": 1}, "x"])
    records = fetch.load_archive_records("index_dividend_ratio", index_code="000300", archive_root=tmp_path)
    # load_existing_records 过滤非 dict
    assert len(records) == 2
    assert records[0]["dividendYield"] == 2.5


def test_get_latest_record_date():
    records = [
        {"trdDt": "2026-08-01"},
        {"trdDt": "2026-08-04"},
        {"date": "2026-08-06"},  # date 字段也识别
    ]
    assert fetch._get_latest_record_date(records, ("trdDt", "date")) == "2026-08-06"


def test_is_archive_fresh_within_7_days():
    now = datetime(2026, 8, 6, tzinfo=fetch.BEIJING_TZ)
    assert fetch.is_archive_fresh("2026-08-04", now=now) is True


def test_is_archive_fresh_stale():
    assert fetch.is_archive_fresh("2026-01-01") is False


def test_is_archive_fresh_unparseable():
    assert fetch.is_archive_fresh("not-a-date") is False


def test_build_and_combine_archive_meta():
    live = fetch._build_archive_meta("live", None)
    arch = fetch._build_archive_meta("archive", "2026-08-04")
    assert live == {"data_source": "live", "archive_latest_date": None}
    assert arch == {"data_source": "archive", "archive_latest_date": "2026-08-04"}
    # 全 live -> live
    assert fetch._combine_archive_meta(live, None) == {"data_source": "live", "archive_latest_date": None}
    # 含 archive -> archive,取最新日期
    combined = fetch._combine_archive_meta(live, fetch._build_archive_meta("archive", "2026-08-01"), arch)
    assert combined == {"data_source": "archive", "archive_latest_date": "2026-08-04"}


# ---------- fetch_json_response ----------


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_json_response_ok(monkeypatch):
    monkeypatch.setattr(fetch.requests, "get", lambda url, timeout=15, headers=None: _FakeResp({"k": 1}))
    assert fetch.fetch_json_response("n", "http://x") == {"k": 1}


def test_fetch_json_response_retries(monkeypatch):
    import requests as real_requests

    calls = {"n": 0}

    def flaky(url, timeout=15, headers=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise real_requests.exceptions.Timeout("boom")
        return _FakeResp([1, 2])

    monkeypatch.setattr(fetch.requests, "get", flaky)
    monkeypatch.setattr(fetch.alerts.time, "sleep", lambda *_: None)
    assert fetch.fetch_json_response("n", "http://x") == [1, 2]
    assert calls["n"] == 2


# ---------- fetch_index_detail ----------


def test_fetch_index_detail_uses_url(monkeypatch):
    captured = {}

    def fake_fetch_json(name, url):
        captured.update(name=name, url=url)
        return {"data": {"trdCode": "000300", "indexName": "沪深300"}}

    monkeypatch.setattr(fetch, "fetch_json_response", fake_fetch_json)
    out = fetch.fetch_index_detail("000300", url="http://custom/detail")
    assert captured["url"] == "http://custom/detail"
    assert out["index_detail_url"] == "http://custom/detail"
    assert out["index_name"] == "沪深300"


# ---------- archive fallback: dividend yield ----------


def test_dividend_yield_live_success(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "fetch_json_response", lambda n, u: _div_rows())
    out = fetch.fetch_index_dividend_yield_with_archive_fallback(
        "000300", archive_root=tmp_path, now=datetime(2026, 8, 6, tzinfo=fetch.BEIJING_TZ)
    )
    assert out["data_source"] == "live"
    assert out["archive_latest_date"] is None
    assert out["index_dividend_yield"] == 2.53


def test_dividend_yield_fallback_to_fresh_archive(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_dividend_ratio" / "000300.json",
        [{"trdDt": "2026-08-04", "dividendYield": 2.5, "trdCode": "000300"}],
    )
    monkeypatch.setattr(fetch, "fetch_json_response", lambda n, u: (_ for _ in ()).throw(RuntimeError("net")))
    out = fetch.fetch_index_dividend_yield_with_archive_fallback(
        "000300", archive_root=tmp_path, now=datetime(2026, 8, 6, tzinfo=fetch.BEIJING_TZ)
    )
    assert out["data_source"] == "archive"
    assert out["archive_latest_date"] == "2026-08-04"
    assert out["index_dividend_yield"] == 2.5


def test_dividend_yield_stale_archive_reraises(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_dividend_ratio" / "000300.json",
        [{"trdDt": "2026-01-01", "dividendYield": 2.5, "trdCode": "000300"}],
    )
    monkeypatch.setattr(fetch, "fetch_json_response", lambda n, u: (_ for _ in ()).throw(RuntimeError("net")))
    with pytest.raises(RuntimeError, match="net"):
        fetch.fetch_index_dividend_yield_with_archive_fallback("000300", archive_root=tmp_path)


# ---------- archive fallback: valuation percentile ----------


def test_valuation_percentile_fallback_to_archive(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_valuation_percentile" / "000300.json",
        [{"trdDt": "2026-08-04", "pETtm": 12.5, "pETtm5Y": 35.0}],
    )
    monkeypatch.setattr(fetch, "fetch_json_response", lambda n, u: (_ for _ in ()).throw(RuntimeError("net")))
    out = fetch.fetch_index_valuation_percentile_with_archive_fallback(
        "000300", archive_root=tmp_path, now=datetime(2026, 8, 6, tzinfo=fetch.BEIJING_TZ)
    )
    assert out["data_source"] == "archive"
    assert out["index_valuation_date"] == "2026-08-04"
    assert out["index_valuation_metrics"]["PE(TTM)"]["current"] == 12.5


# ---------- archive fallback: 10y bond history ----------


def test_bond_history_fallback_to_archive(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "bond_10y" / "china_10y.json",
        [{"日期": "2026-08-04", "中国国债收益率10年": 1.71}, {"日期": "2026-08-03", "中国国债收益率10年": 1.70}],
    )
    monkeypatch.setattr(fetch, "fetch_cn_10y_bond_history", lambda lookback_years=11: (_ for _ in ()).throw(RuntimeError("net")))
    monkeypatch.setattr(fetch, "fetch_chinabond_10y_latest", lambda **kw: (None, None))
    df, meta = fetch.fetch_cn_10y_bond_history_with_archive_fallback(
        archive_root=tmp_path, now=datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    )
    assert meta["data_source"] == "archive"
    assert meta["archive_latest_date"] == "2026-08-04"
    assert meta["bond_backup_date"] is None
    assert len(df) == 2
    assert df["yield_pct"].iloc[-1] == 1.71


def test_bond_history_live_success(monkeypatch, tmp_path):
    live_df = pd.DataFrame({"date": pd.to_datetime(["2026-08-04"]), "yield_pct": [1.71]})
    monkeypatch.setattr(fetch, "fetch_cn_10y_bond_history", lambda lookback_years=11: live_df)
    monkeypatch.setattr(fetch, "fetch_chinabond_10y_latest", lambda **kw: (None, None))
    df, meta = fetch.fetch_cn_10y_bond_history_with_archive_fallback(
        archive_root=tmp_path, now=datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    )
    assert meta["data_source"] == "live"
    assert meta["bond_backup_date"] is None
    assert len(df) == 1


def test_bond_history_appends_chinabond_backup_when_live_lags(monkeypatch, tmp_path):
    live_df = pd.DataFrame({"date": pd.to_datetime(["2026-08-04"]), "yield_pct": [1.70]})
    monkeypatch.setattr(fetch, "fetch_cn_10y_bond_history", lambda lookback_years=11: live_df)
    monkeypatch.setattr(fetch, "fetch_chinabond_10y_latest", lambda **kw: ("2026-08-05", 1.7161))
    df, meta = fetch.fetch_cn_10y_bond_history_with_archive_fallback(
        archive_root=tmp_path, now=datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    )
    assert meta["data_source"] == "live"
    assert meta["bond_backup_date"] == "2026-08-05"
    assert len(df) == 2
    assert df["date"].iloc[-1] == pd.Timestamp("2026-08-05")
    assert df["yield_pct"].iloc[-1] == 1.7161


def test_bond_history_skips_backup_when_live_has_today(monkeypatch, tmp_path):
    live_df = pd.DataFrame({"date": pd.to_datetime(["2026-08-05"]), "yield_pct": [1.7161]})
    monkeypatch.setattr(fetch, "fetch_cn_10y_bond_history", lambda lookback_years=11: live_df)
    calls = {"n": 0}

    def fake_backup(**kw):
        calls["n"] += 1
        return ("2026-08-05", 1.7161)

    monkeypatch.setattr(fetch, "fetch_chinabond_10y_latest", fake_backup)
    df, meta = fetch.fetch_cn_10y_bond_history_with_archive_fallback(
        archive_root=tmp_path, now=datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    )
    assert len(df) == 1
    assert meta["bond_backup_date"] is None
    assert calls["n"] == 0


def test_bond_yield_uses_chinabond_backup_when_eastmoney_lags(monkeypatch):
    em_df = pd.DataFrame({"日期": ["2026-08-04"], "中国国债收益率10年": [1.70]})
    monkeypatch.setattr(fetch.ak, "bond_zh_us_rate", lambda start_date: em_df)
    monkeypatch.setattr(fetch, "fetch_chinabond_10y_latest", lambda **kw: ("2026-08-05", 1.7161))
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    assert fetch.fetch_cn_10y_bond_yield(now=moment) == 1.7161


def test_bond_yield_keeps_eastmoney_when_already_today(monkeypatch):
    em_df = pd.DataFrame({"日期": ["2026-08-05"], "中国国债收益率10年": [1.72]})
    monkeypatch.setattr(fetch.ak, "bond_zh_us_rate", lambda start_date: em_df)
    calls = {"n": 0}

    def fake_backup(**kw):
        calls["n"] += 1
        return ("2026-08-05", 1.7161)

    monkeypatch.setattr(fetch, "fetch_chinabond_10y_latest", fake_backup)
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    assert fetch.fetch_cn_10y_bond_yield(now=moment) == 1.72
    assert calls["n"] == 0


def test_fetch_chinabond_10y_latest_filters_curve_and_picks_latest(monkeypatch):
    """直测中债网解析:多条曲线混在一起时,只取中债国债收益率曲线的 10年 列最新一行。"""
    df = pd.DataFrame(
        {
            "曲线名称": [
                "中债国债收益率曲线",
                "中债国债收益率曲线",
                "中债企业债收益率曲线(AAA)",
                "中债企业债收益率曲线(AAA)",
            ],
            "日期": ["2026-08-04", "2026-08-05", "2026-08-04", "2026-08-05"],
            "10年": [1.70, 1.7161, 2.10, 2.11],
        }
    )
    monkeypatch.setattr(fetch.ak, "bond_china_yield", lambda start_date, end_date: df)
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    cb_date, cb_yield = fetch.fetch_chinabond_10y_latest(now=moment)
    assert cb_date == "2026-08-05"
    assert cb_yield == 1.7161


def test_fetch_chinabond_10y_latest_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(fetch.ak, "bond_china_yield", lambda start_date, end_date: pd.DataFrame())
    cb_date, cb_yield = fetch.fetch_chinabond_10y_latest()
    assert cb_date is None and cb_yield is None


def test_fetch_chinabond_10y_latest_no_curve_name_column(monkeypatch):
    """df 无"曲线名称"列 -> 跳过曲线过滤分支,直接取最新一行;不崩且取到值。"""
    df = pd.DataFrame(
        {"日期": ["2026-08-04", "2026-08-05"], "10年": [1.70, 1.7161]}
    )
    monkeypatch.setattr(fetch.ak, "bond_china_yield", lambda start_date, end_date: df)
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    cb_date, cb_yield = fetch.fetch_chinabond_10y_latest(now=moment)
    assert cb_date == "2026-08-05"
    assert cb_yield == 1.7161


def test_fetch_chinabond_10y_latest_missing_10y_column(monkeypatch):
    """df 缺"10年"列 -> 返回 (None, None),且失败不写缓存。"""
    df = pd.DataFrame(
        {"曲线名称": ["中债国债收益率曲线"], "日期": ["2026-08-05"], "5年": [1.50]}
    )
    monkeypatch.setattr(fetch.ak, "bond_china_yield", lambda start_date, end_date: df)
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    cb_date, cb_yield = fetch.fetch_chinabond_10y_latest(now=moment)
    assert cb_date is None and cb_yield is None
    assert "2026-08-05" not in fetch._CHINABOND_CACHE


def test_fetch_chinabond_10y_latest_drops_nan_yield(monkeypatch):
    """df"10年"列含 NaN 行 -> dropna 跳过 NaN,只取非 NaN 最新行。"""
    df = pd.DataFrame(
        {
            "曲线名称": ["中债国债收益率曲线"] * 3,
            "日期": ["2026-08-03", "2026-08-04", "2026-08-05"],
            "10年": [1.69, float("nan"), 1.7161],
        }
    )
    monkeypatch.setattr(fetch.ak, "bond_china_yield", lambda start_date, end_date: df)
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    cb_date, cb_yield = fetch.fetch_chinabond_10y_latest(now=moment)
    assert cb_date == "2026-08-05"
    assert cb_yield == 1.7161


def test_fetch_chinabond_10y_latest_cache_shares_within_same_day(monkeypatch):
    """同一天(同 key)第二次调用命中缓存,不再请求 ak -> run.py 的 yield/history 共享一次请求。"""
    df = pd.DataFrame({"曲线名称": ["中债国债收益率曲线"], "日期": ["2026-08-05"], "10年": [1.7161]})
    calls = {"n": 0}

    def fake_ak(start_date, end_date):
        calls["n"] += 1
        return df

    monkeypatch.setattr(fetch.ak, "bond_china_yield", fake_ak)
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    r1 = fetch.fetch_chinabond_10y_latest(now=moment)
    r2 = fetch.fetch_chinabond_10y_latest(now=moment)
    assert r1 == r2 == ("2026-08-05", 1.7161)
    assert calls["n"] == 1  # 第二次命中缓存,未再请求


def test_fetch_chinabond_10y_latest_failure_not_cached_so_retry_possible(monkeypatch):
    """失败不缓存:第一次空,第二次换有效 df 仍能取到值(同进程可重试)。"""
    state = {"empty": True}

    def fake_ak(start_date, end_date):
        if state["empty"]:
            state["empty"] = False
            return pd.DataFrame()
        return pd.DataFrame({"曲线名称": ["中债国债收益率曲线"], "日期": ["2026-08-05"], "10年": [1.7161]})

    monkeypatch.setattr(fetch.ak, "bond_china_yield", fake_ak)
    moment = datetime(2026, 8, 5, 16, 0, tzinfo=fetch.BEIJING_TZ)
    assert fetch.fetch_chinabond_10y_latest(now=moment) == (None, None)
    assert fetch.fetch_chinabond_10y_latest(now=moment) == ("2026-08-05", 1.7161)


# ---------- archive fallback: fx ----------


def test_fx_fallback_to_archive(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "fx" / "usd_cnh.json",
        [{"日期": "2026-08-04", "最新价": 7.15, "代码": "USDCNH", "名称": "美元"}],
    )
    # Tier1(SAFE) 与 Tier2(eastmoney) 均失败
    monkeypatch.setattr(fetch.ak, "currency_boc_safe", lambda: (_ for _ in ()).throw(RuntimeError("safe")))
    monkeypatch.setattr(fetch.ak, "forex_hist_em", lambda symbol: (_ for _ in ()).throw(RuntimeError("em")))
    monkeypatch.setattr(fetch.alerts, "run_with_retry", lambda name, fn: fn())  # 跳过重试包装
    df = fetch.fetch_fx_history_with_archive_fallback(
        archive_root=tmp_path, now=datetime(2026, 8, 6, tzinfo=fetch.BEIJING_TZ)
    )
    assert len(df) == 1
    assert df["市场价"].iloc[-1] == 7.15


def test_fx_all_fail_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch.ak, "currency_boc_safe", lambda: (_ for _ in ()).throw(RuntimeError("safe")))
    monkeypatch.setattr(fetch.ak, "forex_hist_em", lambda symbol: (_ for _ in ()).throw(RuntimeError("em")))
    monkeypatch.setattr(fetch.alerts, "run_with_retry", lambda name, fn: fn())
    with pytest.raises(RuntimeError, match="SAFE: safe"):
        fetch.fetch_fx_history_with_archive_fallback(archive_root=tmp_path)


# ---------- archive fallback: PE history ----------


def test_pe_history_fallback_to_archive(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_valuation_percentile" / "000300.json",
        [{"trdDt": "2026-08-03", "pETtm": 12.0}, {"trdDt": "2026-08-04", "pETtm": 12.5}],
    )
    monkeypatch.setattr(fetch, "fetch_index_pe_history", lambda code, url="": (_ for _ in ()).throw(RuntimeError("net")))
    df, meta = fetch.fetch_index_pe_history_with_archive_fallback(
        "000300", archive_root=tmp_path, now=datetime(2026, 8, 6, tzinfo=fetch.BEIJING_TZ)
    )
    assert meta["data_source"] == "archive"
    assert len(df) == 2
    assert df["pe"].iloc[-1] == 12.5


# ---------- fetch_target_index_metrics (编排) ----------


def test_fetch_target_index_metrics_orchestration(monkeypatch, tmp_path):
    def fake_detail(code, url=""):
        return {"index_code": "000300", "index_name": "沪深300", "index_dividend_yield_url": "http://d", "index_valuation_percentile_url": "http://v"}

    def fake_div(code, url="", archive_root=None, now=None):
        return {"index_dividend_yield": 2.53, "data_source": "live", "archive_latest_date": None}

    def fake_val(code, url="", archive_root=None, now=None):
        return {"index_valuation_date": "2026-08-04", "index_valuation_metrics": {}, "data_source": "live", "archive_latest_date": None}

    monkeypatch.setattr(fetch, "fetch_index_detail", fake_detail)
    monkeypatch.setattr(fetch, "fetch_index_dividend_yield_with_archive_fallback", fake_div)
    monkeypatch.setattr(fetch, "fetch_index_valuation_percentile_with_archive_fallback", fake_val)
    out = fetch.fetch_target_index_metrics({"tracking_index_code": "000300"})
    assert out["index_name"] == "沪深300"
    assert out["index_dividend_yield"] == 2.53
    assert out["index_dividend_yield_data_source"] == "live"
    assert out["index_valuation_data_source"] == "live"


def test_fetch_target_index_metrics_no_code_returns_none():
    assert fetch.fetch_target_index_metrics({"type": "etf", "code": "510300"}) is None


def test_fetch_target_index_dividend_yield_passthrough(monkeypatch):
    monkeypatch.setattr(
        fetch, "fetch_target_index_metrics", lambda t: {"index_dividend_yield": 2.5}
    )
    assert fetch.fetch_target_index_dividend_yield({"tracking_index_code": "000300"}) == {"index_dividend_yield": 2.5}


def test_fetch_target_index_dividend_yield_missing_returns_none(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_target_index_metrics", lambda t: {"index_valuation_date": "x"})
    assert fetch.fetch_target_index_dividend_yield({"tracking_index_code": "000300"}) is None


# ---------- fetch_index_data (风格轮动指数日线) ----------


def test_fetch_index_data_akshare_success(monkeypatch):
    """TickFlow 未装时降级 akshare;stock_zh_index_daily_em 返回有效 df 即返回。"""
    monkeypatch.setattr(fetch, "build_tickflow_client", lambda: None)  # 确保 tickflow 不可用

    def fake_em(symbol, start_date, end_date):
        return pd.DataFrame({"日期": ["2026-08-01", "2026-08-04"], "收盘": [4000.0, 4050.0]})

    monkeypatch.setattr(fetch.ak, "stock_zh_index_daily_em", fake_em)
    monkeypatch.setattr(fetch.alerts, "run_with_retry", lambda name, fn: fn())
    df = fetch.fetch_index_data("sh000300", "20260101", "20260831")
    assert len(df) == 2
    assert df["close"].iloc[-1] == 4050.0


def test_fetch_index_data_all_sources_fail_raises(monkeypatch):
    monkeypatch.setattr(fetch, "build_tickflow_client", lambda: None)
    monkeypatch.setattr(fetch.ak, "stock_zh_index_daily_em", lambda **k: (_ for _ in ()).throw(RuntimeError("em")))
    monkeypatch.setattr(fetch.ak, "index_zh_a_hist", lambda **k: (_ for _ in ()).throw(RuntimeError("hist")))
    monkeypatch.setattr(fetch.ak, "stock_zh_index_hist_csindex", lambda **k: (_ for _ in ()).throw(RuntimeError("csi")))
    monkeypatch.setattr(fetch.alerts, "run_with_retry", lambda name, fn: fn())
    with pytest.raises(RuntimeError, match="指数数据获取失败"):
        fetch.fetch_index_data("sh000300", "20260101", "20260831")


def test_fetch_style_rotation_special_index_history_from_tencent(monkeypatch):
    raw = pd.DataFrame({"date": ["2026-08-07", "2026-08-08"], "close": [123.4, 125.6]})
    captured = {}

    def fake_tx(symbol, start_date, end_date, adjust):
        captured.update(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        return raw

    monkeypatch.setattr(fetch.ak, "stock_zh_a_hist_tx", fake_tx)
    monkeypatch.setattr(fetch.alerts, "run_with_retry", lambda name, fn: fn())

    frame = fetch.fetch_style_rotation_special_index_history("399376", "20260801", "20260809")

    assert list(frame.columns) == ["date", "close"]
    assert frame["close"].iloc[-1] == 125.6
    assert captured == {
        "symbol": "sz399376",
        "start_date": "2026-08-01",
        "end_date": "2026-08-09",
        "adjust": "",
    }


def test_fetch_index_data_special_index_prefers_tencent_live_over_archive(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_eod" / "399376.json",
        [{"trdDt": "2026-08-08", "pxClose": 111.0}, {"trdDt": "2026-08-09", "pxClose": 112.0}],
    )
    monkeypatch.setattr(
        fetch,
        "fetch_style_rotation_special_index_history",
        lambda *args, **kwargs: pd.DataFrame(
            {"date": pd.to_datetime(["2026-08-08", "2026-08-09"]), "close": [121.0, 122.0]}
        ),
    )

    frame = fetch.fetch_index_data("399376", "20260801", "20260809", archive_root=tmp_path)

    assert list(frame["date"].dt.strftime("%Y-%m-%d")) == ["2026-08-08", "2026-08-09"]
    assert frame["close"].iloc[-1] == 122.0


def test_fetch_index_data_special_index_falls_back_to_archive_when_tencent_fails(monkeypatch, tmp_path):
    _write_archive(
        tmp_path / "index_eod" / "399376.json",
        [{"trdDt": "2026-08-08", "pxClose": 111.0}, {"trdDt": "2026-08-09", "pxClose": 112.0}],
    )
    monkeypatch.setattr(
        fetch,
        "fetch_style_rotation_special_index_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tencent down")),
    )

    frame = fetch.fetch_index_data("399376", "20260801", "20260809", archive_root=tmp_path)

    assert list(frame["date"].dt.strftime("%Y-%m-%d")) == ["2026-08-08", "2026-08-09"]
    assert frame["close"].iloc[-1] == 112.0


# ---------- fetch_target_index_metrics: detail 失败回退 (P1-4) ----------


def test_fetch_target_index_metrics_detail_failure_falls_back(monkeypatch, capsys):
    """detail 接口失败不应中断整个标的,应继续走股息率/分位归档回退。"""
    target = {"tracking_index_code": "000300"}

    def boom(*a, **k):
        raise RuntimeError("detail boom")

    monkeypatch.setattr(fetch, "fetch_index_detail", boom)
    monkeypatch.setattr(
        fetch, "fetch_index_dividend_yield_with_archive_fallback",
        lambda *a, **k: {"index_dividend_yield": 2.5, "data_source": "archive", "archive_latest_date": "2026-08-06"},
    )
    monkeypatch.setattr(
        fetch, "fetch_index_valuation_percentile_with_archive_fallback",
        lambda *a, **k: {"index_valuation_percentile": 30, "data_source": "archive", "archive_latest_date": "2026-08-06"},
    )
    out = fetch.fetch_target_index_metrics(target)
    assert out is not None
    assert out["index_dividend_yield"] == 2.5
    assert out["index_valuation_percentile"] == 30
    assert "detail 接口失败" in capsys.readouterr().out


def test_fetch_target_index_metrics_detail_success_no_warn(monkeypatch, capsys):
    """detail 成功时不应打印失败回退告警。"""
    target = {"tracking_index_code": "000300"}
    monkeypatch.setattr(fetch, "fetch_index_detail", lambda *a, **k: {"index_code": "000300"})
    monkeypatch.setattr(
        fetch, "fetch_index_dividend_yield_with_archive_fallback",
        lambda *a, **k: {"index_dividend_yield": 2.5, "data_source": "live", "archive_latest_date": None},
    )
    monkeypatch.setattr(
        fetch, "fetch_index_valuation_percentile_with_archive_fallback",
        lambda *a, **k: {"index_valuation_percentile": 30, "data_source": "live", "archive_latest_date": None},
    )
    out = fetch.fetch_target_index_metrics(target)
    assert out is not None
    assert "detail 接口失败" not in capsys.readouterr().out
