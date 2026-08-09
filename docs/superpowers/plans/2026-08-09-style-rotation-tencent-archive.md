# Style Rotation Tencent Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `399376` and `399373` use Tencent-backed archive data for style rotation so valuation and dividend observation no longer rely on fragile live-only fetches.

**Architecture:** Add a narrow special-case path for the two style-rotation indices instead of refactoring the whole index fetch stack. The implementation has three layers: Tencent fetch normalization in `src/valuation/fetch.py`, archive refresh integration in `src/valuation/refresh_archive.py`, and archive-first consumption for style rotation and dividend observation through the existing `index_eod` archive format.

**Tech Stack:** Python, pytest, pandas, akshare, existing `storage.merge_archive` archive format, GitHub Actions archive refresh workflow.

---

### Task 1: Add Tencent Style Index Fetch Helpers

**Files:**
- Modify: `src/valuation/fetch.py`
- Test: `tests/test_valuation_fetch.py`

- [ ] **Step 1: Write the failing tests**

Add tests covering Tencent symbol mapping, frame normalization, and archive-first style index reads.

```python
def test_fetch_style_rotation_index_history_from_tencent(monkeypatch):
    raw = pd.DataFrame({"date": ["2026-08-07", "2026-08-08"], "close": [123.4, 125.6]})
    monkeypatch.setattr(fetch.ak, "stock_zh_a_hist_tx", lambda **kwargs: raw)
    frame = fetch.fetch_style_rotation_special_index_history("399376", "20260801", "20260809")
    assert list(frame.columns) == ["date", "close"]
    assert frame["close"].iloc[-1] == 125.6


def test_fetch_style_rotation_index_history_prefers_archive(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    (archive / "index_eod").mkdir(parents=True)
    (archive / "index_eod" / "399376.json").write_text(
        json.dumps(
            {"records": [{"trdDt": "2026-08-08", "pxClose": 111.0}, {"trdDt": "2026-08-09", "pxClose": 112.0}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        fetch,
        "fetch_style_rotation_special_index_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not fetch live")),
    )
    frame = fetch.fetch_index_data("399376", "20260801", "20260809", archive_root=archive)
    assert frame["close"].iloc[-1] == 112.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_fetch.py -q`

Expected: FAIL with missing helper / unexpected `archive_root` argument / current `fetch_index_data()` still using old live-only path.

- [ ] **Step 3: Implement minimal Tencent fetch helpers in `src/valuation/fetch.py`**

Add a narrow special-index branch instead of changing the generic source order.

```python
STYLE_ROTATION_SPECIAL_INDEX_CODES = {"399376", "399373"}


def is_style_rotation_special_index(code: str) -> bool:
    return extract_index_digits(code) in STYLE_ROTATION_SPECIAL_INDEX_CODES


def _to_tencent_index_symbol(code: str) -> str:
    digits = extract_index_digits(code)
    if digits.startswith("399"):
        return f"sz{digits}"
    return f"sh{digits}"


def fetch_style_rotation_special_index_history(
    code: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    raw = alerts.run_with_retry(
        "stock_zh_a_hist_tx",
        lambda: ak.stock_zh_a_hist_tx(
            symbol=_to_tencent_index_symbol(code),
            start_date=f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}",
            end_date=f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}",
            adjust="",
        ),
    )
    return normalize_dataframe(raw)


def _load_index_eod_archive_frame(
    code: str,
    start_date: str,
    end_date: str,
    archive_root: Path = storage.ARCHIVE_DIR,
) -> pd.DataFrame:
    records = load_archive_records("index_eod", index_code=extract_index_digits(code), archive_root=archive_root)
    if not records:
        return pd.DataFrame(columns=["date", "close"])
    frame = pd.DataFrame(
        [{"date": row.get("trdDt"), "close": row.get("pxClose")} for row in records if row.get("trdDt") is not None]
    )
    normalized = normalize_dataframe(frame)
    return clip_dataframe_by_date(normalized, start_date, end_date)
```

- [ ] **Step 4: Update `fetch_index_data()` to use archive-first for the two special indices**

Only branch for `399376` / `399373`, leave everything else untouched.

