# Dividend Observation Estimate Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the local 930955 estimate ledger to fill official-data gaps in the red-dividend observation chart and label the latest estimated values at the chart’s right edge.

**Architecture:** `dividend_observation_chart.py` remains the single payload builder. It loads the per-index local ledger only when official fields are absent for the same price date, substitutes the raw PE/PB/spread inputs before percentile calculation, and exposes one `latest_estimate` metadata object. The preview renderer consumes that metadata only to render endpoint labels; the market-valuation email remains unchanged.

**Tech Stack:** Python, JSON, existing rolling-percentile code, ECharts, pytest.

---

### Task 1: Merge same-date estimates into the research payload

**Files:**

- Modify: `src/research/dividend_observation_chart.py`
- Modify: `tests/test_dividend_observation_chart.py`

- [ ] **Step 1: Write a failing estimate-overlay test.**

```python
def test_payload_uses_same_date_estimate_when_official_values_are_missing(tmp_path):
    _write(archive / "index_eod" / "930955.json", {
        "records": [{"trdDt": "2026-08-07", "pxClose": 100.0}, {"trdDt": "2026-08-10", "pxClose": 110.0}]
    })
    _write(archive / "index_valuation_percentile" / "930955.json", {
        "records": [{"trdDt": "2026-08-07", "pETtm": 10.0, "pBLf": 1.0}]
    })
    _write(archive / "index_dividend_ratio" / "930955.json", {
        "records": [{"trdDt": "2026-08-07", "dividendYield": 4.0}]
    })
    _write(estimate_root / "930955.json", {"index_code": "930955", "records": [{
        "estimate_date": "2026-08-10", "status": "estimated",
        "estimates": {"pe_ttm": 11.0, "pb_lf": 1.1, "dividend_yield_spread": 2.2, "earnings_yield_spread": 7.4},
    }]})

    payload = build_dividend_observation_payload(archive_root=archive, estimate_root=estimate_root, ...)

    assert payload["latest"]["pe_ttm_percentile"] is not None
    assert payload["latest"]["dividend_yield_spread_percentile"] is not None
    assert payload["meta"]["latest_estimate"] == {
        "date": "2026-08-10", "pe_ttm": 11.0, "pb_lf": 1.1,
        "dividend_yield_spread": 2.2, "earnings_yield_spread": 7.4,
    }
```

- [ ] **Step 2: Run it and verify it fails.**

Run: `python -m pytest tests/test_dividend_observation_chart.py::test_payload_uses_same_date_estimate_when_official_values_are_missing -q`

Expected: FAIL because `estimate_root` and `latest_estimate` are unsupported.

- [ ] **Step 3: Add ledger loading and same-date overlay.**

```python
DEFAULT_ESTIMATE_ROOT = REPO_ROOT / "data" / "research" / "index_valuation_estimates"

def _estimate_by_date(index_code: str, estimate_root: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(estimate_root / f"{index_code}.json") if (estimate_root / f"{index_code}.json").exists() else {}
    return {str(row.get("estimate_date")): row for row in payload.get("records", [])
            if isinstance(row, dict) and row.get("status") == "estimated" and isinstance(row.get("estimates"), dict)}
```

Extend `build_dividend_observation_payload` with `estimate_root`. For each price date, retain official PE/PB and dividend-derived spreads when all required official values exist; otherwise use a same-date estimate record only if all four needed estimate fields are finite. Feed the chosen raw values into the existing `_percentile_series`; set `meta["latest_estimate"]` only when the final price date used an estimate. Preserve the exact existing payload when the ledger is absent, malformed, incomplete, stale, or a same-date official value exists.

- [ ] **Step 4: Add no-overlay guard tests and run chart payload tests.**

```python
def test_payload_ignores_estimate_when_same_date_official_values_exist(tmp_path):
    # official T-day PE/PB/dividend data wins; meta has no latest_estimate

def test_payload_ignores_incomplete_or_wrong_date_estimate(tmp_path):
    # no percentiles are fabricated for a ledger row missing a field or date mismatch
```

