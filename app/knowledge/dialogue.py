"""问候、身份和追问。这些不靠搜到史料才成立。"""

from __future__ import annotations

import re

GREETING = "你好。想谈哪一部作品，或是哪一段经历，都可以。"
THANKS = "不客气。还想谈什么，接着说就是。"
FAREWELL = "好，先谈到这里。愿意的时候，我们再谈文学。"
IDENTITY_REPLY = "我是依据茅盾的作品和生平资料构建的数字化身，并不是茅盾本人。"
PROBE = "在。你想谈作品，还是谈一段经历，都可以。"
HERE = "在。你说。"
CLARIFY = "你指的是哪一件事，或是哪一部作品？"
LENGTH_CLARIFY = "你想让我谈哪一部作品，或是哪一段经历？说了我再按你要的长短来讲。"
CONTENT_NEXT = "你是想接着听这部作品里后来的事，还是想问书写完之后的情况？"
NEXT_CLARIFY = "你是想问这件事后来怎样？若指的是别的，说一下是哪一件。"
PERSON_CLARIFY = "你说的是哪一位？"

_PUNCT = re.compile(r"[\s，,。！？!?、~～]+")
_GREETING = re.compile(r"^(?:你|您)?好(?:呀|啊|吗)?$|^(?:早上好|下午好|晚上好|嗨|哈喽|在吗|您好)$")
_THANKS = re.compile(r"^(?:谢谢|感谢|多谢)(?:你|您)?$")
_FAREWELL = re.compile(r"^(?:再见|拜拜|先这样|下次再聊|告辞)$")
_REALITY = re.compile(
    r"你是不是(?:真的)?茅盾|你是茅盾本人|你是真人|你还活着|真人还是|你是人工智能|你是\s*AI|机器人吗|数字化身"
)
_INTRO = re.compile(r"你是谁(?!写)|你是哪位|你哪位|怎么称呼你|怎么称呼")
_LEAD = re.compile(r"^(?:请|帮我)?(?:测试一下|测试|试一下|试试|在吗|你好)[，,：:\s]*")
_PROBE_ONLY = re.compile(r"^(?:测试一下|测试|试一下|试试)$")
_HERE_ONLY = re.compile(r"^(?:在吗|在么)$")
_CONTEXT = re.compile(
    r"刚才|那之后|那个人|那位|为什么这样说|后来呢|然后呢|接着呢|还有呢|继续说|这个人|这件事|那样说|那他|那她|他是谁|她是谁"
)
_BARE_NEXT = re.compile(r"^(?:后来呢|然后呢|接着呢|还有呢|那之后呢|那呢|继续说|继续)[？?。]*$")
_CONTENT_TOPIC = re.compile(r"主要写|写了什么|写些什么|写什么|讲了什么|内容")
_TIME_TOPIC = re.compile(r"之后|后来|然后|什么时候|哪一年|哪年|出生|逝世|进了|做过|做了什么")
_OPEN = re.compile(r"我觉得|时觉得|我感觉|读起来|难懂|很难进入|人物很多|写不下去|不知道怎么下笔|怎么下笔|卡文|给我(?:一点|些)?建议|怎样写|如何写好|开头很难")
_FACT_ASK = re.compile(r"原名|本名|笔名|哪一年|出生|逝世|主要写|写了什么|写些什么|内容是什么|馆藏|条码")
_DETAIL = re.compile(r"详细|展开讲|说细|讲细|多说一些|说得详细")
_BRIEF = re.compile(r"简短|简单说|短一些|概括一下|一句话")
_LENGTH_ONLY = re.compile(r"^(?:请|帮我|你)?(?:说|讲)?(?:得)?(?:详细|简短|简单)(?:一点|一些|些)?$")


def _compact(question: str) -> str:
    return _PUNCT.sub("", question or "").strip()


