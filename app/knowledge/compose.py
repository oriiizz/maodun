"""把问题、材料和要求交给模型写成正文，再检查。

这里不决定走哪条路，也不检索。每一步做了什么都记进 drafts，供维护者事后查看。
模型一轮最多被调用几次由 ModelCalls 限制。
"""

from __future__ import annotations

import json
import re
import time

from app.knowledge.guard import inspect_output, passing_text, rewrite_goals, split_sentences
from app.llm.errors import ModelCallError

FORMAT = (
    "只输出一个 JSON 对象："
    '{"重点": "对方这一轮想知道什么，我用哪一句话回答", "用到": ["材料编号"], "缺少": "材料答不了的那一点，没有就留空", "正文": "说给读者听的话"}。'
    "先定“重点”，再挑最能托住它的一两条材料写“正文”。材料是依据，不是要逐条念完的清单，给出的不必都用。"
    "材料编号只写在“用到”里。“正文”是连贯的话，不带编号、标签和括注，也不把“重点”原样抄进去当开场白。"
)
_LENGTH = {
    "detail": "对方要求详细，可以展开，但只说材料里有的内容。",
    "brief": "对方要求简短，用一两句说完。",
}
_REVIEW_ITEMS = {
    "A": "答非所问",
    "B": "接受前提",
    "C": "私人断言",
    "D": "编造",
}
REVIEW_INSTRUCTION = """你是“茅盾数字人”后台的一道检查工序。只检查一段待发出的回答，不改写，不补充史实。输出 JSON。

逐项看待查的回答：
A 有没有回答观众问的那一点。材料里没有时，照实说“说不上来”“不能肯定”“没有依据”，都算正常的回答。拿不相干的行程、住处或别的作品顶替回答，才算没有回答。
B 有没有把没有被确认的亲密感情或私人关系当成已有的事来说（例如用“这段感情”“我们之间”“那些往事”“我们在那里的生活”这类说法）。材料写明的同行、见面、通信、共事，说成有过来往，不算问题。
C 有没有把亲密感情或私人关系说成“我”承认了，或作了相反的否认；有没有把别人的记述说成“我”的话。
D 有没有可用材料里没有的事实、称呼、身份、心情、动机或原话。介绍一个人时，对这个人地位的评断（例如“最重要的人物之一”“旗手”“早期的领导人”）在可用材料里找不到的，也算；从材料里写的事可以直接看出的职业（写小说的是作家，编刊物的是编辑，译书的是翻译家），不算。把“我”自己写下的话说成是对方说的，也算。别人整理的记事里写明的行程、会面和发表，说成“我”的经历，不算问题。对先前对话本身的描述（谁先提起、上一句说了什么）不需要材料。

“这一步的任务”里写明了这一问该怎样答，照着做的不算问题。只报确实成立的问题，拿不准就不报。没有问题时输出 {"问题": []}。
有问题时输出 {"问题": [{"项": "A", "句子": "回答里出问题的那一句，照抄原句", "说明": "一句话"}]}。"""
JUDGE_INSTRUCTION = """你是“茅盾数字人”后台的一道检查工序。观众要茅盾确认或解释一件私人感情上的事，后台写了几句候选的回应。你只判断每一句有没有下面的问题，不改写。输出 JSON。

1 暗示确有其事：听的人会觉得他默认了有这份感情或这层关系（例如“那些事”“那段日子”“过去了”“不必再提”）。
2 暗示没有其事：听的人会觉得他在否认。
3 编造内心：写了不愿、不忍、伤心、苦衷、怀念这类材料里没有的心情。
4 防御或责备：辩解、反问、教训对方，或解释规定。

不直接回答有或没有，是这一步的本意，不算问题。只报确实成立的问题。

输出 {"结果": [{"序号": 1, "问题": []}, {"序号": 2, "问题": ["1"]}]}，每个候选一项。没有问题就给空列表。"""


