"""旧演示表格的回答实验。网站访客不走这里，访客回答在 app/knowledge/respond.py。"""

from __future__ import annotations

import json
import re

from app.contracts import AnswerResult, Citation, ControlCue, Evidence
from app.llm.errors import ModelCallError
from app.llm.providers import ChatModel
from app.qa.gate import REFUSAL, select_evidence
from app.retrieval.base import Retriever
from app.retrieval.local_chinese import LocalChineseRetriever

MOODS = {"calm", "reflective", "serious", "warm"}
GESTURES = {"idle", "explain", "recall", "emphasize", "decline"}


def _cue(mood: str, gesture: str, note: str = "") -> ControlCue:
    return ControlCue(
        mood=mood if mood in MOODS else "calm",
        gesture=gesture if gesture in GESTURES else "idle",
        note=note,
    )


def _citation(hit: Evidence, quote: str = "") -> Citation:
    usable_quote = quote.strip() if hit.can_quote else ""
    if not usable_quote and hit.can_quote:
        usable_quote = hit.original_text.strip()
    return Citation(
        entry_id=hit.entry_id,
        source_title=hit.source_title,
        page=hit.page,
        paragraph_no=hit.paragraph_no,
        speaker=hit.speaker,
        speech_kind=hit.speech_kind,
        is_demo=hit.is_demo,
        can_quote=hit.can_quote,
        quote=usable_quote,
        score=hit.score,
    )


def _notices_for(hits: list[Evidence]) -> list[str]:
    notes: list[str] = []
    if any(hit.is_demo for hit in hits):
        notes.append("当前依据包含演示资料。演示资料不是馆藏原文，也不能当作茅盾原话。")
    if any(hit.speech_kind == "fictional" for hit in hits):
        notes.append("依据里有小说人物台词。这类话不能说成茅盾本人的观点。")
    if any(hit.match_basis == "summary" for hit in hits):
        notes.append("有的关键词出现在概括里。概括已单独标出，不能当成原文引用。")
    return notes


def _clean_spoken(text: str, entry_ids: list[str]) -> str:
    cleaned = text or ""
    for entry_id in entry_ids:
        cleaned = cleaned.replace(entry_id, "")
    cleaned = re.sub(r"(?m)^.*(来源编号|条目编号|情绪|动作|gesture|mood).*$\n?", "", cleaned)
    return cleaned.strip()


def _result(
    *,
    spoken_text: str,
    system_notice: str,
    citations: list[Citation],
    evidence: list[Evidence],
    rejected: list[Evidence],
    control: ControlCue,
    model_called: bool,
    provider: str,
) -> AnswerResult:
    return AnswerResult(
        spoken_text=spoken_text,
        system_notice=system_notice,
        citations=citations,
        evidence=evidence,
        rejected=rejected,
        control=control,
        model_called=model_called,
        provider=provider,
    )