Run: `python -m pytest tests/test_dividend_observation_chart.py -q`

Expected: PASS.

- [ ] **Step 5: Commit source and test changes only.**

Run: `git add src/research/dividend_observation_chart.py tests/test_dividend_observation_chart.py && git commit -m "feat: overlay estimates in dividend payload"`

### Task 2: Render endpoint labels for estimated values

**Files:**

- Modify: `src/research/dividend_observation_chart_preview.py`
- Modify: `tests/test_dividend_observation_chart_preview.py`

- [ ] **Step 1: Write a failing HTML contract test.**

```python
def test_preview_html_labels_latest_estimated_values_at_chart_edge():
    payload = _payload()
    payload["meta"]["latest_estimate"] = {
        "date": "2026-01-02", "pe_ttm": 9.12, "pb_lf": 0.88,
        "dividend_yield_spread": 2.72, "earnings_yield_spread": 9.58,
    }
    html = build_preview_html(payload)
    assert "（预估）" in html
    assert "PE 9.12" in html
    assert "股息率差 2.72%" in html
    assert "endLabel" in html
```

- [ ] **Step 2: Run it and verify it fails.**

Run: `python -m pytest tests/test_dividend_observation_chart_preview.py::test_preview_html_labels_latest_estimated_values_at_chart_edge -q`

Expected: FAIL because no endpoint estimate label is rendered.

- [ ] **Step 3: Add estimate-aware endpoint labels to the two ECharts series groups.**

```javascript
function endpointLabel(rawLabel, rawValue, percentile) {
  if (rawValue === null || rawValue === undefined) return null;
  return rawLabel + " " + Number(rawValue).toFixed(2) + "%，分位 "
    + Number(percentile).toFixed(1) + "%（预估）";
}
```

Embed `meta.latest_estimate` in the display payload. When its `date` equals the final plotted date, attach an ECharts `endLabel` and `labelLayout: { moveOverlap: "shiftY" }` to PE, PB, dividend-spread and earnings-spread lines; labels must use the raw estimate and the corresponding plotted percentile. Increase the right chart grid margin only for these charts so labels remain visible. When metadata is absent or not for the last date, do not add endpoint labels and preserve the existing chart appearance.

- [ ] **Step 4: Add the absent/official label guard and run preview tests.**

```python
def test_preview_html_omits_estimate_endpoint_labels_without_matching_latest_estimate():
    html = build_preview_html(_payload())
    assert "（预估）" not in html
```

Run: `python -m pytest tests/test_dividend_observation_chart_preview.py -q`

Expected: PASS.

- [ ] **Step 5: Regenerate only local preview artifacts and verify the feature path.**

Run: `python -m src.research.dividend_observation_chart && python -m src.research.dividend_observation_chart_preview`

Expected: the local preview displays continuous valuation/spread tails and four right-edge `（预估）` labels for 2026-08-10; generated JSON and HTML remain uncommitted.

- [ ] **Step 6: Run the relevant regression suite and commit source/test changes only.**

Run: `python -m pytest tests/test_valuation_estimate_ledger.py tests/test_dividend_observation_chart.py tests/test_dividend_observation_chart_preview.py tests/test_dividend_observation_email_run.py tests/test_valuation_run.py -q`

Expected: PASS.

Run: `git add src/research/dividend_observation_chart_preview.py tests/test_dividend_observation_chart_preview.py && git commit -m "feat: label estimated dividend values"`

## Plan self-review

- Task 1 fulfills official-first raw-value overlay and percentile recomputation; Task 2 fulfills only the approved red-chart endpoint labels.
- Market-valuation email, reconciliation/error computation, workflows, and all generated JSON/HTML commits are explicitly out of scope.
- `estimate_root`, `latest_estimate`, and the four raw estimate keys are named consistently in test, payload, and preview steps.
