"""只读导入 Excel 和 Word。原文件不改写，结果写入知识库表。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from app.database import Database
from app.knowledge.collate import build_node
from app.knowledge.museum import physical_notes, scope_for, visitor_text
from app.knowledge.permissions import direct_self_statement
from app.knowledge.store import clear_corpus, ensure_schema, put_meta
from app.knowledge.textutil import clean

CHAPTER_SHEET = "我走过的道路"
THEME_SHEET = "主题知识库"
MUSEUM_SHEETS = ("手稿", "书信")


class CorpusImportError(Exception):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path, sheet_name: str) -> tuple[list[str], list[dict[str, str]], list[str]]:
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise CorpusImportError(f"无法读取 {path.name}。") from exc
    if sheet_name not in workbook.sheetnames:
        names = "、".join(workbook.sheetnames)
        workbook.close()
        raise CorpusImportError(f"{path.name} 里没有工作表“{sheet_name}”。现有工作表：{names}")
    sheet = workbook[sheet_name]
    iterator = sheet.iter_rows(values_only=True)
    try:
        header_row = next(iterator)
    except StopIteration as exc:
        workbook.close()
        raise CorpusImportError(f"{path.name} 的“{sheet_name}”是空的。") from exc
    headers = [clean(cell) for cell in header_row]
    records: list[dict[str, str]] = []
    issues: list[str] = []
    seen: dict[str, int] = {}
    key = "条目ID" if "条目ID" in headers else "馆藏条码"
    if key not in headers:
        workbook.close()
        raise CorpusImportError(f"{path.name} 的“{sheet_name}”缺少“{key}”列。")
    for offset, row in enumerate(iterator, start=2):
        if row is None or not any(cell not in (None, "") for cell in row):
            continue
        item = {headers[index]: clean(row[index]) if index < len(row) else "" for index in range(len(headers))}
        identity = item.get(key, "")
        if not identity:
            issues.append(f"{sheet_name} 第 {offset} 行没有{key}，已跳过。")
            continue
        if identity in seen:
            issues.append(f"{sheet_name} 的 {identity} 重复（第 {seen[identity]} 行和第 {offset} 行）。保留先出现的一行。")
            continue
        seen[identity] = offset
        item["_row"] = str(offset)
        records.append(item)
    workbook.close()
    return headers, records, issues


def _read_docx(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except Exception as exc:
        raise CorpusImportError(f"无法读取 {path.name}。") from exc
    root = ET.fromstring(xml)
    paragraphs = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        texts = [node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return paragraphs


def _basics(paragraphs: list[str]) -> list[dict]:
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


def _searchable_node(entry_id: str, chapter: dict, theme: dict, node: dict) -> str:
    if node["excluded"]:
        return ""
    parts = [
        entry_id,
        chapter.get("来源书名", ""),
        chapter.get("来源章节或日期条目", ""),
        chapter.get("事件时间", "") if not node["chronology_unresolved"] else "",
        chapter.get("地点", ""),
        chapter.get("人物及关系", ""),
        theme.get("主题分类", ""),
        theme.get("细分主题", ""),
        theme.get("可回答问题与检索词", ""),
        chapter.get("检索标签", ""),
        chapter.get("可回答问题与要点", ""),
        node["title"]["effective"],
        node["summary"]["effective"],
        node["quote"]["effective"],
    ]
    return "\n".join(part for part in parts if part)


def import_corpus(db: Database, directory: Path) -> dict[str, Any]:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise CorpusImportError(f"找不到资料目录：{directory}")
    chapter_path = _find(directory, "统一章节总表", ".xlsx")
    theme_path = _find(directory, "主题知识库", ".xlsx")
    museum_path = _find(directory, "馆藏手稿与书信", ".xlsx")
    basics_path = _find(directory, "基础知识", ".docx")
    ensure_schema(db)
    clear_corpus(db)

    _, chapter_rows, chapter_issues = _rows(chapter_path, CHAPTER_SHEET)
    _, theme_rows, theme_issues = _rows(theme_path, THEME_SHEET)
    chapter_by_id = {row["条目ID"]: row for row in chapter_rows}
    theme_by_id = {row["条目ID"]: row for row in theme_rows}
    chapter_ids = set(chapter_by_id)
    theme_ids = set(theme_by_id)
    missing_theme = sorted(chapter_ids - theme_ids)
    missing_chapter = sorted(theme_ids - chapter_ids)
    issues = chapter_issues + theme_issues
    issues.extend(f"只有章节表有 {item}" for item in missing_theme[:50])
    issues.extend(f"只有主题表有 {item}" for item in missing_chapter[:50])

    node_rows = []
    excluded = 0
    unresolved_fields = 0
    for entry_id in sorted(chapter_ids & theme_ids):
        chapter = chapter_by_id[entry_id]
        theme = theme_by_id[entry_id]
        node = build_node(chapter, theme)
        if node["excluded"]:
            excluded += 1
        if any(node[name]["state"] == "unresolved" for name in ("title", "summary", "quote")):
            unresolved_fields += 1
        nature = clean(chapter.get("信息性质")) or _section(node["limit_theme"], "信息性质")
        speaker = clean(chapter.get("原始发言者")) or _section(node["limit_theme"], "原始发言者")
        provider = clean(chapter.get("信息提供者")) or _section(node["limit_theme"], "信息提供者")
        payload = json.dumps(
            {
                "title": node["title"],
                "summary": node["summary"],
                "quote": node["quote"],
            },
            ensure_ascii=False,
        )
        node_rows.append(
            (
                entry_id,
                chapter.get("来源书名", ""),
                chapter.get("版本与册次", ""),
                chapter.get("来源章节或日期条目", ""),
                chapter.get("页码与段落定位", ""),
                chapter.get("事件时间", ""),
                chapter.get("地点", ""),
                chapter.get("人物及关系", ""),
                nature,
                provider,
                speaker,
                chapter.get("书内所引出处", ""),
                theme.get("主题分类", ""),
                theme.get("可回答问题与检索词", "") or chapter.get("可回答问题与要点", ""),
                theme.get("细分主题", "") or chapter.get("检索标签", ""),
                json.dumps(node["title"], ensure_ascii=False),
                json.dumps(node["summary"], ensure_ascii=False),
                json.dumps(node["quote"], ensure_ascii=False),
                node["limit_chapter"],
                node["limit_theme"],
                int(node["chronology_unresolved"]),
                int(node["style_ok"]),
                int(node["excluded"]),
                int(not node["excluded"]),
                int(direct_self_statement(nature, speaker) and not node["excluded"]),
                _searchable_node(entry_id, chapter, theme, node),
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )
        )

    museum_rows = []
    museum_counts: dict[str, int] = {}
    for sheet_name in MUSEUM_SHEETS:
        _, records, sheet_issues = _rows(museum_path, sheet_name)
        issues.extend(sheet_issues)
        museum_counts[sheet_name] = len(records)
        seen_barcodes: set[str] = set()
        for record in records:
            barcode = record["馆藏条码"]
            if barcode in seen_barcodes:
                issues.append(f"{sheet_name} 条码 {barcode} 在本表重复，保留先出现的一行。")
                continue
            seen_barcodes.add(barcode)
            scope = scope_for(record.get("网络资料状态", ""), record.get("与馆藏的对应程度", ""))
            speaker = record.get("短引说话人", "")
            if speaker in {"—", "-", "无", "待定"}:
                speaker = ""
            item = {
                "title": record.get("馆藏原题名", ""),
                "recorded_date": record.get("原著录日期", ""),
                "sender": record.get("作者或寄信人", ""),
                "recipient": record.get("收信人", ""),
                "found_text": record.get("找到的具体内容", ""),
                "short_quote": record.get("可用观点或原文短引", ""),
                "quote_speaker": speaker,
                "scope": scope,
                "physical": physical_notes(record.get("馆藏原始信息", "")),
            }
            museum_rows.append(
                (
                    f"{sheet_name}|{barcode}",
                    sheet_name,
                    barcode,
                    item["title"],
                    item["recorded_date"],
                    item["sender"],
                    item["recipient"],
                    record.get("网络资料状态", ""),
                    item["found_text"],
                    item["short_quote"],
                    item["quote_speaker"],
                    record.get("与馆藏的对应程度", ""),
                    record.get("来源机构与文章", ""),
                    record.get("来源网址", ""),
                    record.get("需要核对的事项", ""),
                    record.get("馆藏原始信息", ""),
                    scope,
                    visitor_text(item),
                    hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                )
            )

    basics_rows = []
    for unit in _basics(_read_docx(basics_path)):
        body = "\n".join(unit["lines"]).strip()
        searchable = "" if unit["is_source_list"] else "\n".join([unit["unit_id"], unit["title"], body])
        basics_rows.append((unit["unit_id"], unit["title"], body, unit["is_source_list"], searchable))

    db.executemany(
        """
        INSERT INTO corpus_nodes (
            entry_id, source_book, volume, chapter_label, page, event_time, place, people,
            info_nature, provider, speaker, cited_from, themes, questions, tags,
            title_json, summary_json, quote_json, limit_chapter, limit_theme,
            chronology_unresolved, style_ok, rag_excluded, visitor_ok, direct_self,
            searchable, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        node_rows,
    )
    db.executemany(
        """
        INSERT INTO museum_items (
            item_key, sheet, barcode, title, recorded_date, sender, recipient, net_status,
            found_text, short_quote, quote_speaker, correspondence, source_org, source_url,
            checks, catalog_raw, scope, visitor_text, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        museum_rows,
    )
    db.executemany(
        "INSERT INTO basics_units (unit_id, title, body, is_source_list, searchable) VALUES (?, ?, ?, ?, ?)",
        basics_rows,
    )
    sources = {
        "chapter": {"name": chapter_path.name, "sha256": file_sha256(chapter_path), "rows": len(chapter_rows)},
        "theme": {"name": theme_path.name, "sha256": file_sha256(theme_path), "rows": len(theme_rows)},
        "museum": {"name": museum_path.name, "sha256": file_sha256(museum_path), "sheets": museum_counts},
        "basics": {"name": basics_path.name, "sha256": file_sha256(basics_path), "units": len(basics_rows)},
    }
    put_meta(db, "sources", sources)
    put_meta(db, "issues", issues)
    report = {
        "chapter_rows": len(chapter_rows),
        "theme_rows": len(theme_rows),
        "paired": len(node_rows),
        "only_chapter": len(missing_theme),
        "only_theme": len(missing_chapter),
        "excluded": excluded,
        "nodes_with_unresolved_fields": unresolved_fields,
        "manuscripts": museum_counts.get("手稿", 0),
        "letters": museum_counts.get("书信", 0),
        "basics_units": len(basics_rows),
        "issues": issues[:80],
        "issue_count": len(issues),
        "sources": sources,
    }
    put_meta(db, "report", report)
    return report


def _find(directory: Path, stem_part: str, suffix: str) -> Path:
    matches = sorted(path for path in directory.iterdir() if path.is_file() and stem_part in path.name and path.suffix.lower() == suffix)
    if not matches:
        raise CorpusImportError(f"在 {directory} 里没有找到名称包含“{stem_part}”的 {suffix} 文件。")
    return matches[0]


def _section(text: str, label: str) -> str:
    marker = f"【{label}】"
    if marker not in text:
        return ""
    rest = text.split(marker, 1)[1]
    line = rest.split("【", 1)[0].strip()
    return line.splitlines()[0].strip() if line else ""
