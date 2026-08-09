# Dividend Observation Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Monday-to-Saturday dividend observation email that reuses the existing `930955` research payload, keeps local JSON/HTML preview generation intact, and sends a PNG-based HTML email to a dedicated recipient variable.

**Architecture:** Keep research generation in `src/research`, add a new `src/dividend_observation` production module for chart PNG rendering, email HTML rendering, and send/preview orchestration, and minimally extend `src/common/email.py` so this new module can read `DIVIDEND_OBSERVATION_RECEIVER_EMAIL` without affecting existing mailers. Use matplotlib for static charts and reuse the existing `common.email` inline image mechanism for `cid` delivery.

**Tech Stack:** Python 3.12, pytest, matplotlib, existing `src/common/email.py`, GitHub Actions workflow YAML

---

## File Structure

### New Files

- `src/dividend_observation/__init__.py`
  New package marker for the standalone mailer.
- `src/dividend_observation/data.py`
  Bridge from the research payload generator/JSON file into email production code.
- `src/dividend_observation/charts.py`
  Static PNG generation for the 4 email chart sections.
- `src/dividend_observation/render.py`
  Email-compatible HTML for preview and send modes.
- `src/dividend_observation/run.py`
  `--preview` and send entrypoint, section failure handling, dedicated recipient loading.
- `tests/test_common_email.py`
  Tests for custom recipient env loading in `src/common/email.py`.
- `tests/test_dividend_observation_email_charts.py`
  Tests for chart PNG generation and partial-failure behavior.
- `tests/test_dividend_observation_email_render.py`
  Tests for preview/send HTML rendering and failure placeholders.
- `tests/test_dividend_observation_email_run.py`
  Tests for preview/send orchestration and workflow configuration expectations.
- `.github/workflows/dividend-observation.yml`
  Independent Monday-to-Saturday workflow with the dedicated receiver variable.

### Modified Files

- `src/common/email.py`
  Add optional custom recipient env selection while preserving default behavior.
- `src/research/dividend_observation_chart.py`
  No behavior change planned; this file remains the authoritative payload generator and should only be touched if a narrow import/export helper is truly necessary.
- `src/research/dividend_observation_chart_preview.py`
  No behavior change planned; keep the browser preview path intact.

---

### Task 1: Dedicated Recipient Config And Payload Bridge

**Files:**
- Create: `src/dividend_observation/__init__.py`
- Create: `src/dividend_observation/data.py`
- Create: `tests/test_common_email.py`
- Modify: `src/common/email.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_common_email.py
from src.common import email


def test_load_email_config_supports_custom_recipient_variable(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("DIVIDEND_OBSERVATION_RECEIVER_EMAIL", "alpha@example.com,beta@example.com")
    monkeypatch.delenv("RECEIVER_EMAIL", raising=False)
    monkeypatch.delenv("EMAIL_TO", raising=False)

    config = email.load_email_config(recipient_env_name="DIVIDEND_OBSERVATION_RECEIVER_EMAIL")

    assert config["recipients"] == ["alpha@example.com", "beta@example.com"]
    assert config["sender"] == "sender@example.com"


def test_load_email_config_keeps_default_receiver_behavior(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("RECEIVER_EMAIL", "default@example.com")

    config = email.load_email_config()

    assert config["recipients"] == ["default@example.com"]
```

```python
# tests/test_dividend_observation_email_run.py
import json
from pathlib import Path

from src.dividend_observation import data


def test_load_payload_reads_existing_research_json(tmp_path):
    payload_path = tmp_path / "dividend_observation_930955.json"
    payload_path.write_text(json.dumps({"meta": {"index_code": "930955"}, "series": {}, "latest": {}}), encoding="utf-8")

    payload = data.load_payload(payload_path)

    assert payload["meta"]["index_code"] == "930955"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_common_email.py tests/test_dividend_observation_email_run.py -q
```

Expected:

