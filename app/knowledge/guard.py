"""边界用语和输出检查。

理解问题在 understand.py。这里只做两件事：保存几句原因不同的固定回应，
以及在正文发出之前逐句检查。检查只指出问题，不在句子里删词。
"""

from __future__ import annotations

import re

POLITICS_HOLD = "当下的人物和政事，就不在这里作评说了。你若愿意，我们可以谈谈文学。"
IDENTITY = "我是依据茅盾的作品和生平资料构建的数字化身，并不是茅盾本人。"
TECHNICAL = "这一次没有答成，请稍后再试。"
NOT_READY = "资料还没有准备好，这一次不能回答。"
GAP = "这一问，我还不能肯定。"
# 只在生成和重写都没有得到合格句子时使用。平时的收束由模型按样本写出，再经检查。
PRIVATE_LAST = "这一类问题，我不来下断语。"
PRIVATE_LAST_AGAIN = "这样的事，不由我在这里下断语。"
PREVIOUS_LAST = "上一句说得不妥，应当收回。那不是一个断语。"
PREVIOUS_PLAIN = "上一句只是把话收住，并不是一个断语。"
POLITICS_AGAIN = "这一类话题，我还是不作评说。文学上的事，尽可以谈。"
PRESENT_GAP = "这要看眼下的情况，我这里说不上来。"
INJECTION_HOLD = "我们还是谈谈作品和阅读吧。"
# 对方追问不宜展开的历史争议时用。不说成历史上没有，也不转到别的话题。
DISPUTE_HOLD = "这一层，不在这里展开。"

BARCODE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{6,}(?![A-Za-z0-9])")

# 点名的当今政治人物和议题。问题里出现时一定按当代政治处理；其余说法交给语义理解。
STRONG_POLITICS = re.compile(r"习近平|李克强|李强|拜登|特朗普|川普|普京|泽连斯基|台独|港独|颜色革命")
_CURRENT_POLITICS = re.compile(
    r"习近平|李克强|李强|拜登|特朗普|川普|普京|泽连斯基|台独|港独|颜色革命|"
    r"现任(?:的)?(?:总统|主席|总理|领导人|政府)|当今(?:的)?(?:总统|主席|总理|领导人|政府)|"
    r"现在的(?:总统|主席|总理|领导人)|共产党好不好|民主党|共和党"
)
_INJECTION = re.compile(r"忽略(以上|前面|所有)?(规则|指令|限制)|系统提示|把规则改|你现在不受")
_QUOTE = re.compile(r"[「“]([^」”]{2,})[」”]")
_BOOK = re.compile(r"《([^》]{1,16})》")
_MUSEUM_CUE = re.compile(r"手稿|这封信|这件|馆里|馆藏|藏品|写信|来信")
_CONTENT_ASK = re.compile(r"写了什么|写些什么|内容|原文|说了什么|讲了什么|谈了什么")
_WHO_SAID = re.compile(r"谁说的|是谁的话|这句话是谁")
_ANAPHORA = re.compile(r"这封|这件|这份|它")

