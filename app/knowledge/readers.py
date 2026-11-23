"""只读 Excel 和 Word。这里不判断能不能给观众使用，也不写数据库。"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from app.knowledge.textutil import clean

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class SourceReadError(Exception):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sheet_headers(path: Path) -> dict[str, list[str]]:
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise SourceReadError(f"无法读取 {path.name}。") from exc
    found: dict[str, list[str]] = {}
    try:
        for name in workbook.sheetnames:
            sheet = workbook[name]
            row = next(sheet.iter_rows(max_row=1, values_only=True), None)
            found[name] = [clean(cell) for cell in row] if row else []
    finally:
        workbook.close()
    return found


def read_table(path: Path, sheet_name: str | None, id_column: str) -> tuple[str, list[dict[str, str]], list[str]]:
    headers_by_sheet = sheet_headers(path)
    if sheet_name is None:
        matches = [name for name, headers in headers_by_sheet.items() if id_column in headers]
        if len(matches) != 1:
            names = "、".join(headers_by_sheet) or "（没有工作表）"
            raise SourceReadError(f"{path.name} 里无法确定哪一张表含有“{id_column}”。现有工作表：{names}")
        sheet_name = matches[0]
    if sheet_name not in headers_by_sheet:
        names = "、".join(headers_by_sheet) or "（没有工作表）"
        raise SourceReadError(f"{path.name} 里没有工作表“{sheet_name}”。现有工作表：{names}")
    if id_column not in headers_by_sheet[sheet_name]:
        raise SourceReadError(f"{path.name} 的“{sheet_name}”缺少“{id_column}”列。")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise SourceReadError(f"无法读取 {path.name}。") from exc
    sheet = workbook[sheet_name]
    iterator = sheet.iter_rows(values_only=True)
    try:
        header_row = next(iterator)
    except StopIteration as exc:
        workbook.close()
        raise SourceReadError(f"{path.name} 的“{sheet_name}”是空的。") from exc
    headers = [clean(cell) for cell in header_row]
    records: list[dict[str, str]] = []
    issues: list[str] = []
    seen: dict[str, int] = {}
    for offset, row in enumerate(iterator, start=2):
        if row is None or not any(cell not in (None, "") for cell in row):
            continue
        item = {headers[index]: clean(row[index]) if index < len(row) else "" for index in range(len(headers))}
        identity = item.get(id_column, "")
        if not identity:
            issues.append(f"{sheet_name} 第 {offset} 行没有{id_column}，已跳过。")
            continue
        if identity in seen:
            issues.append(f"{sheet_name} 的 {identity} 重复（第 {seen[identity]} 行和第 {offset} 行）。保留先出现的一行。")
            continue
        seen[identity] = offset
        item["_row"] = str(offset)
        records.append(item)
    workbook.close()
    return sheet_name, records, issues


def read_docx_paragraphs(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except Exception as exc:
        raise SourceReadError(f"无法读取 {path.name}。") from exc
    root = ET.fromstring(xml)
    paragraphs = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        texts = [node.text or "" for node in paragraph.iter(f"{WORD_NS}t")]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return paragraphs


def basics_from_paragraphs(paragraphs: list[str]) -> list[dict]:
    units: list[dict] = []
    current: dict | None = None
    source_mode = False
    for line in paragraphs:
        if line.startswith("资料来源"):
            source_mode = True
            if current:
                units.append(current)
            current = {"unit_id": "SOURCES", "title": "资料来源", "lines": [], "is_source_list": 1}
            continue
        matched = None if source_mode else _unit_heading(line)
        if matched:
            if current:
                units.append(current)
            current = {"unit_id": matched[0], "title": matched[1], "lines": [], "is_source_list": 0}
            continue
        if current is not None:
            current["lines"].append(line)
    if current:
        units.append(current)
    return units


def _unit_heading(line: str) -> tuple[str, str] | None:
    if len(line) < 4 or not line.startswith("B"):
        return None
    code = line[:3]
    if not (code[1:].isdigit() and len(code) == 3):
        return None
    title = line[3:].strip()
    return (code, title) if title else None