- `ModuleNotFoundError` for `src.dividend_observation`
- or `TypeError` because `load_email_config()` does not accept `recipient_env_name`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/common/email.py
def load_email_config(*, recipient_env_name: str = "RECEIVER_EMAIL") -> dict[str, Any]:
    recipients_raw = env.get(recipient_env_name) or env.get("EMAIL_TO")
    recipients = [r.strip() for r in recipients_raw.replace(";", ",").split(",") if r.strip()]
    username = (env.get("SMTP_USER") or env.get("EMAIL_USER")).strip()
    password = (env.get("SMTP_PASS") or env.get("EMAIL_PASSWORD")).strip()
    if not recipients or not username or not password:
        raise RuntimeError(
            f"邮件配置不完整,需要 {recipient_env_name}/SMTP_USER/SMTP_PASS"
        )
    host = (env.get("EMAIL_SMTP_HOST") or DEFAULT_SMTP_HOST).strip() or DEFAULT_SMTP_HOST
    port = int(env.get("EMAIL_SMTP_PORT") or str(DEFAULT_SMTP_PORT))
    return {
        "smtp_host": host,
        "smtp_port": port,
        "username": username,
        "password": password,
        "sender": (env.get("EMAIL_FROM") or username).strip() or username,
        "recipients": recipients,
    }
```

```python
# src/dividend_observation/data.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..research.dividend_observation_chart import (
    DEFAULT_OUTPUT_PATH,
    build_dividend_observation_payload,
)


