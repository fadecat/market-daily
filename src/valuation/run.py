"""市场估值板块编排:聚合 5 section(估值核心+高股息+果仁+风格轮动+汇率图)-> 发邮件 / 预览。

用法:
    python -m src.valuation.run            # 聚合 5 section + 发邮件
    python -m src.valuation.run --preview  # 生成预览 HTML(图 base64 内嵌,不发信)

编排约定(参照 ``convertible/run.py``,但正文用 ``render.assemble_email_html`` 卡片布局,
非 ``compose_sections`` 的 ``<div>+<hr>``):

- **估值核心**(8 个指数 PE/PB/股息率/股债收益差)为主 section:循环外取一次 10Y 国债,
  逐标的 ``fetch_target_index_metrics`` + ``attach_equity_bond_*``,单标失败 try/except 跳过
  不中止;再逐 item 生成 PE 分位图。
- **高股息/果仁/风格轮动/汇率图** 为辅 section,按计划顺序(估值+高股息+果仁+风格轮动+汇率图)
  追加为 ``extra_sections``:各自失败 ``notify_alert`` 后 skip,不影响主邮件发出。
- 所有 section 共用同一 tempdir(work_dir),图须存活到 ``send_email`` 读图完成,故发信在
  ``with`` 块内。

板块级静默退出守卫(防同日重复发送):``data/state/valuation.json`` 存 ``last_send_date``,若今日已发过
则静默退出(return 0);发信成功后才更新 state。守卫按**发送日期**(北京今日)判断,不按估值基准日--
指数 PE 估值为 T-1,交易日基准日不变,但国债收益率/汇率图等辅 section 为 T+0 当日数据,若按估值基准日
gate 会误吞这些当日数据。15:31 首跑后 19:11 兜底因 last_send_date 命中而跳过;首跑失败时兜底补发。
``--preview`` 不走守卫。
"""
from __future__ import annotations

import argparse
import base64
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..common import alerts, email, env, storage
from . import charts, estimate_ledger, estimate_overlay, fetch, guorn, metrics, render, style_rotation
from .dividend import render as dividend_render

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "valuation.yaml"
DEFAULT_PREVIEW_PATH = _REPO_ROOT / "preview" / "valuation.html"

STATE_NAME = "valuation"


# ---------- 配置 ----------


