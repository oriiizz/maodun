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
    node_key TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    entry_id TEXT NOT NULL,
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
    source_key TEXT NOT NULL DEFAULT 'museum',
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
    unit_key TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    unit_id TEXT NOT NULL,
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

-- 每一轮的处理经过，只给维护者看，不返回给访客，也不含密钥。
CREATE TABLE IF NOT EXISTS answer_trace (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT,
    created_at TEXT,
    trace_json TEXT
);
"""


def ensure_schema(db: Database) -> None:
    db.execute("SELECT 1")
    with db._lock:
        db._conn.executescript(SCHEMA)
        _migrate_knowledge(db._conn)
        columns = [row[1] for row in db._conn.execute("PRAGMA table_info(sessions)")]
        if "focus_json" not in columns:
            db._conn.execute("ALTER TABLE sessions ADD COLUMN focus_json TEXT")
        columns = [row[1] for row in db._conn.execute("PRAGMA table_info(sessions)")]
        if "visitor_id" not in columns:
            db._conn.execute("ALTER TABLE sessions ADD COLUMN visitor_id TEXT")
        db._conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_visitor ON sessions(visitor_id)")
        db._conn.commit()


def _columns(conn, table: str) -> list[str]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _migrate_knowledge(conn) -> None:
    node_columns = _columns(conn, "corpus_nodes")
    if node_columns and "node_key" not in node_columns:
        conn.execute(
            """
            CREATE TABLE corpus_nodes_v2 (
                node_key TEXT PRIMARY KEY,
                source_key TEXT NOT NULL,
                entry_id TEXT NOT NULL,
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO corpus_nodes_v2 (
                node_key, source_key, entry_id, source_book, volume, chapter_label, page, event_time,
                place, people, info_nature, provider, speaker, cited_from, themes, questions, tags,
                title_json, summary_json, quote_json, limit_chapter, limit_theme,
                chronology_unresolved, style_ok, rag_excluded, visitor_ok, direct_self, searchable, content_hash
            )
            SELECT 'memoir:' || entry_id, 'memoir', entry_id, source_book, volume, chapter_label, page,
                   event_time, place, people, info_nature, provider, speaker, cited_from, themes, questions,
                   tags, title_json, summary_json, quote_json, limit_chapter, limit_theme,
                   chronology_unresolved, style_ok, rag_excluded, visitor_ok, direct_self, searchable, content_hash
              FROM corpus_nodes
            """
        )
        conn.execute("DROP TABLE corpus_nodes")
        conn.execute("ALTER TABLE corpus_nodes_v2 RENAME TO corpus_nodes")
    museum_columns = _columns(conn, "museum_items")
    if museum_columns and "source_key" not in museum_columns:
        conn.execute("ALTER TABLE museum_items ADD COLUMN source_key TEXT NOT NULL DEFAULT 'museum'")
    basics_columns = _columns(conn, "basics_units")
    if basics_columns and "unit_key" not in basics_columns:
        conn.execute(
            """
            CREATE TABLE basics_units_v2 (
                unit_key TEXT PRIMARY KEY,
                source_key TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                title TEXT,
                body TEXT,
                is_source_list INTEGER NOT NULL DEFAULT 0,
                searchable TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO basics_units_v2 (unit_key, source_key, unit_id, title, body, is_source_list, searchable)
            SELECT 'memoir:' || unit_id, 'memoir', unit_id, title, body, is_source_list, searchable
              FROM basics_units
            """
        )
        conn.execute("DROP TABLE basics_units")
        conn.execute("ALTER TABLE basics_units_v2 RENAME TO basics_units")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_entry ON corpus_nodes(entry_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_source ON corpus_nodes(source_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_museum_source ON museum_items(source_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_basics_source ON basics_units(source_key)")


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
