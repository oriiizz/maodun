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
from app.knowledge.apply import stamp_value
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
        self._knowledge_stamp = stamp_value(self.settings.root)
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
        self._knowledge_stamp = stamp_value(self.settings.root)

    def _reload_knowledge_if_needed(self) -> None:
        current = stamp_value(self.settings.root)
        if current == self._knowledge_stamp:
            return
        self.knowledge = KnowledgeIndex.load(self.db)
        from app.knowledge.stylebank import _parsed

        _parsed.cache_clear()
        self._knowledge_stamp = current

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
        report = import_corpus(self.db, directory, root=self.settings.root)
        self._reload_knowledge_if_needed()
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

    def ask(self, question: str, session_id: str | None = None, visitor_id: str | None = None) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("请先输入问题。")
        try:
            turn = self.sessions.begin_turn(session_id or "", question, visitor_id=visitor_id)
        except KeyError as exc:
            raise KeyError(str(exc)) from exc
        self.playback.note_epoch(turn["session_id"], turn["epoch"])
        return self._ask_local(question, turn)

    def _ask_local(self, question: str, turn: dict) -> dict:
        self._reload_knowledge_if_needed()
        session = self.sessions.get(turn["session_id"]) or {}
        try:
            focus = json.loads(session.get("focus_json") or "{}")
        except json.JSONDecodeError:
            focus = {}
        if not isinstance(focus, dict):
            focus = {}
        # 完整对话留在数据库。本轮按预算取近期原文，必要时找回更早原文，并带上较早摘要。
        history_rows = self.db.query(
            """
            SELECT t.id, t.question, t.result_json, t.status, t.created_at,
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
        from app.session.memory import build_context, turns_from_rows, update_after_answer

        all_turns = turns_from_rows(history_rows)
        memory_bundle = build_context(all_turns, question, focus)
        focus = memory_bundle["focus"]
        history = memory_bundle["working"]
        memory_pack = memory_bundle["pack"]
        model = self._corpus_model()
        public, audit, focus = answer_from_corpus(
            self.knowledge,
            question,
            model=model,
            history=history,
            focus=focus,
            provider_name="local_corpus",
            memory=memory_pack,
        )
        focus = update_after_answer(
            focus,
            all_turns,
            question=question,
            spoken=public.get("spoken_text") or "",
            mode=str(audit.get("mode") or ""),
            reading=(audit.get("trace") or {}).get("reading") or {},
        )
        if audit.get("trace") is not None:
            audit["trace"]["memory"] = {
                "meta": memory_pack.get("meta") or {},
                "recalled_ids": (memory_pack.get("meta") or {}).get("recalled_ids") or [],
                "summary_segments": len(memory_pack.get("earlier_summary") or []),
                "open_topics": memory_pack.get("open_topics") or [],
            }
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
        self.db.execute(
            "INSERT OR REPLACE INTO answer_trace (turn_id, session_id, created_at, trace_json) VALUES (?, ?, ?, ?)",
            (turn["turn_id"], turn["session_id"], _now(), json.dumps(audit.get("trace") or {}, ensure_ascii=False)),
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
