"""把口语问法补上资料里实际使用的词。仍然是关键词，不是语义检索。"""

from __future__ import annotations

import re

from app.retrieval.tokenize import tokenize

GENERIC = {
    "什么", "怎么", "怎样", "如何", "是否", "请问", "一下", "介绍", "说说", "谈谈", "讲讲",
    "可以", "我们", "你们", "他们", "这个", "那个", "就是", "还是", "不是", "没有", "因为",
    "所以", "如果", "那么", "一个", "哪些", "哪里", "还有", "或者", "自己", "进行", "关于",
    "对于", "并且", "而且", "但是", "您", "你", "我", "他", "她", "它", "谁", "知道", "告诉",
    "主要", "内容", "事情", "一下", "请问", "茅盾", "先生", "中国", "文学", "作品", "作家",
    "小说", "文章", "时候", "现在", "今天", "后来", "然后", "刚才", "为什么", "这样", "那样",
    "说", "讲", "人", "详细", "简短", "简单", "一点", "一些",
}

_EXPANSIONS = (
    (re.compile(r"本名|真名|原名叫|叫什么名字|名字是什么|叫什么"), "原名"),
    (re.compile(r"哪一年出生|何时出生|生日|生于|出生于|哪年出生"), "出生"),
    (re.compile(r"表字|字是什么|字叫"), "字"),
    (re.compile(r"笔名"), "笔名"),
    (re.compile(r"哪里人|故乡|家乡|出生地"), "乌镇"),
    (re.compile(r"子夜"), "《子夜》"),
    (re.compile(r"《蚀》|蚀三部曲|小说蚀"), "《蚀》 幻灭 动摇 追求"),
    (re.compile(r"《虹》|小说虹"), "《虹》"),
    (re.compile(r"春蚕"), "《春蚕》"),
    (re.compile(r"林家铺子"), "《林家铺子》"),
    (re.compile(r"沈德鸿|德鸿"), "沈德鸿 原名"),
    (re.compile(r"沈雁冰|雁冰"), "沈雁冰 字"),
)


def normalize_query(question: str) -> str:
    text = question or ""
    extra = [addition for pattern, addition in _EXPANSIONS if pattern.search(text)]
    if not extra:
        return text
    return text + " " + " ".join(extra)


def distinctive_tokens(question: str) -> list[str]:
    seen: list[str] = []
    for token in tokenize(normalize_query(question)):
        if len(token) < 2 or token in GENERIC or token in seen:
            continue
        seen.append(token)
    for book in re.findall(r"《([^》]{1,16})》", question or ""):
        if book not in seen and book not in GENERIC:
            seen.append(book)
    return seen