```python
def fetch_index_data(
    code: str,
    start_date: str,
    end_date: str,
    tickflow_daily_count: int = DEFAULT_TICKFLOW_DAILY_COUNT,
    archive_root: Path = storage.ARCHIVE_DIR,
) -> pd.DataFrame:
    if is_style_rotation_special_index(code):
        archived = _load_index_eod_archive_frame(code, start_date, end_date, archive_root=archive_root)
        if not archived.empty:
            print(f"[INFO] 风格指数数据来源: archive ({extract_index_digits(code)})")
            return archived
        live = fetch_style_rotation_special_index_history(code, start_date, end_date)
        if not live.empty:
            print(f"[INFO] 风格指数数据来源: tencent live ({extract_index_digits(code)})")
            return live
    ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_fetch.py -q`

Expected: PASS with the new Tencent helper tests green and existing fetch tests still green.

- [ ] **Step 6: Commit**

```bash
git add src/valuation/fetch.py tests/test_valuation_fetch.py
git commit -m "feat: add tencent fetch for style rotation indexes"
```

### Task 2: Refresh Archive for the Two Style Rotation Indices

**Files:**
- Modify: `src/valuation/refresh_archive.py`
- Test: `tests/test_valuation_refresh_archive.py`

- [ ] **Step 1: Write the failing tests**

Add a dedicated refresh function test plus a `main()` integration count test.

```python
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
    assert captured["identity"] == {"index_code": "399376"}
    assert captured["incoming"][0]["trdDt"] == "2026-08-08"
    assert captured["incoming"][0]["pxClose"] == 3210.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_refresh_archive.py -q`

Expected: FAIL because the new refresh function and main-loop behavior do not exist yet.

- [ ] **Step 3: Implement a dedicated refresh function for style rotation special indices**

Use the same archive schema as other `index_eod` datasets.

```python
STYLE_ROTATION_SPECIAL_INDEX_CODES = ["399376", "399373"]


def refresh_style_rotation_special_index_dataset(index_code: str, updated_at: str) -> List[Path]:
    end_date = fetch.now_in_beijing().strftime("%Y%m%d")
    start_date = (fetch.now_in_beijing() - timedelta(days=365 * 10)).strftime("%Y%m%d")
    frame = fetch.fetch_style_rotation_special_index_history(index_code, start_date, end_date)
    if frame is None or getattr(frame, "empty", True):
        return []
    records = [
        {"trdDt": row["date"].strftime("%Y-%m-%d"), "pxClose": float(row["close"])}
        for row in frame.to_dict(orient="records")
    ]
    path = storage.merge_archive(
        "index_eod",
        {"index_code": index_code},
        records,
        merge_key="trdDt",
        source="akshare.stock_zh_a_hist_tx",
        updated_at=updated_at,
    )
    return [path] if path else []
```

- [ ] **Step 4: Call the new refresh step from `main()`**

Place it after the existing index-code loop so the special indices are always refreshed even though they are not in `valuation.yaml`.

```python
for code in STYLE_ROTATION_SPECIAL_INDEX_CODES:
    paths, success = _run_step(
        "index_eod",
        lambda c=code: refresh_style_rotation_special_index_dataset(c, updated_at),
        code=code,
        target_name=f"风格指数{code}",
    )
    changed.extend(paths)
    if success:
        ok += 1
    else:
        failed.append(f"{code}/index_eod")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_refresh_archive.py -q`

Expected: PASS including the new special-index archive tests.

- [ ] **Step 6: Commit**

```bash
git add src/valuation/refresh_archive.py tests/test_valuation_refresh_archive.py
git commit -m "feat: archive style rotation indexes from tencent"
```

### Task 3: Route Style Rotation and Dividend Observation Through the Archive

**Files:**
- Modify: `src/valuation/style_rotation.py`
- Modify: `src/research/dividend_observation_chart.py`
- Test: `tests/test_valuation_style_rotation.py`
- Test: `tests/test_dividend_observation_chart.py`

- [ ] **Step 1: Write the failing tests**

Add one style-rotation test proving `fetch_index_history()` passes `archive_root`, plus one dividend-observation test proving special-index archive data drives the payload without live fallback.