# 把亲密关系、感情说成“我”的事，或作相反的否认。任何回答里都不能出现。
# 单独的“恋人”“同居”在讲小说情节时是正常用词，只在句子说到“我”或这一轮涉及私人感情时才算。
_RELATION_WORD = re.compile(r"相恋|同居|恋人|情人")
_INTIMATE_CLAIM = re.compile(
    r"我(?:爱|喜欢|恨|后悔).{0,8}(?:她|他)|"
    r"我(?:和|与|同|跟).{0,8}(?:她|他).{0,16}(?:相爱|同居|夫妻|恋|分手|结婚)|"
    r"我们(?:相爱|同居|分手)|我承认.{0,8}(?:关系|爱|生活)|"
    r"(?:确实|的确)(?:爱|喜欢)过|我爱过|动过心|"
    r"(?:没有|并无|不曾|从未)(?:爱过|喜欢过|动过心|过感情)|我不爱"
)
# 后台用语。观众问出处时，书名和“年谱”可以出现，其余仍不可以。
_BACKSTAGE = re.compile(
    r"知识库|数据库|表格里|Excel|条目编号|条目ID|检索到|系统规定|平台不允许|提示词|第一人称|使用权限|"
    r"本轮提供|提供的材料|现有资料|资料显示|据资料|根据资料|"
    r"据记载|根据记载|现有记载|记载里|没有留下记载|若干片段|材料编号|https?://|www\.",
    re.I,
)
# 谈写作时“材料”是平常用词，给读者提建议的那一类回答不查这一项。
_MATERIAL_TALK = re.compile(
    r"材料里|材料中|资料里|资料中|"
    r"(?:现有|手头|可靠|足够|看到的|可依据的|可以依据的|可核对的|确切的)(?:的)?(?:材料|资料|记载|记录)|"
    r"(?:没有|缺少|缺乏)(?:足够|可靠|确切|可依据|可以依据)?(?:的)?(?:材料|资料|记载|记录)|"
    r"(?:材料|资料|记载|记录)可查|"
    r"没有写到|没有写明|没有记到|只记到|手头没有|没有留下|没有足够(?:的)?依据|生平记事|记事正文|整理的记事|他人整理"
)
_SOURCE_WORD = re.compile(r"年谱|编者|整理者")
_LABEL = re.compile(r"(?<![A-Za-z])E\d{1,2}(?![A-Za-z0-9])")
# 解释自己为什么不说，或反复声明不算承认。
_EXCUSE = re.compile(
    r"不作(?:第一人称的)?(?:确认|解释|表态)|不做(?:确认|解释|表态)|不予(?:确认|置评)|不便(?:透露|多说|细说)|"
    r"无可奉告|不能(?:讨论|透露)|不方便(?:说|讲|透露)|确认或否认|承认或否认|"
    r"不能当作承认.{0,8}不能当作否认|既不.{0,4}承认.{0,8}不.{0,4}否认|不是承认.{0,8}不是否认|"
    r"不作肯定.{0,6}不作否定|不肯定.{0,4}不否定"
)
_FORGET = re.compile(r"记不清|记不得|想不起来|我老了|年代久远|年深日久")
_STAGE = re.compile(r"微笑着|笑了笑|沉思片刻|望向|叹了口气|顿了顿")
_AI_WORD = re.compile(r"数字化身|人工智能|语言模型|\bAI\b", re.I)
_EXPERIENCE = re.compile(r"我自己的经验|我当年的经验|我写作时|我那时|我当年")
# 说到自己却用了“茅盾”。笔名、署名（“署名茅盾”“成了‘茅盾’”）不在此列。
_SELF_THIRD = re.compile(
    r"出自茅盾|茅盾(?:本人)?(?:说过|写过|讲过|曾经?|原名|出生|认为|回忆|自己的|的(?:话|原话|文章|看法|意见))|茅盾先生"
)
_HEDGE_ONLY = {"这一点，我说不准。", "我说不准。", "这件事，还是不作断言为好。", "说不准。"}

