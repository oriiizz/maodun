"""SQLite 资料库。保存原文、概括、来源、审核状态和导入记录。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    origin_path TEXT,
    stored_path TEXT,
    content_sha256 TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    filename TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    inserted INTEGER DEFAULT 0,
    updated INTEGER DEFAULT 0,
    unchanged INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    row_count INTEGER DEFAULT 0,
    message TEXT,
    mapping_json TEXT
);

CREATE TABLE IF NOT EXISTS import_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    sheet_name TEXT,
    row_number INTEGER,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_entry_id TEXT,
    sheet_name TEXT,
    row_number INTEGER,
    author TEXT,
    speaker TEXT,
    speech_kind TEXT,
    narrative_level TEXT,
    information_provider TEXT,
    core_person TEXT,
    relation_to_subject TEXT,
    related_people TEXT,
    person_relations TEXT,
    event_title TEXT,
    summary TEXT,
    original_text TEXT,
    context_note TEXT,
    source_title TEXT,
    collection_no TEXT,
    volume_label TEXT,
    volume TEXT,
    page TEXT,
    paragraph_no TEXT,
    chapter TEXT,
    section TEXT,
    time_label TEXT,
    place TEXT,
    material_type TEXT,
    fact_tags TEXT,
    persona_tags TEXT,
    style_tags TEXT,
    answerable_questions TEXT,
    answer_points TEXT,
    style_evidence TEXT,
    evidence_type TEXT,
    evidence_strength TEXT,
    review_status TEXT,
    review_status_raw TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    can_quote INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    token_json TEXT,
    extra_json TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_entries_source ON entries(source_id);
CREATE INDEX IF NOT EXISTS idx_entries_external ON entries(source_id, external_entry_id);
CREATE INDEX IF NOT EXISTS idx_entries_hash ON entries(content_hash);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL,
    app_conversation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    question TEXT,
    raw_question TEXT,
    corrected_question TEXT,
    status TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def transaction(self):
        """多步写入同时成功或同时取消。不要在其中再调用会自行提交的方法。"""

        database = self

        class _Transaction:
            def __enter__(self):
                database._lock.acquire()
                database._conn.execute("BEGIN")
                return database._conn

            def __exit__(self, exc_type, exc, tb):
                try:
                    if exc_type:
                        database._conn.rollback()
                    else:
                        database._conn.commit()
                finally:
                    database._lock.release()
                return False

        return _Transaction()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
