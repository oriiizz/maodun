"""检索命中之后，再看材料本身够不够回答。"""

from __future__ import annotations

import re

from app.knowledge.querynorm import distinctive_tokens

_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_LIMIT_MARK = ("不能", "不要", "未解决", "分歧", "待核", "不得", "不可", "节录", "不能称", "不是本人", "不要当成")


def answerable_text(hit: dict) -> str:
    if hit.get("kind") == "museum":
        return "\n".join(
            part
            for part in (hit.get("title"), hit.get("barcode"), hit.get("visitor_text"), hit.get("quote_speaker"))
            if part
        )
    if hit.get("kind") == "basics":
        return "\n".join(part for part in (hit.get("title"), hit.get("body")) if part)
    quote = (hit.get("quote") or {}).get("effective") or ""
    summary = (hit.get("summary") or {}).get("effective") or ""
    title = (hit.get("title") or {}).get("effective") or ""
    time_text = "" if hit.get("chronology_unresolved") else (hit.get("event_time") or "")
    return "\n".join(
        part
        for part in (
            title,
            summary,
            quote,
            hit.get("source_book"),
            hit.get("people"),
            hit.get("place"),
            time_text,
            hit.get("speaker"),
            hit.get("info_nature"),
        )
        if part
    )


def matching_basics(units: list[dict], question: str, *, limit: int = 2) -> list[dict]:
    """基础知识按正文是否含问题要点补入，避免只被高分史料挤掉。"""

    tokens = distinctive_tokens(question)
    if not tokens:
        return []
    chosen = []
    for unit in units:
        if unit.get("is_source_list") or unit.get("kind") != "basics":
            continue
        text = answerable_text(unit)
        if any(token and token in text for token in tokens):
            chosen.append(unit)
        if len(chosen) >= limit:
            break
    return chosen


_DRAFT = re.compile(r"提要|大纲|设想|未定稿|早期方案|删改方案")
_PROCESS_ASK = re.compile(r"提要|大纲|怎么写|怎样写|创作|修改|构思|书名|过程")
_CONTENT_ASK = re.compile(r"怎样的人|主要写|写了什么|写些什么|什么样|人物")
_SIDE_TOPIC = re.compile(r"书名|改题|题名|脱稿|酝酿|查禁|出版|写跋|跋|结局|采纳")


def is_draft_material(hit: dict) -> bool:
    """创作提要、大纲和早期设想。不能自动当成最终小说情节。"""

    if hit.get("kind") != "evidence":
        return False
    nature = hit.get("info_nature") or ""
    return bool(_DRAFT.search("\n".join([_text(hit.get("title")), _text(hit.get("summary")), nature])))


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("effective") or "")
    return str(value or "")


def _about_writing_process(hit: dict) -> bool:
    if hit.get("kind") == "basics":
        return False
    return bool(_SIDE_TOPIC.search("\n".join([_text(hit.get("title")), _text(hit.get("summary"))])))


def for_this_question(question: str, hits: list[dict]) -> list[dict]:
    if _PROCESS_ASK.search(question or "") or not _CONTENT_ASK.search(question or ""):
        return hits
    return [hit for hit in hits if not is_draft_material(hit) and not _about_writing_process(hit)]


def select_supported(question: str, hits: list[dict]) -> tuple[list[dict], list[str]]:
    tokens = distinctive_tokens(question)
    if not tokens:
        return [], []
    usable = []
    covered: set[str] = set()
    for hit in hits:
        text = answerable_text(hit)
        got = [token for token in tokens if token and token in text]
        if not got:
            continue
        usable.append(hit)
        covered.update(got)
    missing = [token for token in tokens if token not in covered]
    return usable, missing


def limit_note(hit: dict) -> str:
    blob = _URL.sub("", "\n".join([hit.get("limit_theme") or "", hit.get("limit_chapter") or ""]))
    kept = []
    for piece in re.split(r"[。\n]", blob):
        sentence = piece.strip()
        if sentence and any(mark in sentence for mark in _LIMIT_MARK):
            kept.append(sentence)
    return "。".join(kept[:6])
