"""可替换的检索接口。问答模块只依赖 search()，不依赖某一种数据库。"""

from __future__ import annotations

from typing import Protocol

from app.contracts import Evidence


class Retriever(Protocol):
    provider_id: str

    def search(self, query: str, *, limit: int = 4) -> list[Evidence]:
        """返回按相关性排序的证据。没有命中时返回空列表，不编造条目。"""

    def status_message(self) -> str:
        """给界面看的当前检索方式说明。"""
