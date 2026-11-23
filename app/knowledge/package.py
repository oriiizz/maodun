"""交付用的知识包。只含知识记录、来源、权限和版本，不含聊天、密钥和音频。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import Database
from app.knowledge.apply import apply_materials, knowledge_counts
from app.knowledge.store import ensure_schema, meta

FORMAT = "maodun-knowledge-pack"
VERSION = 1

NODE_FIELDS = (
    "node_key", "source_key", "entry_id", "source_book", "volume", "chapter_label", "page", "event_time", "place", "people",
    "info_nature", "provider", "speaker", "cited_from", "themes", "questions", "tags",
    "title_json", "summary_json", "quote_json", "limit_chapter", "limit_theme",
    "chronology_unresolved", "style_ok", "rag_excluded", "visitor_ok", "direct_self", "searchable", "content_hash",
)
MUSEUM_FIELDS = (
    "item_key", "source_key", "sheet", "barcode", "title", "recorded_date", "sender", "recipient", "net_status",
    "found_text", "short_quote", "quote_speaker", "correspondence", "source_org", "source_url",
    "checks", "catalog_raw", "scope", "visitor_text", "content_hash",
)
BASICS_FIELDS = ("unit_key", "source_key", "unit_id", "title", "body", "is_source_list", "searchable")


class PackError(Exception):
    pass


def export_pack(db: Database, directory: Path) -> dict[str, Any]:
    ensure_schema(db)
    directory.mkdir(parents=True, exist_ok=True)
    counts = {
        "nodes": _write_jsonl(directory / "nodes.jsonl", db, "corpus_nodes", NODE_FIELDS),
        "museum": _write_jsonl(directory / "museum.jsonl", db, "museum_items", MUSEUM_FIELDS),
        "basics": _write_jsonl(directory / "basics.jsonl", db, "basics_units", BASICS_FIELDS),
    }
    sources = {}
    raw = meta(db).get("sources")
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            sources = loaded
    manifest = {
        "format": FORMAT,
        "format_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "sources": sources,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_pack(db: Database, directory: Path, mode: str = "replace", root: Path | None = None) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise PackError("知识包里没有 manifest.json。")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackError("知识包清单无法读取。") from exc
    if manifest.get("format") != FORMAT or manifest.get("format_version") != VERSION:
        raise PackError("这不是当前程序能读取的知识包。")
    nodes = _read_jsonl(directory / "nodes.jsonl", NODE_FIELDS)
    museum = _read_jsonl(directory / "museum.jsonl", MUSEUM_FIELDS)
    basics = _read_jsonl(directory / "basics.jsonl", BASICS_FIELDS)
    grouped: dict[str, dict[str, Any]] = {}
    for source_key, kind, rows in _grouped(nodes, "nodes") + _grouped(museum, "museum") + _grouped(basics, "basics"):
        bundle = grouped.setdefault(source_key, {"source_key": source_key, "label": source_key, "nodes": [], "museum": [], "basics": [], "files": [], "issues": []})
        bundle[kind] = rows
    if not grouped:
        raise PackError("知识包是空的，没有替换现有知识。")
    sources = manifest.get("sources") if isinstance(manifest.get("sources"), dict) else {}
    for key, bundle in grouped.items():
        info = sources.get(key) if isinstance(sources.get(key), dict) else {}
        bundle["label"] = info.get("label") or key
        bundle["files"] = info.get("files") or []
        bundle["stats"] = info.get("stats") or {}
    apply_mode = "rebuild" if mode == "replace" else "update"
    report = apply_materials(db, list(grouped.values()), apply_mode, root=root)
    report["pack"] = {"counts": manifest.get("counts") or {}, "loaded": knowledge_counts(db)}
    return report


def _grouped(rows: list[tuple], kind: str) -> list[tuple[str, str, list[tuple]]]:
    found: dict[str, list[tuple]] = {}
    index = {"nodes": 1, "museum": 1, "basics": 1}[kind]
    for row in rows:
        found.setdefault(str(row[index]), []).append(row)
    return [(key, kind, value) for key, value in found.items()]


def _write_jsonl(path: Path, db: Database, table: str, fields: tuple[str, ...]) -> int:
    rows = db.query(f"SELECT {', '.join(fields)} FROM {table}")
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({name: row[name] for name in fields}, ensure_ascii=False) + "\n")
    return len(rows)


def _read_jsonl(path: Path, fields: tuple[str, ...]) -> list[tuple]:
    if not path.is_file():
        raise PackError(f"知识包缺少 {path.name}。")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PackError(f"{path.name} 第 {line_number} 行无法读取。") from exc
        if not isinstance(item, dict):
            raise PackError(f"{path.name} 第 {line_number} 行不是记录。")
        rows.append(tuple(item.get(name) for name in fields))
    return rows
