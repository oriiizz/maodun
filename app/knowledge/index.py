"""访客检索。禁用条目不进索引；同一条目 ID 只有一个节点。"""

from __future__ import annotations

import json
import re

from app.database import Database
from app.knowledge.store import ensure_schema
from app.retrieval.local_chinese import BM25
from app.retrieval.tokenize import tokenize

BARCODE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{6,}(?![A-Za-z0-9])")
ENTRY_ID = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{2,4}-[A-Z]?\d{3,}|B\d{2})(?![A-Za-z0-9])")


def _loads(text: str) -> dict:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class KnowledgeIndex:
    def __init__(self, nodes: list[dict], items: list[dict], units: list[dict]):
        self.nodes = nodes
        self.items = items
        self.units = units
        self.by_entry = {node["entry_id"]: node for node in nodes}
        self.by_barcode = {item["barcode"]: item for item in items}
        self.documents = nodes + items + [unit for unit in units if not unit.get("is_source_list")]
        token_lists = [tokenize(doc.get("searchable") or "") for doc in self.documents]
        self.ranker = BM25(token_lists)

    @classmethod
    def load(cls, db: Database) -> "KnowledgeIndex":
        ensure_schema(db)
        nodes = []
        for row in db.query("SELECT * FROM corpus_nodes WHERE visitor_ok = 1"):
            item = dict(row)
            item["kind"] = "evidence"
            item["title"] = _loads(item.pop("title_json"))
            item["summary"] = _loads(item.pop("summary_json"))
            item["quote"] = _loads(item.pop("quote_json"))
            nodes.append(item)
        items = []
        for row in db.query("SELECT * FROM museum_items"):
            item = dict(row)
            item["kind"] = "museum"
            item["searchable"] = "\n".join(
                [
                    item["sheet"],
                    item["barcode"],
                    item["title"] or "",
                    item["sender"] or "",
                    item["recipient"] or "",
                    item["recorded_date"] or "",
                    item["visitor_text"] or "",
                ]
            )
            items.append(item)
        units = []
        for row in db.query("SELECT * FROM basics_units"):
            item = dict(row)
            item["kind"] = "basics"
            units.append(item)
        return cls(nodes, items, units)

    def status_message(self) -> str:
        return (
            f"本地知识库：可用生平节点 {len(self.nodes)} 条，馆藏 {len(self.items)} 件，"
            f"基础知识 {sum(1 for unit in self.units if not unit.get('is_source_list'))} 则。"
            "禁用条目不参与回答。"
        )

    def node(self, entry_id: str) -> dict | None:
        return self.by_entry.get(entry_id)

    def museum(self, barcode: str) -> dict | None:
        return self.by_barcode.get(barcode)

    def excluded_ids(self, db: Database) -> set[str]:
        rows = db.query("SELECT entry_id FROM corpus_nodes WHERE rag_excluded = 1")
        return {row["entry_id"] for row in rows}

    def search(self, question: str, *, limit: int = 6) -> list[dict]:
        question = (question or "").strip()
        if not question:
            return []
        chosen: list[dict] = []
        seen: set[str] = set()

        def add(doc: dict) -> None:
            key = f"{doc['kind']}:{doc.get('entry_id') or doc.get('barcode') or doc.get('unit_id')}"
            if key in seen:
                return
            seen.add(key)
            chosen.append(doc)

        barcodes = BARCODE.findall(question)
        entry_ids = ENTRY_ID.findall(question)
        for barcode in barcodes:
            item = self.by_barcode.get(barcode)
            if item:
                add(item)
        for entry_id in entry_ids:
            node = self.by_entry.get(entry_id)
            if node:
                add(node)
        if barcodes or entry_ids:
            return chosen[:limit]
        tokens = [token for token in tokenize(question) if len(token) >= 2]
        if not tokens or not self.documents:
            return []
        scored = []
        for index, doc in enumerate(self.documents):
            score = self.ranker.score(tokens, index)
            compact = doc.get("searchable") or ""
            for token in tokens:
                if token and token in compact:
                    score += 0.15
            if score <= 0:
                continue
            scored.append((score, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return []
        best = scored[0][0]
        for score, doc in scored:
            if score < best * 0.35:
                break
            add(doc)
            if len(chosen) >= limit:
                break
        return chosen