def _prompt(persona: dict, question: str, evidence: list[Evidence]) -> list[dict[str, str]]:
    boundaries = persona.get("boundaries") or []
    if isinstance(boundaries, str):
        boundaries = [boundaries]
    payload = []
    for hit in evidence:
        payload.append(
            {
                "entry_id": hit.entry_id,
                "source_title": hit.source_title,
                "volume": hit.volume,
                "page": hit.page,
                "paragraph_no": hit.paragraph_no,
                "speaker": hit.speaker,
                "speech_kind": hit.speech_kind,
                "can_quote": hit.can_quote,
                "is_demo": hit.is_demo,
                "time": hit.time_label,
                "place": hit.place,
                "context_note": hit.context_note,
                "original_text": hit.original_text,
                "summary": hit.summary,
                "neighbors": [
                    {
                        "entry_id": item.entry_id,
                        "placement": item.placement,
                        "page": item.page,
                        "original_text": item.original_text,
                    }
                    for item in hit.neighbors
                ],
            }
        )
    system = "\n".join(
        [
            f"你在为“{persona.get('display_name', '茅盾数字人')}”准备一句口头回答。",
            str(persona.get("disclaimer") or ""),
            str(persona.get("voice") or ""),
            "必须遵守：",
            *[f"- {item}" for item in boundaries],
            "- 只能使用下面这份证据。证据里没有的生平、观点和情节都不要补充。",
            "- speech_kind 为 fictional 的内容是小说人物台词，要明确说这是作品里的角色发言。",
            "- speech_kind 为 quoted_person 时，说明是史料中某人的话，不是数字人自己的主张。",
            "- is_demo 为 true 的条目必须说这是演示资料，不是馆藏。",
            "- 原文可以简短转述。只有 can_quote 为 true 时才引用原句，并说明那是史料里的表述。",
            "- spoken_text 里不要写条目编号、页码清单、情绪或动作标签。",
            '- 只输出 JSON：{"spoken_text":"","used_entry_ids":[""],"control":{"mood":"calm","gesture":"explain"}}',
            "- mood 只能是 calm、reflective、serious、warm。gesture 只能是 idle、explain、recall、emphasize、decline。",
        ]
    )
    user = json.dumps({"question": question, "evidence": payload}, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_model_payload(raw: str, allowed: dict[str, Evidence]) -> tuple[str, list[str], ControlCue]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ModelCallError("模型没有按约定返回结构化结果。本次没有采用这段回答。")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelCallError("模型返回的结构无法解析。本次没有采用这段回答。") from exc
    if not isinstance(data, dict):
        raise ModelCallError("模型返回的结构无法解析。本次没有采用这段回答。")
    requested = data.get("used_entry_ids") or []
    if not isinstance(requested, list):
        requested = []
    used = [str(item) for item in requested if str(item) in allowed]
    if not used:
        raise ModelCallError("模型没有标出本次证据里的条目，回答已被拦住，避免把无来源的话当成馆藏。")
    control = data.get("control") or {}
    if not isinstance(control, dict):
        control = {}
    spoken = _clean_spoken(str(data.get("spoken_text") or ""), list(allowed))
    if not spoken:
        raise ModelCallError("模型没有给出可朗读的正文。资料证据仍保留在来源区。")
    return spoken, used, _cue(str(control.get("mood") or ""), str(control.get("gesture") or ""))


def answer_with_evidence(
    *,
    question: str,
    retriever: Retriever,
    model: ChatModel | None,
    persona: dict,
    provider_name: str,
) -> AnswerResult:
    try:
        hits = retriever.search(question, limit=8)
    except ModelCallError as exc:
        return _result(
            spoken_text="",
            system_notice=exc.notice,
            citations=[],
            evidence=[],
            rejected=[],
            control=_cue("calm", "decline"),
            model_called=False,
            provider=provider_name,
        )
    corpus = retriever.entries if isinstance(retriever, LocalChineseRetriever) else None
    kept, refusal = select_evidence(question, hits, corpus)
    rejected = [hit for hit in hits if hit.entry_id not in {item.entry_id for item in kept}]
    if refusal:
        return _result(
            spoken_text=refusal,
            system_notice="证据检查没有通过，所以没有调用模型，也没有把相近条目解释成答案。",
            citations=[],
            evidence=[],
            rejected=rejected,
            control=_cue("calm", "decline"),
            model_called=False,
            provider=provider_name,
        )
    extra = "".join(f"{note}" for note in _notices_for(kept))
    if model is None or not model.configured():
        notice = "尚未配置语言模型，不能生成人物回答。下面是检索到的馆藏或导入条目，供核对来源。"
        if model is not None:
            notice = model.missing_notice()
        if extra:
            notice = f"{notice}{extra}"
        return _result(
            spoken_text="",
            system_notice=notice,
            citations=[_citation(hit) for hit in kept],
            evidence=kept,
            rejected=rejected,
            control=_cue("calm", "idle"),
            model_called=False,
            provider=provider_name,
        )
    try:
        raw = model.complete(_prompt(persona, question, kept))
        spoken, used_ids, control = _parse_model_payload(raw, {hit.entry_id: hit for hit in kept})
    except ModelCallError as exc:
        return _result(
            spoken_text="",
            system_notice=exc.notice,
            citations=[_citation(hit) for hit in kept],
            evidence=kept,
            rejected=rejected,
            control=_cue("serious", "decline"),
            model_called=True,
            provider=provider_name,
        )
    by_id = {hit.entry_id: hit for hit in kept}
    chosen = [by_id[entry_id] for entry_id in used_ids]
    notice = "口头回答由模型根据上述证据生成，是数字化模拟，不是史料原话。"
    if extra:
        notice = f"{notice}{extra}"
    return _result(
        spoken_text=spoken,
        system_notice=notice,
        citations=[_citation(hit) for hit in chosen],
        evidence=chosen,
        rejected=rejected,
        control=control,
        model_called=True,
        provider=getattr(model, "provider_id", provider_name),
    )

