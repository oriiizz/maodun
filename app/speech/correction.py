"""识别纠错只在有上下文时替换。单独的“矛盾”不会被改成“茅盾”。"""

from __future__ import annotations

import re


PROTECTED = (
    "自相矛盾",
    "前后矛盾",
    "矛盾之处",
    "矛盾冲突",
    "矛盾心理",
    "这个矛盾",
    "这种矛盾",
    "有矛盾",
    "的矛盾",
)

# 夜和业同音。子和职只差平翘舌和声调，通用识别会偏向常见词“职业”。
ZIYE_HEARD = "职业|子叶|紫夜|自夜|只夜|字夜"
OCCUPATION_PHRASES = (
    "职业生涯",
    "职业道德",
    "职业教育",
    "职业规划",
    "职业选择",
    "职业技能",
    "职业病",
    "职业发展",
    "职业培训",
    "职业学校",
    "职业身份",
    "职业道路",
    "职业状况",
    "职业生活",
    "职业构成",
    "职业装",
    "职业女性",
    "职业男性",
    "职业作家",
    "职业编辑",
    "职业记者",
    "职业革命",
    "职业倦怠",
    "你的职业",
    "我的职业",
    "他的职业",
    "她的职业",
    "您的职业",
    "本人的职业",
    "自己的职业",
    "个人的职业",
    "别人的职业",
    "大家的职业",
    "人们的职业",
    "社会上的职业",
    "各种职业",
    "多种职业",
    "不同职业",
    "其他职业",
    "任何职业",
    "所有职业",
    "什么职业",
    "哪种职业",
    "哪个职业",
    "一份职业",
    "一种职业",
    "这个职业",
    "那个职业",
    "某种职业",
    "选择职业",
    "从事职业",
    "找职业",
    "作为职业",
    "当作职业",
    "当成职业",
    "为职业",
    "的职业是",
    "的职业都",
    "的职业很多",
    "的职业不少",
    "职业是什么",
    "职业是谁",
    "职业是作家",
    "职业是编辑",
    "职业是记者",
    "职业是教员",
)
def correct_transcript(text: str, lexicon: set[str] | None = None) -> dict[str, object]:
    """返回原始文本、纠正后文本，以及每一处改动的理由。"""
    raw = text or ""
    lexicon = lexicon or set()
    masked, replacements = _mask(raw)
    corrected = masked
    changes: list[dict[str, str]] = []

    def substitute(pattern: str, replacement: str, reason: str) -> None:
        nonlocal corrected

        def repl(match: re.Match[str]) -> str:
            changes.append({"from": match.group(0), "to": replacement, "reason": reason})
            return replacement

        corrected = re.sub(pattern, repl, corrected)

    substitute(r"矛盾(?=先生|同志|全集)", "茅盾", "后面紧挨着称谓或“全集”，更像人名而不是“矛盾”这个词")
    if "子夜" in lexicon:
        corrected = _mask_phrases(corrected, OCCUPATION_PHRASES, replacements)
        substitute(
            rf"(?<=《)(?:{ZIYE_HEARD})(?=》)",
            "子夜",
            "出现在书名号里，资料词表中有作品《子夜》",
        )
        substitute(
            rf"职业|(?<![片树落黄绿新嫩枯])子叶|紫夜|自夜|只夜|字夜",
            "子夜",
            "夜和业同音，子和职又只差平翘舌。已排除职业搭配后，其余“职业”按作品《子夜》纠正",
        )
    corrected = _unmask(corrected, replacements)
    return {"raw_text": raw, "corrected_text": corrected, "changes": changes}


def _mask(text: str) -> tuple[str, list[str]]:
    kept: list[str] = []
    return _mask_phrases(text, PROTECTED, kept), kept


def _mask_phrases(text: str, phrases: tuple[str, ...], kept: list[str]) -> str:
    masked = text
    for phrase in phrases:

        def repl(match: re.Match[str]) -> str:
            kept.append(match.group(0))
            return f"\ue000{len(kept) - 1}\ue001"

        masked = re.sub(re.escape(phrase), repl, masked)
    return masked


def _unmask(text: str, kept: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        return kept[int(match.group(1))]

    return re.sub(r"\ue000(\d+)\ue001", repl, text)
