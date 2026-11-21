"""本地知识库表。访客检索只读 visitor_ok 为 1 的节点。"""

from __future__ import annotations

import json

from app.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_nodes (
    entry_id TEXT PRIMARY KEY,
    source_book TEXT,
    volume TEXT,
    chapter_label TEXT,
    page TEXT,
    event_time TEXT,
    place TEXT,
    people TEXT,
    info_nature TEXT,
    provider TEXT,
    speaker TEXT,
    cited_from TEXT,
    themes TEXT,
    questions TEXT,
    tags TEXT,
    title_json TEXT,
    summary_json TEXT,
    quote_json TEXT,
    limit_chapter TEXT,
    limit_theme TEXT,
    chronology_unresolved INTEGER NOT NULL DEFAULT 0,
    style_ok INTEGER NOT NULL DEFAULT 0,
    rag_excluded INTEGER NOT NULL DEFAULT 0,
    visitor_ok INTEGER NOT NULL DEFAULT 0,
    direct_self INTEGER NOT NULL DEFAULT 0,
    searchable TEXT,
    content_hash TEXT
);

CREATE TABLE IF NOT EXISTS museum_items (
    item_key TEXT PRIMARY KEY,
    sheet TEXT NOT NULL,
    barcode TEXT NOT NULL,
    title TEXT,
    recorded_date TEXT,
    sender TEXT,
    recipient TEXT,
    net_status TEXT,
    found_text TEXT,
    short_quote TEXT,
    quote_speaker TEXT,
    correspondence TEXT,
    source_org TEXT,
    source_url TEXT,
    checks TEXT,
    catalog_raw TEXT,
    scope TEXT,
    visitor_text TEXT,
    content_hash TEXT
);

CREATE TABLE IF NOT EXISTS basics_units (
    unit_id TEXT PRIMARY KEY,
    title TEXT,
    body TEXT,
    is_source_list INTEGER NOT NULL DEFAULT 0,
    searchable TEXT
);

CREATE TABLE IF NOT EXISTS answer_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    turn_id TEXT,
    question TEXT,
    spoken_text TEXT,
    mode TEXT,
    hit_ids TEXT,
    model_called INTEGER,
    provider TEXT,
    notice TEXT,
    created_at TEXT
);
"""


def ensure_schema(db: Database) -> None:
    db.execute("SELECT 1")
    with db._lock:
        db._conn.executescript(SCHEMA)
        columns = [row[1] for row in db._conn.execute("PRAGMA table_info(sessions)")]
        if "focus_json" not in columns:
            db._conn.execute("ALTER TABLE sessions ADD COLUMN focus_json TEXT")
        db._conn.commit()


def clear_corpus(db: Database) -> None:
    for table in ("corpus_nodes", "museum_items", "basics_units", "corpus_meta"):
        db.execute(f"DELETE FROM {table}")


def put_meta(db: Database, key: str, value: object) -> None:
    db.execute(
        "INSERT INTO corpus_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)),
    )


def meta(db: Database) -> dict[str, str]:
    return {row["key"]: row["value"] for row in db.query("SELECT key, value FROM corpus_meta")}
