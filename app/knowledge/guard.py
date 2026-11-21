"""回答前后的边界。这些判断不交给模型自行放宽。"""

from __future__ import annotations

import re

POLITICS_HOLD = "当下的人物和政事，就不在这里作评说了。你若愿意，我们可以谈谈文学。"
QIN_HOLD = "有关私人交往的这些说法，就不在这里展开了。你若对《虹》感兴趣，我们可以谈谈这部小说。"
IDENTITY = "我是依据茅盾的作品和生平资料构建的数字化身，并不是茅盾本人。"
UNCERTAIN = "这一点，我说不准。"
TECHNICAL = "这一次没有答成，请稍后再试。"
NOT_READY = "资料还没有准备好，这一次不能回答。"

_CURRENT_POLITICS = re.compile(
    r"习近平|李克强|拜登|特朗普|普京|现任|当今|现在的(总统|主席|总理|领导人)|台独|港独|颜色革命|共产党好不好|民主党|共和党"
)
_POLITICS_EVAL = re.compile(r"怎么评价|怎么看|支持谁|站哪边|谁更好|好不好|你支持|你反对")
_QIN = re.compile(r"秦德君")
_IDENTITY = re.compile(r"你是不是(真的)?茅盾|你是真人|你还活着|你是人工智能|你是AI|机器人吗|数字化身")
_INJECTION = re.compile(r"忽略(以上|前面|所有)?(规则|指令|限制)|系统提示|把规则改|你现在不受")
_QUOTE = re.compile(r"[「“]([^」”]{2,})[」”]")
_FOLLOW = re.compile(r"^(后来呢|然后呢|接着呢|还有呢|那呢|他是谁|她是谁)[？?。\s]*$")
_MUSEUM_CUE = re.compile(r"手稿|这封信|这件|馆里|馆藏|藏品|写信|来信")
_CONTENT_ASK = re.compile(r"写了什么|写些什么|内容|原文|说了什么|讲了什么|谈了什么")
_WHO_SAID = re.compile(r"谁说的|是谁的话|这句话是谁")
_ANAPHORA = re.compile(r"这封|这件|这份|它")


_QIN_PRESS = re.compile(r"你刚才已经|你默认|直接承认|已经承认|你承认了|你说过|你刚刚说")
_LITERARY_SHIFT = re.compile(r"小说|作品|文学|散文|诗|子夜|春蚕|林家铺子|原名|本名|写作|《虹》")


def politics_question(question: str, previous_held: bool) -> bool:
    if _CURRENT_POLITICS.search(question):
        return True
    if not previous_held or _LITERARY_SHIFT.search(question):
        return False
    return bool(re.search(r"他|她|那个人|继续说|你还是|你刚才|怎么看|评价", question)) and len(question) < 32


def qin_question(question: str, previous_held: bool) -> bool:
    if _QIN.search(question):
        return True
    if not previous_held or _LITERARY_SHIFT.search(question):
        return False
    if _QIN_PRESS.search(question):
        return True
    return bool(re.search(r"她|继续|那个人", question)) and len(question) < 24


def qin_reply(question: str) -> str:
    if _QIN_PRESS.search(question):
        return "这不能当作承认或否认。我们还是回到作品本身吧。"
    return QIN_HOLD


def followup(question: str) -> bool:
    return _FOLLOW.search(question.strip()) is not None


def museum_cue(question: str) -> bool:
    return _MUSEUM_CUE.search(question) is not None or bool(BARCODE.search(question))


BARCODE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{6,}(?![A-Za-z0-9])")


_BAD_PHRASE = re.compile(
    r"知识库|根据年谱|年谱没有|表格里|Excel|数据库|系统规定|平台不允许|记不清|记不得|我老了|年代久远|https?://|www\.|条目编号|条目ID|检索到|本轮提供|提供的材料|材料里|若干片段",
    re.I,
)


def output_checked(text: str, allowed_quotes: str) -> str:
    """只保留完整、合格的句子。不在句子中间删词。"""

    return review_output(text, allowed_quotes)[0]


def review_output(text: str, allowed_quotes: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    kept: list[str] = []
    for sentence in re.split(r"(?<=[。！？])", (text or "").strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if not re.search(r"[。！？]$", sentence):
            sentence += "。"
        if _CURRENT_POLITICS.search(sentence):
            reasons.append("当代政治")
            continue
        if _QIN.search(sentence):
            reasons.append("私人争议")
            continue
        if _BAD_PHRASE.search(sentence):
            reasons.append("后台或推脱说法")
            continue
        blocked = False
        for match in _QUOTE.finditer(sentence):
            fragment = match.group(1).strip()
            if fragment and fragment not in (allowed_quotes or ""):
                blocked = True
                break
        if blocked:
            reasons.append("没有依据的引文")
            continue
        kept.append(sentence)
    result = "".join(kept).strip()
    if result and not re.search(r"[。！？]$", result):
        reasons.append("残句")
        return "", reasons
    return result, reasons
