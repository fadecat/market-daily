# Index Valuation Estimate Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist per-index, auditable T-day estimates whenever the official PE/PB/dividend feed lags the index close. Start with 930955; do not change consumers in this phase.

**Architecture:** Create a standalone `src.valuation.estimate_ledger` producer. It reads existing EOD, valuation, and dividend archives, accepts real 10Y-bond history from the existing fetch fallback, and writes `data/research/index_valuation_estimates/<index_code>.json`. It neither alters official archives nor connects the red-dividend chart or market-valuation email.

**Tech Stack:** Python 3, stdlib JSON/path, existing pandas and pytest.

---

## File structure

- Create: `src/valuation/estimate_ledger.py` — pure calculation, upsert/write, CLI.
- Create: `tests/test_valuation_estimate_ledger.py` — calculation, availability, persistence, CLI tests.
- Create: `data/research/index_valuation_estimates/930955.json` — first generated ledger.
- Do not modify: `src/research/dividend_observation_chart.py`, `src/dividend_observation/*`, `src/valuation/run.py`, or workflows.

### Task 1: Calculation contract

**Files:**

- Create: `tests/test_valuation_estimate_ledger.py`
- Create: `src/valuation/estimate_ledger.py`

- [ ] **Step 1: Write the failing calculation test.**

```python
def test_build_estimate_records_uses_price_factor_and_same_day_real_bond(tmp_path):
    archive = tmp_path / "archive"
    _write_archive(archive / "index_eod" / "930955.json", [
        {"trdDt": "2026-08-07", "pxClose": 11291.4885},
        {"trdDt": "2026-08-10", "pxClose": 11364.4956},
    ])
    _write_archive(archive / "index_valuation_percentile" / "930955.json", [
        {"trdDt": "2026-08-07", "pETtm": 8.8056, "pBLf": 0.8705},
    ])
    _write_archive(archive / "index_dividend_ratio" / "930955.json", [
        {"trdDt": "2026-08-07", "dividendYield": 4.4536},
    ])
    bonds = pd.DataFrame({"date": pd.to_datetime(["2026-08-10"]), "yield_pct": [1.7074]})
    rows = build_estimate_records("930955", archive_root=archive, bond_history=bonds)
    assert rows[0]["estimate_date"] == "2026-08-10"
    assert rows[0]["inputs"]["bond_10y"] == {"date": "2026-08-10", "yield_pct": 1.7074}
    assert rows[0]["estimates"] == {
        "pe_ttm": 8.862534, "pb_lf": 0.876128, "dividend_yield": 4.424989,
        "dividend_yield_spread": 2.717589, "earnings_yield_spread": 9.576054,
    }
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `python -m pytest tests/test_valuation_estimate_ledger.py::test_build_estimate_records_uses_price_factor_and_same_day_real_bond -q`

Expected: FAIL with `ModuleNotFoundError` for `src.valuation.estimate_ledger`.

- [ ] **Step 3: Implement the smallest pure builder.**

```python
def build_estimate_records(index_code: str, *, archive_root: Path | str,
                           bond_history: pd.DataFrame) -> list[dict[str, Any]]:
    prices = _load_price_map(index_code, Path(archive_root))
    valuations = _load_valuation_map(index_code, Path(archive_root))
    dividends = _load_dividend_map(index_code, Path(archive_root))
    bonds = _bond_map(bond_history)
    results = []
    for estimate_date, estimate_close in sorted(prices.items()):
        if estimate_date in valuations and estimate_date in dividends:
            continue
        valuation_base = _latest_base(valuations, estimate_date, prices)
        dividend_base = _latest_base(dividends, estimate_date, prices)
        bond = bonds.get(estimate_date)
        if valuation_base and dividend_base and bond:
            results.append(_estimate_row(estimate_date, estimate_close, valuation_base, dividend_base, bond))
    return results
```

`_estimate_row` must preserve separate valuation/dividend base dates and closes; calculate PE/PB with each price factor, dividend yield inversely, and both spreads with the same-day real bond rate. Round five estimated values to six decimals. Never use a T-1 bond row as T-day data.

- [ ] **Step 4: Add guard tests and verify all calculation cases.**

```python
def test_builder_skips_date_with_all_official_inputs(tmp_path):
    assert build_estimate_records("000300", archive_root=archive, bond_history=bonds) == []

def test_builder_skips_date_without_same_day_bond(tmp_path):
    assert build_estimate_records("000300", archive_root=archive, bond_history=t_minus_one_bonds) == []
