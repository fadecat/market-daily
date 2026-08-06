"""common/whitelist 测试(fixture xlsx 验证 stdlib 解析 + 真实白名单 sanity)。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common import whitelist  # noqa: E402


# ── normalize_stock_code ──────────────────────────────────────────────────────
def test_normalize_stock_code():
    assert whitelist.normalize_stock_code("  600001.SH ") == "600001"
    assert whitelist.normalize_stock_code("1") == "000001"
    assert whitelist.normalize_stock_code("abc") == ""
    assert whitelist.normalize_stock_code(None) == ""


# ── fixture xlsx(openpyxl 写,stdlib 解析读)──────────────────────────────────
@pytest.fixture()
def fixture_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["代码", "名称", "企业性质"])
    ws.append(["600001", "测试股", "中央国有企业"])
    ws.append(["000001", "示例银行", "地方国有企业"])
    ws.append(["600001", "重复代码应去重", "中央国有企业"])  # 重复 code 去重
    path = tmp_path / "whitelist.xlsx"
    wb.save(path)
    return str(path)


def test_load_entries(fixture_xlsx):
    entries = whitelist.load_stock_whitelist_entries_from_xlsx(fixture_xlsx)
    assert len(entries) == 2  # 重复 600001 去重
    codes = {e["stock_code"] for e in entries}
    assert codes == {"600001", "000001"}
    assert entries[0]["stock_name"]  # 名称非空


def test_load_entries_with_extra_header(fixture_xlsx):
    entries = whitelist.load_stock_whitelist_entries_from_xlsx(
        fixture_xlsx, extra_headers=(("企业性质", "enterprise_nature"),)
    )
    nature_map = {e["stock_code"]: e["enterprise_nature"] for e in entries}
    assert nature_map["600001"] == "中央国有企业"
    assert nature_map["000001"] == "地方国有企业"


def test_load_code_whitelist(fixture_xlsx):
    codes = whitelist.load_stock_code_whitelist_from_xlsx(fixture_xlsx)
    assert isinstance(codes, frozenset)
    assert "600001" in codes and "000001" in codes


def test_load_enterprise_nature_map(fixture_xlsx):
    nature_map = whitelist.load_stock_enterprise_nature_map_from_xlsx(fixture_xlsx)
    assert nature_map["600001"] == "中央国有企业"
    assert nature_map["000001"] == "地方国有企业"


def test_load_entries_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        whitelist.load_stock_whitelist_entries_from_xlsx(str(tmp_path / "nope.xlsx"))


def test_load_entries_missing_code_column(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["没有代码列", "名称"])
    wb.active.append(["600001", "x"])
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(RuntimeError):
        whitelist.load_stock_whitelist_entries_from_xlsx(str(path))


# ── 真实白名单 sanity(文件存在则验证可加载)──────────────────────────────────
def test_real_whitelist_loads():
    path = whitelist.DEFAULT_WHITELIST_XLSX
    if not path.exists():
        pytest.skip("真实白名单文件不存在")
    entries = whitelist.load_stock_whitelist_entries_from_xlsx(str(path))
    assert len(entries) > 0
    assert all(len(e["stock_code"]) == 6 for e in entries)
