"""把内部知识记录写入数据库。失败时不替换已经可用的记录，也不动会话。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import Database
from app.knowledge.store import ensure_schema, put_meta

NODE_SQL = """
INSERT INTO corpus_nodes (
    node_key, source_key, entry_id, source_book, volume, chapter_label, page, event_time, place, people,
    info_nature, provider, speaker, cited_from, themes, questions, tags,
    title_json, summary_json, quote_json, limit_chapter, limit_theme,
    chronology_unresolved, style_ok, rag_excluded, visitor_ok, direct_self,
    searchable, content_hash
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

MUSEUM_SQL = """
INSERT INTO museum_items (
    item_key, source_key, sheet, barcode, title, recorded_date, sender, recipient, net_status,
    found_text, short_quote, quote_speaker, correspondence, source_org, source_url,
    checks, catalog_raw, scope, visitor_text, content_hash
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

BASICS_SQL = """
INSERT INTO basics_units (
    unit_key, source_key, unit_id, title, body, is_source_list, searchable
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""


class ApplyError(Exception):
    pass


def apply_materials(db: Database, bundles: list[dict[str, Any]], mode: str, *, root: Path | None = None) -> dict[str, Any]:
    if mode not in {"rebuild", "update", "add", "remove"}:
        raise ApplyError("更新方式只能是新增、更新、删除或完整重建。")
    ensure_schema(db)
    keys = [bundle["source_key"] for bundle in bundles]
    if mode == "add":
        occupied = _existing_keys(db, keys)
        if occupied:
            raise ApplyError("这些资料已经存在，没有覆盖：" + "、".join(occupied))
    try:
        with db.transaction() as conn:
            if mode == "rebuild":
                for table in ("corpus_nodes", "museum_items", "basics_units"):
                    conn.execute(f"DELETE FROM {table}")
            else:
                for key in keys:
                    for table in ("corpus_nodes", "museum_items", "basics_units"):
                        conn.execute(f"DELETE FROM {table} WHERE source_key = ?", (key,))
            if mode != "remove":
                for bundle in bundles:
                    if bundle.get("nodes"):
                        conn.executemany(NODE_SQL, bundle["nodes"])
                    if bundle.get("museum"):
                        conn.executemany(MUSEUM_SQL, bundle["museum"])
                    if bundle.get("basics"):
                        conn.executemany(BASICS_SQL, bundle["basics"])
    except ApplyError:
        raise
    except Exception as exc:
        raise ApplyError("知识库没有替换，原有记录仍可使用。") from exc
    _write_meta(db, bundles, mode)
    if root is not None:
        touch_stamp(root)
    report = {
        "mode": mode,
        "sources": keys,
        "style_samples": check_style_samples(db),
    }
    report["counts"] = knowledge_counts(db)
    return report


def knowledge_counts(db: Database) -> dict[str, int]:
    ensure_schema(db)
    nodes = db.query_one("SELECT COUNT(*) AS count FROM corpus_nodes")
    usable = db.query_one("SELECT COUNT(*) AS count FROM corpus_nodes WHERE visitor_ok = 1")
    museum = db.query_one("SELECT COUNT(*) AS count FROM museum_items")
    basics = db.query_one("SELECT COUNT(*) AS count FROM basics_units WHERE is_source_list = 0")
    return {
        "nodes": int(nodes["count"] if nodes else 0),
        "visitor_nodes": int(usable["count"] if usable else 0),
        "museum": int(museum["count"] if museum else 0),
        "basics": int(basics["count"] if basics else 0),
    }


def check_style_samples(db: Database) -> dict[str, list[str]]:
    """样本要能在资料库里找到，是可用的本人文字，“原文”一行要与摘录一字不差。"""

    import json

    from app.knowledge.stylebank import _parsed, sample_quotes

    _parsed.cache_clear()
    _guide, samples = _parsed()
    quotes = sample_quotes()
    missing = []
    ambiguous = []
    unfit = []
    altered = []
    for sample in samples:
        rows = db.query(
            "SELECT source_key, visitor_ok, direct_self, style_ok, quote_json FROM corpus_nodes WHERE entry_id = ?",
            (sample["entry_id"],),
        )
        if not rows:
            missing.append(sample["entry_id"])
            continue
        if len(rows) > 1:
            ambiguous.append(sample["entry_id"])
            continue
        row = rows[0]
        if not (row["visitor_ok"] and row["direct_self"] and row["style_ok"]):
            unfit.append(sample["entry_id"])
        try:
            stored = str((json.loads(row["quote_json"] or "{}") or {}).get("effective") or "")
        except json.JSONDecodeError:
            stored = ""
        pieces = [piece.strip() for piece in quotes.get(sample["entry_id"], "").split("……") if piece.strip()]
        if not pieces or any(piece not in stored for piece in pieces):
            altered.append(sample["entry_id"])
    return {"missing": missing, "ambiguous": ambiguous, "unfit": unfit, "altered": altered}


def touch_stamp(root: Path) -> None:
    path = root / "data" / "knowledge.stamp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds"), encoding="utf-8")


def stamp_value(root: Path) -> str:
    path = root / "data" / "knowledge.stamp"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _existing_keys(db: Database, keys: list[str]) -> list[str]:
    found = []
    for key in keys:
        row = db.query_one(
            """
            SELECT 1 AS present FROM corpus_nodes WHERE source_key = ?
            UNION SELECT 1 FROM museum_items WHERE source_key = ?
            UNION SELECT 1 FROM basics_units WHERE source_key = ?
            LIMIT 1
            """,
            (key, key, key),
        )
        if row:
            found.append(key)
    return found


def _write_meta(db: Database, bundles: list[dict[str, Any]], mode: str) -> None:
    from app.knowledge.store import meta

    current = {}
    raw = meta(db).get("sources")
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                current = loaded
        except json.JSONDecodeError:
            current = {}
    if mode == "rebuild":
        current = {}
    for bundle in bundles:
        key = bundle["source_key"]
        if mode == "remove":
            current.pop(key, None)
            continue
        current[key] = {
            "label": bundle.get("label") or key,
            "files": bundle.get("files") or [],
            "stats": bundle.get("stats") or {},
        }
    issues = []
    for bundle in bundles:
        issues.extend(bundle.get("issues") or [])
    put_meta(db, "sources", current)
    put_meta(db, "issues", issues[:200])
    put_meta(db, "updated_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
