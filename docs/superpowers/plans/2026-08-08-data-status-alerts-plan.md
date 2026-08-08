# Data Status And Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make webhook alerts and daily emails explain data freshness, partial failures, and actual user impact without exposing opaque internal dataset names as the primary message.

**Architecture:** Add a small common data-status module containing human-readable dataset metadata, failure classification, and compact alert-detail formatting. Use it in archive refresh alerts, preserve existing good board-level failure titles, add explicit timing/quality notes to valuation and convertible emails, and fix valuation fallback code resolution so archive fallback can continue after an index-detail failure.

**Tech Stack:** Python 3.10+, pytest, requests exceptions, existing SMTP/HTML renderers, JSON/YAML configuration.

---

### Task 1: Define Data Status Behavior With Failing Tests

**Files:**
- Create: `tests/test_common_data_status.py`
- Test: `tests/test_valuation_refresh_archive.py`
- Test: `tests/test_valuation_render.py`
- Test: `tests/test_convertible_index.py`

- [ ] **Step 1: Add tests for human-readable dataset metadata**

```python
from src.common.data_status import build_data_alert_title, dataset_status


def test_archive_dataset_title_uses_business_name():
    assert build_data_alert_title(
        "index_eod",
        code="000300",
        target_name="沪深300",
    ) == "市场估值数据刷新失败：沪深300"


def test_dataset_status_describes_real_impact():
    status = dataset_status("fx")
    assert status["label"] == "汇率"
    assert "汇率图" in status["impact"]
```

- [ ] **Step 2: Add tests for failure classification and compact searchable detail**

```python
import requests

from src.common.data_status import classify_failure, format_data_failure_detail


def test_classify_network_failure():
    error = requests.ConnectionError("connection reset")
    assert classify_failure(error) == "网络抓取失败"


def test_classify_field_failure():
    error = RuntimeError("Income statement row not found: 归属母公司净利润")
    assert classify_failure(error) == "字段解析失败"


def test_format_detail_contains_impact_and_raw_error():
    detail = format_data_failure_detail(
        "fx",
        error=RuntimeError("ProxyError: remote end closed connection"),
    )
    assert "影响范围：市场估值汇率图及汇率归档回退" in detail
    assert "原因分类：网络抓取失败" in detail
    assert "原始错误：ProxyError" in detail
```

- [ ] **Step 3: Add tests for valuation index-code fallback**

```python
from src.valuation.fetch import resolve_target_index_code


def test_valuation_target_extracts_index_code_from_detail_url():
    target = {
        "type": "valuation",
        "code": "000300",
        "index_detail_url": "https://example.test/index/detail?indexCode=000300",
    }
    assert resolve_target_index_code(target) == "000300"
```

- [ ] **Step 4: Add tests for expected timing notes**

```python
from src.valuation.render import _build_global_info


def test_valuation_global_info_explains_close_based_data():
    html = _build_global_info("2026-08-08 12:06", "2026-08-07", 1.71)
    assert "最近交易日收盘数据" in html
    assert "2026-08-07" in html
```

- [ ] **Step 5: Add tests for convertible index timing**

```python
from src.convertible.index_chart.run import build_section


def test_convertible_index_section_mentions_close_update(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.convertible.index_chart.run.history.build_merged_history",
        lambda: ([{"date": "2026-08-07", "value": 100}], {}),
    )
    monkeypatch.setattr(
        "src.convertible.index_chart.run.charts.generate_cb_index_chart",
        lambda path, records: path.write_bytes(b"png") or path,
    )
    section = build_section(tmp_path)
    assert "A股收盘后更新" in section["html"]
    assert "2026-08-07" in section["html"]
```

- [ ] **Step 6: Run the focused tests and confirm the new behavior fails**

Run:

```powershell
python -m pytest --basetemp=.pytest_tmp tests/test_common_data_status.py tests/test_valuation_refresh_archive.py tests/test_valuation_render.py tests/test_convertible_index.py -q
```

Expected: the new tests fail because the data-status module, fallback extraction, and timing notes do not exist yet.

### Task 2: Add Common Data Status Metadata And Formatting

**Files:**
- Create: `src/common/data_status.py`
- Modify: `src/common/alerts.py:68`
- Test: `tests/test_common_data_status.py`

