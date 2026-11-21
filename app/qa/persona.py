"""人物设定与事实资料分开存放。这里只读设定，不提供生平事实。"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_persona(path: Path) -> dict:
    if not path.is_file():
        return {
            "display_name": "茅盾数字人",
            "disclaimer": "这是数字化模拟，不是茅盾本人。",
            "voice": "用简短的普通话回答。",
            "boundaries": [],
        }
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {"display_name": "茅盾数字人", "disclaimer": "这是数字化模拟，不是茅盾本人。", "boundaries": []}
    return data


def load_lexicon(path: Path) -> list[str]:
    if not path.is_file():
        return ["茅盾", "沈雁冰", "子夜", "我走过的道路"]
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
