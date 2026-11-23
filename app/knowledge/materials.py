"""为这一问找材料，并按归属决定每条材料能支持什么。

检索由程序完成。模型只看到这里整理过的内容和用法说明，看不到条目编号、书页和网址。
"""

from __future__ import annotations

import re

from app.knowledge.guard import BARCODE
from app.knowledge.index import KnowledgeIndex
from app.knowledge.querynorm import normalize_query
from app.knowledge.support import (
    answerable_text,
    for_this_question,
    is_draft_material,
    limit_note,
    matching_basics,
    process_material,
    scan_answerable,
    select_supported,
)

_SELF = {"茅盾", "沈雁冰", "沈德鸿", "雁冰", "德鸿", "你", "我"}
# 别人对私人关系的说法。只在不是本人自述、句子说到具体的人、又不是在讲作品时才扣下。
_RELATION = re.compile(r"相恋|同居|热恋|恋人|情人|情妇|私情|恋爱关系|浪漫而")
# “与某人结束在某地的生活”“同某人一起生活”这类写法，要句子里点了另一个人的名字才算。
_SHARED_LIFE = re.compile(r"共同生活|一起生活|生活在一起|结束.{0,6}生活")
_PAIR = re.compile(r"二人|两人|他们|她|他")
_FIELDS = ("title", "summary", "quote")
_FIELD_NAME = {"title": "标题", "summary": "概括", "quote": "原文"}


def hit_id(hit: dict) -> str:
    return str(hit.get("entry_id") or hit.get("item_key") or hit.get("unit_id") or "")


def entities_of(reading: dict, parts: list[dict]) -> list[str]:
    names: list[str] = []
    candidates = [reading.get("person"), reading.get("work")]
    for part in parts:
        value = (part.get("object") or "").strip()
        if value and len(value.strip("《》")) <= 6 and not re.search(r"的|你|我|什么|哪", value):
            candidates.append(value)
    for value in candidates:
        value = (value or "").strip()
        if value and value not in _SELF and value not in names:
            names.append(value)
    return names


_YEAR = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")


def years_of(hit: dict) -> list[int]:
    if hit.get("chronology_unresolved"):
        return []
    return [int(year) for year in _YEAR.findall(str(hit.get("event_time") or ""))]


def latest_year(hits: list[dict]) -> int:
    years = [year for hit in hits for year in years_of(hit)]
    return max(years) if years else 0


def later_records(index: KnowledgeIndex, query: str, after_year: int, skip: set[str], *, limit: int = 5) -> list[dict]:
    """“后来呢”：写同一件事、时间不早于上一轮所用材料的记录，按先后排。"""

    found = []
    for rank, hit in enumerate(index.search(query, limit=30)):
        if hit.get("kind") == "museum" or hit.get("is_source_list") or hit_id(hit) in skip:
            continue
        years = years_of(hit)
        if years and min(years) >= after_year:
            found.append((min(years), rank, hit))
    found.sort(key=lambda entry: (entry[0], entry[1]))
    return [hit for _year, _rank, hit in found[:limit]]


_CONTINUE = {"之后", "后来", "以后", "此后", "接着", "然后", "接下来", "再后来"}


