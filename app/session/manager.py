"""会话、轮次和“第几代”标记。新一轮开始后，旧任务的迟到结果会被丢掉。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.database import Database


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionManager:
    def __init__(self, db: Database):
        self.db = db

    def create(self) -> dict:
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        self.db.execute(
            "INSERT INTO sessions (id, epoch, created_at, updated_at) VALUES (?, 0, ?, ?)",
            (session_id, timestamp, timestamp),
        )
        return {"id": session_id, "epoch": 0}

    def get(self, session_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def begin_turn(self, session_id: str, question: str, raw_question: str | None = None) -> dict:
        session = self.get(session_id)
        if session is None:
            created = self.create()
            session_id = created["id"]
            session = self.get(session_id)
        assert session is not None
        epoch = int(session["epoch"]) + 1
        timestamp = now_iso()
        self.db.execute(
            "UPDATE sessions SET epoch = ?, updated_at = ? WHERE id = ?",
            (epoch, timestamp, session_id),
        )
        self.db.execute(
            """
            UPDATE turns
               SET status = 'cancelled', finished_at = ?, error = ?
             WHERE session_id = ? AND status = 'running'
            """,
            (timestamp, "已开始新一轮，这一轮被取消。", session_id),
        )
        turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        self.db.execute(
            """
            INSERT INTO turns (
                id, session_id, epoch, question, raw_question, corrected_question, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (turn_id, session_id, epoch, question, raw_question or question, question, timestamp),
        )
        return {"session_id": session_id, "turn_id": turn_id, "epoch": epoch}

    def finish_turn(self, session_id: str, turn_id: str, epoch: int, result: dict) -> bool:
        session = self.get(session_id)
        timestamp = now_iso()
        if session is None or int(session["epoch"]) != epoch:
            self.db.execute(
                """
                UPDATE turns
                   SET status = 'discarded', finished_at = ?, error = ?, result_json = ?
                 WHERE id = ?
                """,
                (timestamp, "这一轮已经过期，迟到的结果已丢弃。", json.dumps(result, ensure_ascii=False), turn_id),
            )
            return False
        self.db.execute(
            """
            UPDATE turns
               SET status = 'done', finished_at = ?, result_json = ?, error = NULL
             WHERE id = ?
            """,
            (timestamp, json.dumps(result, ensure_ascii=False), turn_id),
        )
        return True

    def fail_turn(self, turn_id: str, message: str) -> None:
        self.db.execute(
            "UPDATE turns SET status = 'error', finished_at = ?, error = ? WHERE id = ?",
            (now_iso(), message, turn_id),
        )

    def cancel(self, session_id: str) -> dict | None:
        session = self.get(session_id)
        if session is None:
            return None
        epoch = int(session["epoch"]) + 1
        timestamp = now_iso()
        self.db.execute(
            "UPDATE sessions SET epoch = ?, updated_at = ? WHERE id = ?",
            (epoch, timestamp, session_id),
        )
        self.db.execute(
            """
            UPDATE turns
               SET status = 'cancelled', finished_at = ?, error = ?
             WHERE session_id = ? AND status = 'running'
            """,
            (timestamp, "已取消。", session_id),
        )
        return {"id": session_id, "epoch": epoch}

    def history(self, session_id: str) -> list[dict]:
        rows = self.db.query(
            "SELECT id, epoch, question, status, error, created_at, finished_at FROM turns WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        return [dict(row) for row in rows]
