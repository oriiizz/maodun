"""把不同表格的列名对应到同一套字段。认不出的列会原样保留，不丢弃。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# 长名称放在前面，避免“原文”抢先匹配“原文引用（OCR文本）”。
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "external_entry_id": ("条目ID", "条目id", "段落ID", "资料条目ID"),
    "volume_label": ("册别",),
    "chapter": ("章节",),
    "section": ("小节",),
    "time_label": ("时间", "年代", "时期"),
    "place": ("地点", "场所"),
    "core_person": ("核心人物",),
    "relation_to_subject": ("与茅盾关系",),
    "related_people": ("相关人物",),
    "person_relations": ("人物关系",),
    "event_title": ("事件标题", "标题"),
    "summary": ("客观内容概括", "内容概括", "AI概括", "概括"),
    "narrative_level": ("叙述层级",),
    "information_provider": ("原始信息提供者", "信息提供者"),
    "speaker": ("原始发言者", "发言者", "说话人"),
    "original_text": ("原文引用（OCR文本）", "原文引用(OCR文本)", "原文引用", "原文", "正文", "OCR文本"),
    "context_note": ("规范化与上下文说明", "规范化文本", "上下文说明"),
    "source_title": ("来源书名", "书名"),
    "collection_no": ("馆藏编号", "馆藏号"),
    "volume": ("册次",),
    "page": ("页码", "页"),
    "paragraph_no": ("段落编号",),
    "fact_tags": ("事实类RAG标签", "事实标签"),
    "persona_tags": ("人格类RAG标签", "人格标签"),
    "style_tags": ("风格类RAG标签", "语言风格标签", "风格标签"),
    "answerable_questions": ("可回答问题",),
    "answer_points": ("回答要点",),
    "style_evidence": ("可作为茅盾语言风格证据", "语言风格证据"),
    "evidence_type": ("证据类型",),
    "evidence_strength": ("证据强度",),
    "review_status_raw": ("事实核验状态", "核验状态", "审核状态"),
    "author": ("作者",),
    "material_type": ("资料类型", "类型"),
    "person_info_raw": ("人物信息",),
    "source_nature_raw": ("信息来源性质",),
    "source_locator": ("来源定位",),
    "search_tags_raw": ("检索标签",),
    "evidence_status_raw": ("证据状态",),
}


PACKED_TARGETS = {
    "person_info_raw": {
        "核心人物": "core_person",
        "与茅盾关系": "relation_to_subject",
        "相关人物": "related_people",
        "人物关系": "person_relations",
    },
    "source_nature_raw": {
        "叙述层级": "narrative_level",
        "信息提供者": "information_provider",
        "原始信息提供者": "information_provider",
        "原始发言者": "speaker",
        "发言者": "speaker",
    },
    "source_locator": {
        "来源": "source_title",
        "书名": "source_title",
        "册次": "volume",
        "页码": "page",
        "段落编号": "paragraph_no",
        "馆藏编号": "collection_no",
    },
    "search_tags_raw": {
        "事实": "fact_tags",
        "人格": "persona_tags",
        "风格": "style_tags",
    },
    "evidence_status_raw": {
        "语言风格证据": "style_evidence",
        "证据类型": "evidence_type",
        "证据强度": "evidence_strength",
        "核验状态": "review_status_raw",
        "审核状态": "review_status_raw",
    },
}


def normalize_header(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("（", "(").replace("）", ")").strip()
    return re.sub(r"\s+", "", text)


@dataclass
class ColumnMapping:
    """canonical field -> 表格里的原始列名。"""

    columns: dict[str, str] = field(default_factory=dict)
    unknown_headers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"columns": self.columns, "unknown_headers": self.unknown_headers}


def match_header(header: str) -> str | None:
    normalized = normalize_header(header)
    if not normalized:
        return None
    best: tuple[int, str] | None = None
    for field_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if normalize_header(alias) == normalized:
                weight = len(alias)
                if best is None or weight > best[0]:
                    best = (weight, field_name)
    return None if best is None else best[1]


def build_mapping(headers: list[str]) -> ColumnMapping:
    mapping = ColumnMapping()
    used: set[str] = set()
    for header in headers:
        if not str(header or "").strip():
            continue
        field_name = match_header(header)
        if field_name and field_name not in used:
            mapping.columns[field_name] = header
            used.add(field_name)
        elif field_name is None:
            mapping.unknown_headers.append(header)
    return mapping


def split_packed(text: str) -> dict[str, str]:
    """把“键：值；值；下一键：值”拆开。没有键的片段接着上一个键。"""
    current: str | None = None
    grouped: dict[str, list[str]] = {}
    for part in re.split(r"[；;]", text or ""):
        piece = part.strip()
        if not piece:
            continue
        if "：" in piece or ":" in piece:
            splitter = "：" if "：" in piece else ":"
            key, value = piece.split(splitter, 1)
            current = key.strip()
            grouped.setdefault(current, [])
            if value.strip():
                grouped[current].append(value.strip())
        elif current:
            grouped[current].append(piece)
    return {key: "；".join(values) for key, values in grouped.items()}


def apply_packed_fields(record: dict[str, str]) -> dict[str, str]:
    """独立列优先。只有独立列为空时，才从打包列里补。"""
    for source_field, targets in PACKED_TARGETS.items():
        packed = split_packed(record.get(source_field, ""))
        if not packed:
            continue
        for label, target in targets.items():
            if record.get(target, "").strip():
                continue
            if packed.get(label):
                record[target] = packed[label]
    return record


_EMPTY = {"", "—", "-", "－", "无", "None", "null"}


def clean_cell(value: str) -> str:
    text = (value or "").strip()
    return "" if text in _EMPTY else text


def map_review_status(raw: str) -> str:
    text = clean_cell(raw)
    if not text:
        return "pending"
    if any(word in text for word in ("驳回", "不采用", "无效", "错误")):
        return "rejected"
    if any(word in text for word in ("已核对", "已审核", "已核验", "通过")):
        return "reviewed"
    return "pending"


def speech_kind(narrative: str, speaker: str, relation: str) -> str:
    narrative = narrative or ""
    speaker = clean_cell(speaker)
    relation = clean_cell(relation)
    if any(word in narrative for word in ("小说", "虚构", "角色台词", "人物台词", "作品人物")):
        return "fictional"
    if any(word in narrative for word in ("演示角色",)):
        return "fictional"
    self_names = {"茅盾", "沈雁冰", "沈德鸿", "本人", "自己"}
    if "自述" in narrative or speaker in self_names or relation in {"本人", "自己"}:
        return "self"
    if speaker:
        return "quoted_person"
    return "unknown"