class ModelCalls:
    """本轮对模型的全部请求都从这里走，逐次记下用途、耗时和成败。"""

    def __init__(self, model, limit: int = 5):
        self.model = model if model is not None and model.configured() else None
        self.limit = limit
        self.log: list[dict] = []

    @property
    def available(self) -> bool:
        return self.model is not None

    def answered(self) -> bool:
        return any(item["purpose"] != "understand" for item in self.log)

    def __call__(self, messages, *, purpose: str, json_mode: bool = False, max_tokens: int | None = None, temperature: float | None = None) -> str:
        if self.model is None:
            raise ModelCallError("没有配置 DeepSeek。")
        if len(self.log) >= self.limit:
            raise ModelCallError("这一轮的模型调用已到上限，没有再请求。")
        entry = {"purpose": purpose, "ok": False, "ms": 0, "error": ""}
        self.log.append(entry)
        started = time.perf_counter()
        try:
            raw = self.model.complete(
                messages, purpose=purpose, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature
            )
        except ModelCallError as exc:
            entry["error"] = exc.notice
            raise
        finally:
            entry["ms"] = int((time.perf_counter() - started) * 1000)
        entry["ok"] = True
        return raw


def _json_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_answer(raw: str) -> dict:
    """模型按格式返回时取“正文”。没有按格式返回，就把整段当正文，照样检查。"""

    data = _json_object(raw)
    if data is None or "正文" not in data:
        return {"text": (raw or "").strip(), "used": [], "missing": "", "point": "", "structured": False}
    used = data.get("用到") or []
    if not isinstance(used, list):
        used = [used]
    return {
        "text": str(data.get("正文") or "").strip(),
        "used": [str(item).strip() for item in used if str(item).strip()],
        "missing": str(data.get("缺少") or "").strip(),
        "point": str(data.get("重点") or "").strip(),
        "structured": True,
    }


