"""同一会话的整段记忆：完整记录在库里，本轮按预算组织近期原文、较早摘要和相关找回。

DeepSeek 的聊天接口无状态。多轮内容由本模块装进请求，不依赖密钥记住过去。
摘要由程序增量整理，默认不另调模型。原始 turns 仍是权威记录。
"""

from __future__ import annotations

import re
from typing import Any

from app.knowledge.querynorm import distinctive_tokens

# 生成与理解直接看到的近期完整问答。
RECENT_LIMIT = 6
# 超过这个轮次后，更早内容压成摘要段，原文仍可从本会话找回。
SUMMARY_AFTER = 8
# 每一段摘要覆盖多少轮。
SEGMENT_SIZE = 4
# 本轮从更早原文里最多找回几轮。
RECALL_LIMIT = 4
# 已讲内容拼进去重依据时的字数上限。
TOLD_CHAR_LIMIT = 9000

_EARLIER_CUE = re.compile(
    r"前面|先前|早先|早些|刚才谈过|我(?:前面|之前|早先)(?:说|提|讲)|"
    r"那件事|那回|那次谈|还记得|你还记得|上回|上一次谈|"
    r"母亲发|发消息|招聘|家庭教育|幸福吗"
)
_CORRECTION = re.compile(r"不是这个意思|我说的是|搞错了|弄错了|纠正一下|我是说|指的是")
_OPEN_ASK = re.compile(r"还有(?:别的|其他|吗|呢)|后来呢|那之后|为什么|怎么看|幸福")


def turns_from_rows(rows: list[Any]) -> list[dict]:
    """把数据库里的轮次整理成问答用的结构。"""

    turns = []
    for row in rows:
        if hasattr(row, "keys"):
            item = dict(row)
        else:
            item = dict(row)
        spoken = ""
        result = item.get("result_json")
        if isinstance(result, str) and result:
            import json

            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                spoken = str(payload.get("spoken_text") or "")
        elif item.get("spoken"):
            spoken = str(item.get("spoken") or "")
        mode = str(item.get("mode") or "")
        status = str(item.get("status") or "done")
        usable = (
            status == "done"
            and mode not in {"model_error", "no_model", "not_ready"}
            and not spoken.startswith(("这一次没有答成", "资料还没有准备好"))
        )
        question = str(item.get("question") or "")
        if not question and status not in {"done", "running"}:
            continue
        turns.append(
            {
                "id": str(item.get("id") or item.get("turn_id") or ""),
                "question": question,
                "spoken": spoken if usable else spoken,
                "usable": usable,
                "mode": mode,
                "status": status,
                "created_at": str(item.get("created_at") or ""),
            }
        )
    return [turn for turn in turns if turn["question"] or turn["spoken"]]


def build_context(all_turns: list[dict], question: str, focus: dict | None = None) -> dict:
    """为本轮组织可用上下文。

    返回 working（交给问答主流程的轮次列表）、pack（写入请求与诊断）、focus（带上更新后的记忆状态）。
    """

    focus = dict(focus or {})
    usable = [turn for turn in all_turns if turn.get("question")]
    recent = usable[-RECENT_LIMIT:]
    older = usable[:-RECENT_LIMIT] if len(usable) > RECENT_LIMIT else []
    memory = dict(focus.get("memory") or {})
    segments = list(memory.get("segments") or [])
    if len(usable) >= SUMMARY_AFTER:
        segments = refresh_segments(usable[:-RECENT_LIMIT], segments)
    recalled = recall_turns(older, question, focus, limit=RECALL_LIMIT)
    if _EARLIER_CUE.search(question or "") and not recalled and older:
        # 明确回指更早内容时，至少带回最早几轮里词面最贴的。
        recalled = recall_turns(older, question, focus, limit=RECALL_LIMIT, loose=True)

    seen = {turn.get("id") or f"{turn['question']}|{turn.get('spoken', '')[:20]}" for turn in recent}
    working = list(recent)
    for turn in recalled:
        key = turn.get("id") or f"{turn['question']}|{turn.get('spoken', '')[:20]}"
        if key in seen:
            continue
        seen.add(key)
        working.append(turn)
    working.sort(key=lambda item: item.get("created_at") or "")

    told_text = _session_told_text(usable)
    open_topics = list(memory.get("open_topics") or [])
    corrections = list(memory.get("corrections") or [])
    if _CORRECTION.search(question or ""):
        note = (question or "").strip()[:80]
        if note and note not in corrections:
            corrections.append(note)
            corrections = corrections[-8:]

    pack = {
        "recent": [_public_turn(turn) for turn in recent],
        "earlier_summary": segments,
        "recalled": [_public_turn(turn) for turn in recalled],
        "open_topics": open_topics,
        "corrections": corrections,
        "told_text": told_text,
        "meta": {
            "total_turns": len(usable),
            "recent_count": len(recent),
            "older_count": len(older),
            "recalled_ids": [turn.get("id") or "" for turn in recalled],
            "summary_segments": len(segments),
            "summary_through": segments[-1]["to"] if segments else "",
        },
    }
    focus["memory"] = {
        "segments": segments,
        "open_topics": open_topics,
        "corrections": corrections,
        "summary_through": pack["meta"]["summary_through"],
        "total_turns": len(usable),
    }
    return {"working": working, "pack": pack, "focus": focus}


