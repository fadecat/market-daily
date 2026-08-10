# 市场估值邮件估算值接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 为市场估值邮件配置的八只指数补齐 T 日估值、分位、利差和 PE 图表尾点，并明确标记预估值。

**Architecture:** estimate_ledger.py 保持为唯一估算公式和账本写入者。新增 estimate_overlay.py 只读取和校验账本、使用归档历史重建邮件所需指标；run.py 在取得真正的底层 index_code 后调用它，渲染层只读取 item 上的元数据。

**Tech Stack:** Python、pandas、JSON 归档、pytest、matplotlib。

---

## 文件结构

- src/valuation/estimate_ledger.py：邮件可注入已获取的国债历史，安全只读指定账本记录。
- src/valuation/estimate_overlay.py（新建）：完整同日估算的校验、指标与分位重算、PE 图表历史拼接。
- src/valuation/run.py：刷新并应用每个已解析底层指数的账本。
- src/valuation/render.py、src/valuation/charts.py：展示（预估，日期）。
- tests/test_valuation_estimate_ledger.py、tests/test_valuation_run.py、tests/test_valuation_render.py、tests/test_valuation_charts.py：扩展。
- tests/test_valuation_estimate_overlay.py（新建）：覆盖层的纯函数测试。

### Task 1: 暴露可安全复用的账本接口

**Files:**

- Modify: src/valuation/estimate_ledger.py
- Modify: tests/test_valuation_estimate_ledger.py

- [ ] **Step 1: 先写失败测试**

~~~python
def test_refresh_uses_supplied_bond_history_without_fetching(monkeypatch, tmp_path):
    supplied = pd.DataFrame([{"date": "2026-08-10", "yield_pct": 1.7074}])
    monkeypatch.setattr(estimate_ledger, "_upsert_and_write", lambda *args: True)
    assert estimate_ledger.refresh_estimate_ledger(
        "930955", archive_root=tmp_path, output_root=tmp_path,
        bond_history=supplied,
        bond_history_fetcher=lambda **kwargs: pytest.fail("must not fetch"),
    ) is True

def test_load_estimate_record_rejects_mismatched_or_incomplete_ledger(tmp_path):
    (tmp_path / "930955.json").write_text(
        json.dumps({"index_code": "930956", "records": []}), encoding="utf-8"
    )
    assert estimate_ledger.load_estimate_record("930955", "2026-08-10", tmp_path) is None
~~~

- [ ] **Step 2: 确认测试按预期失败**

Run: python -m pytest tests/test_valuation_estimate_ledger.py::test_refresh_uses_supplied_bond_history_without_fetching tests/test_valuation_estimate_ledger.py::test_load_estimate_record_rejects_mismatched_or_incomplete_ledger -q

Expected: FAIL，refresh_estimate_ledger 没有 bond_history 参数，且 load_estimate_record 不存在。

- [ ] **Step 3: 最小实现**

~~~python
def load_estimate_record(index_code: str, estimate_date: str, output_root: Path | str = DEFAULT_OUTPUT_ROOT) -> dict[str, Any] | None:
    code = _validate_index_code(index_code)
    try:
        payload = _load_ledger_payload(Path(output_root) / f"{code}.json")
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if str(payload.get("index_code") or "").strip() != code:
        return None
    for record in payload.get("records", []):
        if isinstance(record, dict) and record.get("estimate_date") == estimate_date and isinstance(record.get("estimates"), dict):
            return record
    return None
~~~

给 refresh_estimate_ledger 增加如下参数：bond_history: pd.DataFrame | None = None。非空时直接传给 build_estimate_records，空时保留现有 bond_history_fetcher 行为。读取失败、代码不符、日期不符、records 非 list 或 estimates 非 dict 一律返回 None。

- [ ] **Step 4: 运行账本测试**

Run: python -m pytest tests/test_valuation_estimate_ledger.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~bash
git add src/valuation/estimate_ledger.py tests/test_valuation_estimate_ledger.py
git commit -m "feat: expose valuation estimate records"
~~~

### Task 2: 实现纯估算覆盖层

**Files:**

- Create: src/valuation/estimate_overlay.py
- Create: tests/test_valuation_estimate_overlay.py

- [ ] **Step 1: 写出覆盖与回退的失败测试**