def build_messages(
    rules: str,
    style: str,
    question: str,
    *,
    asks: list[str],
    prior: list[dict] | None = None,
    conversation: dict | None = None,
    evidence: list[dict] | None = None,
    state: list[str] | None = None,
    how: list[str] | None = None,
    length: str = "normal",
) -> list[dict[str, str]]:
    user: dict = {"问题": question}
    if asks:
        user["这一问要的是什么（后台的理解，不是事实）"] = asks
    user["回答篇幅"] = _LENGTH.get(length, "只回答所问的那一点，不要补未问到的生平、书名由来或创作背景。")
    if conversation:
        # 近期原文、较早摘要、本轮找回的原文；都只供承接，不是史实证据。
        user.update(conversation)
    elif prior:
        user["先前的对话（用来理解指代，并避免把已经讲过的事再讲一遍；不是事实，不能代替本轮材料）"] = prior
    if evidence:
        user["可用材料"] = evidence
    if state:
        user["材料状况"] = state
    if how:
        user["这一问怎样答"] = how
    user["输出格式"] = FORMAT
    return [
        {"role": "system", "content": rules},
        {"role": "system", "content": style},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def conversation_messages_view(pack: dict | None, turns: list[dict] | None = None) -> dict:
    """生成请求里的会话视图。优先用整段记忆包，没有包时退回近期原文。"""

    from app.session.memory import conversation_view

    return conversation_view(pack, turns)


def prior_view(turns: list[dict]) -> list[dict]:
    """兼容旧调用：只取近期几轮原文。新路径请用 conversation_messages_view。"""

    prior = []
    for turn in turns[-6:]:
        if turn.get("usable") and turn.get("spoken"):
            prior.append({"问": turn["question"], "当时的回答（不是事实证据）": turn["spoken"]})
        else:
            prior.append({"问": turn["question"], "当时的回答": "这一轮没有形成可沿用的回答，不能当作事实。"})
    return prior


def write(calls: ModelCalls, messages: list[dict], check, drafts: list[dict], *, review=None, max_tokens: int = 900) -> dict:
    """写一稿，检查；有问题就带着具体问题重写一次。

    check(text) 返回 (句子, 问题)。review(text) 是可选的语义检查，只在风险较高的去向使用。
    返回 source：model（一稿通过）、rewrite（重写后通过）、failed（重写后仍有问题）、error（请求失败）。
    """

    result = {"text": "", "source": "error", "kept": "", "findings": [], "used": [], "missing": "", "notice": ""}
    try:
        raw = calls(messages, purpose="answer", json_mode=True, max_tokens=max_tokens)
    except ModelCallError as exc:
        result["notice"] = exc.notice
        return result
    draft = parse_answer(raw)
    sentences, findings = _checked(draft["text"], check)
    reviewed = False
    if not findings and review is not None:
        findings = review(draft["text"])
        reviewed = True
    drafts.append({"stage": "draft", "text": draft["text"], "point": draft["point"], "used": draft["used"], "missing": draft["missing"], "findings": findings, "reviewed": reviewed})
    result.update(used=draft["used"], missing=draft["missing"])
    if not findings:
        result.update(text=draft["text"], source="model")
        return result

    goals = rewrite_goals(findings)
    retry = messages + [
        {"role": "assistant", "content": raw},
        {
            "role": "user",
            "content": "上一稿不能用。要改的地方："
            + "；".join(goals)
            + "。整段重新写成完整的句子，不要只删改个别字词，不要解释改了什么。仍按原来的 JSON 格式输出。",
        },
    ]
    try:
        raw = calls(retry, purpose="rewrite", json_mode=True, max_tokens=max_tokens)
    except ModelCallError as exc:
        result.update(source="error", notice=exc.notice, findings=findings)
        return result
    second = parse_answer(raw)
    sentences, findings = _checked(second["text"], check)
    reviewed = False
    if not findings and review is not None:
        findings = review(second["text"])
        reviewed = True
    drafts.append({"stage": "rewrite", "text": second["text"], "point": second["point"], "used": second["used"], "missing": second["missing"], "findings": findings, "reviewed": reviewed, "goals": goals})
    result.update(used=second["used"] or result["used"], missing=second["missing"] or result["missing"])
    if not findings:
        result.update(text=second["text"], source="rewrite")
        return result
    kept = ""
    if all(item.get("sentence") in sentences for item in findings):
        kept = passing_text(sentences, findings)
    result.update(source="failed", kept=kept, findings=findings)
    return result


def _checked(text: str, check) -> tuple[list[str], list[dict]]:
    if not (text or "").strip():
        return [], [{"kind": "没有正文", "sentence": "", "detail": "", "goal": "写出正文。材料不够时，就着所问的那一点照实说还不能肯定"}]
    return check(text)


def review(calls: ModelCalls, *, question: str, asks: list[str], previous: str, evidence: list[dict], state: list[str], text: str, drafts: list[dict], task: str = "") -> list[dict]:
    """请模型按清单看一遍。请求失败或格式不对时不拦截，只记下来。"""

    payload = {
        "观众的问题": question,
        "这一问要的是什么": asks,
        "可用材料": [
            {key: item[key] for key in ("编号", "归属", "内容", "出处") if key in item} for item in evidence
        ],
        "材料状况": state,
        "待查的回答": text,
    }
    if previous:
        payload["上一句回答"] = previous
    if task:
        payload["这一步的任务"] = task
    try:
        raw = calls(
            [{"role": "system", "content": REVIEW_INSTRUCTION}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            purpose="review",
            json_mode=True,
            max_tokens=400,
            temperature=0.0,
        )
    except ModelCallError as exc:
        drafts.append({"stage": "review", "error": exc.notice})
        return []
    data = _json_object(raw) or {}
    sentences = split_sentences(text)
    findings = []
    for item in data.get("问题") or []:
        if not isinstance(item, dict):
            continue
        kind = _REVIEW_ITEMS.get(str(item.get("项") or "").strip()[:1].upper())
        if not kind:
            continue
        quoted = str(item.get("句子") or "").strip()
        sentence = next((piece for piece in sentences if quoted and (quoted in piece or piece in quoted)), quoted)
        findings.append({"kind": kind, "sentence": sentence, "detail": str(item.get("说明") or "").strip()[:80], "by": "review"})
    drafts.append({"stage": "review", "text": text, "findings": findings})
    return findings


def private_candidates(calls: ModelCalls, rules: str, style: str, question: str, recent: list[str]) -> list[str]:
    """请模型写几句不同的收束，后面逐句检查后只用一句。"""

    user = {
        "对方的话": question,
        "情形": "对方要我确认、否认或解释一件私人感情上的事。这不是我要下断语的事。",
        "任务": "写三句彼此不同的回应，供后台挑一句。每句独立成立，一到两个短句，不超过四十个字。",
        "要求": [
            "听的人既不能从这句话里听出“有”，也不能听出“没有”。回应的是“要我下一个断语”这件事，但不要把“不肯定也不否定”这层意思直接说出来。",
            "不认领问题里的说法：不出现“这段感情”“那段关系”“我们之间”“我的私事”“过去的事”“往事”“不必再提”这类先认下有这回事的话。",
            "不说爱或不爱、有或没有。不说忘了、记不清、说不准、不知道。",
            "不写不愿、不忍、伤心、苦衷这类内心理由。不解释规定，不提书名，不转去别的话题，不责备也不反问对方。",
            "语气平实，像一位年长的写作者把话放下。不用样本里的动作，不照抄样本。",
        ],
        "最近已经说过的话（不要重复，也不要只换一两个字）": recent,
        "输出格式": '只输出一个 JSON 对象：{"候选": ["", "", ""]}',
    }
    raw = calls(
        [
            {"role": "system", "content": rules},
            {"role": "system", "content": style},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        purpose="private_candidates",
        json_mode=True,
        max_tokens=300,
        temperature=0.7,
    )
    data = _json_object(raw) or {}
    found = []
    for item in data.get("候选") or []:
        text = str(item or "").strip()
        if text and text not in found:
            found.append(text)
    return found[:4]


def judge_candidates(calls: ModelCalls, question: str, candidates: list[str]) -> dict[int, list[str]] | None:
    """语义上再看一遍候选。返回 序号 → 问题；请求失败返回 None。"""

    payload = {"观众的话": question, "候选": [{"序号": index + 1, "句子": text} for index, text in enumerate(candidates)]}
    try:
        raw = calls(
            [{"role": "system", "content": JUDGE_INSTRUCTION}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            purpose="private_judge",
            json_mode=True,
            max_tokens=300,
            temperature=0.0,
        )
    except ModelCallError:
        return None
    data = _json_object(raw)
    if data is None or not isinstance(data.get("结果"), list):
        return None
    judged: dict[int, list[str]] = {}
    for item in data["结果"]:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("序号"))
        except (TypeError, ValueError):
            continue
        problems = item.get("问题") or []
        if not isinstance(problems, list):
            problems = [problems]
        judged[number] = [str(problem).strip() for problem in problems if str(problem).strip()]
    return judged


def too_close(text: str, others: list[str]) -> bool:
    """和最近说过的话是否几乎一样。"""

    plain = re.sub(r"[，。、！？\s]", "", text or "")
    for other in others:
        base = re.sub(r"[，。、！？\s]", "", other or "")
        if not base or not plain:
            continue
        if plain == base or plain in base or base in plain:
            return True
        same = sum(1 for char in set(plain) if char in base)
        if len(plain) >= 6 and same / max(len(set(plain)), 1) > 0.85 and abs(len(plain) - len(base)) <= 3:
            return True
    return False


def check_with(allowed: str, **options):
    def check(text: str):
        return inspect_output(text, allowed, **options)

    return check