def gather(
    index: KnowledgeIndex | None,
    question: str,
    reading: dict,
    parts: list[dict],
    *,
    topic: str = "",
    item: dict | None = None,
    extra: str = "",
    after: tuple[int, set[str]] | None = None,
) -> dict:
    """按对象和所求检索。返回查询、命中、扣下的内容和没有覆盖的对象。

    after：接着上一轮往下问时，上一轮所用材料里最晚的年份和那些材料的编号。
    """

    kinds = {part["type"] for part in parts}
    entities = entities_of(reading, parts)
    bare = [name.strip("《》") for name in entities]
    words = list(entities)
    for word in reading.get("search") or []:
        if word not in words and word not in _SELF:
            words.append(word)
    base = question
    if "follow" in kinds:
        # “那之后呢”这样的问法本身没有可查的词，“之后”“后来”还会带出不相干的标题。
        words = [word for word in words if word not in _CONTINUE]
        base = "" if words else (topic or question)
    if extra:
        base = f"{extra} {base}"
    query = normalize_query(" ".join([*words, base]).strip())
    result = {"query": query, "entities": entities, "hits": [], "withheld": [], "missing": [], "found": []}
    if index is None:
        result["missing"] = entities
        return result

    hits, seen = _search(index, query, question, item)
    if bare:
        naming = [hit for hit in hits if any(name in answerable_text(hit) for name in bare)]
        if not naming:
            # 排名没有带出写到这个对象的记录时，再按正文找一遍。
            naming = scan_answerable(index, bare, limit=6, skip=seen)
        if naming:
            keep_item = [hit for hit in hits if item and hit is item and hit not in naming]
            hits = keep_item + naming
    hits = _fit_request(kinds, question, hits)
    if "follow" in kinds and after and after[0]:
        later = later_records(index, query, after[0], after[1])
        if later:
            known = {hit_id(hit) for hit in later}
            hits = later + [hit for hit in hits if hit_id(hit) not in known and hit.get("kind") != "basics"]
    if "person_intro" in kinds and reading.get("person"):
        # 介绍一个人，先找写这个人是谁的：基础知识、本人自述里以这个人为主的记录。
        own = person_records(index, reading["person"])
        known = {hit_id(hit) for hit in own}
        hits = own + [hit for hit in hits if hit_id(hit) not in known]
        hits.sort(key=lambda hit: 0 if hit.get("kind") == "basics" else (1 if hit.get("direct_self") else 2))
    result["found"] = [hit_id(hit) for hit in hits]
    kept, withheld = scope_filter(hits[:8])
    result["hits"] = kept
    result["withheld"] = withheld
    result["missing"] = [
        name for name, short in zip(entities, bare) if not any(short in answerable_text(hit) for hit in kept)
    ]
    return result


def _search(index: KnowledgeIndex, query: str, question: str, item: dict | None) -> tuple[list[dict], set[str]]:
    hits: list[dict] = []
    seen: set[str] = set()
    for hit in matching_basics(index.units, query) + index.search(query, limit=6):
        key = hit_id(hit)
        if not key or key in seen:
            continue
        seen.add(key)
        hits.append(hit)
    if item and all(hit.get("barcode") != item.get("barcode") for hit in hits):
        hits = [item, *hits]
    asked_barcode = bool(BARCODE.search(question or ""))
    hits = [
        hit
        for hit in hits
        if hit.get("kind") != "museum" or hit.get("barcode") == (item or {}).get("barcode") or asked_barcode
    ]
    kept, missing = select_supported(query, hits)
    if missing or not kept:
        more = scan_answerable(index, missing, skip=set(seen)) if missing else []
        if more:
            for hit in more:
                seen.add(hit_id(hit))
            kept, _missing = select_supported(query, kept + more)
    return kept, seen


def _fit_request(kinds: set[str], question: str, hits: list[dict]) -> list[dict]:
    """问作品内容和人物分析时，不拿创作提要和成书经过当作情节。问创作过程时保留。"""

    if "process" in kinds:
        return hits
    if kinds & {"work", "analysis"}:
        return [hit for hit in hits if not is_draft_material(hit) and not process_material(hit)]
    return for_this_question(question, hits)


def _other_people(hit: dict) -> list[str]:
    names = []
    for piece in re.split(r"[；;、，,]", hit.get("people") or ""):
        name = re.sub(r"[（(].*?[)）]", "", piece).strip()
        if len(name) >= 2 and name not in _SELF:
            names.append(name)
    return names


