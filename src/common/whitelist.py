"""国资白名单 Excel 解析(高股息筛选 + 转债企业性质列共用)。

移植自 jisilu_ggx/main.py L160-161 + L371-549:手写 zip+xml 解析(stdlib zipfile +
ElementTree,非 openpyxl),三个 load 函数带 lru_cache。``normalize_stock_code`` 为
本仓 canonical 实现(转债 screening、巨潮 cninfo_cache 共用同一口径)。白名单文件默认
``data/whitelist/state_owned_whitelist.xlsx``(去日期戳固定名),可用 ``STATE_OWNED_WHITELIST_XLSX``
环境变量覆盖;名单更新直接覆盖该文件并 git commit。
"""
from __future__ import annotations

import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from . import env


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WHITELIST_XLSX = _REPO_ROOT / "data" / "whitelist" / "state_owned_whitelist.xlsx"
STATE_OWNED_WHITELIST_XLSX = env.get("STATE_OWNED_WHITELIST_XLSX") or str(DEFAULT_WHITELIST_XLSX)

XLSX_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
XLSX_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def normalize_stock_code(value: Any) -> str:
    """提取数字部分并左补零至 6 位;无数字返回空串。"""
    digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)


def _xlsx_sheet_path(zf: zipfile.ZipFile, sheet_name: str | None = None) -> str:
    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_root.findall("r:Relationship", XLSX_REL_NS)
    }

    sheets = workbook_root.find("s:sheets", XLSX_NS)
    if sheets is None:
        raise RuntimeError("Excel 缺少 sheets 节点")

    sheet_nodes = sheets.findall("s:sheet", XLSX_NS)
    if not sheet_nodes:
        raise RuntimeError("Excel 没有可读取的工作表")

    target_node = sheet_nodes[0]
    if sheet_name:
        for node in sheet_nodes:
            if node.attrib.get("name") == sheet_name:
                target_node = node
                break
        else:
            raise RuntimeError(f"Excel 中不存在工作表: {sheet_name}")

    rel_id = target_node.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    target = rel_map.get(rel_id)
    if not target:
        raise RuntimeError(f"Excel 工作表关系缺失: {target_node.attrib.get('name', '')}")
    # Target 可能相对("worksheets/sheet1.xml")或绝对("/xl/worksheets/sheet1.xml"),
    # 统一归一到 zip 内 "xl/..." 路径。
    target = target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return "xl/" + target


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(cell.itertext()).strip()

    if cell_type == "s":
        index_text = cell.findtext("s:v", default="", namespaces=XLSX_NS).strip()
        if not index_text:
            return ""
        return shared_strings[int(index_text)]

    return cell.findtext("s:v", default="", namespaces=XLSX_NS).strip()


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []

    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall("s:si", XLSX_NS):
        values.append("".join(item.itertext()).strip())
    return values


@lru_cache(maxsize=8)
def load_stock_whitelist_entries_from_xlsx(
    xlsx_path: str = STATE_OWNED_WHITELIST_XLSX,
    sheet_name: str | None = None,
    code_header: str = "代码",
    name_header: str = "名称",
    extra_headers: tuple = (),
):
    """读取国资白名单 Excel。

    extra_headers: ``((header, key), ...)`` 元组,用于额外捕获指定表头列的原文,
        每行结果会增加 ``key`` 字段。必须用可哈希的元组以兼容 lru_cache。
        默认 () 时仅返回 stock_code / stock_name,行为与历史一致。
    """
    path = Path(xlsx_path)
    if not path.exists():
        raise FileNotFoundError(f"未找到国资白名单文件: {path}")

    with zipfile.ZipFile(path) as zf:
        shared_strings = _xlsx_shared_strings(zf)
        sheet_path = _xlsx_sheet_path(zf, sheet_name=sheet_name)
        root = ET.fromstring(zf.read(sheet_path))

    rows = root.find("s:sheetData", XLSX_NS)
    if rows is None:
        raise RuntimeError(f"Excel 工作表为空: {path}")

    header_map = {}
    code_column = None
    name_column = None
    extra_columns: dict[str, str] = {}  # key -> column letter
    stock_entries = []
    seen_codes: set[str] = set()

    for row_index, row in enumerate(rows.findall("s:row", XLSX_NS), 1):
        current = {}
        for cell in row.findall("s:c", XLSX_NS):
            ref = cell.attrib.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            if not match:
                continue
            current[match.group(1)] = _xlsx_cell_value(cell, shared_strings)

        if row_index == 1:
            header_map = current
            for column, header in header_map.items():
                if header == code_header:
                    code_column = column
            if code_column is None:
                raise RuntimeError(f"Excel 未找到 `{code_header}` 列: {path}")
            for column, header in header_map.items():
                if header == name_header:
                    name_column = column
                    break
            for header_text, key in extra_headers:
                for column, header in header_map.items():
                    if header == header_text:
                        extra_columns[key] = column
                        break
            continue

        raw_code = current.get(code_column, "")
        stock_code = normalize_stock_code(raw_code)
        if not stock_code or stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        entry = {
            "stock_code": stock_code,
            "stock_name": str(current.get(name_column, "")).strip() if name_column else "",
        }
        for key, column in extra_columns.items():
            entry[key] = str(current.get(column, "")).strip() if column else ""
        stock_entries.append(entry)

    if not stock_entries:
        raise RuntimeError(f"Excel 白名单为空: {path}")
    return tuple(stock_entries)


@lru_cache(maxsize=8)
def load_stock_code_whitelist_from_xlsx(
    xlsx_path: str = STATE_OWNED_WHITELIST_XLSX, sheet_name: str | None = None, code_header: str = "代码"
) -> frozenset[str]:
    entries = load_stock_whitelist_entries_from_xlsx(
        xlsx_path=xlsx_path,
        sheet_name=sheet_name,
        code_header=code_header,
    )
    return frozenset(entry["stock_code"] for entry in entries)


@lru_cache(maxsize=8)
def load_stock_enterprise_nature_map_from_xlsx(
    xlsx_path: str = STATE_OWNED_WHITELIST_XLSX,
    sheet_name: str | None = None,
    code_header: str = "代码",
    nature_header: str = "企业性质",
) -> dict[str, str]:
    """返回 ``{stock_code: 企业性质原文}`` 映射,供可转债日报展示「企业性质」列。

    取值来自白名单 Excel 的「企业性质」列(如 中央国有企业 / 地方国有企业)。
    与高股息日报二次筛选共用同一份白名单文件,保证口径一致。
    """
    entries = load_stock_whitelist_entries_from_xlsx(
        xlsx_path=xlsx_path,
        sheet_name=sheet_name,
        code_header=code_header,
        extra_headers=((nature_header, "enterprise_nature"),),
    )
    return {entry["stock_code"]: entry["enterprise_nature"] for entry in entries}