~~~python
def test_apply_estimate_replaces_all_current_values_and_builds_t_day_history():
    result = estimate_overlay.apply_estimate(
        _official_item(index_code="931052", valuation_date="2026-08-07"),
        estimate=_estimate("2026-08-10", pe=8.86, pb=0.88, dividend_yield=4.42),
        price_date="2026-08-10", valuation_history=_valuation_history(),
        dividend_history=_dividend_history(), bond_history=_bond_history(),
    )
    assert result.item["estimate_meta"] == {"date": "2026-08-10", "status": "estimated"}
    assert result.item["index_valuation_metrics"]["PE(TTM)"]["current"] == 8.86
    assert result.item["index_valuation_metrics"]["PB(LF)"]["current"] == 0.88
    assert result.item["index_dividend_yield"] == 4.42
    assert result.item["equity_bond_ratio"] == pytest.approx(100 / 8.86 - 1.7074)
    assert result.pe_history.iloc[-1].to_dict() == {"date": pd.Timestamp("2026-08-10"), "pe": 8.86}

def test_apply_estimate_returns_none_for_partial_or_date_mismatched_record():
    assert estimate_overlay.apply_estimate(
        _official_item(), estimate={"estimates": {"pe_ttm": 8.0}},
        price_date="2026-08-10", valuation_history=_valuation_history(),
        dividend_history=_dividend_history(), bond_history=_bond_history(),
    ) is None
~~~

- [ ] **Step 2: 确认模块不存在而失败**

Run: python -m pytest tests/test_valuation_estimate_overlay.py -q

Expected: FAIL，estimate_overlay 无法导入。

- [ ] **Step 3: 实现不可变的覆盖 API**

~~~python
@dataclass(frozen=True)
class EstimateOverlay:
    item: dict[str, Any]
    pe_history: pd.DataFrame

def apply_estimate(
    item: dict[str, Any], *, estimate: dict[str, Any], price_date: str,
    valuation_history: pd.DataFrame, dividend_history: pd.DataFrame,
    bond_history: pd.DataFrame,
) -> EstimateOverlay | None:
    """Return a complete same-date estimated item, otherwise None."""

def apply_from_archives(
    item: dict[str, Any], *, estimate: dict[str, Any], price_date: str,
    archive_root: Path | str, bond_history: pd.DataFrame,
) -> EstimateOverlay | None:
    """Load local histories then delegate to apply_estimate."""

def latest_price_date(index_code: str, archive_root: Path | str) -> str | None:
    """Return the latest valid close date for the requested six-digit index."""
~~~

严格读取正数 pe_ttm、pb_lf、dividend_yield 和有限的两条利差；要求 estimate_date 等于 price_date，并要求当天国债存在。apply_from_archives 只从 archive_root 的估值、股息率和价格归档读取相应指数历史后调用 apply_estimate。复制输入 item 后，整套替换 PE/PB 的 current、股息率、股债收益差和比值；不得留任何 T-1 current。基于归档历史加上 T 日估值重算 PE/PB 的 3M、6M、1Y、2Y、3Y、5Y、10Y、今年以来、成立以来分位；用 metrics.compute_equity_bond_spread_percentiles 和含 T 日 PE、国债的历史重算股债收益差和比值分位。返回拼好 T 日 PE 的 pe_history。

- [ ] **Step 4: 增加防回归测试并运行**

~~~python
def test_apply_estimate_does_not_mutate_official_item():
    item = _official_item()
    estimate_overlay.apply_estimate(
        item, estimate=_estimate("2026-08-10"), price_date="2026-08-10",
        valuation_history=_valuation_history(), dividend_history=_dividend_history(),
        bond_history=_bond_history(),
    )
    assert item["index_valuation_metrics"]["PE(TTM)"]["current"] == 9.0

def test_apply_estimate_returns_none_when_t_day_bond_is_missing():
    assert estimate_overlay.apply_estimate(
        _official_item(), estimate=_estimate("2026-08-10"), price_date="2026-08-10",
        valuation_history=_valuation_history(), dividend_history=_dividend_history(),
        bond_history=pd.DataFrame(columns=["date", "yield_pct"]),
    ) is None
~~~

Run: python -m pytest tests/test_valuation_estimate_overlay.py tests/test_valuation_metrics.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~bash
git add src/valuation/estimate_overlay.py tests/test_valuation_estimate_overlay.py
git commit -m "feat: build market valuation estimate overlay"
~~~

### Task 3: 将覆盖层接入市场估值邮件

**Files:**

- Modify: src/valuation/run.py
- Modify: tests/test_valuation_run.py

- [ ] **Step 1: 编写数据接入失败测试**

~~~python
def test_fetch_valuation_items_uses_resolved_index_code_for_estimate(monkeypatch, tmp_path):
    refreshed = []
    target = {"name": "中证价值100", "code": "512040", "type": "valuation", "index_detail_url": "https://x/?indexCode=931052"}
    monkeypatch.setattr(run.estimate_ledger, "refresh_estimate_ledger", lambda code, **kwargs: refreshed.append(code) or True)
    monkeypatch.setattr(run.estimate_ledger, "load_estimate_record", lambda code, date, **kwargs: _estimate(date) if code == "931052" else None)
    monkeypatch.setattr(run.estimate_overlay, "apply_from_archives", lambda item, **kwargs: _overlay(item))
    items, _ = run._fetch_valuation_items([target], tmp_path)
    assert refreshed == ["931052"]
    assert items[0]["estimate_meta"]["status"] == "estimated"