def scope_filter(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """同一条里客观事实和私人关系的说法混在一起时，只留前者。整条都是后者就不用这条。"""

    kept: list[dict] = []
    withheld: list[dict] = []
    for hit in hits:
        if hit.get("kind") != "evidence" or hit.get("direct_self"):
            kept.append(hit)
            continue
        names = _other_people(hit)
        view = dict(hit)
        dropped = False
        for field in _FIELDS:
            value = hit.get(field)
            text = (value or {}).get("effective") if isinstance(value, dict) else str(value or "")
            remaining = []
            for sentence in re.split(r"(?<=[。；;])", text or ""):
                if not sentence.strip():
                    continue
                named = any(name in sentence for name in names)
                relation = (_RELATION.search(sentence) and (named or _PAIR.search(sentence))) or (
                    _SHARED_LIFE.search(sentence) and any(re.search(rf"[与和同跟]{re.escape(name)}", sentence) for name in names)
                )
                if relation and "《" not in sentence:
                    withheld.append({"id": hit_id(hit), "field": _FIELD_NAME[field], "reason": "别人对私人关系的记述，不能当作本人的话"})
                    dropped = True
                    continue
                remaining.append(sentence)
            view[field] = {"effective": "".join(remaining).strip()}
        if dropped:
            view["people"] = "；".join(
                piece.strip()
                for piece in re.split(r"[；;]", hit.get("people") or "")
                if piece.strip() and not re.search(r"关系|恋|同居", piece)
            )
            view["_limit"] = "。".join(
                piece for piece in limit_note(hit).split("。") if piece and not _RELATION.search(piece)
            )
        if not any((view.get(field) or {}).get("effective") for field in ("summary", "quote")):
            continue
        kept.append(view)
    return kept, withheld


def attribution(hit: dict) -> tuple[str, str, str]:
    """归属、可以支持什么、不能支持什么。"""

    if hit.get("kind") == "museum":
        return "馆藏编目", "题名、寄收双方、日期、页数、书写工具这些写明的物件信息", "没有写明的正文、写信用意、墨迹和修改"
    if hit.get("kind") == "basics":
        return "基础知识", "常见的身份、生平和作品事实", "其中别人的回忆和性格解释，不能改成我自己的话"
    if hit.get("direct_self"):
        return "本人自述", "我的经历、当时的想法和判断，可以用自己的话讲", "原文没有写到的心情、现场和对话"
    nature = hit.get("info_nature") or ""
    speaker = re.sub(r"[（(].*?[)）]", "", hit.get("speaker") or "").strip()
    if "编者" in nature or "年谱" in (hit.get("source_book") or ""):
        return "他人整理的生平记事", "时间、地点、行程、会面、发表这类外部事实", "我的心情、动机、对人的感情和评价"
    if speaker and "茅盾" not in speaker and "无独立" not in speaker:
        return f"他人的说法（{speaker[:12]}）", "这位说话人说过什么", "改成我的原话、我的看法或我的内心"
    return (nature[:16] or "整理的记述"), "其中写明的外部事实", "我的心情、动机和没有写到的原话"


def evidence_view(hit: dict, label: str, *, sources: bool = False) -> dict:
    owner, can, cannot = attribution(hit)
    if hit.get("kind") == "museum":
        return {
            "编号": label,
            "归属": owner,
            "内容": {"条码": hit.get("barcode"), "题名": hit.get("title") or "", "介绍": hit.get("visitor_text") or ""},
            "可以支持": can,
            "不能支持": cannot,
        }
    if hit.get("kind") == "basics":
        return {
            "编号": label,
            "归属": owner,
            "内容": {"题": hit.get("title") or "", "正文": hit.get("body") or ""},
            "可以支持": can,
            "不能支持": cannot,
            "其他限制": "其中的辨误和限定要保留。",
        }
    limits = []
    if is_draft_material(hit):
        limits.append("这是创作提要、大纲或早期设想，不是成书后的情节")
    if hit.get("chronology_unresolved"):
        limits.append("时间有分歧，不要定成唯一的日期")
    note = hit["_limit"] if "_limit" in hit else limit_note(hit)
    if note:
        limits.append(note)
    content = {
        "事": (hit.get("title") or {}).get("effective") or "",
        "概括": (hit.get("summary") or {}).get("effective") or "",
        "原文": (hit.get("quote") or {}).get("effective") or "",
        "时间": "" if hit.get("chronology_unresolved") else (hit.get("event_time") or ""),
        "地点": hit.get("place") or "",
        "人物": hit.get("people") or "",
    }
    view = {
        "编号": label,
        "归属": owner,
        "内容": {key: value for key, value in content.items() if value},
        "可以支持": can,
        "不能支持": cannot,
    }
    if limits:
        view["其他限制"] = "。".join(limits)
    if sources:
        view["出处"] = {
            "书": hit.get("source_book") or "",
            "章节": (hit.get("chapter_label") or "").splitlines()[0] if hit.get("chapter_label") else "",
            "页码": (hit.get("page") or "").splitlines()[0] if hit.get("page") else "",
            "说明": "只说这里写明的定位，没有写明的不猜。",
        }
    return view


def person_records(index: KnowledgeIndex, name: str, *, limit: int = 4) -> list[dict]:
    short = name.strip("《》")
    ranked = []
    for order, doc in enumerate(getattr(index, "documents", [])):
        if doc.get("kind") == "museum" or doc.get("is_source_list"):
            continue
        if doc.get("kind") == "basics":
            if short in f"{doc.get('title') or ''}{doc.get('body') or ''}":
                ranked.append((0, order, doc))
            continue
        if not doc.get("direct_self"):
            continue
        people = doc.get("people") or ""
        title = (doc.get("title") or {}).get("effective") or ""
        if re.search(rf"核心人物[:：]\s*{re.escape(short)}", people):
            ranked.append((1, order, doc))
        elif short in title:
            ranked.append((2, order, doc))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [doc for _rank, _order, doc in ranked[:limit]]


def allowed_quotes(hits: list[dict], *, sources: bool = False) -> str:
    parts = []
    for hit in hits:
        if sources:
            parts.extend([hit.get("source_book") or "", hit.get("chapter_label") or ""])
        if hit.get("kind") == "evidence":
            parts.append((hit.get("quote") or {}).get("effective") or "")
        elif hit.get("kind") == "museum":
            parts.append(hit.get("short_quote") or "")
            parts.append(hit.get("visitor_text") or "")
        else:
            parts.append(hit.get("body") or "")
    return "\n".join(parts)


def assess_person(name: str, hits: list[dict]) -> dict:
    """介绍一个人，材料里有没有身份。行程和同行不算身份。"""

    short = name.strip("《》")
    naming = [hit for hit in hits if short in answerable_text(hit)]
    identity = [hit for hit in naming if hit.get("kind") == "basics" or hit.get("direct_self")]
    roles: list[str] = []
    for hit in naming:
        people = hit.get("people") or ""
        # 本人自述里以这个人为主的记录，写有“与茅盾关系”。
        if hit.get("direct_self") and re.search(rf"核心人物[:：]\s*{re.escape(short)}", people):
            match = re.search(r"与茅盾关系[:：]\s*(.+)", people)
            if match and match.group(1).strip() not in roles:
                roles.append(match.group(1).strip())
        for piece in re.split(r"[；;\n]", people):
            if short in piece:
                match = re.search(r"[（(](.*?)[)）]", piece)
                if match and match.group(1) not in roles:
                    roles.append(match.group(1))
    return {"naming": len(naming), "identity": [hit_id(hit) for hit in identity], "roles": roles}


def contact_view(name: str, hit: dict, label: str) -> dict | None:
    """材料里没有这个人的身份时，只把写到这个人的那几句交给模型，不把整段行程交过去。"""

    short = name.strip("《》")
    summary = (hit.get("summary") or {}).get("effective") if isinstance(hit.get("summary"), dict) else ""
    pieces = [piece.strip() for piece in re.split(r"(?<=[。；;])", summary or "") if short in piece]
    if not pieces:
        return None
    owner, can, cannot = attribution(hit)
    content = {"事": "".join(pieces)}
    if hit.get("event_time") and not hit.get("chronology_unresolved"):
        content["时间"] = hit["event_time"]
    return {"编号": label, "归属": owner, "内容": content, "可以支持": "有过这一点来往", "不能支持": cannot + "；也不能说明这个人的身份"}


# 不宜在普通交流里主动展开的争议。只影响这一问交给模型的文字，不改资料库。
_DISPUTE = re.compile(r"农民运动|过火|祖宗牌位|牌位|强迫剪发|处置军官")
_STANCE = re.compile(r"枪杆子|掌握武装|千军万马")
_SCENE = re.compile(r"第一次|初次|见到|见面|相识|同住|寓所|相处|默坐|相陪|沉思|楼上|楼下|印象")
_NOT_PLACE = {"这里", "那里", "哪里", "这时", "那时", "后来", "一起", "二十", "三十", "当时", "同年", "次年"}


def asks_dispute(question: str) -> bool:
    """对方是不是在问不宜展开的争议本身。"""

    text = question or ""
    if re.search(r"写得.{0,4}过火|太过火|过于", text):
        return False
    return _DISPUTE.search(text) is not None


def places_in(question: str) -> list[str]:
    found = []
    for match in re.finditer(r"在([\u4e00-\u9fff]{2,3})", question or ""):
        word = match.group(1)
        if word in _NOT_PLACE or word in found:
            continue
        found.append(word)
    return found


def _sentences_of(text: str) -> list[str]:
    return [piece for piece in re.split(r"(?<=[。；;])", text or "") if piece.strip()]


def _without_dispute(text: str) -> tuple[str, bool]:
    kept = []
    dropped = False
    for sentence in _sentences_of(text):
        if _DISPUTE.search(sentence):
            dropped = True
            continue
        kept.append(sentence)
    return "".join(kept).strip(), dropped


def _field_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("effective") or "")
    return str(value or "")