def update_after_answer(
    focus: dict,
    all_turns: list[dict],
    *,
    question: str,
    spoken: str,
    mode: str,
    reading: dict | None = None,
) -> dict:
    """本轮结束后更新派生记忆。不改原始 turns。"""

    focus = dict(focus or {})
    memory = dict(focus.get("memory") or {})
    usable = [turn for turn in all_turns if turn.get("question")]
    # 把本轮也算进去，便于摘要覆盖到最新滑出窗口的内容。
    current = {
        "id": "",
        "question": question,
        "spoken": spoken,
        "usable": mode not in {"model_error", "no_model", "not_ready"},
        "mode": mode,
        "created_at": "9999",
    }
    combined = usable + [current]
    if len(combined) >= SUMMARY_AFTER:
        memory["segments"] = refresh_segments(combined[:-RECENT_LIMIT], list(memory.get("segments") or []))
        memory["summary_through"] = memory["segments"][-1]["to"] if memory["segments"] else ""
    memory["total_turns"] = len(combined)
    open_topics = list(memory.get("open_topics") or [])
    topic = _topic_label(question, reading or {})
    if topic and _OPEN_ASK.search(question or ""):
        if topic not in open_topics:
            open_topics.append(topic)
    if topic and mode in {"generated", "continue", "museum", "boundary"} and not _OPEN_ASK.search(question or ""):
        open_topics = [item for item in open_topics if item != topic]
    memory["open_topics"] = open_topics[-12:]
    if _CORRECTION.search(question or ""):
        corrections = list(memory.get("corrections") or [])
        note = question.strip()[:80]
        if note and note not in corrections:
            corrections.append(note)
        memory["corrections"] = corrections[-8:]
    focus["memory"] = memory
    return focus


def refresh_segments(older_turns: list[dict], previous: list[dict] | None = None) -> list[dict]:
    """按固定窗口把更早轮次收成摘要段。覆盖声明范围内的全部轮次。"""

    if not older_turns:
        return []
    segments = []
    for start in range(0, len(older_turns), SEGMENT_SIZE):
        chunk = older_turns[start : start + SEGMENT_SIZE]
        topics = []
        digests = []
        for turn in chunk:
            label = _topic_label(turn.get("question") or "", {})
            if label and label not in topics:
                topics.append(label)
            digests.append(_digest(turn))
        first_id = chunk[0].get("id") or f"i{start}"
        last_id = chunk[-1].get("id") or f"i{start + len(chunk) - 1}"
        segments.append(
            {
                "from": first_id,
                "to": last_id,
                "count": len(chunk),
                "topics": topics[:6],
                "text": "｜".join(digests),
            }
        )
    # 若上一版摘要里有用户纠正，保留在 topics 旁的标记由 corrections 字段管，这里只重建覆盖。
    _ = previous
    return segments