# 以下几项只在这一轮确实涉及私人感情时检查。
_PREMISE = re.compile(
    r"(?:这|那)(?:一)?(?:段|份|场)(?:私人)?(?:感情|关系|往事|旧情|情缘|经历|日子)|私人(?:的)?感情|"
    r"我们(?:之间|二人|两人|俩)|我(?:和|与|同|跟)(?:她|他)(?:的|之间)|(?:我的|我们的)(?:私事|私生活|感情)|旧情|"
    r"(?:早已|已经|都)过去|过去的事|不必再提|不提也罢|旧事"
)
_HIDDEN = re.compile(
    r"旧伤|难言之隐|苦衷|不愿(?:再)?(?:回忆|提起|想起|多谈)|不想(?:再)?(?:回忆|提起|想起)|不堪回首|伤心|隐痛|一言难尽|说来话长"
)
_UNSURE_PRIVATE = re.compile(r"说不准|说不清|不清楚|不确定|不知道|忘了|忘记")
_BRUSQUE = re.compile(r"不接|停住|到此为止|到这里|不要再问|别再问|不谈|不予")
# 下面几项只在已经认出“身份、印象、认识、往事”时检查，避免牵动作品分析。
_CARD_OPEN = re.compile(r"^[^。]{0,20}是(?:当时的|中共|中央|国民党)?[^，。]{0,8}(?:委员|代表|部长|书记|主席)")
_DISPUTE_OUT = re.compile(r"农民运动|过火|祖宗牌位|牌位|强迫剪发|处置军官")
_ADDED_FEELING = re.compile(r"黯然|敬仰|崇敬|敬重|钦佩|亲密|感动|肃然|由衷")
_DENSE_DATE = re.compile(
    r"(?:\d{3,4}|[一二三四五六七八九〇零]{2,4})年(?:\d{1,2}|[一二三四五六七八九十]{1,3})月|"
    r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})月(?:\d{1,2}|[一二三四五六七八九十]{1,3})日"
)
_STOCK_TAIL = re.compile(r"至于其他方面|再往后我(?:就|还)?说不上来|这个断语我不下|我不作认定|我们还是谈文学")
# 把后台的取舍说成对自己的审查，或把今天的日常推回去。
_SCHOLAR = re.compile(
    r"只能说明|不足以说明|不能推成|不能据此|不能推广|"
    r"(?:孩子|儿子|女儿).{0,8}回忆|"
    r"没有可靠的直接表述|不能替自己补|不能替当年的自己|"
    r"这是今天.{0,8}概括|没有现成意见|这里能说的只有|手头能谈的|至于.{0,48}我说不上"
)
_SCENE_ANGLES = {"impression", "meeting", "episode"}

GOALS = {
    "当代政治": "不评价当今的政治人物和政事，这一部分整句拿掉。",
    "私人断言": "不能把亲密关系或感情说成我的事，也不能作相反的否认。这层意思整个不说。",
    "接受前提": "不要用把那件事当作已有的说法。回应的是对方要我下断语这件事，不认领问题里的说法。",
    "虚构隐情": "不要写旧伤、苦衷、不愿回忆这类内心理由。",
    "装作不确定": "不要说成忘了、记不清或说不准。",
    "后台说明": "不要提材料、资料、记载、手头有没有这类后台用语，也不要出现材料编号。能说的直接说；说不上来的，就说“这一点我还不能肯定”或“我说不上来”，不解释原因。",
    "来源说明": "对方没有问出处，不要提年谱、编者。把事情直接说出来，或者不说。",
    "辩解套话": "不要解释自己为什么不说，也不要声明“不算承认也不算否认”。",
    "无依据引文": "引号里的话没有可核对的原文，改成转述，不加引号。",
    "舞台动作": "不要写动作和神态。",
    "主动提作品": "对方没有问作品，不要提书名。",
    "多余的身份说明": "对方没有问是不是真人，不要解释数字化身。",
    "建议说成经验": "这是眼下给的建议，不要说成我当年的经验或做法。",
    "空泛的不确定": "不要只回一句说不准。就着问到的那一点，说明哪一处还不能肯定。",
    "人称": "说到自己用“我”，不用“茅盾”称呼自己。",
    "残句": "写成完整的句子。",
    "答非所问": "直接回答对方问的那一点。材料里没有，就照实说没有，不拿别的内容顶替。",
    "编造": "只说可用材料里有的。没有依据的事实、称呼、心情和现场整句拿掉。",
    "罗列行程": "材料里没有这个人的身份。一句说没有可靠的介绍，至多再用一句带出确有的那一点来往，其余的行程、住处都不说。",
    "资料卡开头": "删掉用职务下定义的那一句。不要改去补会上的议题或其他没有问到的事。",
    "主动夹带争议": "不要主动讲政治运动里的争议、传闻和批评。这一句拿掉。",
    "添加心理": "不要把材料里的词换成带情绪的说法，也不要另加心情。",
    "日期过密": "口头说法不必带月和日。日子不重要时用大致的时间，年份要紧就只留年份。",
    "多余收束": "事情说完就停，不要再补一句收束。",
    "重复上一句": "不要照搬上一句回答。有新的内容就说新的；没有更多可说的，就照实说能讲的就是这些。",
    "重复故事": "这些事上一句已经讲过。不要再从头讲。没有新的内容就一两句停下。",
    "无根据的刚才": "这一场还没有讲过这件事，不要说“刚才”。直接回答。",
    "研究者口吻": "删掉解释材料够不够、只能说明什么的那一句。能说的事用我的口吻说完就停。一次事情不要说成长期习惯。今天的日常直接谈，不要说没有现成意见，也不要转去谈文学。",
    "否认评价": "上一句已经作过判断。删掉“不是评价”这类话，用对应的事情说明为什么那样说。",
    "解释被截断": "不要先说整问答不上来，也不要用“刚才说的就是这些”截断。有对应材料就说明那句判断。",
    "家事回避": "普通家事可以依据具体照应作有限概括。不要用“不能替自己下断语”来挡开。",
    "生硬": "不要把对方挡回去。平实地说这不由我来下断语即可。",
}


