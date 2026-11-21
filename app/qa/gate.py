"""证据检查。检索到文字，还不等于可以拿来回答。"""

from __future__ import annotations

import re

from app.contracts import Evidence
from app.retrieval.local_chinese import GENERIC, STOPWORDS, IndexedEntry, compact
from app.retrieval.tokenize import tokenize

GLUE = {
    "页码", "哪一页", "一页", "哪页", "出自", "出处", "告诉", "知道", "记得", "说过",
    "提到", "写过", "叫做", "是什么", "哪一年", "哪年", "什么时候", "何时", "何年",
    "多少", "几岁", "哪里", "哪儿",
}
BIO_PATTERN = re.compile(r"是谁|何人|原名|本名|哪里人|哪儿人|出生|故乡|家乡|生于|生在|何年出生")
POSTHUMOUS_PATTERN = re.compile(r"去世之?后|逝世之?后|死后|身后|198[2-9]\s*年|199\d\s*年|20[0-2]\d\s*年")
SUBJECT_PATTERN = re.compile(r"茅盾|沈雁冰|沈德鸿")

REFUSAL = "目前资料中没有找到相关记载。如果您指的是某部作品、某一年或某一件事，可以说得再具体一些。"
POSTHUMOUS = "这是我去世以后的事情。目前资料里没有这类记载，我也不能把没有留下的记录说成事实。"


def specific_tokens(question: str) -> list[str]:
    tokens = []
    for token in tokenize(question):
        if token in STOPWORDS or token in GENERIC or token in GLUE or len(token) < 2:
            continue
        if token.startswith(("哪", "怎")):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _entry_text(item: IndexedEntry) -> str:
    return item.blob


def _hit_text(hit: Evidence) -> str:
    return compact("".join([hit.original_text, hit.summary, hit.event_title, hit.context_note, hit.fact_tags, hit.chapter, hit.section]))


def _bio_score(hit: Evidence) -> int:
    blob = "".join([hit.chapter, hit.section, hit.event_title, hit.fact_tags, hit.original_text, hit.summary])
    score = 0
    for word in ("原名", "本名", "乌镇", "童年", "出生", "沈德鸿", "沈雁冰", "家世"):
        if word in blob:
            score += 2
    return score


def select_evidence(
    question: str,
    hits: list[Evidence],
    corpus: list[IndexedEntry] | None = None,
) -> tuple[list[Evidence], str]:
    """返回可以支撑回答的证据，以及证据不足时要说的那句话。"""
    tokens = specific_tokens(question)
    posthumous = POSTHUMOUS_PATTERN.search(question) is not None

    def refuse() -> tuple[list[Evidence], str]:
        return [], POSTHUMOUS if posthumous else REFUSAL

    if corpus is not None:
        texts = [_entry_text(item) for item in corpus]

        def frequency(token: str) -> int:
            return sum(1 for text in texts if token in text)
    else:
        hit_texts = [_hit_text(hit) for hit in hits]

        def frequency(token: str) -> int:
            return sum(1 for text in hit_texts if token in text)

    known = [token for token in tokens if frequency(token) > 0]
    unknown = [token for token in tokens if frequency(token) == 0 and token not in GLUE]
    if unknown or (tokens and not known):
        return refuse()

    if not known:
        if BIO_PATTERN.search(question) and SUBJECT_PATTERN.search(question):
            related = [hit for hit in hits if SUBJECT_PATTERN.search(_hit_text(hit))]
            related.sort(key=_bio_score, reverse=True)
            if related and _bio_score(related[0]) > 0:
                return related[:3], ""
            if related:
                return related[:1], ""
        return refuse()

    rarest = min(known, key=lambda token: (frequency(token), -len(token)))
    need = len(known) if len(known) <= 3 else max(2, (len(known) + 1) // 2)
    kept: list[Evidence] = []
    for hit in hits:
        text = _hit_text(hit)
        matched = [token for token in known if token in text or token in hit.matched_terms]
        if rarest not in text and rarest not in hit.matched_terms:
            continue
        if len(matched) < need:
            continue
        kept.append(hit)
    if not kept:
        return refuse()
    return kept[:4], ""
