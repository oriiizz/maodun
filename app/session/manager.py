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

    def create(self, visitor_id: str = "") -> dict:
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        self.db.execute(
            "INSERT INTO sessions (id, epoch, visitor_id, created_at, updated_at) VALUES (?, 0, ?, ?, ?)",
            (session_id, visitor_id, timestamp, timestamp),
        )
        return {"id": session_id, "epoch": 0, "visitor_id": visitor_id}

    def get(self, session_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def owned(self, session_id: str, visitor_id: str) -> dict | None:
        session = self.get(session_id)
        if session is None or not visitor_id or session.get("visitor_id") != visitor_id:
            return None
        return session

    def begin_turn(self, session_id: str, question: str, raw_question: str | None = None, visitor_id: str | None = None) -> dict:
        session = self.get(session_id) if session_id else None
        if visitor_id is not None:
            if session_id and (session is None or session.get("visitor_id") != visitor_id):
                raise KeyError(session_id)
            if session is None:
                session = self.create(visitor_id)
                session_id = session["id"]
        elif session is None:
            session = self.create("")
            session_id = session["id"]
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
            """
            SELECT id, question, status, result_json, created_at
              FROM turns
             WHERE session_id = ?
             ORDER BY created_at
            """,
            (session_id,),
        )
        items = []
        for row in rows:
            spoken = ""
            try:
                payload = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                spoken = str(payload.get("spoken_text") or "")
            items.append({
                "id": row["id"],
                "question": row["question"] or "",
                "spoken_text": spoken,
                "status": row["status"] or "",
                "created_at": row["created_at"] or "",
            })
        return items

    def list_for(self, visitor_id: str) -> list[dict]:
        if not visitor_id:
            return []
        rows = self.db.query(
            """
            SELECT s.id, s.updated_at,
                   (
                       SELECT t.question FROM turns t
                        WHERE t.session_id = s.id AND t.question != ''
                        ORDER BY t.created_at
                        LIMIT 1
                   ) AS title
              FROM sessions s
             WHERE s.visitor_id = ?
             ORDER BY s.updated_at DESC
            """,
            (visitor_id,),
        )
        return [{"id": row["id"], "updated_at": row["updated_at"], "title": row["title"] or "新对话"} for row in rows]

    def export_text(self, session_id: str) -> str:
        lines = ["与茅盾对话", ""]
        for turn in self.history(session_id):
            if turn["status"] not in {"done", "running"} or not (turn["question"] or turn["spoken_text"]):
                continue
            lines.append(f"时间：{_local_time(turn['created_at'])}")
            lines.append("问：")
            lines.append(turn["question"])
            lines.append("")
            lines.append("答：")
            lines.append(turn["spoken_text"] or "（这一轮还没有回答）")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def delete(self, session_id: str, visitor_id: str) -> bool:
        if self.owned(session_id, visitor_id) is None:
            return False
        self._remove_ids([session_id])
        return True

    def delete_all(self, visitor_id: str) -> int:
        if not visitor_id:
            return 0
        rows = self.db.query("SELECT id FROM sessions WHERE visitor_id = ?", (visitor_id,))
        ids = [row["id"] for row in rows]
        self._remove_ids(ids)
        return len(ids)

    def _remove_ids(self, session_ids: list[str]) -> None:
        for session_id in session_ids:
            self.db.execute("DELETE FROM answer_audit WHERE session_id = ?", (session_id,))
            self.db.execute("DELETE FROM answer_trace WHERE session_id = ?", (session_id,))
            self.db.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def _local_time(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value