- [ ] **Step 1: Add business-facing metadata for each dataset**

Implement `DATASET_STATUS` with these entries:

```python
DATASET_STATUS = {
    "index_eod": {
        "label": "指数收盘价",
        "scope": "市场估值历史价格归档与归档校验",
        "timing": "A股收盘后更新",
    },
    "index_dividend_ratio": {
        "label": "指数股息率",
        "scope": "市场估值股息率及其实时失败后的归档回退",
        "timing": "收盘后或次日更新",
    },
    "index_valuation_percentile": {
        "label": "指数估值分位",
        "scope": "市场估值PE/PB分位及PE历史回退",
        "timing": "收盘后或次日更新",
    },
    "bond_10y": {
        "label": "10Y国债",
        "scope": "市场估值股债收益差和股债比值",
        "timing": "交易日更新",
    },
    "fx": {
        "label": "汇率",
        "scope": "市场估值汇率图及汇率归档回退",
        "timing": "实时源可用，归档为行情快照",
    },
    "cb_index": {
        "label": "转债等权指数",
        "scope": "转债行情指数图和三低轮动基准对比",
        "timing": "A股收盘后更新",
    },
    "cninfo": {
        "label": "巨潮财报",
        "scope": "高股息TTM归母净利润和PE-TTM",
        "timing": "财报披露后更新，优先使用本地缓存",
    },
}
```

- [ ] **Step 2: Implement the small public API**

Implement:

```python
def dataset_status(dataset: str) -> dict[str, str]:
    # Return a known status or a safe generic status for an unexpected key.

def build_data_alert_title(
    dataset: str,
    *,
    code: str = "",
    target_name: str = "",
    partial: bool = False,
) -> str:
    # Produce titles such as:
    # 市场估值数据刷新失败：沪深300
    # 市场估值数据部分缺失：巨潮财报
    # 转债指数数据刷新失败

def classify_failure(error: Exception) -> str:
    # Classify requests/httpx/network text as 网络抓取失败,
    # missing field/column/row text as 字段解析失败,
    # response/empty/list/code text as 返回数据异常,
    # filesystem/write errors as 归档写入失败,
    # otherwise 程序异常.

def format_data_failure_detail(
    dataset: str,
    *,
    error: Exception,
    code: str = "",
    target_name: str = "",
    action: str = "",
    trace: str = "",
) -> str:
    # Return short Markdown lines containing impact, classification,
    # optional raw identifiers, action, original error, and at most four
    # traceback lines.
```

- [ ] **Step 3: Add a safe exception helper without changing existing alerts**

Add `notify_data_failure(...)` to `src/common/alerts.py`. It should call the existing `notify_alert()` with the title and detail produced by `data_status.py`, so current board-level `notify_alert()` call sites remain compatible.

- [ ] **Step 4: Run the focused data-status tests**

Run:

```powershell
python -m pytest --basetemp=.pytest_tmp tests/test_common_data_status.py -q
```

Expected: PASS.

### Task 3: Make Archive Refresh Alerts Human-Readable And Impact-Aware

**Files:**
- Modify: `src/valuation/refresh_archive.py:130-178`
- Modify: `tests/test_valuation_refresh_archive.py:150-220`

- [ ] **Step 1: Update refresh-step tests to assert user-facing titles**

Change the existing assertion from:

```python
assert alerted and alerted[0][0] == "bond_10y 归档刷新失败"
```

to:

```python
assert alerted and alerted[0][0] == "市场估值数据刷新失败：10Y国债"
assert "影响范围：市场估值股债收益差和股债比值" in alerted[0][1]
```

Add an index case asserting:

```python
assert alerted[0][0] == "市场估值数据刷新失败：沪深300"
assert "数据类型：指数收盘价" in alerted[0][1]
assert "内部任务：index_eod" in alerted[0][1]
```

- [ ] **Step 2: Pass dataset identity separately from display text**

Refactor `_run_step()` to accept `dataset`, optional `code`, and optional `target_name` instead of receiving an opaque combined name such as `index_eod:000300`.

- [ ] **Step 3: Use `notify_data_failure()` for per-dataset failures**

Keep the refresh loop best-effort: failed steps are recorded and later steps continue. The alert title must use the business label; the detail must include the internal dataset and code only as searchable diagnostic fields.