def _has_body(hit: dict) -> bool:
    """标题单独留下来，不算还能讲的内容。"""

    if hit.get("kind") == "basics":
        return bool((hit.get("body") or "").strip())
    return any(_field_text(hit.get(field)).strip() for field in ("summary", "quote"))


def _strip_dispute(hit: dict) -> tuple[dict, bool]:
    """拿掉争议句子。返回的是副本，不改索引里的原记录。"""

    if hit.get("kind") == "basics":
        title, cut_title = _without_dispute(str(hit.get("title") or ""))
        body, cut_body = _without_dispute(str(hit.get("body") or ""))
        if not (cut_title or cut_body):
            return hit, False
        view = dict(hit)
        view["title"] = title
        view["body"] = body
        return view, True
    if hit.get("kind") != "evidence":
        return hit, False
    view = dict(hit)
    dropped = False
    for field in _FIELDS:
        cleaned, cut = _without_dispute(_field_text(hit.get(field)))
        if cut:
            dropped = True
            view[field] = {"effective": cleaned}
    return (view if dropped else hit), dropped


def scene_records(index: KnowledgeIndex | None, person: str, *, places: list[str] | None = None, limit: int = 6) -> list[dict]:
    """印象、相识和某件往事：把写到见面、相处、同住的记录找出来，避免只靠排名。"""

    short = (person or "").strip("《》")
    if index is None or not short:
        return []
    places = places or []
    ranked = []
    for order, doc in enumerate(index.documents):
        if doc.get("kind") == "museum" or doc.get("is_source_list"):
            continue
        text = answerable_text(doc)
        if short not in text and short not in (doc.get("place") or ""):
            continue
        place_hit = any(place in (doc.get("place") or "") or place in text for place in places)
        scene_hit = _SCENE.search(text) is not None
        if not scene_hit and not place_hit:
            continue
        if _DISPUTE.search(text) and not scene_hit:
            continue
        score = (8 if place_hit else 0) + (4 if scene_hit else 0) + (3 if doc.get("direct_self") else 0)
        if _STANCE.search(text):
            score -= 6
        if score <= 0:
            continue
        ranked.append((score, -order, doc))
    ranked.sort(reverse=True)
    return [doc for _score, _order, doc in ranked[:limit]]


