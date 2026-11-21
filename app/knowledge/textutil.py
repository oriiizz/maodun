"""比较原文时忽略空白和引号，不把排版差异当成事实冲突。"""

from __future__ import annotations

import re

_DROP = re.compile(r"[\s\u3000\"“”'‘’「」『』]")


def comparable(text: str) -> str:
    return _DROP.sub("", text or "")


def clean(text: object) -> str:
    if text is None:
        return ""
    return str(text).replace("\u3000", " ").strip()