- [ ] **Step 4: Make the aggregate failure list human-readable**

Print values such as `沪深300/指数收盘价` and `汇率`, while keeping the raw dataset identifier in the detailed alert only.

- [ ] **Step 5: Run archive-refresh tests**

Run:

```powershell
python -m pytest --basetemp=.pytest_tmp tests/test_valuation_refresh_archive.py -q
```

Expected: PASS.

### Task 4: Fix Valuation Fallback Identity And Report Partial CNINFO Quality

**Files:**
- Modify: `src/valuation/fetch.py:800-875`
- Modify: `src/valuation/dividend/supplement.py:350-390, 500-570`
- Modify: `tests/test_valuation_fetch.py`
- Modify: `tests/test_valuation_dividend_supplement.py`

- [ ] **Step 1: Make `resolve_target_index_code()` parse `index_detail_url`**

When `tracking_index_code` and `index_code` are absent, extract `indexCode` from `index_detail_url` before falling back to the configured target code. This allows dividend-ratio and valuation-percentile archive fallback to continue after the detail endpoint fails.

- [ ] **Step 2: Add a CNINFO TTM error marker to row-local metrics**

When `_supplement_ttm_metrics()` cannot obtain a financial snapshot, preserve the existing fail-open behavior but add:

```python
{
    "ttm_text": "",
    "ttm_value_yi": None,
    "pe_ttm_text": "",
    "error": str(exc),
}
```

Successful metrics must continue to omit the error marker.

- [ ] **Step 3: Add a concise partial-quality note to the supplement summary**

Count rows with the error marker and add a line only when nonzero:

```text
巨潮TTM部分缺失：1只，已保留股票但TTM/PE-TTM为空
```

Do not turn one missing company report into a board-level failure or webhook alert.

- [ ] **Step 4: Run valuation and supplement tests**

Run:

```powershell
python -m pytest --basetemp=.pytest_tmp tests/test_valuation_fetch.py tests/test_valuation_dividend_supplement.py -q
```

Expected: PASS.

### Task 5: Add Explicit Timing Notes To Generated Emails

**Files:**
- Modify: `src/valuation/render.py:505-574`
- Modify: `src/convertible/index_chart/run.py:20-58`
- Modify: `tests/test_valuation_render.py`
- Modify: `tests/test_convertible_index.py`

- [ ] **Step 1: Add a valuation timing line**

When a valuation date exists, render:

```text
数据时点：指数估值按最近交易日收盘数据，当前基准日为 YYYY-MM-DD
```

Keep the existing trigger time, valuation date, and bond yield fields.

- [ ] **Step 2: Add a convertible-index timing line**

Include in the index-chart section:

```text
转债等权指数：A股收盘后更新，当前数据截至 YYYY-MM-DD
```

This must appear in both email and preview because both consume the same section HTML.

- [ ] **Step 3: Run renderer tests**

Run:

```powershell
python -m pytest --basetemp=.pytest_tmp tests/test_valuation_render.py tests/test_convertible_index.py -q
```

Expected: PASS.

### Task 6: Verify End To End Without Duplicate Mail

**Files:**
- Modify: none
- Verify: `preview/valuation.html`
- Verify: `preview/rotation.html`
- Verify: `preview/convertible.html`
- Verify: `preview/coal.html`
- Verify: `preview/commodity.html`
- Verify: `preview/verify_report.md`

- [ ] **Step 1: Run the complete unit suite**

Run:

```powershell
python -m pytest --basetemp=.pytest_tmp -q
```

Expected: all tests pass.

- [ ] **Step 2: Regenerate all five previews with external access**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m src.preview.generate
```

Expected: all five boards report `[OK]`.

- [ ] **Step 3: Run static verification**

Run:

```powershell
python -m src.preview.verify
```

Expected: no new failures caused by the alert/timing changes; any pre-existing stale archive must be reported with its human-readable dataset name.

- [ ] **Step 4: Inspect generated user-facing text**

Verify the valuation preview contains `数据时点` and the `2026-08-07` as-of date, the convertible preview contains `A股收盘后更新`, and no preview contains raw `index_eod:` or `bond_10y` as the primary visible title.