_STORY_ANCHORS = (
    "默坐相陪",
    "默然沉思",
    "不敢打扰",
    "寓所",
    "住楼上",
    "住楼下",
    "大学问家",
    "博闻强记",
    "谈笑风生",
    "一见如故",
    "第一次见到",
)
# 这些词一出现，就足以认出是已经讲过的那一件，不必凑满两个。
_DISTINCT_ANCHORS = {
    "默坐相陪",
    "默然沉思",
    "不敢打扰",
    "住楼上",
    "住楼下",
    "大学问家",
    "博闻强记",
    "谈笑风生",
    "一见如故",
}


def story_anchors(text: str) -> list[str]:
    """同一件事换了措辞也能对上的若干锚点。只认整段话里确实出现过的。"""

    return [anchor for anchor in _STORY_ANCHORS if anchor in (text or "")]


def sentence_already_told(sentence: str, previous: str) -> bool:
    """这一句是不是在说已经讲给对方听过的事。说法可以不同，但要能对上内容。"""

    prev_raw = previous or ""
    cur_raw = sentence or ""
    prev = "".join(re.findall(r"[\u4e00-\u9fff]", prev_raw))
    cur = "".join(re.findall(r"[\u4e00-\u9fff]", cur_raw))
    found = [anchor for anchor in story_anchors(cur_raw) if anchor in prev_raw]
    if any(anchor in _DISTINCT_ANCHORS for anchor in found):
        return True
    if len(found) >= 2:
        return True
    # 短句里只要出现已经讲过的那件事，例如条目标题“默坐相陪”、一句“寓所同住”。
    if found and len(cur) <= 12:
        return True
    if len(cur) < 6 or len(prev) < 6:
        return False
    if any(cur[index : index + 8] in prev for index in range(max(len(cur) - 7, 0))):
        return True
    # 中间插了日期或几个字，两头仍是同一句。
    matched = 0
    index = 0
    while index <= len(cur) - 6:
        if cur[index : index + 6] in prev:
            matched += 1
            index += 6
            if matched >= 2:
                return True
        else:
            index += 1
    return False


