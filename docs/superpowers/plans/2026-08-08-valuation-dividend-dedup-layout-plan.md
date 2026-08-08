# Valuation Dividend Dedup Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Eastmoney the source of truth for the valuation dividend area, remove overlapping Jisilu rows, and move the two context charts ahead of the dividend tables.

**Architecture:** Keep data fetching unchanged and make the behavior change in two narrow places: expose Eastmoney stock codes from the supplement assembler, then consume that metadata in the dividend renderer before building the Jisilu table. Update section ordering only in `src/valuation/run.py` so presentation order remains isolated from data assembly.

**Tech Stack:** Python, pytest, existing valuation email renderers

---

### Task 1: Lock the new behavior with tests

**Files:**
- Modify: `tests/test_valuation_dividend_supplement.py`
- Modify: `tests/test_valuation_dividend_render.py`
- Modify: `tests/test_valuation_run.py`

- [ ] Add a supplement test that proves `build_dividend_email_supplement()` returns normalized stock codes for later dedup.
- [ ] Add a render test where Eastmoney already contains one Jisilu stock and verify the duplicate row is removed while the non-overlapping row remains.
- [ ] Add a render test for the “去重东财后无新增标的” empty state.
- [ ] Update the run order test to expect `style -> fx -> dividend -> guorn`.

### Task 2: Implement the minimal production changes

**Files:**
- Modify: `src/valuation/dividend/supplement.py`
- Modify: `src/valuation/dividend/render.py`
- Modify: `src/valuation/run.py`

- [ ] Expose a stable `stock_codes` list from the Eastmoney supplement payload.
- [ ] Add a small helper in the dividend renderer to collect supplement codes and filter Jisilu rows by stock code.
- [ ] Update header and empty-state copy to reflect filter and dedup results without becoming verbose.
- [ ] Reorder extra sections in `run.py` to `style_rotation`, `fx`, `dividend`, `guorn`.

### Task 3: Verify behavior end to end

**Files:**
- Modify: `preview/valuation.html` if regenerated

- [ ] Run targeted pytest commands for the changed valuation tests.
- [ ] Regenerate the valuation preview and verify the new reading order in the produced HTML.
- [ ] If verification is green, stage the changed source, tests, and docs together for review or commit.
