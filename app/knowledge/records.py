"""把已经读出的表格行整理成内部知识记录。归属、校勘和能否给观众使用都在这里决定。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.knowledge.catalog import Material
from app.knowledge.collate import build_node
from app.knowledge.museum import physical_notes, scope_for, visitor_text
from app.knowledge.permissions import direct_self_statement
from app.knowledge.readers import SourceReadError, basics_from_paragraphs, file_sha256, read_docx_paragraphs, read_table
from app.knowledge.textutil import clean


class RecordError(Exception):
    pass


def compile_material(material: Material) -> dict[str, Any]:
    issues: list[str] = []
    nodes: list[tuple] = []
    museum: list[tuple] = []
    basics: list[tuple] = []
    stats: dict[str, Any] = {"source_key": material.source_key, "label": material.label}
    try:
        if material.chapter or material.theme:
            nodes, pair_stats, pair_issues = _paired(material)
            issues.extend(pair_issues)
            stats.update(pair_stats)
        if material.museum:
            museum, museum_stats, museum_issues = _museum(material)
            issues.extend(museum_issues)
            stats.update(museum_stats)
        if material.basics:
            basics, basics_stats = _basics(material)
            stats.update(basics_stats)
    except SourceReadError as exc:
        raise RecordError(str(exc)) from exc
    if not nodes and not museum and not basics:
        raise RecordError(f"{material.label} 没有读到可保存的记录。")
    return {
        "source_key": material.source_key,
        "label": material.label,
        "nodes": nodes,
        "museum": museum,
        "basics": basics,
        "issues": issues,
        "stats": stats,
        "files": [_file_info(path) for path in material.files()],
    }


def _paired(material: Material) -> tuple[list[tuple], dict[str, Any], list[str]]:
    if not material.chapter or not material.theme:
        raise RecordError(f"{material.label} 需要同时有章节表和主题表，才能按条目 ID 对应。")
    _chapter_sheet, chapter_rows, issues = read_table(material.chapter, material.chapter_sheet, "条目ID")
    _theme_sheet, theme_rows, theme_issues = read_table(material.theme, material.theme_sheet, "条目ID")
    issues.extend(theme_issues)
    chapter_by_id = {row["条目ID"]: row for row in chapter_rows}
    theme_by_id = {row["条目ID"]: row for row in theme_rows}
    missing_theme = sorted(set(chapter_by_id) - set(theme_by_id))
    missing_chapter = sorted(set(theme_by_id) - set(chapter_by_id))
    issues.extend(f"只有章节表有 {item}" for item in missing_theme[:50])
    issues.extend(f"只有主题表有 {item}" for item in missing_chapter[:50])
    rows = []
    excluded = 0
    unresolved_fields = 0
    for entry_id in sorted(set(chapter_by_id) & set(theme_by_id)):
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
        payload = json.dumps({"title": node["title"], "summary": node["summary"], "quote": node["quote"]}, ensure_ascii=False)
        rows.append(
            (
                f"{material.source_key}:{entry_id}",
                material.source_key,
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
    stats = {
        "chapter_rows": len(chapter_rows),
        "theme_rows": len(theme_rows),
        "paired": len(rows),
        "only_chapter": len(missing_theme),
        "only_theme": len(missing_chapter),
        "excluded": excluded,
        "nodes_with_unresolved_fields": unresolved_fields,
    }
    return rows, stats, issues


def _museum(material: Material) -> tuple[list[tuple], dict[str, Any], list[str]]:
    assert material.museum is not None
    sheets = material.museum_sheets or None
    if not sheets:
        from app.knowledge.readers import sheet_headers

        sheets = [name for name, headers in sheet_headers(material.museum).items() if "馆藏条码" in headers]
    issues: list[str] = []
    rows = []
    counts: dict[str, int] = {}
    for sheet_name in sheets:
        _used, records, sheet_issues = read_table(material.museum, sheet_name, "馆藏条码")
        issues.extend(sheet_issues)
        counts[sheet_name] = len(records)
        seen: set[str] = set()
        for record in records:
            barcode = record["馆藏条码"]
            if barcode in seen:
                issues.append(f"{sheet_name} 条码 {barcode} 在本表重复，保留先出现的一行。")
                continue
            seen.add(barcode)
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
            rows.append(
                (
                    f"{material.source_key}:{sheet_name}|{barcode}",
                    material.source_key,
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
    return rows, {"museum_sheets": counts, "museum_items": len(rows)}, issues


def _basics(material: Material) -> tuple[list[tuple], dict[str, Any]]:
    assert material.basics is not None
    units = basics_from_paragraphs(read_docx_paragraphs(material.basics))
    if not units:
        raise RecordError(f"{material.basics.name} 里没有读到 B01 这样的基础知识条目。")
    rows = []
    for unit in units:
        body = "\n".join(unit["lines"]).strip()
        searchable = "" if unit["is_source_list"] else "\n".join([unit["unit_id"], unit["title"], body])
        rows.append(
            (
                f"{material.source_key}:{unit['unit_id']}",
                material.source_key,
                unit["unit_id"],
                unit["title"],
                body,
                unit["is_source_list"],
                searchable,
            )
        )
    return rows, {"basics_units": len(rows)}


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


def _section(text: str, label: str) -> str:
    marker = f"【{label}】"
    if marker not in text:
        return ""
    rest = text.split(marker, 1)[1]
    line = rest.split("【", 1)[0].strip()
    return line.splitlines()[0].strip() if line else ""


def _file_info(path) -> dict[str, str]:
    return {"name": path.name, "sha256": file_sha256(path)}
