"""本机中文检索。

适合几千到大约一万条段落，用来核对导入和来源。
它按分词、原词和相邻段落找证据，没有语义向量，所以换一种说法时可能找不到。
以后可以换成千帆知识库或别的向量检索，问答模块不用重写。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from app.contracts import Evidence, NeighborPassage
from app.database import Database
from app.retrieval.tokenize import tokenize

STOPWORDS = {
    "的", "了", "吗", "呢", "吧", "啊", "呀", "么", "什么", "怎么", "怎样", "怎么样",
    "为什么", "为何", "如何", "是否", "请问", "一下", "介绍", "说说", "谈谈", "可以",
    "我们", "你们", "他们", "这个", "那个", "就是", "还是", "不是", "没有", "因为",
    "所以", "如果", "那么", "一个", "哪些", "哪里", "哪儿", "以及", "还有", "或者",
    "自己", "进行", "关于", "对于", "以及", "及其", "并", "并且", "而且", "但是",
    "您", "你", "我", "他", "她", "它", "哪", "谁", "吗", "请问您",
}
GENERIC = {"茅盾", "先生", "中国", "文学", "作品", "作家", "小说", "文章", "时候", "现在", "今天"}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def passage_blob(row: dict) -> str:
    """用来判断“这段话里有没有这些词”。书名不放进来，否则每一行都会命中书名。"""
    parts = [
        row.get("original_text"),
        row.get("summary"),
        row.get("context_note"),
        row.get("event_title"),
        row.get("fact_tags"),
        row.get("time_label"),
        row.get("place"),
        row.get("chapter"),
        row.get("section"),
        row.get("answerable_questions"),
        row.get("speaker"),
    ]
    return compact("".join(str(part or "") for part in parts))


@dataclass
class IndexedEntry:
    row: dict
    tokens: set[str]
    summary_tokens: set[str]
    title_tokens: set[str]
    original_compact: str
    summary_compact: str
    blob: str


class BM25:
    def __init__(self, documents: list[list[str]]):
        self.documents = documents
        self.count = len(documents)
        self.lengths = [len(doc) for doc in documents]
        self.average = (sum(self.lengths) / self.count) if self.count else 0.0
        self.frequency: list[Counter[str]] = [Counter(doc) for doc in documents]
        document_frequency: Counter[str] = Counter()
        for freq in self.frequency:
            document_frequency.update(freq.keys())
        self.document_frequency = document_frequency

    def score(self, query_tokens: list[str], index: int, *, k1: float = 1.5, b: float = 0.75) -> float:
        if not self.count or not query_tokens:
            return 0.0
        score = 0.0
        length = self.lengths[index]
        frequencies = self.frequency[index]
        for token in query_tokens:
            freq = frequencies.get(token, 0)
            if not freq:
                continue
            seen = self.document_frequency.get(token, 0)
            idf = math.log(1 + (self.count - seen + 0.5) / (seen + 0.5))
            denominator = freq + k1 * (1 - b + b * length / (self.average or 1))
            score += idf * (freq * (k1 + 1)) / denominator
        return score


class LocalChineseRetriever:
    """限制：同义词、隐喻和只在英文分词里才自然的词序，这一版都不能靠语义补上。"""

    provider_id = "local"

    def __init__(self, db: Database):
        self.db = db
        self.entries: list[IndexedEntry] = []
        self.reload()

    def status_message(self) -> str:
        return (
            f"本机中文检索，已索引 {len(self.entries)} 条。"
            "能按词语、原句片段和邻近段落查找；换一种说法、或资料里没写过的词，可能找不到。"
            "这不是向量语义检索。"
        )

    def reload(self) -> None:
        rows = self.db.query("SELECT * FROM entries ORDER BY source_id, row_number, id")
        indexed: list[IndexedEntry] = []
        for row in rows:
            item = dict(row)
            searchable = "\n".join(
                [
                    item.get("original_text") or "",
                    item.get("summary") or "",
                    item.get("event_title") or "",
                    item.get("context_note") or "",
                    item.get("fact_tags") or "",
                    item.get("time_label") or "",
                    item.get("place") or "",
                    item.get("chapter") or "",
                    item.get("section") or "",
                    item.get("answerable_questions") or "",
                ]
            )
            all_tokens = set(tokenize(searchable))
            summary_tokens = set(tokenize(item.get("summary") or ""))
            title_tokens = set(
                tokenize(" ".join([item.get("event_title") or "", item.get("chapter") or "", item.get("section") or ""]))
            )
            indexed.append(
                IndexedEntry(
                    row=item,
                    tokens=all_tokens,
                    summary_tokens=summary_tokens,
                    title_tokens=title_tokens,
                    original_compact=compact(item.get("original_text") or ""),
                    summary_compact=compact(item.get("summary") or ""),
                    blob=passage_blob(item),
                )
            )
        self.entries = indexed
        self._original_bm25 = BM25([list(item.tokens - item.summary_tokens) or list(item.tokens) for item in indexed])
        self._summary_bm25 = BM25([list(item.summary_tokens) for item in indexed])
        self._title_bm25 = BM25([list(item.title_tokens) for item in indexed])

    def search(self, query: str, *, limit: int = 4) -> list[Evidence]:
        if not self.entries or not query.strip():
            return []
        query_tokens = [token for token in tokenize(query) if token not in STOPWORDS]
        specific = [token for token in query_tokens if token not in GENERIC and len(token) >= 2]
        ranked: list[tuple[float, list[str], str, IndexedEntry]] = []
        for index, item in enumerate(self.entries):
            score = self._original_bm25.score(query_tokens, index)
            score += 0.45 * self._summary_bm25.score(query_tokens, index)
            score += 0.25 * self._title_bm25.score(query_tokens, index)
            matched: list[str] = []
            basis = "none"
            for token in specific or query_tokens:
                in_original = token in item.original_compact
                in_summary = token in item.summary_tokens or token in item.summary_compact
                in_blob = token in item.blob
                questions = compact(item.row.get("answerable_questions") or "")
                if in_original:
                    matched.append(token)
                    score += 1.2 + min(len(token), 12) * 0.08
                    basis = "both" if basis == "summary" else "original"
                elif in_summary:
                    matched.append(token)
                    score += 0.35
                    if basis == "none":
                        basis = "summary"
                elif in_blob:
                    matched.append(token)
                    score += 0.8
                    if basis == "none":
                        basis = "metadata"
                if token in questions:
                    score += 1.5
            if basis == "none" and score > 0:
                basis = "related"
            if score > 0:
                ranked.append((score, matched, basis, item))
        ranked.sort(key=lambda item: item[0], reverse=True)
        top = ranked[: max(limit, 8)]
        evidences = [self._to_evidence(score, matched, basis, item) for score, matched, basis, item in top]
        return self._with_neighbors(evidences)[:limit]

    def candidate_pool(self, query: str, *, limit: int = 8) -> list[Evidence]:
        return self.search(query, limit=limit)

    def _to_evidence(self, score: float, matched: list[str], basis: str, item: IndexedEntry) -> Evidence:
        row = item.row
        return Evidence(
            entry_id=row["id"],
            source_id=row["source_id"],
            original_text=row.get("original_text") or "",
            summary=row.get("summary") or "",
            context_note=row.get("context_note") or "",
            source_title=row.get("source_title") or "",
            collection_no=row.get("collection_no") or "",
            volume=row.get("volume") or row.get("volume_label") or "",
            page=row.get("page") or "",
            paragraph_no=row.get("paragraph_no") or "",
            chapter=row.get("chapter") or "",
            section=row.get("section") or "",
            author=row.get("author") or "",
            speaker=row.get("speaker") or "",
            speech_kind=row.get("speech_kind") or "unknown",
            narrative_level=row.get("narrative_level") or "",
            event_title=row.get("event_title") or "",
            fact_tags=row.get("fact_tags") or "",
            time_label=row.get("time_label") or "",
            place=row.get("place") or "",
            review_status=row.get("review_status") or "pending",
            is_demo=bool(row.get("is_demo")),
            can_quote=bool(row.get("can_quote")),
            score=round(score, 4),
            matched_terms=matched,
            match_basis=basis if basis != "none" else "related",
            provider=self.provider_id,
        )

    def _with_neighbors(self, evidences: list[Evidence]) -> list[Evidence]:
        by_source: dict[str, list[IndexedEntry]] = defaultdict(list)
        for item in self.entries:
            by_source[item.row["source_id"]].append(item)
        positions = {
            item.row["id"]: index for group in by_source.values() for index, item in enumerate(group)
        }
        for evidence in evidences:
            group = by_source.get(evidence.source_id, [])
            index = positions.get(evidence.entry_id)
            if index is None:
                continue
            for placement, neighbor_index in (("before", index - 1), ("after", index + 1)):
                if neighbor_index < 0 or neighbor_index >= len(group):
                    continue
                neighbor = group[neighbor_index].row
                if neighbor.get("chapter") != evidence.chapter or neighbor.get("section") != evidence.section:
                    continue
                evidence.neighbors.append(
                    NeighborPassage(
                        entry_id=neighbor["id"],
                        placement=placement,
                        original_text=neighbor.get("original_text") or "",
                        summary=neighbor.get("summary") or "",
                        page=neighbor.get("page") or "",
                        paragraph_no=neighbor.get("paragraph_no") or "",
                    )
                )
        return evidences
