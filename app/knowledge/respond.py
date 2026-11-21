"""本地检索之后再生成。访客只收到检查过的同一份正文。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import ROOT
from app.knowledge.dialogue import (
    CLARIFY,
    LENGTH_CLARIFY,
    PERSON_CLARIFY,
    asks_identity,
    asks_correction,
    asks_which_person,
    is_contextual,
    is_self_intro,
    length_preference,
    next_step_reply,
    open_chat,
    peel_preface,
    social_reply,
)
from app.knowledge.guard import (
    IDENTITY,
    NOT_READY,
    POLITICS_HOLD,
    QIN_HOLD,
    TECHNICAL,
    _ANAPHORA,
    _CONTENT_ASK,
    _INJECTION,
    _WHO_SAID,
    museum_cue,
    politics_question,
    qin_question,
    qin_reply,
    review_output,
)
from app.knowledge.querynorm import distinctive_tokens, normalize_query
from app.knowledge.index import KnowledgeIndex
from app.knowledge.stylebank import style_message
from app.knowledge.support import for_this_question, is_draft_material, limit_note, matching_basics, select_supported
from app.llm.errors import ModelCallError

_RULES = ROOT / "data" / "persona" / "system_rules.txt"
_BAD_TURN = {"model_error", "no_model", "not_ready"}
_BAD_TEXT = ("这一次没有答成", "资料还没有准备好")
GAP = "这一问，我还不能肯定。"


def load_rules() -> str:
    return _RULES.read_text(encoding="utf-8").strip()


def public_payload(spoken: str, *, provider: str, model_called: bool) -> dict:
    return {
        "spoken_text": spoken,
        "system_notice": "",
        "citations": [],
        "evidence": [],
        "rejected": [],
        "control": {"mood": "calm", "gesture": "explain" if spoken else "decline", "note": ""},
        "model_called": model_called,
        "provider": provider,
    }


def answer_from_corpus(
    index: KnowledgeIndex | None,
    question: str,
    *,
    model,
    history: list,
    focus: dict | None,
    provider_name: str,
) -> tuple[dict, dict, dict]:
    """返回公开结果、审计信息和更新后的焦点。"""

    question = peel_preface((question or "").strip())
    focus = dict(focus or {})
    turns = _turns(history)
    audit = {"mode": "", "hit_ids": [], "notice": ""}
    ready = index is not None and (index.nodes or index.items or index.units)

    if politics_question(question, focus.get("hold") == "politics"):
        focus["hold"] = "politics"
        audit["mode"] = "politics"
        return public_payload(POLITICS_HOLD, provider=provider_name, model_called=False), audit, focus
    if qin_question(question, focus.get("hold") == "qin"):
        focus["hold"] = "qin"
        audit["mode"] = "boundary"
        return public_payload(qin_reply(question), provider=provider_name, model_called=False), audit, focus
    focus.pop("hold", None)

    if _INJECTION.search(question) and not _literary_remainder(question):
        audit["mode"] = "injection"
        return public_payload("我们还是谈谈作品和阅读吧。", provider=provider_name, model_called=False), audit, focus

    social = social_reply(question)
    if social:
        audit["mode"] = "social"
        return public_payload(social, provider=provider_name, model_called=False), audit, focus
    contextual = is_contextual(question)
    topic = str(focus.get("topic") or "")
    step = next_step_reply(question, topic)
    if step:
        audit["mode"] = "clarify"
        return public_payload(step, provider=provider_name, model_called=False), audit, focus
    if contextual and not topic and not _barcode_in(question):
        audit["mode"] = "clarify"
        spoken = LENGTH_CLARIFY if social == "" else CLARIFY
        return public_payload(spoken, provider=provider_name, model_called=False), audit, focus
    if asks_which_person(question) and not _referent(turns):
        audit["mode"] = "clarify"
        return public_payload(PERSON_CLARIFY, provider=provider_name, model_called=False), audit, focus

    if asks_correction(question):
        previous = _previous_spoken(turns)
        note = (
            f"上一句实际是：{previous or '（没有上一句）'}。"
            "先对上对方的疑问。上一句若没有那个问题，就说明它说的是什么，不要用“我没有”或“刚才并没有”起头。"
            "若确实把没有材料的记忆说成了亲历，只改那一点。姓名和籍贯仍可以用我来讲。"
        )
        hits, missing = _search_hits(index, normalize_query(question), question, None) if ready else ([], [])
        audit["hit_ids"] = [_hit_id(hit) for hit in hits]
        payload, audit, focus = _from_model(
            question, hits, turns, False, missing, model, provider_name, audit, focus,
            contextual=False, follow_note=note,
        )
        if previous and payload["spoken_text"] in {GAP, TECHNICAL}:
            payload["spoken_text"] = f"上一句是这么说的：{previous}"
            audit["mode"] = "correction"
        return payload, audit, focus

    if open_chat(question):
        audit["hit_ids"] = []
        focus["topic"] = question
        return _from_model(
            question, [], turns, False, [], model, provider_name, audit, focus,
            contextual=False,
            historical=False,
            follow_note="这是阅读感受或一般写作建议。直接回应读者，不要改成作品讲解或生平。不要说成我当年的经验、主张或原话，也不要因为没有史料而追加不确定的句子。",
        )

    if is_self_intro(question):
        hits = _intro_hits(index)
        audit["hit_ids"] = [_hit_id(hit) for hit in hits]
        focus["topic"] = question
        return _from_model(
            question, hits, turns, False, [], model, provider_name, audit, focus,
            contextual=False,
            follow_note="这是人物自我介绍。用第一人称说姓名和所做的文学工作。不要解释数字化身，不要说不是本人。",
        )

    if not ready:
        audit["mode"] = "not_ready"
        audit["notice"] = "知识库还没有导入。"
        return public_payload(NOT_READY, provider=provider_name, model_called=False), audit, focus

    identity = asks_identity(question)
    barcode = _barcode_in(question) or (focus.get("barcode") if _ANAPHORA.search(question) or contextual else "")
    if barcode and index.museum(barcode):
        focus["barcode"] = barcode
        focus["sheet"] = index.museum(barcode)["sheet"]
    elif not museum_cue(question) and not _ANAPHORA.search(question) and not contextual:
        focus.pop("barcode", None)
        focus.pop("sheet", None)

    item = index.museum(focus["barcode"]) if focus.get("barcode") else None
    if item and re.search(r"为什么", question) and re.search(r"写|寄", question) and item.get("scope") != "public_text":
        audit["mode"] = "museum"
        audit["hit_ids"] = [item["item_key"]]
        focus["topic"] = question
        return public_payload(_museum_motive(item), provider=provider_name, model_called=False), audit, focus
    if item and (_CONTENT_ASK.search(question) or _WHO_SAID.search(question) or "介绍" in question):
        spoken = _museum_answer(item, question)
        if spoken:
            if identity and IDENTITY not in spoken:
                spoken = f"{IDENTITY}{spoken}"
            audit["mode"] = "museum"
            audit["hit_ids"] = [item["item_key"]]
            focus["topic"] = question
            return public_payload(spoken, provider=provider_name, model_called=False), audit, focus

    referent = _referent(turns) if asks_which_person(question) else ""
    search_text = normalize_query(referent or (topic if contextual and topic else question))
    if (contextual and topic and topic not in search_text) or (referent and referent not in search_text):
        search_text = normalize_query(f"{referent or topic} {question}")
    hits, missing = _search_hits(index, search_text, question, item if focus.get("barcode") else None)
    audit["hit_ids"] = [_hit_id(hit) for hit in hits]
    if not hits:
        audit["mode"] = "empty"
        spoken = f"{IDENTITY}{GAP}" if identity else _gap_line(question)
        if not contextual:
            focus["topic"] = question
        return public_payload(spoken, provider=provider_name, model_called=False), audit, focus

    follow_note = ""
    if contextual and topic:
        follow_note = "上一轮回答只帮助理解指代，不是事实。这一问若是接着上一件经历，就谈那件事之后，不要改答成另一部作品的创作过程。"
    return _from_model(
        question, hits, turns, identity, missing, model, provider_name, audit, focus,
        contextual=contextual, follow_note=follow_note,
    )


def _search_hits(index: KnowledgeIndex | None, search_text: str, question: str, item: dict | None) -> tuple[list[dict], list[str]]:
    if index is None:
        return [], []
    hits = []
    seen: set[str] = set()
    for hit in matching_basics(index.units, search_text) + index.search(search_text, limit=6):
        key = _hit_id(hit)
        if not key or key in seen:
            continue
        seen.add(key)
        hits.append(hit)
    if item and all(hit.get("barcode") != item.get("barcode") for hit in hits):
        hits = [item, *hits]
    hits = [
        hit
        for hit in hits
        if hit.get("kind") != "museum" or hit.get("barcode") == (item or {}).get("barcode") or _barcode_in(question)
    ]
    kept, missing = select_supported(search_text, hits)
    return for_this_question(question, kept), missing


def _from_model(
    question: str,
    hits: list[dict],
    turns: list[dict],
    identity: bool,
    missing: list[str],
    model,
    provider_name: str,
    audit: dict,
    focus: dict,
    *,
    contextual: bool,
    follow_note: str,
    historical: bool = True,
) -> tuple[dict, dict, dict]:
    if model is None or not model.configured():
        audit["mode"] = "no_model"
        audit["notice"] = "找到了资料，但没有配置 DeepSeek。"
        return public_payload(TECHNICAL, provider=provider_name, model_called=False), audit, focus
    messages = _messages(question, hits, turns, identity, length_preference(question), missing, follow_note, historical)
    spoken, notice, called = _generate(model, messages, _allowed_quotes(hits), question=question, historical=historical)
    spoken = _drop_unearned_memory(spoken, follow_note)
    if identity and spoken and IDENTITY not in spoken:
        spoken = f"{IDENTITY}{spoken}"
    if not contextual:
        focus["topic"] = question
    reserved = spoken in {GAP, TECHNICAL, POLITICS_HOLD, QIN_HOLD} or spoken.startswith(("这一次没有答成", "资料还没有准备好"))
    audit["mode"] = "generated" if called and spoken and not reserved else ("model_error" if called else "empty")
    if notice:
        audit["notice"] = notice
        if not called:
            audit["mode"] = "empty"
    return public_payload(spoken, provider=provider_name, model_called=called), audit, focus


def _referent(turns: list[dict]) -> str:
    previous = ""
    for turn in reversed(turns or []):
        if turn.get("usable") and turn.get("spoken"):
            previous = turn["spoken"]
            break
    if not previous:
        return ""
    skipped = {"茅盾", "沈雁冰", "沈德鸿", "原名", "笔名", "子夜", "数字化身"}
    names = [token for token in distinctive_tokens(previous) if token not in skipped]
    return names[0] if names else ""


def _previous_spoken(turns: list[dict]) -> str:
    for turn in reversed(turns or []):
        if turn.get("usable") and turn.get("spoken"):
            return turn["spoken"]
    return ""


def _memory_claim(text: str) -> bool:
    return bool(re.search(r"亲眼|我记得|我清楚记得|当时的神情|亲历", text or ""))


_HEDGE = {"这一点，我说不准。", "这件事，还是不作断言为好。"}


def _drop_unearned_memory(spoken: str, follow_note: str) -> str:
    if "不要说成我当年" not in (follow_note or "") or not spoken:
        return spoken
    kept = []
    for sentence in re.split(r"(?<=[。！？])", spoken):
        piece = sentence.strip()
        if not piece:
            continue
        if piece in _HEDGE or re.search(r"我自己的经验|我当年的经验|我写作时|我那时", piece):
            continue
        kept.append(sentence)
    return "".join(kept).strip()


def _needs_first_person_sentence(question: str, text: str) -> bool:
    if not re.search(r"原名|本名|他的字|你的字|我的字|字呢|字是什么", question or ""):
        return False
    return bool(re.search(r"茅盾原名|他原名|他字|他于\d|他出生|他1896", text or ""))


def _physical_sentence(item: dict) -> str:
    physical = ""
    for line in (item.get("visitor_text") or "").splitlines():
        if line.startswith("形制记录："):
            physical = line.split("：", 1)[1].strip()
    if not physical:
        return ""
    parts = [part.strip() for part in physical.split("、") if part.strip()]
    pages = next((part for part in parts if part.endswith("页")), "")
    tool = next((part for part in parts if "笔" in part and "信封" not in part), "")
    rest = [part for part in parts if part not in {pages, tool}]
    sentence = ""
    if pages and tool:
        sentence = f"这封信是{pages}，用{tool}写成"
    elif pages:
        sentence = f"这封信是{pages}"
    elif tool:
        sentence = f"这封信用{tool}写成"
    if rest:
        sentence = f"{sentence}，{'，'.join(rest)}" if sentence else "，".join(rest)
    return f"{sentence}。" if sentence else ""


def _museum_motive(item: dict) -> str:
    spoken = "这封信为什么这样写，这里还不能确定。"
    if item.get("scope") in {"catalog", "clue", "candidate", "unknown"}:
        spoken += "具体内容也还不能作确切介绍。"
    return spoken + _physical_sentence(item)


def _brief_name(question: str) -> bool:
    return bool(re.search(r"原名|本名|他的字|你的字|我的字|字呢|字是什么", question or "")) and not re.search(r"为什么", question or "")


def _gap_line(question: str) -> str:
    if re.search(r"天气|股市|票价|开放|预约", question or ""):
        return "这要看眼下的情况，我这里说不上来。"
    return GAP


def _intro_hits(index: KnowledgeIndex | None) -> list[dict]:
    if index is None:
        return []
    wanted = {"B01", "B03", "B06"}
    return [unit for unit in index.units if unit.get("unit_id") in wanted and not unit.get("is_source_list")]


def _failed_generation(historical: bool, kept: str) -> str:
    if kept:
        return kept
    if historical:
        return GAP
    return "你可以说说卡在哪里。"


def _generate(model, messages: list[dict[str, str]], allowed: str, *, question: str = "", historical: bool = True) -> tuple[str, str, bool]:
    try:
        raw = model.complete(messages)
    except ModelCallError as exc:
        return TECHNICAL, exc.notice, True
    spoken, reasons = review_output(raw, allowed)
    if _needs_first_person_sentence(question, spoken or raw):
        reasons.append("基本生平要写成完整的第一人称句子")
    if not historical:
        spoken = _drop_unearned_memory(spoken, "不要说成我当年")
        if not spoken or spoken in _HEDGE:
            reasons.append("一般建议被写成了史料不足")
    if spoken and not reasons:
        return spoken, "", True
    kept = spoken
    if not raw.strip():
        return _failed_generation(historical, ""), "模型没有返回可用正文。", True
    hedge = (
        "若需要史实而材料不足，用完整的一句“这一点，我说不准。”"
        if historical
        else "这是一般建议，不要追加“这一点，我说不准。”"
    )
    retry = messages + [
        {"role": "assistant", "content": raw},
        {
            "role": "user",
            "content": (
                "上一稿不能直接使用，原因："
                + "、".join(dict.fromkeys(reasons))
                + "。请重新写完整的句子，不要残句，不要沿用不合格的说法，也不要靠替换单个代词。"
                + hedge
            ),
        },
    ]
    try:
        second = model.complete(retry)
    except ModelCallError as exc:
        return TECHNICAL, exc.notice, True
    spoken, reasons = review_output(second, allowed)
    if not historical:
        spoken = _drop_unearned_memory(spoken, "不要说成我当年")
    if _needs_first_person_sentence(question, spoken):
        reasons.append("基本生平要写成完整的第一人称句子")
    if spoken and not reasons:
        return spoken, "", True
    if kept:
        return kept, "已去掉不合格的句子。", True
    if "当代政治" in reasons:
        return POLITICS_HOLD, "重写后仍涉及当代政治。", True
    if "私人争议" in reasons:
        return QIN_HOLD, "重写后仍涉及不作展开的私人争议。", True
    return _failed_generation(historical, kept), "重写后仍不合格，已改用完整的保留回答。", True


def _turns(history: list) -> list[dict]:
    turns = []
    for item in history or []:
        if isinstance(item, str):
            turns.append({"question": item, "spoken": "", "usable": False})
            continue
        if not isinstance(item, dict):
            continue
        spoken = str(item.get("spoken") or "")
        mode = str(item.get("mode") or "")
        usable = bool(item.get("usable")) and mode not in _BAD_TURN and not spoken.startswith(_BAD_TEXT)
        turns.append({"question": str(item.get("question") or ""), "spoken": spoken if usable else "", "usable": usable})
    return [turn for turn in turns if turn["question"]]


def _messages(
    question: str,
    hits: list[dict],
    turns: list[dict],
    identity: bool,
    length: str,
    missing: list[str],
    follow_note: str = "",
    historical: bool = True,
) -> list[dict[str, str]]:
    evidence = [_evidence_view(hit, question) for hit in hits]
    prior = []
    for turn in turns[-3:]:
        if turn["usable"] and turn["spoken"]:
            prior.append({"问": turn["question"], "当时的回答（不是事实证据）": turn["spoken"]})
        else:
            prior.append({"问": turn["question"], "当时的回答": "上一轮没有形成可沿用的回答，不能当作事实。"})
    length_text = {
        "detail": "用户要求详细，可以展开，但只说材料里有的内容。",
        "brief": "用户要求简短，用一两句说完。",
    }.get(length, "只回答所问的那一点，不要补未问到的生平、书名由来或创作背景。")
    user = {
        "问题": question,
        "回答篇幅": length_text,
        "需要先说明数字化身身份": identity,
        "先前的对话（只帮助理解指代，不是事实，不能代替本轮材料）": prior,
    }
    if historical and evidence:
        user["参考材料"] = evidence
    if historical and missing:
        user["材料没有覆盖、不要补写的方面"] = missing
    if follow_note:
        user["这一问怎样理解"] = follow_note
    if re.search(r"他的字|你的字|我的字|字呢|字是什么", question or "") and not re.search(r"原名|本名|笔名", question or ""):
        user["基本身份的说法"] = "只回答字，写成完整的“我字……”。不要补原名、笔名和生卒。"
    elif re.search(r"原名|本名", question or "") and not re.search(r"字呢|字是什么|笔名|出生|哪一年", question or ""):
        user["基本身份的说法"] = "只回答原名，写成完整的“我原名……”。不要补字、笔名、出生和逝世。"
    elif re.search(r"原名|本名", question or ""):
        user["基本身份的说法"] = "用完整的第一人称句子回答问到的项目。不要写成“茅盾原名”或“他出生”。"
    if re.search(r"怎样的人|主要写|写了什么|写些什么|什么样", question or ""):
        user["作品问题"] = "只回答所问的内容。提要、大纲和早期设想不要说成最终小说情节。不要补书名由来和创作过程。有情节依据时，分析用“从这些行为来看”，不要超出那些行为。"
    return [
        {"role": "system", "content": load_rules()},
        {"role": "system", "content": style_message(question, brief=_brief_name(question))},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def _evidence_view(hit: dict, question: str = "") -> dict:
    if hit.get("kind") == "museum":
        return {
            "类型": "馆藏物件",
            "条码": hit.get("barcode"),
            "可用介绍": hit.get("visitor_text") or "",
            "限制": "网址和后台字段不能说出来。没有写明的正文、墨迹和修改都不要补。",
        }
    if hit.get("kind") == "basics":
        return {
            "类型": "基础知识",
            "标题": hit.get("title"),
            "正文": hit.get("body") or "",
            "使用方式": "可靠、没有争议的基本身份和生平用第一人称，说“我原名”。私人关系、内心和本人是否承认过，不能从这里改成我的表态。",
            "限制": "保留其中的辨误和归属限制。资料来源列表不是文学事实。",
        }
    use = []
    if hit.get("direct_self"):
        use.append("这是本人自述。经历、思考和感受可以用自己的话讲，不必说根据记载，也不必逐字照搬。不要另造材料没有的心理、动机、现场和记忆保证。")
    else:
        use.append("可靠且没有争议的普通身份、生平和作品事实可以自然用第一人称，不必逐项都是本人自述。私人关系、内心、动机，以及本人是否承认过，不能凭这段他人记载改写成我的表态。")
    if is_draft_material(hit):
        use.append("这是创作提要、大纲或早期设想，不是小说最终情节。")
    if hit.get("chronology_unresolved"):
        use.append("时间记载有未解决的分歧，不要裁定成唯一日期。")
    if not hit.get("style_ok"):
        use.append("不能当作语言风格样本。")
    quote = (hit.get("quote") or {}).get("effective") or ""
    summary = (hit.get("summary") or {}).get("effective") or ""
    title = (hit.get("title") or {}).get("effective") or ""
    view = {
        "类型": "生平资料",
        "来源书": hit.get("source_book") or "",
        "发言者": hit.get("speaker") or "",
        "信息性质": hit.get("info_nature") or "",
        "时间": "" if hit.get("chronology_unresolved") else (hit.get("event_time") or ""),
        "地点": hit.get("place") or "",
        "人物": hit.get("people") or "",
        "标题": title,
        "概括": summary,
        "可用原文": quote,
        "使用方式": "".join(use),
        "使用限制": limit_note(hit),
    }
    if re.search(r"出处|哪本书|哪一页|核对原文|原文在", question or ""):
        view["章节"] = hit.get("chapter_label") or ""
        view["页码"] = hit.get("page") or ""
        view["出处说明"] = "只使用上面写明的定位；没有写明的不要猜测。"
    return view


def _allowed_quotes(hits: list[dict]) -> str:
    parts = []
    for hit in hits:
        if hit.get("kind") == "evidence":
            parts.append((hit.get("quote") or {}).get("effective") or "")
        elif hit.get("kind") == "museum":
            parts.append(hit.get("short_quote") or "")
            parts.append(hit.get("visitor_text") or "")
        else:
            parts.append(hit.get("body") or "")
    return "\n".join(parts)


def _museum_answer(item: dict, question: str) -> str:
    scope = item.get("scope") or ""
    title = item.get("title") or "这件藏品"
    speaker = item.get("quote_speaker") or ""
    if _WHO_SAID.search(question):
        if speaker and "鲁迅" in speaker:
            return "标题里的这句话，是茅盾回忆中的鲁迅说的，不能算作茅盾自己的话。还不能说已经和馆藏原件逐字对过。"
        if speaker:
            return f"这句短引记在{speaker}名下。还不能据此说整件都已经逐字对过。"
        return "这句话是谁说的，这里还不能确定。"
    if scope in {"catalog", "clue", "candidate", "unknown"} and _CONTENT_ASK.search(question):
        return f"《{title}》的具体内容，这里还不能作确切介绍。{_physical_sentence(item)}".strip()
    if scope == "public_text" and _CONTENT_ASK.search(question):
        return ""
    return ""


def _barcode_in(question: str) -> str:
    from app.knowledge.index import BARCODE

    found = BARCODE.findall(question or "")
    return found[0] if found else ""


def _hit_id(hit: dict) -> str:
    return str(hit.get("entry_id") or hit.get("item_key") or hit.get("unit_id") or "")


def _literary_remainder(question: str) -> bool:
    return any(word in question for word in ("子夜", "作品", "小说", "散文", "诗"))