def load_valuation_config(config_path: str = str(DEFAULT_CONFIG_PATH)) -> List[Dict[str, Any]]:
    """加载 ``config/valuation.yaml`` 的 ``targets``,仅保留 ``type: valuation``。"""
    with open(config_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    targets = data.get("targets") or []
    targets = [t for t in targets if isinstance(t, dict) and t.get("type") == "valuation"]
    if not targets:
        raise ValueError(f"valuation 配置无 type=valuation 标的: {config_path}")
    return targets


# ---------- 主 section:估值核心 ----------


def _fetch_valuation_items(
    targets: List[Dict[str, Any]], work_dir: Path
) -> Tuple[List[Dict[str, Any]], Dict[str, Path]]:
    """逐标的取估值指标 + 挂股债收益差,再逐 item 生成 PE 分位图。

    10Y 国债循环外取一次(``fetch_cn_10y_bond_yield`` + ``fetch_cn_10y_bond_history_with_archive_fallback``),
    失败仅 WARN,股债收益差/比值字段缺但不中止。单标 fetch 失败/空 -> 跳过该标的不中止。
    返回 ``(valuation_items, chart_paths)``;chart_paths 以 ``index_code``(回退 ``code``)为 key。
    """
    cn_10y_yield: Optional[float] = None
    cn_10y_bond_history = None
    cn_10y_bond_meta: Dict[str, Optional[str]] = {"data_source": "live", "archive_latest_date": None}
    try:
        cn_10y_yield = fetch.fetch_cn_10y_bond_yield()
        cn_10y_bond_history, cn_10y_bond_meta = fetch.fetch_cn_10y_bond_history_with_archive_fallback()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 10Y国债获取失败,股债收益差将不显示: {exc}")

    items: List[Dict[str, Any]] = []
    overlay_pe_histories: Dict[str, Any] = {}
    for target in targets:
        label = target.get("name") or target.get("code") or ""
        try:
            metrics_dict = fetch.fetch_target_index_metrics(target)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {label} 估值指标获取失败,跳过: {exc}")
            continue
        if not metrics_dict:
            print(f"[WARN] {label} 估值指标为空,跳过")
            continue
        item: Dict[str, Any] = {"name": target.get("name"), "code": target.get("code")}
        item.update(metrics_dict)
        index_code = str(item.get("index_code") or "").strip()
        overlay = None
        try:
            if not index_code:
                raise ValueError("缺少 index_code")
            if cn_10y_bond_history is None or cn_10y_bond_history.empty:
                raise ValueError("缺少可复用的 10Y 国债历史")
            price_date = estimate_overlay.latest_price_date(index_code, storage.ARCHIVE_DIR)
            if not price_date:
                raise ValueError("缺少最新收盘价日期")
            estimate_ledger.refresh_estimate_ledger(
                index_code, bond_history=cn_10y_bond_history
            )
            estimate = estimate_ledger.load_estimate_record(
                index_code,
                price_date,
                output_root=estimate_ledger.DEFAULT_OUTPUT_ROOT,
            )
            if estimate is None:
                raise ValueError(f"缺少 {price_date} 的估算记录")
            overlay = estimate_overlay.apply_from_archives(
                item,
                estimate=estimate,
                price_date=price_date,
                archive_root=storage.ARCHIVE_DIR,
                bond_history=cn_10y_bond_history,
            )
            if overlay is None:
                raise ValueError("估算覆盖缺少所需归档数据")
            item = overlay.item
            overlay_pe_histories[index_code] = overlay.pe_history
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {index_code or label} 估算覆盖失败,保留正式估值: {exc}")
        if overlay is None and cn_10y_yield is not None:
            metrics.attach_equity_bond_ratio(
                item,
                cn_10y_yield,
                data_source=cn_10y_bond_meta.get("data_source") or "live",
                archive_latest_date=cn_10y_bond_meta.get("archive_latest_date"),
            )
        if overlay is None and cn_10y_bond_history is not None and not cn_10y_bond_history.empty:
            metrics.attach_equity_bond_spread(item, cn_10y_bond_history)
        items.append(item)

    chart_paths: Dict[str, Path] = {}
    for item in items:
        code = str(item.get("index_code") or item.get("code") or "").strip()
        if not code:
            continue
        try:
            png_path = charts.generate_valuation_percentile_chart(
                item, work_dir, pe_history=overlay_pe_histories.get(code)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] {code} 估值分位图生成失败,片段不带图: {exc}")
            continue
        if png_path:
            chart_paths[code] = png_path
    return items, chart_paths


# ---------- 辅 section 收集(顺序即邮件呈现顺序)----------


def _build_extra_sections(work_dir: Path) -> Tuple[List[str], Dict[str, str]]:
    """风格轮动 -> 汇率图 -> 高股息 -> 果仁,各自失败 skip。返回 (extra_sections, inline_images)。"""
    extra_sections: List[str] = []
    inline_images: Dict[str, str] = {}

    # 1. 风格轮动(build_section 内部失败返回 None)
    style_section = style_rotation.build_section(work_dir)
    if style_section:
        extra_sections.append(style_section["html"])
        inline_images.update(style_section.get("inline_images") or {})

    # 2. 汇率图
    try:
        fx_path = charts.generate_fx_chart(work_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 汇率图生成失败,跳过: {exc}")
        fx_path = None
    if fx_path:
        extra_sections.append(render.render_fx_chart_section(fx_path))
        inline_images[render.FX_CHART_CID] = str(fx_path)

    # 3. 高股息(build_section 内部单次 get_cookie,失败已 notify_alert 并返回 None)
    dividend_section = dividend_render.build_section(work_dir)
    if dividend_section:
        extra_sections.append(dividend_section["html"])
        inline_images.update(dividend_section.get("inline_images") or {})

    # 4. 果仁行业估值(独立 GUORN_COOKIE)
    guorn_cookie = env.get("GUORN_COOKIE")
    if guorn_cookie:
        try:
            snapshot = guorn.fetch_industry_valuation(guorn_cookie)
            guorn_html = render.render_guorn_section(
                industry_rows=snapshot.industry_rows,
                latest_date=snapshot.latest_date,
                error_message=None,
            )
        except Exception as exc:  # noqa: BLE001
            alerts.notify_alert("果仁行业估值获取失败", str(exc))
            guorn_html = render.render_guorn_section(
                industry_rows=None, latest_date=None, error_message=str(exc)
            )
        if guorn_html:
            extra_sections.append(guorn_html)
    else:
        print("[INFO] 未配置 GUORN_COOKIE,跳过果仁行业估值区块")

    return extra_sections, inline_images


# ---------- 聚合 ----------


def _build_bundle(work_dir: Path) -> Dict[str, Any]:
    """聚合主+辅 section,返回 ``{html, inline_images, valuation_date}``。"""
    targets = load_valuation_config()
    now = fetch.now_in_beijing()

    valuation_items, chart_paths = _fetch_valuation_items(targets, work_dir)
    extra_sections, inline_images = _build_extra_sections(work_dir)

    html, core_inline_images = render.assemble_email_html(
        current_time=now,
        valuation_items=valuation_items,
        chart_paths=chart_paths,
        extra_sections=extra_sections,
    )
    inline_images.update(core_inline_images)

    valuation_date = ""
    for item in valuation_items:
        vd = str(item.get("index_valuation_date") or "").strip()
        if vd:
            valuation_date = vd
            break
    return {"html": html, "inline_images": inline_images, "valuation_date": valuation_date}


def _build_subject(bundle: Dict[str, Any]) -> str:
    date = bundle.get("valuation_date") or fetch.now_in_beijing().strftime("%Y-%m-%d")
    return f"市场估值日报 {date}".strip()


def _cid_to_data_uri(html: str, inline_images: Dict[str, str]) -> str:
    """把 HTML 里的 ``src="cid:xxx"`` 替换为 base64 data URI,供预览页离线显示。

    ``assemble_email_html`` 返回的已是完整 ``<!doctype html>`` 文档,无需再包外壳。
    """
    for cid, path in inline_images.items():
        ext = Path(path).suffix.lower().lstrip(".")
        mime = "image/png" if ext == "png" else ("image/jpeg" if ext in ("jpg", "jpeg") else "image/png")
        data = Path(path).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        html = html.replace(f'src="cid:{cid}"', f'src="data:{mime};base64,{b64}"')
    return html


# ---------- 入口 ----------


def run_send() -> int:
    """聚合 5 section + 发邮件。tempdir 须存活到 send_email 读图完成,故发信在 with 块内。"""
    prev = storage.load_state(STATE_NAME, default={}) or {}
    today = fetch.now_in_beijing().strftime("%Y-%m-%d")
    # 防同日重复:今日已发过则跳过(15:31 首跑后 19:11 兜底命中跳过)
    if prev.get("last_send_date") == today:
        print(f"[INFO] 今日已发信({today}),跳过")
        return 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = _build_bundle(Path(tmpdir))

            subject = _build_subject(bundle)
            ok = email.send_email(
                subject, bundle["html"], inline_images=bundle["inline_images"] or None
            )
            if ok:
                storage.save_state(STATE_NAME, {"last_send_date": today})
            return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001
        alerts.notify_alert("市场估值板块运行失败", str(exc))
        raise


def run_preview(output_path: Path = DEFAULT_PREVIEW_PATH) -> Path:
    """生成预览 HTML(图 base64 内嵌,不发信,不走静默守卫)。"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = _build_bundle(Path(tmpdir))
            html = _cid_to_data_uri(bundle["html"], bundle["inline_images"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"市场估值板块预览生成失败: {exc}")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[INFO] 预览已生成: {out}")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="市场估值板块(估值核心+高股息+果仁+风格轮动+汇率图)"
    )
    parser.add_argument("--preview", action="store_true", help="生成预览 HTML(不发信)")
    parser.add_argument("--output", default=str(DEFAULT_PREVIEW_PATH), help="预览输出路径")
    args = parser.parse_args(argv)
    if args.preview:
        run_preview(Path(args.output))
        return 0
    return run_send()


if __name__ == "__main__":
    raise SystemExit(main())