def recall_turns(
    older: list[dict],
    question: str,
    focus: dict | None = None,
    *,
    limit: int = RECALL_LIMIT,
    loose: bool = False,
) -> list[dict]:
    """从本会话更早的原文里找回与当前问题相关的轮次。"""

    if not older:
        return []
    focus = focus or {}
    tokens = distinctive_tokens(question or "")
    person = str(focus.get("person") or "")
    work = str(focus.get("work") or "").strip("《》")
    for extra in (person, work):
        if extra and extra not in tokens:
            tokens.append(extra)
    for segment in (focus.get("memory") or {}).get("segments") or []:
        for topic in segment.get("topics") or []:
            if topic and topic not in tokens and topic in (question or ""):
                tokens.append(topic)
    if not tokens and not loose:
        return []

    ranked = []
    for order, turn in enumerate(older):
        blob = f"{turn.get('question') or ''}\n{turn.get('spoken') or ''}"
        score = sum(3 if token in blob else 0 for token in tokens)
        if person and person in blob:
            score += 2
        if loose and score == 0 and tokens:
            # 宽松模式：任一轮与任一摘要主题有交集也算。
            score = sum(1 for token in tokens if token and len(token) >= 2 and token in blob)
        if score <= 0:
            continue
        ranked.append((score, order, turn))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    picked = [item[2] for item in ranked[:limit]]
    picked.sort(key=lambda item: item.get("created_at") or "")
    return picked


def conversation_view(pack: dict | None, turns: list[dict] | None = None) -> dict:
    """放进生成与理解请求的会话视图。历史不是史实证据。"""

    pack = pack or {}
    view: dict[str, Any] = {}
    recent = pack.get("recent")
    if recent is None and turns:
        recent = [_public_turn(turn) for turn in turns[-3:]]
    if recent:
        view["近期问答（原文，用来理解指代和承接；不是史实证据）"] = recent
    if pack.get("earlier_summary"):
        view["更早内容摘要（覆盖其声明的轮次；细节以找回的原文为准；不是史实证据）"] = pack["earlier_summary"]
    if pack.get("recalled"):
        view["本轮找回的较早原文（本会话内；不是史实证据）"] = pack["recalled"]
    if pack.get("corrections"):
        view["用户后来的澄清（以澄清后的意思为准）"] = pack["corrections"]
    if pack.get("open_topics"):
        view["尚未收住的话题"] = pack["open_topics"]
    return view


def _session_told_text(turns: list[dict]) -> str:
    parts = []
    total = 0
    for turn in turns:
        if not turn.get("usable"):
            continue
        spoken = (turn.get("spoken") or "").strip()
        if not spoken:
            continue
        if total + len(spoken) > TOLD_CHAR_LIMIT:
            remain = TOLD_CHAR_LIMIT - total
            if remain > 40:
                parts.append(spoken[:remain])
            break
        parts.append(spoken)
        total += len(spoken)
    return "\n".join(parts)


def _digest(turn: dict) -> str:
    question = re.sub(r"\s+", "", (turn.get("question") or ""))[:36]
    if turn.get("usable") and turn.get("spoken"):
        answer = re.sub(r"\s+", "", turn["spoken"])[:48]
        return f"问{question}→答{answer}"
    return f"问{question}→（未形成可沿用的回答）"


def _topic_label(question: str, reading: dict) -> str:
    person = str(reading.get("person") or "")
    work = str(reading.get("work") or "")
    if person:
        return person
    if work:
        return work
    text = question or ""
    for label, pattern in (
        ("家庭", r"家庭|父母|母亲|父亲|孩子|幸福"),
        ("阅读写作", r"写作|阅读|读书|作文"),
        ("馆藏", r"这封信|手稿|藏品|DX\d+|DG\d+"),
    ):
        if re.search(pattern, text):
            return label
    tokens = distinctive_tokens(text)
    return tokens[0] if tokens else ""


def _public_turn(turn: dict) -> dict:
    item = {"问": turn.get("question") or ""}
    if turn.get("usable") and turn.get("spoken"):
        item["答"] = turn["spoken"]
    else:
        item["答"] = "这一轮没有形成可沿用的回答，不能当作事实。"
    if turn.get("id"):
        item["轮次"] = turn["id"]
    return item
