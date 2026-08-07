"""数据与邮件预览的本地静态校验。

不重跑任何抓取/策略,只读取 ``data/state``、``data/archive`` 与
``preview/*.html`` 做静态检查,输出 ``preview/verify_report.md``。

校验项:
1. 状态快照:各板块 state 关键字段非空(valuation / etf_rotation_20d /
   cb_three_low / cctda_coal_daily)。
2. 净值/持仓连续性:ETF 与三低轮动的 holdings_history 日期严格递增、
   nav 非空、末尾日期对齐 last_run_date。
3. 归档日期连续性:index/bond/fx 归档记录无重复日期、升序、末尾日期不过期;
   guorn_meta 快照文件名日期连续。
4. 预览 HTML:无残留未解析的 ``cid:`` 内嵌图引用。

用法::

    python -m src.preview.verify
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATE_DIR = _REPO_ROOT / "data" / "state"
_ARCHIVE_DIR = _REPO_ROOT / "data" / "archive"
_PREVIEW_DIR = _REPO_ROOT / "preview"
_REPORT_PATH = _PREVIEW_DIR / "verify_report.md"

# (state 名, 中文标签, 必需键)
_STATE_SPECS: list[tuple[str, str, tuple[str, ...]]] = [
    ("valuation", "市场估值", ("last_valuation_date",)),
    ("etf_rotation_20d", "资产轮动ETF", ("holdings_history", "portfolio_nav", "next_holding")),
    ("cb_three_low", "转债三低轮动", ("holdings_history", "next_holding")),
    ("cctda_coal_daily", "煤炭日报", ("article_url", "sent_at")),
]

# 需做持仓连续性校验的 state 名
_CONTINUITY_STATES = ("etf_rotation_20d", "cb_three_low")

# 归档数据集:(数据集目录, 日期键, 单文件名或 None)
_INDEX_DATASETS = ("index_eod", "index_dividend_ratio", "index_valuation_percentile")
_FIXED_FILES = [
    ("bond_10y", "日期", "china_10y.json"),
    ("fx", "日期", "usd_cnh.json"),
]

_CID_RE = re.compile(r"cid:")


@dataclass
class CheckResult:
    """单条校验结果。"""

    section: str
    name: str
    ok: bool
    detail: str = ""
    issues: list[str] = field(default_factory=list)

    @property
    def mark(self) -> str:
        return "✅" if self.ok else "❌"


# ----------------------------- 纯函数校验 -----------------------------


def _parse_date(value: Any) -> Optional[date]:
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _today() -> date:
    """当前日期(抽出便于测试注入)。"""
    return date.today()


def _days_stale(date_str: Any, today: date) -> Optional[int]:
    """返回日期距今天数;无法解析返回 None。"""
    d = _parse_date(date_str)
    if d is None:
        return None
    return (today - d).days


def check_state(name: str, label: str, required_keys: tuple[str, ...], state: Any) -> list[str]:
    """校验单个 state 快照的关键字段。"""
    if state is None:
        return [f"{label}({name}) 状态缺失"]
    issues: list[str] = []
    for k in required_keys:
        v = state.get(k) if isinstance(state, dict) else None
        if v is None or v == "":
            issues.append(f"{label}({name}) 缺少 {k}")
    if "holdings_history" in required_keys:
        hist = state.get("holdings_history") if isinstance(state, dict) else None
        if not isinstance(hist, list) or not hist:
            issues.append(f"{label}({name}) holdings_history 为空")
    return issues


def check_holdings_continuity(label: str, state: Any) -> list[str]:
    """校验 holdings_history:日期严格递增、nav 非空、末尾日期==last_run_date。"""
    if not isinstance(state, dict):
        return []  # 缺失已在状态快照报过
    hist = state.get("holdings_history") or []
    if not hist:
        return []
    issues: list[str] = []
    prev: Optional[str] = None
    for i, e in enumerate(hist):
        if not isinstance(e, dict):
            issues.append(f"{label} 第{i}条非对象")
            continue
        d = e.get("date")
        nav = e.get("nav")
        if d is None:
            issues.append(f"{label} 第{i}条缺 date")
        elif prev is not None and str(d) <= prev:
            issues.append(f"{label} 日期非严格递增: {prev} -> {d} (#{i})")
        prev = str(d) if d is not None else prev
        if nav is None:
            issues.append(f"{label} 第{i}条 nav 为空")
    last_run = state.get("last_run_date")
    if last_run and hist and isinstance(hist[-1], dict):
        last_date = hist[-1].get("date")
        if last_date is not None and str(last_date) != str(last_run):
            issues.append(f"{label} 末尾日期 {last_date} != last_run_date {last_run}")
    return issues


def check_archive_dates(
    label: str, records: list, date_key: str, today: date, *, max_stale_days: int = 10
) -> list[str]:
    """校验归档记录:非空、无重复日期、升序、末尾日期可解析且不过期。"""
    if not records:
        return [f"{label} 无记录"]
    issues: list[str] = []
    dates: list[str] = []
    for r in records:
        if not isinstance(r, dict):
            issues.append(f"{label} 存在非对象记录")
            continue
        d = r.get(date_key)
        if d is None:
            issues.append(f"{label} 存在缺 {date_key} 的记录")
            continue
        dates.append(str(d)[:10])
    if dates:
        seen: set[str] = set()
        dups = sorted({d for d in dates if d in seen or seen.add(d)})
        if dups:
            issues.append(f"{label} 重复日期 {len(dups)} 个: {dups[:5]}")
        if dates != sorted(dates):
            issues.append(f"{label} 日期未升序")
        last = dates[-1]
        stale = _days_stale(last, today)
        if stale is None:
            issues.append(f"{label} 末尾日期 {last} 无法解析")
        elif stale > max_stale_days:
            issues.append(f"{label} 末尾日期 {last} 过期({stale}天 > {max_stale_days})")
    return issues


def check_guorn_meta_dates(file_dates: list[str], today: date, *, max_stale_days: int = 10) -> list[str]:
    """校验 guorn_meta 快照文件名日期:非空、无重复、升序、末尾不过期。"""
    if not file_dates:
        return ["guorn_meta 无快照"]
    issues: list[str] = []
    dups = sorted({d for d in file_dates if file_dates.count(d) > 1})
    if dups:
        issues.append(f"guorn_meta 重复日期: {dups}")
    if file_dates != sorted(file_dates):
        issues.append("guorn_meta 文件名日期未升序")
    last = file_dates[-1]
    stale = _days_stale(last, today)
    if stale is None:
        issues.append(f"guorn_meta 末尾日期 {last} 无法解析")
    elif stale > max_stale_days:
        issues.append(f"guorn_meta 末尾日期 {last} 过期({stale}天 > {max_stale_days})")
    return issues


def check_preview_html(name: str, html: str) -> list[str]:
    """校验预览 HTML 非空且无残留未解析 cid: 引用。"""
    if not html:
        return [f"{name} 为空或缺失"]
    leftover = _CID_RE.findall(html)
    if leftover:
        return [f"{name} 残留未解析 cid: 引用 {len(leftover)} 处"]
    return []


# ----------------------------- 文件读取 -----------------------------


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_state(state_dir: Path, name: str) -> Any:
    return _load_json(state_dir / f"{name}.json")


def _records_of(data: Any) -> list:
    if isinstance(data, dict):
        recs = data.get("records")
        if isinstance(recs, list):
            return recs
        return []
    if isinstance(data, list):
        return data
    return []


# ----------------------------- 编排 -----------------------------


def run_all(
    *,
    today: Optional[date] = None,
    state_dir: Optional[Path] = None,
    archive_dir: Optional[Path] = None,
    preview_dir: Optional[Path] = None,
    max_stale_days: Optional[int] = None,
    load_state_fn: Optional[Callable[[str], Any]] = None,
) -> list[CheckResult]:
    """执行全部静态校验,返回结果列表。"""
    today = today or _today()
    state_dir = Path(state_dir) if state_dir else _STATE_DIR
    archive_dir = Path(archive_dir) if archive_dir else _ARCHIVE_DIR
    preview_dir = Path(preview_dir) if preview_dir else _PREVIEW_DIR
    stale = max_stale_days if max_stale_days is not None else 10
    loader = load_state_fn or (lambda n: _load_state(state_dir, n))

    results: list[CheckResult] = []

    # 1. 状态快照 + 2. 持仓连续性
    for name, label, keys in _STATE_SPECS:
        state = loader(name)
        issues = check_state(name, label, keys, state)
        results.append(
            CheckResult("状态快照", f"{label}({name})", not issues, _state_detail(state), issues)
        )
        if name in _CONTINUITY_STATES:
            ci = check_holdings_continuity(label, state)
            results.append(CheckResult("净值/持仓连续性", f"{label}({name})", not ci, "", ci))

    # 3. 归档日期连续性
    for dataset in _INDEX_DATASETS:
        ddir = archive_dir / dataset
        if not ddir.is_dir():
            results.append(CheckResult("归档日期连续性", dataset, False, "目录缺失", [f"{dataset} 目录缺失"]))
            continue
        for f in sorted(ddir.glob("*.json")):
            recs = _records_of(_load_json(f))
            issues = check_archive_dates(f"{dataset}/{f.stem}", recs, "trdDt", today, max_stale_days=stale)
            results.append(CheckResult("归档日期连续性", f"{dataset}/{f.stem}", not issues, "", issues))
    for dataset, key, fname in _FIXED_FILES:
        p = archive_dir / dataset / fname
        if not p.exists():
            results.append(CheckResult("归档日期连续性", dataset, False, "文件缺失", [f"{p} 缺失"]))
            continue
        recs = _records_of(_load_json(p))
        issues = check_archive_dates(dataset, recs, key, today, max_stale_days=stale)
        results.append(CheckResult("归档日期连续性", dataset, not issues, "", issues))
    gm = archive_dir / "guorn_meta"
    if gm.is_dir():
        file_dates = sorted(f.stem for f in gm.glob("*.json"))
        issues = check_guorn_meta_dates(file_dates, today, max_stale_days=stale)
        results.append(CheckResult("归档日期连续性", "guorn_meta", not issues, "", issues))

    # 4. 预览 HTML cid 完整性
    if preview_dir.is_dir():
        for p in sorted(preview_dir.glob("*.html")):
            html = p.read_text(encoding="utf-8") if p.exists() else ""
            issues = check_preview_html(p.stem, html)
            results.append(CheckResult("预览HTML", p.stem, not issues, "", issues))

    return results


def _state_detail(state: Any) -> str:
    if not isinstance(state, dict):
        return "缺失"
    if "last_valuation_date" in state:
        return f"last_valuation_date={state['last_valuation_date']}"
    if "holdings_history" in state:
        return f"{len(state.get('holdings_history') or [])}条 nav={state.get('portfolio_nav', state.get('nav', ''))}"
    if "article_url" in state:
        return f"sent_at={state.get('sent_at', '')}"
    return ""


# ----------------------------- 报告 -----------------------------


def build_report(results: list[CheckResult], today: date) -> str:
    """把校验结果渲染为 markdown 报告。"""
    total = len(results)
    fails = sum(1 for r in results if not r.ok)
    summary = "✅ 全部通过" if fails == 0 else f"❌ {fails} 项问题"

    lines: list[str] = [
        "# 数据校验报告",
        "",
        f"生成日期: {today.isoformat()}",
        f"总体: {summary}（共 {total} 项,通过 {total - fails}）",
        "",
    ]
    sections: dict[str, list[CheckResult]] = {}
    for r in results:
        sections.setdefault(r.section, []).append(r)
    for section, items in sections.items():
        lines.append(f"## {section}")
        lines.append("")
        lines.append("| 项目 | 状态 | 详情 |")
        lines.append("|---|---|---|")
        for r in items:
            detail = r.detail or ("；".join(r.issues) if r.issues else "OK")
            # 转义 markdown 表格分隔符
            detail = detail.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {r.name} | {r.mark} | {detail} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="静态校验 data/state、data/archive 与 preview/*.html")
    parser.add_argument("--output", default=str(_REPORT_PATH), help="报告输出路径")
    args = parser.parse_args()

    today = _today()
    results = run_all(today=today)
    report = build_report(results, today)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    fails = sum(1 for r in results if not r.ok)
    print(f"[INFO] 校验完成: {len(results)} 项, {fails} 项问题 -> {out}")
    for r in results:
        if not r.ok:
            print(f"  {r.mark} [{r.section}] {r.name}: {'；'.join(r.issues)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