```python
def test_fetch_index_history_uses_archive_first_for_special_index(monkeypatch):
    monkeypatch.setattr(
        sr,
        "fetch_index_data",
        lambda symbol, start_date, end_date, **kwargs: pd.DataFrame(
            {"date": pd.to_datetime(["2026-08-08", "2026-08-09"]), "close": [10.0, 10.5]}
        ),
    )
    frame = sr.fetch_index_history("399376")
    assert frame["close"].iloc[-1] == 10.5


def test_build_dividend_observation_payload_reads_style_rotation_from_archive_special_indexes(tmp_path):
    archive = tmp_path / "archive"
    _write(
        archive / "index_eod" / "399376.json",
        {"records": [{"trdDt": "2026-01-01", "pxClose": 100.0}, {"trdDt": "2026-01-02", "pxClose": 101.0}]},
    )
    _write(
        archive / "index_eod" / "399373.json",
        {"records": [{"trdDt": "2026-01-01", "pxClose": 100.0}, {"trdDt": "2026-01-02", "pxClose": 100.2}]},
    )
    ...
    payload = build_dividend_observation_payload(
        archive_root=archive,
        style_rotation_fetcher=None,
        force_refresh_style_rotation_payload=True,
        ...
    )
    assert payload["series"]["style_rotation_spread_percentile"][-1] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_valuation_style_rotation.py tests/test_dividend_observation_chart.py -q`

Expected: FAIL because the style-rotation special-index archive path is not wired through yet.

- [ ] **Step 3: Update `style_rotation.fetch_index_history()` to explicitly use the fetch-layer archive-first branch**

This should remain thin; the branching belongs in `fetch.py`.

```python
def fetch_index_history(symbol: str) -> pd.DataFrame:
    end_date = pd.Timestamp.today().normalize()
    start_date = end_date - pd.Timedelta(days=365 * 10)
    frame = fetch_index_data(
        symbol,
        start_date.strftime("%Y%m%d"),
        end_date.strftime("%Y%m%d"),
        tickflow_daily_count=STYLE_ROTATION_TICKFLOW_DAILY_COUNT,
    )
    normalized = normalize_price_frame(frame)
    if normalized.empty:
        raise RuntimeError(f"指数历史数据规范化后为空: {symbol}")
    return normalized
```

- [ ] **Step 4: Keep dividend observation on the same shared style-rotation payload path**

Do not add a second special case in `dividend_observation_chart.py`; just ensure its forced style-rotation refresh still calls the shared style-rotation collector.

```python
def _default_style_rotation_fetcher() -> dict[str, Any]:
    from ..valuation.style_rotation import collect_style_rotation_preview_payload
    payload = collect_style_rotation_preview_payload()
    if not isinstance(payload, dict):
        raise ValueError("style rotation payload must be a mapping")
    return payload
```

The code may already look like this; if so, leave production code unchanged and only keep the regression test.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_valuation_style_rotation.py tests/test_dividend_observation_chart.py -q`

Expected: PASS with the new archive-first coverage.

- [ ] **Step 6: Commit**

```bash
git add src/valuation/style_rotation.py src/research/dividend_observation_chart.py tests/test_valuation_style_rotation.py tests/test_dividend_observation_chart.py
git commit -m "test: cover archive-first style rotation consumption"
```

### Task 4: Full Verification and Archive Sanity Check

**Files:**
- Verify only: working tree

- [ ] **Step 1: Run the focused archive and style-rotation test set**

Run:

```bash
python -m pytest tests/test_valuation_fetch.py tests/test_valuation_refresh_archive.py tests/test_valuation_style_rotation.py tests/test_dividend_observation_chart.py tests/test_dividend_observation_email_run.py tests/test_dividend_observation_refresh_local.py -q
```

Expected: PASS, no new failures.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
python -m pytest --basetemp=.pytest_tmp
```

Expected: PASS for the whole repo.

- [ ] **Step 3: Smoke-check the local refresh command**

Run:

```bash
python -m src.dividend_observation.refresh_local --skip-archive
```

Expected:
- `data/research/dividend_observation_930955.json` refreshed
- `preview/dividend_observation_930955.html` regenerated
- `preview/dividend_observation_email.html` regenerated
- no style-rotation missing-file warning

- [ ] **Step 4: Commit final verification-only adjustments if needed**

```bash
git add -A
git commit -m "chore: finalize style rotation tencent archive wiring"
```

Only do this if verification required a real tracked change; otherwise skip the commit.