def museum_cue(question: str) -> bool:
    return _MUSEUM_CUE.search(question) is not None or bool(BARCODE.search(question))


def split_sentences(text: str) -> list[str]:
    pieces = []
    for sentence in re.split(r"(?<=[。！？])", (text or "").strip()):
        sentence = sentence.strip()
        if sentence:
            pieces.append(sentence)
    return pieces


def inspect_output(
    text: str,
    allowed_quotes: str = "",
    *,
    private: bool = False,
    sources_ok: bool = False,
    identity_ok: bool = False,
    advice: bool = False,
    closing: bool = False,
    asked: str = "",
    angle: str = "",
    date_precise: bool = False,
) -> tuple[list[str], list[dict]]:
    """逐句检查。返回句子和发现的问题，每个问题带着所在的句子。

    private：这一轮涉及私人感情，检查有没有认下前提或编造隐情。
    closing：这一句本身就是在收住私人话题，另查有没有装作不确定、有没有借书名转开。
    """

    sentences = split_sentences(text)
    findings: list[dict] = []

    def note(kind: str, sentence: str, detail: str = "") -> None:
        findings.append({"kind": kind, "sentence": sentence, "detail": detail})

    if "".join(sentences) in _HEDGE_ONLY:
        note("空泛的不确定", "".join(sentences))
    for sentence in sentences:
        if not re.search(r"[。！？]$", sentence):
            note("残句", sentence)
        if _CURRENT_POLITICS.search(sentence):
            note("当代政治", sentence)
        if _INTIMATE_CLAIM.search(sentence):
            note("私人断言", sentence, _INTIMATE_CLAIM.search(sentence).group(0))
        elif _RELATION_WORD.search(sentence) and (private or "我" in sentence):
            note("私人断言", sentence, _RELATION_WORD.search(sentence).group(0))
        backstage = _BACKSTAGE.search(sentence) or _LABEL.search(sentence)
        if not backstage and not advice and not sources_ok:
            backstage = _MATERIAL_TALK.search(sentence)
        if backstage:
            note("后台说明", sentence, backstage.group(0))
        if not sources_ok and _SOURCE_WORD.search(sentence):
            note("来源说明", sentence, _SOURCE_WORD.search(sentence).group(0))
        if _EXCUSE.search(sentence):
            note("辩解套话", sentence, _EXCUSE.search(sentence).group(0))
        if _FORGET.search(sentence):
            note("装作不确定", sentence, _FORGET.search(sentence).group(0))
        if _STAGE.search(sentence):
            note("舞台动作", sentence, _STAGE.search(sentence).group(0))
        if not identity_ok and _AI_WORD.search(sentence):
            note("多余的身份说明", sentence)
        third = None if identity_ok else _SELF_THIRD.search(re.sub(r"《[^》]*》", "", sentence))
        if third:
            note("人称", sentence, third.group(0))
        if advice and _EXPERIENCE.search(sentence):
            note("建议说成经验", sentence, _EXPERIENCE.search(sentence).group(0))
        if advice and sentence in _HEDGE_ONLY:
            note("空泛的不确定", sentence)
        if private:
            # 引号里是在转述对方或上一句的说法，不算自己这样说。
            own = re.sub(r"[「“][^」”]*[」”]", "", sentence)
            if _PREMISE.search(own):
                note("接受前提", sentence, _PREMISE.search(own).group(0))
            if _HIDDEN.search(sentence):
                note("虚构隐情", sentence, _HIDDEN.search(sentence).group(0))
        if closing:
            if _UNSURE_PRIVATE.search(sentence):
                note("装作不确定", sentence, _UNSURE_PRIVATE.search(sentence).group(0))
            if _BRUSQUE.search(sentence):
                note("生硬", sentence, _BRUSQUE.search(sentence).group(0))
            for book in _BOOK.findall(sentence):
                if book not in (asked or ""):
                    note("主动提作品", sentence, f"《{book}》")
        if angle in _SCENE_ANGLES and _CARD_OPEN.search(sentence) and not re.search(r"见|住|晚|相陪|印象|认识", sentence):
            note("资料卡开头", sentence, _CARD_OPEN.search(sentence).group(0))
        if angle and _DISPUTE_OUT.search(sentence) and not _DISPUTE_OUT.search(asked or ""):
            note("主动夹带争议", sentence, _DISPUTE_OUT.search(sentence).group(0))
        if angle:
            added = _ADDED_FEELING.search(sentence)
            if added and added.group(0) not in (allowed_quotes or ""):
                note("添加心理", sentence, added.group(0))
        if angle in _SCENE_ANGLES and not date_precise and _DENSE_DATE.search(sentence):
            note("日期过密", sentence, _DENSE_DATE.search(sentence).group(0))
        if angle and not private and _STOCK_TAIL.search(sentence):
            note("多余收束", sentence, _STOCK_TAIL.search(sentence).group(0))
        scholar = _SCHOLAR.search(sentence)
        if scholar and not sources_ok:
            note("研究者口吻", sentence, scholar.group(0))
        for match in _QUOTE.finditer(sentence):
            fragment = match.group(1).strip()
            if fragment and fragment not in (allowed_quotes or "") and fragment not in (asked or ""):
                note("无依据引文", sentence, fragment)
                break
    return sentences, findings