```

Run: `python -m pytest tests/test_valuation_estimate_ledger.py -q`

Expected: PASS.

- [ ] **Step 5: Commit this task.**

Run: `git add src/valuation/estimate_ledger.py tests/test_valuation_estimate_ledger.py && git commit -m "feat: add index valuation estimate calculation"`

### Task 2: Per-index persistence and CLI

**Files:**

- Modify: `src/valuation/estimate_ledger.py`
- Modify: `tests/test_valuation_estimate_ledger.py`

- [ ] **Step 1: Write failing upsert and CLI tests.**

```python
def test_refresh_estimate_ledger_upserts_and_preserves_reconciliation(tmp_path):
    output = tmp_path / "estimates" / "930955.json"
    _write_json(output, {"index_code": "930955", "records": [{
        "estimate_date": "2026-08-10",
        "reconciliation": {"official_value": {"pe_ttm": 8.86}},
    }]})
    refresh_estimate_ledger("930955", archive_root=archive, output_root=output.parent,
                            bond_history_fetcher=lambda **_: (bonds, {"data_source": "live"}))
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["records"][0]["reconciliation"]["official_value"]["pe_ttm"] == 8.86
    assert saved["records"][0]["estimates"]["pe_ttm"] == 8.862534

def test_main_writes_only_requested_index(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger.fetch, "fetch_cn_10y_bond_history_with_archive_fallback", lambda **_: (bonds, {"data_source": "live"}))
    assert ledger.main(["--index-code", "930955", "--archive-root", str(archive), "--output-root", str(output_root)]) == 0
    assert (output_root / "930955.json").exists()
```

- [ ] **Step 2: Verify the new tests fail.**

Run: `python -m pytest tests/test_valuation_estimate_ledger.py::test_refresh_estimate_ledger_upserts_and_preserves_reconciliation tests/test_valuation_estimate_ledger.py::test_main_writes_only_requested_index -q`

Expected: FAIL because `refresh_estimate_ledger` and `main` are missing.

- [ ] **Step 3: Implement upsert/write/CLI.**

```python
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "research" / "index_valuation_estimates"

def refresh_estimate_ledger(index_code: str, *, archive_root=DEFAULT_ARCHIVE_ROOT,
                            output_root=DEFAULT_OUTPUT_ROOT,
                            bond_history_fetcher=fetch.fetch_cn_10y_bond_history_with_archive_fallback) -> bool:
    history, meta = bond_history_fetcher(archive_root=Path(archive_root))
    incoming = build_estimate_records(index_code, archive_root=archive_root, bond_history=history)
    return _upsert_and_write(Path(output_root) / f"{index_code}.json", index_code, incoming, meta)

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成指数估算账本")
    parser.add_argument("--index-code", action="append", required=True)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    for code in dict.fromkeys(args.index_code):
        refresh_estimate_ledger(code, archive_root=args.archive_root, output_root=args.output_root)
    return 0
```

The file shape is `{ "schema_version": 1, "index_code": ..., "updated_at": ..., "records": [...] }`. Upsert by `estimate_date`, overlay generated data while retaining future unrelated fields such as `reconciliation`, sort by date, and omit a write when content is unchanged.

- [ ] **Step 4: Verify persistence and regression tests.**

Run: `python -m pytest tests/test_valuation_estimate_ledger.py tests/test_valuation_fetch.py tests/test_valuation_refresh_archive.py -q`

Expected: PASS.

- [ ] **Step 5: Commit this task.**

Run: `git add src/valuation/estimate_ledger.py tests/test_valuation_estimate_ledger.py && git commit -m "feat: persist per-index valuation estimates"`

### Task 3: Seed 930955 without connecting consumers

**Files:**

- Create: `data/research/index_valuation_estimates/930955.json`

- [ ] **Step 1: Produce the first ledger.**

Run: `python -m src.valuation.estimate_ledger --index-code 930955`

Expected: one `estimated` record per price date lacking official inputs, only where the real 10Y bond has the exact same date.

- [ ] **Step 2: Confirm existing consumers remain untouched.**

Run: `python -m pytest tests/test_dividend_observation_chart.py tests/test_dividend_observation_email_run.py tests/test_valuation_run.py -q`

Expected: PASS; no official archives, research payloads, previews, or email renderers are changed by the producer.

- [ ] **Step 3: Inspect and commit only the seed data.**

Run: `Get-Content data/research/index_valuation_estimates/930955.json -Raw`

Expected: 2026-08-10 includes status, base dates/values, T-day close, T-day 10Y date/value, and all five estimates.

Run: `git add data/research/index_valuation_estimates/930955.json && git commit -m "chore: seed 930955 valuation estimate ledger"`

## Plan self-review

- Tasks 1–2 cover the approved generic producer and per-index persistence; Task 3 creates the first requested ledger.
- Reconciliation, error fields, the red-dividend chart, and the market-valuation email are deliberately excluded.
- Paths, record keys, test commands, and CLI names agree across all tasks.
