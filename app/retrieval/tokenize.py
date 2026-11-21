"""中文分词。英文按空格切开的办法不能直接用于中文，所以这里用结巴分词，并保留原文字串供核对。"""

from __future__ import annotations

import json
import logging
import re

import jieba

_READY = False
_EXTRA_WORDS: set[str] = set()


def configure_tokenizer(lexicon_words: list[str]) -> None:
    global _READY
    jieba.setLogLevel(logging.ERROR)
    for word in lexicon_words:
        cleaned = word.strip()
        if len(cleaned) >= 2:
            jieba.add_word(cleaned, freq=100000)
            _EXTRA_WORDS.add(cleaned)
    _READY = True


def tokenize(text: str) -> list[str]:
    if not _READY:
        jieba.setLogLevel(logging.ERROR)
    pieces = []
    for token in jieba.lcut(text or ""):
        token = token.strip().lower()
        if not token or token.isspace():
            continue
        if re.fullmatch(r"[\W_]+", token, flags=re.UNICODE):
            continue
        pieces.append(token)
    return pieces


def dumps_tokens(tokens: list[str]) -> str:
    return json.dumps(tokens, ensure_ascii=False)


def loads_tokens(payload: str | None) -> list[str]:
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data if str(item).strip()]