def _without_told_text(text: str, previous: str) -> tuple[str, bool]:
    """留下还没讲过的句子。一句里后半截是新的，只去掉已经讲过的分句。"""

    kept = []
    dropped = False
    for sentence in _sentences_of(text):
        if not sentence_already_told(sentence, previous):
            kept.append(sentence)
            continue
        clauses = [piece for piece in re.split(r"(?<=[，,])", sentence) if piece.strip()]
        fresh = [
            piece
            for piece in clauses
            if len(clauses) > 1
            and len(re.findall(r"[\u4e00-\u9fff]", piece)) >= 8
            and not sentence_already_told(piece, previous)
        ]
        if fresh:
            kept.append("".join(fresh))
        dropped = True
    return "".join(kept).strip(), dropped


def without_told(hit: dict, previous: str) -> dict | None:
    """按已经说出口的话，拿掉这条里讲过的句子。整条都讲过才丢掉；还有没讲的就留下。

    不按条目编号一刀切：另一条记录写的是同一件事，句子对得上也会拿掉。
    返回副本，不改资料库里的原记录。
    """

    if not (previous or "").strip():
        return hit
    if hit.get("kind") == "basics":
        body, cut = _without_told_text(str(hit.get("body") or ""), previous)
        if not cut:
            return hit
        if not body:
            return None
        view = dict(hit)
        view["body"] = body
        return view
    if hit.get("kind") not in {"evidence", ""} and hit.get("kind") != "evidence":
        return hit
    view = dict(hit)
    dropped = False
    for field in ("title", "summary", "quote"):
        cleaned, cut = _without_told_text(_field_text(hit.get(field)), previous)
        if not cut:
            continue
        dropped = True
        current = hit.get(field)
        if isinstance(current, dict):
            updated = dict(current)
            updated["effective"] = cleaned
            view[field] = updated
        else:
            view[field] = cleaned
    if not dropped:
        return hit
    if not _has_body(view):
        return None
    return view


def repeats_told(text: str, previous: str) -> bool:
    """新的一段是不是又在讲已经说过的那件往事。句子不必相同。"""

    told = set(story_anchors(previous))
    found = story_anchors(text)
    shared = [anchor for anchor in found if anchor in told]
    if len(shared) >= 2:
        return True
    if any(anchor in _DISTINCT_ANCHORS for anchor in shared):
        return True
    sentences = [piece for piece in _sentences_of(text) if piece.strip()]
    repeated = [piece for piece in sentences if sentence_already_told(piece, previous)]
    if repeated and len(repeated) >= max(1, len(sentences) - 1) and len("".join(repeated)) >= 12:
        return True
    # 这条里能对上的事，上一轮都说过，没有新的一件。
    return bool(found and told and set(found) <= told)


