"""风格说明随每次生成发送。样本只教说法，不提供事实。"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config import ROOT

_PATH = ROOT / "data" / "persona" / "style_guide.txt"
_SCENE = re.compile(r"^【(.+?)】(\S+)\s*$", re.M)


@lru_cache(maxsize=1)
def _parsed() -> tuple[str, tuple[dict, ...]]:
    text = _PATH.read_text(encoding="utf-8").strip()
    marks = list(_SCENE.finditer(text))
    guide = text[: marks[0].start()].strip() if marks else text
    samples = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        samples.append({"scene": mark.group(1), "entry_id": mark.group(2), "body": text[mark.end() : end].strip()})
    return guide, tuple(samples)


def style_message(question: str, *, brief: bool = False) -> str:
    guide, samples = _parsed()
    if brief:
        return guide + "\n\n这一问一两句即可，不要把样本里的事情写进来。"
    chosen = _choose(question or "", samples)
    blocks = [guide, "本轮可参考的说法（不是事实，不能引用成原话）："]
    for sample in chosen:
        blocks.append(f"{sample['scene']}（{sample['entry_id']}）\n{sample['body']}")
    return "\n\n".join(blocks)


def _choose(question: str, samples: tuple[dict, ...]) -> list[dict]:
    wanted: list[str] = []
    if re.search(r"信|写信|通信", question):
        wanted.append("通信与评论")
    if re.search(r"为什么|原因|做什么用|用来", question):
        wanted.append("说明原因")
    if re.search(r"小时候|童年|记得|后来觉得", question):
        wanted.append("回看经历")
    if re.search(r"意见|要求|批评|不同意|怎么看", question):
        wanted.append("直接提意见")
    if re.search(r"有把握|不确定|部分|一方面", question):
        wanted.append("区分把握")
    if not wanted:
        wanted = ["区分把握", "直接提意见"]
    picked = []
    for name in wanted:
        for sample in samples:
            if sample["scene"] == name and sample not in picked:
                picked.append(sample)
        if len(picked) >= 2:
            break
    if len(picked) < 2:
        for sample in samples:
            if sample not in picked:
                picked.append(sample)
            if len(picked) >= 2:
                break
    return picked[:2]
