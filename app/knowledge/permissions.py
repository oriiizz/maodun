"""从限制原文判断能不能进入访客检索，以及字段还能怎么用。"""

from __future__ import annotations

RAG_BAN = "不进入正式RAG"
UNRESOLVED_MARKERS = (
    "不能称已解决",
    "不作已解决",
    "保留双方",
    "系年分歧",
    "日期分歧",
    "分歧",
    "未解决",
)
QUOTE_ADOPTION = ("按实页恢复", "换回", "纠正", "校字", "须撤回")
STYLE_BLOCK = ("适用性：否", "暂不适用", "不作茅盾本人语言风格")


def rag_excluded(*texts: str) -> bool:
    blob = "\n".join(text or "" for text in texts)
    return RAG_BAN in blob


def chronology_unresolved(limit: str) -> bool:
    text = limit or ""
    return any(marker in text for marker in ("不能称已解决", "不作已解决", "系年分歧", "日期分歧", "保留双方"))


def style_allowed(limit: str) -> bool:
    text = limit or ""
    return not any(marker in text for marker in STYLE_BLOCK)


def direct_self_statement(nature: str, speaker: str) -> bool:
    nature = nature or ""
    speaker = speaker or ""
    return "直接自述" in nature and "茅盾" in speaker and "待逐句" not in speaker
