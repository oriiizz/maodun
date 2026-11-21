"""馆藏物件和作品背景分开。条码是物件身份，不按收信人合并。"""

from __future__ import annotations

import re

from app.knowledge.textutil import clean

def scope_for(status: str, correspondence: str) -> str:
    status = status or ""
    if "候选" in status:
        return "candidate"
    if "未找到" in status:
        return "catalog"
    if "仅目录" in status:
        return "clue"
    if "仅关联" in status:
        return "background"
    if "图片或节录" in status:
        return "excerpt"
    if "公开正文" in status:
        return "public_text"
    if "作品阅读" in status:
        return "work_intro"
    if "具体内容" in status:
        return "described"
    if "已对版" in (correspondence or "") or "本件正文" in (correspondence or ""):
        return "confirmed"
    return "unknown"


def physical_notes(catalog: str) -> str:
    text = catalog or ""
    notes: list[str] = []
    pages = re.search(r"(?:实有)?页数[:：]\s*(\d+)", text)
    if pages:
        notes.append(f"{pages.group(1)}页")
    for label, pattern in (
        ("书写工具", r"书写工具[:：]\s*([^；;\n]+)"),
        ("纸别", r"纸别[:：]\s*([^；;\n]+)"),
    ):
        found = re.search(pattern, text)
        if found:
            notes.append(found.group(1).strip())
        del label
    if "信封" in text:
        notes.append("信封有残损" if "残" in text else "有信封")
    return "、".join(notes)


def visitor_text(row: dict) -> str:
    """给模型和确定性介绍用的文字。网址不放进来。"""

    scope = row["scope"]
    bits = [f"题名：{row['title']}" if row["title"] else ""]
    if row["recorded_date"]:
        bits.append(f"著录日期：{row['recorded_date']}（按原记录保留，缺少年月时不补）")
    if row["sender"]:
        bits.append(f"作者或寄信人：{row['sender']}")
    if row["recipient"]:
        bits.append(f"收信人：{row['recipient']}")
    if row["physical"]:
        bits.append(f"形制记录：{row['physical']}")
    body = clean(row.get("found_text"))
    quote = clean(row.get("short_quote"))
    speaker = clean(row.get("quote_speaker"))
    if scope == "candidate":
        bits.append("本件正文仍是待核候选，不能当成已经确认的原文。")
    elif scope in {"catalog", "clue", "unknown"}:
        bits.append("这里还没有可核实的本件正文。")
    elif scope == "background":
        if body:
            bits.append("相关背景（不是这件的正文）：" + body)
        else:
            bits.append("只有相关背景，没有这件的正文。")
    elif scope == "excerpt":
        if quote or body:
            bits.append("节录（不能当作已经读过全件）：" + (quote or body))
        if speaker:
            bits.append(f"短引说话人：{speaker}")
    elif scope == "public_text":
        if body or quote:
            bits.append("同题公开文字（不能当作已经与馆藏逐字对版）：" + (body or quote))
        if speaker:
            bits.append(f"其中引语的说话人：{speaker}")
    elif scope in {"work_intro", "described", "confirmed"}:
        label = "本件已确认的内容：" if scope == "confirmed" else "资料中的内容介绍（不等于看见了未见页面）："
        if body or quote:
            bits.append(label + (body or quote))
        if speaker:
            bits.append(f"短引说话人：{speaker}")
    return "\n".join(bit for bit in bits if bit)