def load_payload(path: Path | str = DEFAULT_OUTPUT_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dividend observation payload must be a mapping")
    return payload


def build_or_load_payload(
    *,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    force_refresh: bool = True,
) -> dict[str, Any]:
    path = Path(output_path)
    if force_refresh:
        payload = build_dividend_observation_payload()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload
    if path.exists():
        return load_payload(path)
    payload = build_dividend_observation_payload()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_common_email.py tests/test_dividend_observation_email_run.py -q
```

Expected:

- `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/common/email.py src/dividend_observation/__init__.py src/dividend_observation/data.py tests/test_common_email.py tests/test_dividend_observation_email_run.py
git commit -m "feat: add dividend observation email config bridge"
```

### Task 2: Static Chart PNG Generation

**Files:**
- Create: `src/dividend_observation/charts.py`
- Create: `tests/test_dividend_observation_email_charts.py`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from src.dividend_observation import charts


def _payload() -> dict:
    return {
        "meta": {"index_name": "红利低波100", "analysis_window_years": 3},
        "series": {
            "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "index_close": [100.0, 98.0, 99.0],
            "drawdown_peak": [0.0, -0.02, -0.01],
            "dividend_yield_spread_percentile": [40.0, 60.0, 70.0],
            "earnings_yield_spread_percentile": [35.0, 55.0, 65.0],
            "pe_ttm_percentile": [60.0, 45.0, 50.0],
            "pb_lf_percentile": [58.0, 44.0, 48.0],
            "style_rotation_spread_percentile": [80.0, 78.0, 82.0],
        },
    }


def test_generate_all_chart_images_writes_pngs(tmp_path):
    bundle = charts.generate_chart_bundle(_payload(), tmp_path)

    assert set(bundle.keys()) == {"price", "spread", "valuation", "style"}
    assert all(item.image_path and Path(item.image_path).exists() for item in bundle.values())
    assert all(item.error is None for item in bundle.values())


def test_generate_chart_bundle_marks_section_error_when_series_missing(tmp_path):
    payload = _payload()
    payload["series"]["style_rotation_spread_percentile"] = [None, None, None]

    bundle = charts.generate_chart_bundle(payload, tmp_path)

    assert bundle["style"].image_path is None
    assert "暂无数据" in bundle["style"].error
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_dividend_observation_email_charts.py -q
```

Expected:

- `ModuleNotFoundError` for `src.dividend_observation.charts`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/dividend_observation/charts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ..common import fonts

PRICE_CHART_CID = "dividend_observation_price_chart"
SPREAD_CHART_CID = "dividend_observation_spread_chart"
VALUATION_CHART_CID = "dividend_observation_valuation_chart"
STYLE_CHART_CID = "dividend_observation_style_chart"


@dataclass
class ChartResult:
    cid: str
    image_path: str | None
    error: str | None = None


def generate_chart_bundle(payload: dict[str, Any], work_dir: Path) -> dict[str, ChartResult]:
    work_dir.mkdir(parents=True, exist_ok=True)
    return {
        "price": _safe_render_price_chart(payload, work_dir / "price.png"),
        "spread": _safe_render_two_line_chart(payload, work_dir / "spread.png", "dividend_yield_spread_percentile", "earnings_yield_spread_percentile", "利率相对吸引力", SPREAD_CHART_CID),
        "valuation": _safe_render_two_line_chart(payload, work_dir / "valuation.png", "pe_ttm_percentile", "pb_lf_percentile", "绝对定价", VALUATION_CHART_CID),
        "style": _safe_render_single_line_chart(payload, work_dir / "style.png", "style_rotation_spread_percentile", "风格挤压", STYLE_CHART_CID),
    }
```

Include concrete helpers in the file:

- `_parse_dates(series_dates: list[str]) -> list[datetime.date]`
- `_save_figure(fig, output_path)`
- `_safe_render_price_chart(...)`
- `_safe_render_two_line_chart(...)`
- `_safe_render_single_line_chart(...)`

Behavior requirements inside implementation:

- call `fonts.apply_cjk(plt)`
- return `ChartResult(cid=..., image_path=None, error="该图暂无数据")` when a chart has no usable series
- save PNG with `dpi=130`

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_dividend_observation_email_charts.py -q
```

Expected:

- `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/dividend_observation/charts.py tests/test_dividend_observation_email_charts.py
git commit -m "feat: add dividend observation email charts"
```

### Task 3: Email HTML Rendering For Preview And Send Modes

**Files:**
- Create: `src/dividend_observation/render.py`
- Create: `tests/test_dividend_observation_email_render.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.dividend_observation import render
from src.dividend_observation.charts import ChartResult


def _payload() -> dict:
    return {
        "meta": {"index_code": "930955", "index_name": "红利低波100", "analysis_window_years": 3},
        "latest": {
            "date": "2026-08-07",
            "index_close": 11291.48,
            "drawdown_peak": -0.0955,
            "pe_ttm_percentile": 74.47,
            "pb_lf_percentile": 72.88,
            "style_rotation_spread_percentile": 83.73,
            "event_state": "temporary_recovery",
        },
    }


def test_build_preview_html_embeds_base64_sources():
    charts = {
        "price": ChartResult(cid="price", image_path=None, error=None),
        "spread": ChartResult(cid="spread", image_path=None, error=None),
        "valuation": ChartResult(cid="valuation", image_path=None, error=None),
        "style": ChartResult(cid="style", image_path=None, error=None),
    }
    data_uri_map = {key: "data:image/png;base64,AAAA" for key in charts}

    html = render.build_preview_html(_payload(), charts, data_uri_map)

    assert "data:image/png;base64,AAAA" in html
    assert "价格与回撤" in html
    assert "利率相对吸引力" in html


def test_build_email_html_uses_cid_and_failure_placeholder():
    charts = {
        "price": ChartResult(cid="price", image_path="price.png", error=None),
        "spread": ChartResult(cid="spread", image_path=None, error="该图暂无数据"),
        "valuation": ChartResult(cid="valuation", image_path="valuation.png", error=None),
        "style": ChartResult(cid="style", image_path="style.png", error=None),
    }

    html = render.build_email_html(_payload(), charts)

    assert 'src="cid:price"' in html
    assert "该图暂无数据" in html
    assert "近窗回撤" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_dividend_observation_email_render.py -q
```

Expected:

- `ModuleNotFoundError` for `src.dividend_observation.render`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/dividend_observation/render.py
from __future__ import annotations

from html import escape
from typing import Mapping

from .charts import ChartResult

STATE_LABELS = {
    "failed_recovery": "修复失败",
    "temporary_recovery": "临时修复",
    "confirmed_recovery": "确认修复",
}


def build_email_html(payload: dict, charts: Mapping[str, ChartResult]) -> str:
    return _build_html(payload, charts, image_src_map={name: f"cid:{item.cid}" for name, item in charts.items() if item.image_path})


def build_preview_html(payload: dict, charts: Mapping[str, ChartResult], data_uri_map: Mapping[str, str]) -> str:
    return _build_html(payload, charts, image_src_map=data_uri_map)
```

Include concrete private helpers in the file:

- `_fmt_pct(value, scale_100=True)`
- `_fmt_num(value, digits=2)`
- `_state_label(value)`
- `_render_card_grid(latest)`
- `_render_section(title, note, formula, chart, image_src)`
- `_build_html(payload, charts, image_src_map)`

Rendering requirements:

- keep section order `price -> spread -> valuation -> style`
- when a chart has `error`, render `<div>该图暂无数据</div>` instead of `<img>`
- render `-` for missing top-card values
- include current analysis window years in formula text

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_dividend_observation_email_render.py -q
```

Expected:

- `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/dividend_observation/render.py tests/test_dividend_observation_email_render.py
git commit -m "feat: add dividend observation email html renderer"
```

### Task 4: Preview And Send Orchestration

**Files:**
- Create: `src/dividend_observation/run.py`
- Modify: `tests/test_dividend_observation_email_run.py`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from src.dividend_observation import run


def test_run_preview_writes_html(monkeypatch, tmp_path):
    monkeypatch.setattr(run.data, "build_or_load_payload", lambda **_: {"meta": {"analysis_window_years": 3, "index_name": "红利低波100"}, "latest": {}, "series": {}})
    monkeypatch.setattr(run.charts, "generate_chart_bundle", lambda payload, work_dir: {})
    monkeypatch.setattr(run.render, "build_preview_html", lambda payload, charts, data_uri_map: "<html>preview</html>")

    output = run.run_preview(tmp_path / "preview.html")

    assert output.exists()
    assert output.read_text(encoding="utf-8") == "<html>preview</html>"


def test_run_send_uses_dedicated_recipient_and_still_sends_on_partial_chart_failure(monkeypatch, tmp_path):
    payload = {
        "meta": {"analysis_window_years": 3, "index_name": "红利低波100"},
        "latest": {"date": "2026-08-07", "event_state": "temporary_recovery"},
        "series": {},
    }
    sent = {}

    monkeypatch.setattr(run.data, "build_or_load_payload", lambda **_: payload)
    monkeypatch.setattr(run.charts, "generate_chart_bundle", lambda payload, work_dir: {"price": run.charts.ChartResult(cid="price", image_path=None, error="该图暂无数据")})
    monkeypatch.setattr(run.render, "build_email_html", lambda payload, charts: "<html>mail</html>")
    monkeypatch.setattr(run.email, "load_email_config", lambda **kwargs: {"sender": "sender@example.com", "recipients": ["only@example.com"], "username": "sender@example.com", "password": "secret", "smtp_host": "smtp.qq.com", "smtp_port": 465})
    monkeypatch.setattr(run.email, "send_email", lambda subject, html, inline_images=None, config=None: sent.update({"subject": subject, "html": html, "config": config, "inline_images": inline_images}) or True)

    code = run.run_send()

    assert code == 0
    assert sent["config"]["recipients"] == ["only@example.com"]
    assert sent["html"] == "<html>mail</html>"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_dividend_observation_email_run.py -q
```

Expected:

- `ImportError` because `run` module does not exist

- [ ] **Step 3: Write the minimal implementation**

```python
# src/dividend_observation/run.py
from __future__ import annotations

import argparse
import base64
import tempfile
from pathlib import Path

from ..common import alerts, email
from . import charts, data, render

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREVIEW_PATH = REPO_ROOT / "preview" / "dividend_observation_email.html"


def _build_subject() -> str:
    from datetime import datetime
    return f"红利观察日报 | {datetime.now().strftime('%Y-%m-%d')}"
```

Complete this file with the following concrete behaviors:

- `run_preview(output_path=DEFAULT_PREVIEW_PATH) -> Path`
  - call `data.build_or_load_payload(force_refresh=True)`
  - create temp dir
  - build chart bundle
  - convert chart PNG bytes into `data:image/png;base64,...`
  - render preview HTML
  - write output path
- `run_send() -> int`
  - call `data.build_or_load_payload(force_refresh=True)`
  - create temp dir
  - build chart bundle
  - call `email.load_email_config(recipient_env_name="DIVIDEND_OBSERVATION_RECEIVER_EMAIL")`
  - build `inline_images` from successful chart paths only
  - render send HTML
  - call `email.send_email(..., config=config, inline_images=inline_images or None)`
  - return `0`
- hard failure path:
  - wrap `run_send()` in `try/except`
  - on fatal exception call `alerts.notify_alert("红利观察邮件运行失败", f"{type(exc).__name__}: {exc}")`
  - re-raise
- CLI:
  - `--preview`
  - `--output`

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_dividend_observation_email_run.py -q
```

Expected:

- `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/dividend_observation/run.py tests/test_dividend_observation_email_run.py
git commit -m "feat: add dividend observation email runner"
```

### Task 5: Workflow And Full Verification

**Files:**
- Create: `.github/workflows/dividend-observation.yml`
- Modify: `tests/test_dividend_observation_email_run.py`

- [ ] **Step 1: Write the failing workflow test**

```python
from pathlib import Path


def test_dividend_observation_workflow_uses_dedicated_receiver_variable():
    workflow = Path(".github/workflows/dividend-observation.yml")

    assert workflow.exists()
    text = workflow.read_text(encoding="utf-8")
    assert "DIVIDEND_OBSERVATION_RECEIVER_EMAIL" in text
    assert "python -m src.dividend_observation.run" in text
    assert "1-6" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
python -m pytest tests/test_dividend_observation_email_run.py::test_dividend_observation_workflow_uses_dedicated_receiver_variable -q
```

Expected:

- `AssertionError` because the workflow file does not exist

- [ ] **Step 3: Write the workflow**

```yaml
name: 红利观察日报

on:
  schedule:
    - cron: "20 11 * * 1-6"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  send:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python + CJK fonts
        uses: ./.github/actions/setup-python-cjk

      - name: 运行红利观察邮件
        env:
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          ALERT_WEBHOOK: ${{ secrets.ALERT_WEBHOOK }}
          DIVIDEND_OBSERVATION_RECEIVER_EMAIL: ${{ vars.DIVIDEND_OBSERVATION_RECEIVER_EMAIL }}
        run: python -m src.dividend_observation.run
```

- [ ] **Step 4: Run focused and full verification**

Run:

```powershell
python -m pytest tests/test_common_email.py tests/test_dividend_observation_email_charts.py tests/test_dividend_observation_email_render.py tests/test_dividend_observation_email_run.py -q
python -m src.research.dividend_observation_chart
python -m src.dividend_observation.run --preview
```

Expected:

- all dividend observation email tests pass
- `data/research/dividend_observation_930955.json` refreshes successfully
- `preview/dividend_observation_email.html` is written successfully

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/dividend-observation.yml tests/test_dividend_observation_email_run.py preview/dividend_observation_email.html
git commit -m "feat: add dividend observation email workflow"
```

## Self-Review

### Spec coverage

- Independent standalone module: covered by Tasks 1-4
- Keep research JSON/HTML preview path intact: preserved in Task 1 and Task 4
- PNG charts for email: covered by Task 2
- Base64 preview and `cid` email send split: covered by Task 3 and Task 4
- Dedicated receiver variable: covered by Task 1 and Task 5
- Monday-to-Saturday workflow: covered by Task 5
- Partial-failure send behavior: covered by Task 2, Task 3, and Task 4

No gaps remain.

### Placeholder scan

- No `TODO`
- No `TBD`
- No cross-references like “same as previous task”
- Each task names exact files, commands, and expected outcomes

### Type consistency

- `ChartResult` is defined once in `src/dividend_observation/charts.py` and referenced consistently by render/run/tests
- `build_or_load_payload()` remains the single payload entrypoint for the email module
- `load_email_config(recipient_env_name=...)` is the only new common-email API extension

No naming conflicts remain.
