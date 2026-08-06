"""商品极值板块:CCTDA 中国煤炭运销协会日报抓取与邮件转发。

数据流:列表页取最新日报 -> 详情页解析(图片或 PDF) -> PDF 转 PNG ->
以 cid 内联图片发邮件 -> 按 article_url 去重写状态。

移植自旧仓库 ``monitor_cctda_coal_daily.py``,接 common 层:
- 网络抓取用 ``common.alerts.run_with_retry`` 包裹
- 状态用 ``common.storage``(data/state/cctda_coal_daily.json)
- 邮件用 ``common.email``

依赖:beautifulsoup4、PyMuPDF(fitz,PDF 转图,懒加载)。
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.common import alerts

CCTDA_LIST_URL = "https://www.cctda.org.cn/index.php?m=content&c=index&a=lists&catid=75"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
    )
}

STATE_NAME = "cctda_coal_daily"


def now_in_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


# ---------- 抓取与解析 ----------

def _fetch_once(url: str, *, timeout: int = 20) -> str:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding or "utf-8"
    return response.text


def fetch_html(url: str, *, timeout: int = 20) -> str:
    """抓取页面 HTML,带重试。"""
    return alerts.run_with_retry(f"cctda fetch {url}", lambda: _fetch_once(url, timeout=timeout))


def parse_latest_article_from_list(html: str, base_url: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    first_row = soup.select_one(".news_list ul li")
    if first_row is None:
        raise RuntimeError("未找到最新日报列表项")

    link = first_row.select_one("a[href], el-link[href]")
    if link is None:
        raise RuntimeError("未找到最新日报链接")

    article_title = link.get_text(" ", strip=True)
    article_url = urljoin(base_url, link.get("href", "").strip())
    list_date_node = first_row.select_one(".rt")
    list_date = list_date_node.get_text(" ", strip=True) if list_date_node else ""
    if not article_title or not article_url:
        raise RuntimeError("最新日报标题或链接为空")

    return {"article_title": article_title, "article_url": article_url, "list_date": list_date}


def _extract_published_at(title_node) -> str:
    title_text = title_node.get_text(" ", strip=True)
    match = re.search(r"(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", title_text)
    return match.group(1) if match else ""


def parse_detail_content(html: str, base_url: str) -> Dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one(".news_nr .title h1") or soup.select_one(".title h1")
    article_node = soup.select_one("#article-content")
    if title_node is None or article_node is None:
        raise RuntimeError("详情页缺少标题或正文")

    article_title = title_node.get_text("\n", strip=True).split("\n", 1)[0].strip()
    published_at = _extract_published_at(title_node)

    image_urls = [
        urljoin(base_url, node.get("src", "").strip())
        for node in article_node.select("img[src]")
        if node.get("src", "").strip()
    ]
    if image_urls:
        return {
            "article_title": article_title,
            "published_at": published_at,
            "content_type": "images",
            "image_urls": image_urls,
        }

    for link in article_node.select("a[href]"):
        href = link.get("href", "").strip()
        if href.lower().endswith(".pdf"):
            return {
                "article_title": article_title,
                "published_at": published_at,
                "content_type": "pdf",
                "pdf_url": urljoin(base_url, href),
            }

    raise RuntimeError("详情页既没有图片也没有 PDF")


def should_skip_article(latest: Dict[str, str], saved_state: Dict[str, object]) -> bool:
    """最新日报的 url 与已发送状态一致则跳过。"""
    return str(saved_state.get("article_url", "")).strip() == latest["article_url"].strip()


# ---------- 内容物化(下载/PDF 转图) ----------

def compute_content_hash(values: List[str]) -> str:
    return "sha256:" + hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def compute_file_hash(paths: List[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _download_once(url: str, timeout: int) -> bytes:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.content


def download_report_images(image_urls: List[str], output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []
    for index, image_url in enumerate(image_urls, start=1):
        content = alerts.run_with_retry(
            f"cctda image {index}", lambda u=image_url: _download_once(u, 30)
        )
        output_path = output_dir / f"page_{index:02d}.png"
        output_path.write_bytes(content)
        saved.append(output_path)
    return saved


def download_pdf(pdf_url: str, output_path: Path) -> Path:
    content = alerts.run_with_retry("cctda pdf", lambda: _download_once(pdf_url, 60))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    return output_path


def render_pdf_to_pngs(pdf_path: Path, output_dir: Path) -> List[Path]:
    import fitz  # PyMuPDF,懒加载

    output_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(pdf_path)
    saved: List[Path] = []
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            output_path = output_dir / f"page_{index + 1:02d}.png"
            pixmap.save(output_path)
            saved.append(output_path)
    finally:
        document.close()
    return saved


def materialize_report_pages(detail: Dict[str, object], workspace_dir: Path) -> Tuple[List[Path], str]:
    """把详情页内容落成 PNG 列表,返回 (图片路径, 内容 hash)。"""
    content_type = str(detail["content_type"])
    if content_type == "images":
        image_urls = [str(url) for url in detail["image_urls"]]
        image_paths = download_report_images(image_urls, workspace_dir / "images")
        return image_paths, compute_content_hash(image_urls)
    if content_type == "pdf":
        pdf_path = download_pdf(str(detail["pdf_url"]), workspace_dir / "report.pdf")
        image_paths = render_pdf_to_pngs(pdf_path, workspace_dir / "images")
        return image_paths, compute_file_hash(image_paths)
    raise RuntimeError(f"不支持的内容类型: {content_type}")


# ---------- 邮件正文(邮件与预览共用) ----------

def build_report_html_body(
    subject: str,
    article_url: str,
    fetched_at: str,
    image_sources: List[str],
) -> str:
    """构建邮件正文。image_sources 既可以是 cid:引用(发信),也可以是 data:URI(预览)。"""
    image_blocks: List[str] = []
    for index, image_src in enumerate(image_sources, start=1):
        image_blocks.append(
            f'<div style="margin:0 0 18px 0">'
            f'<img src="{escape(image_src)}" alt="report-page-{index}" '
            f'style="display:block;width:100%;max-width:960px;height:auto;border:0">'
            f"</div>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"></head><body "
        "style=\"margin:0;padding:24px;background:#f5f5f7;font-family:Arial,'Microsoft YaHei',sans-serif\">"
        "<div style=\"max-width:980px;margin:0 auto;background:#ffffff;padding:24px;border-radius:12px\">"
        f"<h1 style=\"margin:0 0 8px 0;font-size:24px\">{escape(subject)}</h1>"
        f"<p style=\"margin:0 0 6px 0\">详情链接: <a href=\"{escape(article_url)}\">{escape(article_url)}</a></p>"
        f"<p style=\"margin:0 0 18px 0\">抓取时间: {escape(fetched_at)}</p>"
        + "".join(image_blocks)
        + "</div></body></html>"
    )


def build_email_html(subject: str, article_url: str, fetched_at: str, image_count: int) -> str:
    """发信用:图片用 cid:report_page_N 引用,实际图片由 common.email 内联。"""
    return build_report_html_body(
        subject=subject,
        article_url=article_url,
        fetched_at=fetched_at,
        image_sources=[f"cid:report_page_{index}" for index in range(1, image_count + 1)],
    )


def build_preview_html(subject: str, article_url: str, fetched_at: str, image_paths: List[Path]) -> str:
    """预览用:图片转 base64 data URI 内嵌,单文件可浏览器直开。"""
    import base64

    image_sources = [
        f"data:image/png;base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"
        for p in image_paths
    ]
    return build_report_html_body(subject, article_url, fetched_at, image_sources)