def social_reply(question: str) -> str | None:
    text = _compact(question)
    if not text:
        return None
    if _GREETING.fullmatch(text):
        return GREETING
    if _THANKS.fullmatch(text):
        return THANKS
    if _FAREWELL.fullmatch(text):
        return FAREWELL
    if _PROBE_ONLY.fullmatch(text):
        return PROBE
    if _HERE_ONLY.fullmatch(text):
        return HERE
    if is_reality_check(question) and not _has_other_subject(text):
        return IDENTITY_REPLY
    if _LENGTH_ONLY.fullmatch(text):
        return ""
    return None


def peel_preface(question: str) -> str:
    """去掉句首的测试、在吗。后面若还有问题，留给正常回答。"""

    text = (question or "").strip()
    nxt = _LEAD.sub("", text, count=1).strip()
    return nxt if nxt and nxt != text else text


def is_reality_check(question: str) -> bool:
    return _REALITY.search(question or "") is not None


def is_self_intro(question: str) -> bool:
    return _INTRO.search(question or "") is not None and not is_reality_check(question)


def length_preference(question: str) -> str:
    if _DETAIL.search(question or ""):
        return "detail"
    if _BRIEF.search(question or ""):
        return "brief"
    return "normal"


def open_chat(question: str) -> bool:
    """阅读感受和一般写作建议。具体史实、馆藏和受限话题不走这里。"""

    text = question or ""
    if not _OPEN.search(text) or _FACT_ASK.search(text):
        return False
    if re.search(r"[A-Z]{2}\d{6,}", text):
        return False
    return True


def _named_subject(topic: str, previous: str) -> str:
    """从上一问和上一句里取出实际提到的对象，不另编一个。"""

    names: list[str] = []
    matched = re.search(r"([\u4e00-\u9fff]{2,6})(?:是怎样|是谁|写了|主要)", topic or "")
    if matched:
        names.append(matched.group(1))
    for book in re.findall(r"《([^》]{1,16})》", f"{topic or ''}\n{previous or ''}"):
        label = f"《{book}》"
        if label not in names and book not in names:
            names.append(label)
        if len(names) >= 2:
            break
    if names:
        return "、".join(names[:2])
    place = re.search(r"(?:进|到|去|谈)([\u4e00-\u9fff]{2,8})", topic or "")
    if place:
        return place.group(1)
    sentence = re.split(r"[。！？]", previous or "")[0].strip()
    return sentence[:12] if sentence else ""


def next_step_reply(question: str, topic: str, previous: str = "") -> str | None:
    """含糊的“后来呢”先问清。指明上一句里的对象。上一问本身是一段时间经历时，才接着谈。"""

    if not _BARE_NEXT.fullmatch(_compact(question)):
        return None
    subject = _named_subject(topic, previous)
    if _CONTENT_TOPIC.search(topic or ""):
        if subject:
            return f"上一句谈的是{subject}。你是想接着听其中后来的情节，还是问写完以后的事？"
        return CONTENT_NEXT
    if _TIME_TOPIC.search(topic or ""):
        return None
    if subject:
        return f"上一句谈的是{subject}。你是想问这件事后来怎样，还是另有所指？"
    return NEXT_CLARIFY


def asks_correction(question: str) -> bool:
    return re.search(r"收回|说得太满|说得过|亲眼记得|过于确定", question or "") is not None


def is_contextual(question: str) -> bool:
    text = _compact(question)
    if _LENGTH_ONLY.fullmatch(text):
        return True
    return _CONTEXT.search(question or "") is not None


def _has_other_subject(text: str) -> bool:
    from app.knowledge.querynorm import distinctive_tokens

    stripped = _REALITY.sub("", text)
    stripped = _INTRO.sub("", stripped)
    stripped = re.sub(r"你是谁|吗|呢|呀|啊|请问", "", stripped)
    tokens = [token for token in distinctive_tokens(stripped) if token not in {"谁", "身份", "真人", "本人"}]
    return bool(tokens)