def test_fetch_valuation_items_keeps_official_item_when_estimate_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(run.estimate_ledger, "refresh_estimate_ledger", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad ledger")))
    items, _ = run._fetch_valuation_items([_target()], tmp_path)
    assert "estimate_meta" not in items[0]
~~~

- [ ] **Step 2: 确认测试失败**

Run: python -m pytest tests/test_valuation_run.py::test_fetch_valuation_items_uses_resolved_index_code_for_estimate tests/test_valuation_run.py::test_fetch_valuation_items_keeps_official_item_when_estimate_fails -q

Expected: FAIL。

- [ ] **Step 3: 在 _fetch_valuation_items 组装 item 后应用覆盖**

实现 _try_apply_estimate(item, bond_history)：使用 item 的 index_code，不是配置的 code；调用 refresh_estimate_ledger(code, bond_history=bond_history)；读取最新价格日的同日记录；使用 estimate_overlay.apply_from_archives 构造覆盖。任一异常只打印 WARN 并返回原 item。估算成功时跳过旧的 attach_equity_bond_ratio 和 attach_equity_bond_spread，并把返回的 pe_history 传给 charts.generate_valuation_percentile_chart；未覆盖的项目保持原有调用。

- [ ] **Step 4: 运行接入回归**

Run: python -m pytest tests/test_valuation_run.py tests/test_valuation_estimate_overlay.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~bash
git add src/valuation/run.py tests/test_valuation_run.py
git commit -m "feat: overlay estimates in market valuation email"
~~~

### Task 4: 标记正文和图表中的预估值

**Files:**

- Modify: src/valuation/render.py
- Modify: src/valuation/charts.py
- Modify: tests/test_valuation_render.py
- Modify: tests/test_valuation_charts.py

- [ ] **Step 1: 编写失败测试**

~~~python
def test_render_email_item_marks_estimated_current_values():
    item = _full_item()
    item["estimate_meta"] = {"date": "2026-08-10", "status": "estimated"}
    html = render.render_email_item_percentile_block(item)
    assert html.count("（预估，2026-08-10）") == 5

def test_render_email_item_does_not_mark_official_values():
    assert "（预估" not in render.render_email_item_percentile_block(_full_item())
~~~

- [ ] **Step 2: 确认渲染测试失败**

Run: python -m pytest tests/test_valuation_render.py::test_render_email_item_marks_estimated_current_values tests/test_valuation_render.py::test_render_email_item_does_not_mark_official_values -q

Expected: FAIL。

- [ ] **Step 3: 最小展示实现**

~~~python
def _estimate_suffix(item: Dict) -> str:
    meta = item.get("estimate_meta")
    if isinstance(meta, dict) and meta.get("status") == "estimated" and meta.get("date"):
        return f"（预估，{meta['date']}）"
    return ""
~~~

只在 PE、PB、股息率、股债收益差和股债比值法的当前值后追加后缀；分位、标题、其他邮件区块不变。charts._draw_valuation_main 的最后 PE 注释依据 estimate_meta 加（预估）。

- [ ] **Step 4: 运行渲染和图表测试**

Run: python -m pytest tests/test_valuation_render.py tests/test_valuation_charts.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~bash
git add src/valuation/render.py src/valuation/charts.py tests/test_valuation_render.py tests/test_valuation_charts.py
git commit -m "feat: mark estimated values in valuation email"
~~~

### Task 5: 端到端验证与本地预览

**Files:**

- No source changes expected.

- [ ] **Step 1: 运行完整相关测试**

Run: python -m pytest tests/test_valuation_estimate_ledger.py tests/test_valuation_estimate_overlay.py tests/test_valuation_run.py tests/test_valuation_render.py tests/test_valuation_charts.py tests/test_dividend_observation_chart.py tests/test_dividend_observation_chart_preview.py -q

Expected: PASS。已知无关的 test_valuation_refresh_archive.py::test_main_all_fail_returns_1 不在本次命令中。

- [ ] **Step 2: 生成本地预览，不发送邮件**

Run: python -m src.valuation.run --preview --output preview/valuation_email_estimated.html

Expected: 有完整 T 日账本估算的项目显示（预估）；HTML、PNG、账本 JSON 只本地保留，不暂存、不提交。

- [ ] **Step 3: 核对提交范围**

Run: git status --short && git log --oneline -5

Expected: 代码提交仅包含本计划列出的 Python、测试和文档；生成 JSON、HTML、PNG 与用户已有归档 JSON 均未暂存。
