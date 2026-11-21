"""模块之间传递的数据结构。换供应商时保持这些字段不变。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def to_plain(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    return value


@dataclass
class ModuleStatus:
    module: str
    provider: str
    state: str
    message: str

    @property
    def connected(self) -> bool:
        return self.state == "ready"


@dataclass
class NeighborPassage:
    entry_id: str
    placement: str
    original_text: str
    summary: str
    page: str
    paragraph_no: str


@dataclass
class Evidence:
    """一条可展示、可追溯的检索证据。"""

    entry_id: str
    source_id: str
    original_text: str
    summary: str
    context_note: str
    source_title: str
    collection_no: str
    volume: str
    page: str
    paragraph_no: str
    chapter: str
    section: str
    author: str
    speaker: str
    speech_kind: str
    narrative_level: str
    event_title: str
    fact_tags: str
    time_label: str
    place: str
    review_status: str
    is_demo: bool
    can_quote: bool
    score: float
    matched_terms: list[str] = field(default_factory=list)
    match_basis: str = "original"
    neighbors: list[NeighborPassage] = field(default_factory=list)
    provider: str = "local"


@dataclass
class ControlCue:
    """给画面和动作用，不进入朗读文本。"""

    mood: str = "calm"
    gesture: str = "idle"
    note: str = ""


@dataclass
class Citation:
    entry_id: str
    source_title: str
    page: str
    paragraph_no: str
    speaker: str
    speech_kind: str
    is_demo: bool
    can_quote: bool
    quote: str
    score: float


@dataclass
class AnswerResult:
    spoken_text: str
    system_notice: str
    citations: list[Citation]
    evidence: list[Evidence]
    rejected: list[Evidence]
    control: ControlCue
    model_called: bool
    provider: str
    turn_id: str = ""
    session_id: str = ""
    epoch: int = 0
    discarded: bool = False


@dataclass
class Transcript:
    raw_text: str
    corrected_text: str
    changes: list[dict[str, str]]
    provider: str
    session_id: str
    turn_id: str


@dataclass
class AudioSegment:
    utterance_id: str
    turn_id: str
    session_id: str
    epoch: int
    text: str
    audio: bytes
    mime: str


@dataclass
class AvatarEvent:
    """2D 或三维人物都接收同一种事件，不假设只靠文字就能生成嘴型。"""

    session_id: str
    turn_id: str
    segment_id: str
    epoch: int
    event_type: str
    payload: dict[str, Any]