def further_records(
    index: KnowledgeIndex | None,
    person: str,
    skip: set[str],
    previous: str,
    *,
    limit: int = 3,
) -> list[dict]:
    """同一人还没讲过的相处片段。已经讲过的故事、争议和后来的评断都不取。"""

    short = (person or "").strip("《》")
    if index is None or not short:
        return []
    # skip 是已经用过的编号。同一条里还可能有没讲过的句子，所以不按编号整条排除。
    del skip
    ranked = []
    for order, doc in enumerate(index.documents):
        if doc.get("kind") == "museum" or doc.get("is_source_list"):
            continue
        if not doc.get("direct_self"):
            continue
        text = answerable_text(doc)
        if short not in text:
            continue
        if _DISPUTE.search(text) or _STANCE.search(text) or not _SCENE.search(text):
            continue
        trimmed = without_told(doc, previous) if (previous or "").strip() else doc
        if trimmed is None:
            continue
        ranked.append((-order, trimmed))
    ranked.sort(reverse=True)
    return [doc for _order, doc in ranked[:limit]]


def narrow_person_hits(hits: list[dict], question: str, *, angle: str, person: str) -> tuple[list[dict], list[dict]]:
    """按这一问的问法留下适合说的材料。省略的只是这一问不交给生成的部分。"""

    places = places_in(question)
    usable: list[dict] = []
    omitted: list[dict] = []
    scene_angle = angle in {"impression", "meeting", "episode"}
    for hit in hits:
        text = answerable_text(hit)
        if scene_angle and _STANCE.search(text) and not _STANCE.search(question or ""):
            omitted.append({"id": hit_id(hit), "reason": "这是后来的评断，这一问不拿来展开。原文仍在资料库"})
            continue
        view, dropped = _strip_dispute(hit)
        if not _has_body(view):
            omitted.append({"id": hit_id(hit), "reason": "含不宜在这一问展开的争议，原文仍在资料库"})
            continue
        if dropped:
            omitted.append({"id": hit_id(hit), "reason": "拿掉了不宜在这一问展开的争议句子，原文仍在资料库"})
        usable.append(view)
    ranked = sorted(usable, key=lambda hit: _display_rank(hit, angle, person, places), reverse=True)
    limit = 4 if scene_angle else 6
    for hit in ranked[limit:]:
        omitted.append({"id": hit_id(hit), "reason": "这一问用更相关的经历来说，这条不放进生成"})
    return ranked[:limit], omitted


def _display_rank(hit: dict, angle: str, person: str, places: list[str]) -> int:
    text = answerable_text(hit)
    score = 0
    if person and person in text:
        score += 2
    if hit.get("direct_self"):
        score += 3
    if _SCENE.search(text):
        score += 4
    if angle == "meeting" and re.search(r"第一次|初次|见到|见面", text):
        score += 6
    if angle == "episode" and places and any(place in (hit.get("place") or "") or place in text for place in places):
        score += 8
    if angle == "impression" and re.search(r"第一次|同住|寓所|默坐|相陪|见到|印象", text):
        score += 3
    if angle == "identity" and (hit.get("kind") == "basics" or "与茅盾关系" in (hit.get("people") or "")):
        score += 5
    if not hit.get("direct_self") and hit.get("kind") != "basics" and angle in {"impression", "meeting", "episode"}:
        score -= 1
    return score


def prompt_hit(hit: dict, angle: str) -> dict:
    """印象、相识和往事不把“与茅盾关系”里的职务交给模型，避免资料卡式开头。"""

    if angle not in {"impression", "meeting", "episode"}:
        return hit
    people = hit.get("people") or ""
    if "与茅盾关系" not in people:
        return hit
    view = dict(hit)
    view["people"] = "\n".join(line for line in people.splitlines() if not line.strip().startswith("与茅盾关系"))
    return view


def link_records(index: KnowledgeIndex | None, first: str, second: str, *, limit: int = 4) -> list[dict]:
    """整库里同时写到两者的记录。问“有没有关系”时，只有这样的记录才算依据。"""

    a, b = first.strip("《》"), second.strip("《》")
    if index is None or not a or not b:
        return []
    found = []
    for doc in getattr(index, "documents", []):
        if doc.get("is_source_list") or doc.get("kind") == "museum":
            continue
        text = answerable_text(doc)
        if a in text and b in text:
            found.append(doc)
        if len(found) >= limit:
            break
    return found
