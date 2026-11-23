"""风格说明随每次生成发送。样本只教说法，不提供事实。"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config import ROOT

_PATH = ROOT / "data" / "persona" / "style_guide.txt"
_SCENE = re.compile(r"^【(.+?)】(\S+)\s*$", re.M)
# 没有指定场景时可以补位的样本。收住话头和介绍人物只在对应的问题里用。
_GENERAL = {"说明原因", "区分把握", "回看经历", "先事后判断", "直接提意见", "通信与评论"}


@lru_cache(maxsize=2)
def _load(stamp: int) -> tuple[str, tuple[dict, ...]]:
    text = _PATH.read_text(encoding="utf-8").strip()
    # “缺的样本”是给维护者看的记录，不发给模型。
    text = text.split("\n缺的样本", 1)[0].strip()
    marks = list(_SCENE.finditer(text))
    guide = text[: marks[0].start()].strip() if marks else text
    samples = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        samples.append({"scene": mark.group(1), "entry_id": mark.group(2), "body": text[mark.end() : end].strip()})
    return guide, tuple(samples)


def _parsed() -> tuple[str, tuple[dict, ...]]:
    """文件改动后，下一次提问就读到新内容。"""

    return _load(_PATH.stat().st_mtime_ns)


_parsed.cache_clear = _load.cache_clear  # type: ignore[attr-defined]


def style_version() -> str:
    import hashlib

    return hashlib.sha1(_PATH.read_bytes()).hexdigest()[:10]


def sample_quotes() -> dict[str, str]:
    """样本编号对应的“原文”一行，供核对是否与资料库一致。"""

    found = {}
    for sample in _parsed()[1]:
        match = re.search(r"^原文：(.*)$", sample["body"], re.M)
        found[sample["entry_id"]] = match.group(1).strip() if match else ""
    return found


def style_choice(question: str, *, brief: bool = False, scenes: list[str] | None = None) -> tuple[str, list[str]]:
    """返回风格说明和本轮选用的样本编号。scenes 由这一问的去向指定；没有指定时按问题里的词挑。"""

    guide, samples = _parsed()
    if brief:
        return guide + "\n\n这一问一两句即可，不要把样本里的事情写进来。", []
    chosen = _by_scene(scenes, samples) if scenes else _choose(question or "", samples)
    blocks = [guide, "本轮可参考的说法（不是事实，不能引用成原话）："]
    for sample in chosen:
        blocks.append(f"{sample['scene']}（{sample['entry_id']}）\n{sample['body']}")
    return "\n\n".join(blocks), [sample["entry_id"] for sample in chosen]


def _by_scene(scenes: list[str], samples: tuple[dict, ...]) -> list[dict]:
    picked = []
    for name in scenes:
        for sample in samples:
            if sample["scene"] == name and sample not in picked:
                picked.append(sample)
    return picked[:2]


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
    picked = _by_scene(wanted, samples)
    if len(picked) < 2:
        for sample in samples:
            if sample not in picked and sample["scene"] in _GENERAL:
                picked.append(sample)
            if len(picked) >= 2:
                break
    return picked[:2]
