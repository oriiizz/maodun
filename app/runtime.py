"""把导入、检索、问答、会话接在一起。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import Settings
from app.contracts import to_plain
from app.database import Database
from app.display.events import avatar_status, events_for_answer
import json

from app.contracts import AnswerResult, ControlCue
from app.ingest.demo_data import write_demo_workbook
from app.ingest.excel_import import ImportError as WorkbookImportError
from app.ingest.excel_import import import_workbook
from app.knowledge.import_sources import CorpusImportError, import_corpus
from app.knowledge.index import KnowledgeIndex
from app.knowledge.respond import answer_from_corpus
from app.knowledge.store import ensure_schema
from app.llm.providers import DeepSeekChatModel
from app.qa.persona import load_lexicon, load_persona
from app.retrieval.local_chinese import LocalChineseRetriever
from app.retrieval.tokenize import configure_tokenizer
from app.session.manager import SessionManager
from app.speech.interfaces import PlaybackQueue, build_recognizer, build_synthesizer


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.persona = load_persona(settings.persona_path)
        self.lexicon = set(load_lexicon(settings.lexicon_path))
        configure_tokenizer(sorted(self.lexicon))
        self.http = httpx.Client(timeout=settings.request_timeout_seconds)
        ensure_schema(self.db)
        self.retriever = LocalChineseRetriever(self.db)
        self.knowledge = KnowledgeIndex.load(self.db)
        self.sessions = SessionManager(self.db)
        self.recognizer = build_recognizer(settings, self.http)
        self.synthesizer = build_synthesizer(settings, self.http)
        self.playback = PlaybackQueue()

    def close(self) -> None:
        self.http.close()
        self.db.close()

    def refresh_index(self) -> None:
        reload = getattr(self.retriever, "reload", None)
        if reload:
            reload()
        self.knowledge = KnowledgeIndex.load(self.db)

    def import_path(self, path: Path, *, is_demo: bool = False) -> dict:
        report = import_workbook(
            self.db,
            path,
            originals_dir=self.settings.originals_dir,
            is_demo=is_demo,
        )
        self.refresh_index()
        report["issues"] = self.batch_issues(report["batch_id"])
        report["index_rebuilt"] = True
        report["index_message"] = "这份表格进入演示条目，不进入访客使用的本地知识库。"
        return report

    def import_corpus(self, directory: Path) -> dict:
        report = import_corpus(self.db, directory)
        self.knowledge = KnowledgeIndex.load(self.db)
        return report

    def import_demo(self) -> dict:
        target = self.settings.originals_dir / "_generated" / "演示资料.xlsx"
        write_demo_workbook(target)
        return self.import_path(target, is_demo=True)

    def batch_issues(self, batch_id: str) -> list[dict]:
        rows = self.db.query(
            """
            SELECT severity, sheet_name, row_number, message
              FROM import_issues
             WHERE batch_id = ?
             ORDER BY id
            """,
            (batch_id,),
        )
        return [dict(row) for row in rows]

    def sources(self) -> list[dict]:
        rows = self.db.query(
            """
            SELECT s.*, COUNT(e.id) AS entry_count
              FROM sources s
              LEFT JOIN entries e ON e.source_id = s.id
             GROUP BY s.id
             ORDER BY s.updated_at DESC
            """
        )
        return [dict(row) for row in rows]

    def batches(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM batches ORDER BY started_at DESC LIMIT 20")
        return [dict(row) for row in rows]

    def list_entries(self, *, query: str = "", source_id: str = "", limit: int = 20, offset: int = 0) -> dict:
        if query.strip():
            hits = self.retriever.search(query.strip(), limit=max(1, min(limit, 50)))
            return {"mode": "search", "items": [to_plain(hit) for hit in hits], "total": len(hits)}
        where = ""
        params: list[object] = []
        if source_id:
            where = "WHERE source_id = ?"
            params.append(source_id)
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM entries {where}", tuple(params))
        rows = self.db.query(
            f"""
            SELECT id, source_id, external_entry_id, source_title, volume, page, paragraph_no,
                   chapter, section, speaker, speech_kind, event_title, original_text, summary,
                   review_status, is_demo, can_quote, material_type, time_label, place
              FROM entries
              {where}
             ORDER BY source_id, row_number
             LIMIT ? OFFSET ?
            """,
            tuple([*params, max(1, min(limit, 100)), max(0, offset)]),
        )
        return {"mode": "list", "items": [dict(row) for row in rows], "total": int(total["count"] if total else 0)}

    def get_entry(self, entry_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM entries WHERE id = ?", (entry_id,))
        if row is None:
            return None
        item = dict(row)
        item.pop("token_json", None)
        row_number = int(item.get("row_number") or 0)
        neighbors = self.db.query(
            """
            SELECT id, page, paragraph_no, original_text, summary, row_number
              FROM entries
             WHERE source_id = ? AND chapter = ? AND section = ?
               AND row_number IN (?, ?)
             ORDER BY row_number
            """,
            (
                item["source_id"],
                item.get("chapter") or "",
                item.get("section") or "",
                row_number - 1,
                row_number + 1,
            ),
        )
        item["neighbors"] = [dict(neighbor) for neighbor in neighbors]
        return item

    def set_review(self, entry_id: str, status: str) -> None:
        if status not in {"pending", "reviewed", "rejected"}:
            raise ValueError("审核状态只能是 pending、reviewed 或 rejected。")
        current = self.get_entry(entry_id)
        if current is None:
            raise KeyError(entry_id)
        from datetime import datetime, timezone

        self.db.execute(
            "UPDATE entries SET review_status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now(timezone.utc).isoformat(timespec="seconds"), entry_id),
        )

    def search(self, query: str, limit: int = 8) -> list[dict]:
        return [to_plain(hit) for hit in self.retriever.search(query, limit=limit)]

    def ask(self, question: str, session_id: str | None = None) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("请先输入问题。")
        turn = self.sessions.begin_turn(session_id or "", question)
        self.playback.note_epoch(turn["session_id"], turn["epoch"])
        return self._ask_local(question, turn)

    def _ask_local(self, question: str, turn: dict) -> dict:
        session = self.sessions.get(turn["session_id"]) or {}
        try:
            focus = json.loads(session.get("focus_json") or "{}")
        except json.JSONDecodeError:
            focus = {}
        if not isinstance(focus, dict):
            focus = {}
        history_rows = self.db.query(
            """
            SELECT t.question, t.result_json,
                   (
                       SELECT a.mode FROM answer_audit a
                        WHERE a.turn_id = t.id
                        ORDER BY a.id DESC
                        LIMIT 1
                   ) AS mode
              FROM turns t
             WHERE t.session_id = ? AND t.status = 'done' AND t.id != ?
             ORDER BY t.created_at
            """,
            (turn["session_id"], turn["turn_id"]),
        )
        history = []
        for row in history_rows:
            spoken = ""
            try:
                payload = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                spoken = str(payload.get("spoken_text") or "")
            mode = row["mode"] or ""
            usable = mode not in {"model_error", "no_model", "not_ready"} and not spoken.startswith(
                ("这一次没有答成", "资料还没有准备好")
            )
            if row["question"]:
                history.append({"question": row["question"], "spoken": spoken, "usable": usable, "mode": mode})
        model = self._corpus_model()
        public, audit, focus = answer_from_corpus(
            self.knowledge,
            question,
            model=model,
            history=history,
            focus=focus,
            provider_name="local_corpus",
        )
        self.db.execute(
            "UPDATE sessions SET focus_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(focus, ensure_ascii=False), _now(), turn["session_id"]),
        )
        result = AnswerResult(
            spoken_text=public["spoken_text"],
            system_notice="",
            citations=[],
            evidence=[],
            rejected=[],
            control=ControlCue(
                mood=public["control"]["mood"],
                gesture=public["control"]["gesture"],
            ),
            model_called=bool(public["model_called"]),
            provider="local_corpus",
            session_id=turn["session_id"],
            turn_id=turn["turn_id"],
            epoch=turn["epoch"],
        )
        payload = to_plain(result)
        payload["avatar_events"] = to_plain(events_for_answer(result))
        self.db.execute(
            """
            INSERT INTO answer_audit (
                session_id, turn_id, question, spoken_text, mode, hit_ids, model_called, provider, notice, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn["session_id"],
                turn["turn_id"],
                question,
                result.spoken_text,
                audit.get("mode") or "",
                json.dumps(audit.get("hit_ids") or [], ensure_ascii=False),
                int(result.model_called),
                "local_corpus",
                audit.get("notice") or "",
                _now(),
            ),
        )
        accepted = self.sessions.finish_turn(turn["session_id"], turn["turn_id"], turn["epoch"], payload)
        if not accepted:
            payload["discarded"] = True
            payload["spoken_text"] = ""
            payload["system_notice"] = "这一轮已经取消或被新的问题代替，迟到的结果已丢弃。"
            payload["avatar_events"] = []
        return payload

    def _corpus_model(self):
        return DeepSeekChatModel(self.settings, self.http)

    def status(self) -> dict:
        counts = self.db.query_one("SELECT COUNT(*) AS entries FROM entries")
        sources = self.db.query_one("SELECT COUNT(*) AS sources FROM sources")
        retriever_name = getattr(self.retriever, "provider_id", "local")
        retriever_message = self.retriever.status_message() + " 访客问答不使用这套演示条目。"
        return {
            "project": "中国现代文学馆 · 茅盾数字人",
            "disclaimer": self.persona.get("disclaimer", ""),
            "counts": {
                "entries": int(counts["entries"] if counts else 0),
                "sources": int(sources["sources"] if sources else 0),
            },
            "retriever": {"provider": retriever_name, "message": retriever_message},
            "qa": self._qa_status(),
            "asr": to_plain(self.recognizer.status()),
            "tts": to_plain(self.synthesizer.status()),
            "avatar": to_plain(avatar_status(self.settings.avatar_provider)),
            "knowledge": self._knowledge_status(),
            "asr_mode": "realtime_stream" if self.settings.asr_provider == "baidu_realtime" else "record_then_recognize",
        }

    def _knowledge_status(self) -> dict:
        return {"kind": "local_corpus", "message": self.knowledge.status_message()}

    def _qa_status(self) -> dict:
        ready = self.settings.deepseek_configured
        count = len(self.knowledge.nodes) + len(self.knowledge.items)
        if not count:
            message = "本地知识库还没有导入。访客提问不会改用其他模型或旧表格。"
        elif ready:
            message = f"本地知识库检索后，调用 DeepSeek 官方模型 {self.settings.deepseek_model} 生成回答。页面和语音使用同一份检查过的正文。"
        else:
            message = "本地知识库可以检索。请在 .env 填写 DEEPSEEK_API_KEY 后才能生成人物回答。"
        return {"provider": "deepseek", "configured": ready and count > 0, "message": message}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["Runtime", "WorkbookImportError", "CorpusImportError"]
