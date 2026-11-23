"""登记资料从哪来。程序只认这份登记和相对路径，不认某台电脑的桌面位置。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import ROOT
from app.knowledge.readers import SourceReadError, sheet_headers

CHAPTER_MARKS = ("来源书名", "页码与段落定位")
THEME_MARKS = ("主题分类", "信息来源与使用限制", "可回答问题与检索词")
MUSEUM_MARK = "馆藏条码"


class CatalogError(Exception):
    pass


@dataclass
class Material:
    source_key: str
    label: str
    chapter: Path | None = None
    chapter_sheet: str | None = None
    theme: Path | None = None
    theme_sheet: str | None = None
    basics: Path | None = None
    museum: Path | None = None
    museum_sheets: list[str] = field(default_factory=list)

    def files(self) -> list[Path]:
        return [path for path in (self.chapter, self.theme, self.basics, self.museum) if path]


def sources_root() -> Path:
    raw = os.environ.get("SOURCES_DIR", "").strip()
    path = Path(raw) if raw else ROOT / "data" / "sources"
    if not path.is_absolute():
        path = ROOT / path
    return path


def pack_dir() -> Path:
    raw = os.environ.get("KNOWLEDGE_PACK", "").strip()
    path = Path(raw) if raw else ROOT / "data" / "knowledge-pack"
    if not path.is_absolute():
        path = ROOT / path
    return path


def load_catalog(path: Path | None = None) -> list[Material]:
    catalog_path = path or (sources_root() / "catalog.json")
    if not catalog_path.is_file():
        raise CatalogError(f"还没有资料登记：{catalog_path.name}。请把原始文件放进资料目录并登记。")
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{catalog_path.name} 不是有效的登记文件。") from exc
    base = catalog_path.parent
    items = payload.get("materials") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise CatalogError("登记文件里还没有任何资料。")
    materials = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not str(item.get("source_key") or "").strip():
            raise CatalogError("每一份资料都要有一个稳定的 source_key。")
        source_key = _key(str(item["source_key"]))
        if source_key in seen:
            raise CatalogError(f"资料标识重复：{source_key}")
        seen.add(source_key)
        materials.append(
            Material(
                source_key=source_key,
                label=str(item.get("label") or source_key),
                chapter=_optional(base, item.get("chapter")),
                chapter_sheet=_text(item.get("chapter_sheet")),
                theme=_optional(base, item.get("theme")),
                theme_sheet=_text(item.get("theme_sheet")),
                basics=_optional(base, item.get("basics")),
                museum=_optional(base, item.get("museum")),
                museum_sheets=[str(name) for name in item.get("sheets") or []],
            )
        )
    missing = [str(path) for material in materials for path in material.files() if not path.is_file()]
    if missing:
        names = "、".join(Path(item).name for item in missing)
        raise CatalogError(f"登记的文件还没有放进资料目录：{names}")
    return materials


def discover_directory(directory: Path) -> list[Material]:
    """按表头识别同结构的表格，不要求固定文件名，也不要求正好四份。"""

    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise CatalogError(f"找不到资料目录：{directory}")
    chapters: list[tuple[Path, str]] = []
    themes: list[tuple[Path, str]] = []
    museums: list[tuple[Path, list[str]]] = []
    basics: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if suffix == ".docx":
            basics.append(path)
            continue
        if suffix != ".xlsx":
            continue
        try:
            headers = sheet_headers(path)
        except SourceReadError as exc:
            raise CatalogError(str(exc)) from exc
        museum_sheets = [name for name, header in headers.items() if MUSEUM_MARK in header]
        if museum_sheets:
            museums.append((path, museum_sheets))
            continue
        for name, header in headers.items():
            if "条目ID" not in header:
                continue
            if any(mark in header for mark in CHAPTER_MARKS):
                chapters.append((path, name))
            elif any(mark in header for mark in THEME_MARKS):
                themes.append((path, name))
    materials: list[Material] = []
    used_themes: set[tuple[Path, str]] = set()
    for chapter_path, chapter_sheet in chapters:
        from app.knowledge.readers import read_table

        _, rows, _issues = read_table(chapter_path, chapter_sheet, "条目ID")
        ids = {row["条目ID"] for row in rows}
        best: tuple[int, tuple[Path, str]] | None = None
        for theme in themes:
            _, theme_rows, _theme_issues = read_table(theme[0], theme[1], "条目ID")
            overlap = len(ids & {row["条目ID"] for row in theme_rows})
            if overlap and (best is None or overlap > best[0]):
                best = (overlap, theme)
        theme_path = None
        theme_sheet = None
        if best:
            used_themes.add(best[1])
            theme_path, theme_sheet = best[1]
        digest = hashlib.sha256(chapter_path.name.encode("utf-8")).hexdigest()[:8]
        materials.append(
            Material(
                source_key=f"book-{digest}",
                label=chapter_path.stem,
                chapter=chapter_path,
                chapter_sheet=chapter_sheet,
                theme=theme_path,
                theme_sheet=theme_sheet,
            )
        )
    for theme in themes:
        if theme in used_themes:
            continue
        digest = hashlib.sha256(theme[0].name.encode("utf-8")).hexdigest()[:8]
        materials.append(Material(source_key=f"theme-{digest}", label=theme[0].stem, theme=theme[0], theme_sheet=theme[1]))
    for path in basics:
        digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:8]
        target = materials[0] if len(materials) == 1 else None
        if target and target.basics is None and target.chapter:
            target.basics = path
        else:
            materials.append(Material(source_key=f"basics-{digest}", label=path.stem, basics=path))
    for path, sheets in museums:
        digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:8]
        materials.append(Material(source_key=f"museum-{digest}", label=path.stem, museum=path, museum_sheets=sheets))
    if not materials:
        raise CatalogError("这个目录里没有可识别的章节表、主题表、基础知识或馆藏表。")
    return materials


def _optional(base: Path, value: object) -> Path | None:
    text = _text(value)
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    return path


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _key(value: str) -> str:
    text = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", text):
        raise CatalogError(f"资料标识只能使用字母、数字、短横线：{value}")
    return text