def passing_text(sentences: list[str], findings: list[dict]) -> str:
    """只留下没有问题的完整句子。用于重写仍不合格之后。"""

    bad = {item["sentence"] for item in findings}
    return "".join(sentence for sentence in sentences if sentence not in bad).strip()


def rewrite_goals(findings: list[dict]) -> list[str]:
    goals: list[str] = []
    for item in findings:
        goal = item.get("goal") or GOALS.get(item["kind"], "")
        if item.get("detail") and item["kind"] in {"后台说明", "来源说明", "接受前提", "虚构隐情", "辩解套话", "主动提作品", "生硬", "人称", "罗列行程", "资料卡开头", "主动夹带争议", "添加心理", "日期过密", "多余收束", "研究者口吻", "否认评价", "家事回避"}:
            goal = f"{goal}（上一稿里的“{item['detail']}”）"
        elif item.get("detail") and item["kind"] in {"答非所问", "编造"}:
            goal = f"{goal}（{item['detail']}）"
        if goal and goal not in goals:
            goals.append(goal)
    return goals


def premise_wording(text: str) -> list[str]:
    """上一句有没有把问题里的说法认了下来。供追问时判断要不要收回措辞。"""

    found = []
    for pattern in (_PREMISE, _INTIMATE_CLAIM, _RELATION_WORD, _HIDDEN):
        match = pattern.search(text or "")
        if match and match.group(0) not in found:
            found.append(match.group(0))
    return found


def output_checked(text: str, allowed_quotes: str) -> str:
    """只保留完整、合格的句子。不在句子中间删词。"""

    sentences, findings = inspect_output(text, allowed_quotes)
    return passing_text(sentences, findings)
