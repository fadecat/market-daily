"""商品极值板块测试(不依赖网络/PyMuPDF)。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.commodity import cctda  # noqa: E402

LIST_HTML = """
<html><body>
<div class="news_list"><ul>
  <li><a href="/news/123.html">CCTD 环渤海动力煤日报 2026-08-06</a><span class="rt">2026-08-06</span></li>
  <li><a href="/news/122.html">旧日报</a></li>
</ul></div>
</body></html>
"""

DETAIL_HTML_IMAGES = """
<html><body>
<div class="news_nr">
  <div class="title"><h1>日报正文 2026-08-06 09:00:00</h1></div>
  <div id="article-content">
    <img src="/img/p1.png"/>
    <img src="/img/p2.png"/>
  </div>
</div>
</body></html>
"""

DETAIL_HTML_PDF = """
<html><body>
<div class="news_nr">
  <div class="title"><h1>日报正文 2026-08-06</h1></div>
  <div id="article-content"><a href="/files/report.pdf">下载 PDF</a></div>
</div>
</body></html>
"""


def test_parse_latest_article_from_list():
    latest = cctda.parse_latest_article_from_list(LIST_HTML, "https://www.cctda.org.cn/")
    assert latest["article_title"] == "CCTD 环渤海动力煤日报 2026-08-06"
    assert latest["article_url"] == "https://www.cctda.org.cn/news/123.html"
    assert latest["list_date"] == "2026-08-06"


def test_parse_latest_article_missing():
    with pytest.raises(RuntimeError):
        cctda.parse_latest_article_from_list("<html></html>", "https://x/")


def test_parse_detail_content_images():
    detail = cctda.parse_detail_content(DETAIL_HTML_IMAGES, "https://www.cctda.org.cn/")
    assert detail["content_type"] == "images"
    assert detail["image_urls"] == [
        "https://www.cctda.org.cn/img/p1.png",
        "https://www.cctda.org.cn/img/p2.png",
    ]
    assert detail["published_at"] == "2026-08-06 09:00:00"


def test_parse_detail_content_pdf():
    detail = cctda.parse_detail_content(DETAIL_HTML_PDF, "https://www.cctda.org.cn/")
    assert detail["content_type"] == "pdf"
    assert detail["pdf_url"] == "https://www.cctda.org.cn/files/report.pdf"


def test_should_skip_article():
    latest = {"article_url": "https://x/123.html", "article_title": "t"}
    assert cctda.should_skip_article(latest, {"article_url": "https://x/123.html"}) is True
    assert cctda.should_skip_article(latest, {"article_url": "https://x/999.html"}) is False
    assert cctda.should_skip_article(latest, {}) is False


def test_build_email_html_uses_cid():
    html = cctda.build_email_html("标题", "https://x/123", "2026-08-06 09:00:00", 3)
    assert "cid:report_page_1" in html and "cid:report_page_3" in html
    assert "cid:report_page_4" not in html
    assert "标题" in html


def test_build_preview_html_embeds_base64(tmp_path: Path):
    img = tmp_path / "page_01.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    html = cctda.build_preview_html("标题", "https://x/123", "now", [img])
    assert "data:image/png;base64," in html
    assert "https://x/123" in html


def test_compute_content_hash_stable():
    a = cctda.compute_content_hash(["u1", "u2"])
    b = cctda.compute_content_hash(["u1", "u2"])
    assert a == b and a.startswith("sha256:")
