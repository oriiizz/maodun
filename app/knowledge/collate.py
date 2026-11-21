"""同一条目的两张表合成一个节点。只采用限制文字里写明范围的修订。"""

from __future__ import annotations

from app.knowledge.permissions import chronology_unresolved, rag_excluded, style_allowed
from app.knowledge.textutil import clean, comparable


def _mentions(limit: str, words: tuple[str, ...]) -> bool:
    return any(word in (limit or "") for word in words)


def decide_field(chapter_value: str, theme_value: str, limit: str, field: str) -> dict:
    """field 为 title、summary 或 quote。"""

    chapter_value = clean(chapter_value)
    theme_value = clean(theme_value)
    limit = limit or ""
    if comparable(chapter_value) == comparable(theme_value):
        return {
            "chapter": chapter_value,
            "theme": theme_value,
            "effective": theme_value or chapter_value,
            "state": "same",
            "reason": "两表该字段在忽略空白和引号后一致。",
        }
    if rag_excluded(limit):
        return {
            "chapter": chapter_value,
            "theme": theme_value,
            "effective": "",
            "state": "excluded",
            "reason": "整条不进入正式检索，校字说明不解除这道限制。",
        }
    about = {
        "title": ("标题", "题名"),
        "summary": ("概括", "口径", "系年", "日期"),
        "quote": ("摘录", "原句", "句号", "标点", "校字"),
    }[field]
    mentioned = _mentions(limit, about)
    unresolved = chronology_unresolved(limit) or "未解决" in limit
    adopted = _mentions(limit, ("按实页恢复", "换回", "纠正", "校字", "须撤回"))
    if field == "quote" and adopted and mentioned and not ("摘录" in limit and "不能称已解决" in limit and "摘录" in limit):
        if not (unresolved and _mentions(limit, ("摘录不能", "原文不能", "引文不能"))):
            return {
                "chapter": chapter_value,
                "theme": theme_value,
                "effective": theme_value,
                "state": "theme_revision",
                "reason": "主题表写明了这一处摘录的修订范围，采用主题表原文。",
            }
    if unresolved and (mentioned or field == "summary"):
        return {
            "chapter": chapter_value,
            "theme": theme_value,
            "effective": "",
            "state": "unresolved",
            "reason": "限制文字保留了未解决的分歧，这个字段不用于定论或逐字引用。",
        }
    if adopted and mentioned:
        return {
            "chapter": chapter_value,
            "theme": theme_value,
            "effective": theme_value,
            "state": "theme_revision",
            "reason": "主题表写明了该字段的局部修订，采用主题表文字。",
        }
    return {
        "chapter": chapter_value,
        "theme": theme_value,
        "effective": "",
        "state": "unresolved",
        "reason": "两表该字段不一致，又没有写明对应哪一处修订，所以不拿来下定论。",
    }


def build_node(chapter: dict, theme: dict) -> dict:
    limit = clean(theme.get("信息来源与使用限制"))
    chapter_limit = clean(chapter.get("上下文与使用限制"))
    title = decide_field(chapter.get("条目标题", ""), theme.get("条目标题", ""), limit, "title")
    summary = decide_field(chapter.get("内容概括", ""), theme.get("内容概括", ""), limit, "summary")
    quote = decide_field(chapter.get("原文摘录", ""), theme.get("原文摘录", ""), limit, "quote")
    excluded = rag_excluded(limit, chapter_limit)
    return {
        "title": title,
        "summary": summary,
        "quote": quote,
        "excluded": excluded,
        "chronology_unresolved": chronology_unresolved(limit) or chronology_unresolved(chapter_limit),
        "style_ok": style_allowed(limit) and style_allowed(chapter_limit) and not excluded,
        "limit_theme": limit,
        "limit_chapter": chapter_limit,
    }
