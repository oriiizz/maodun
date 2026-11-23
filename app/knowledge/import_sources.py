"""按登记或按一个目录生成知识记录，并写入数据库。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.database import Database
from app.knowledge.apply import ApplyError, apply_materials
from app.knowledge.catalog import CatalogError, discover_directory, load_catalog
from app.knowledge.records import RecordError, compile_material
from app.knowledge.store import ensure_schema


class CorpusImportError(Exception):
    pass


def import_registered(
    db: Database,
    mode: str = "rebuild",
    source_key: str | None = None,
    catalog_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    try:
        materials = load_catalog(catalog_path)
    except CatalogError as exc:
        raise CorpusImportError(str(exc)) from exc
    if source_key:
        materials = [item for item in materials if item.source_key == source_key]
        if not materials:
            raise CorpusImportError(f"登记里没有这份资料：{source_key}")
        if mode == "rebuild":
            mode = "update"
    return _compile_and_apply(db, materials, mode, root)


def import_corpus(db: Database, directory: Path, mode: str = "update", root: Path | None = None) -> dict[str, Any]:
    """从一个目录识别同结构资料。只替换这些资料，不删除目录里没有的其他资料。"""

    try:
        materials = discover_directory(directory)
    except CatalogError as exc:
        raise CorpusImportError(str(exc)) from exc
    return _compile_and_apply(db, materials, mode, root)


def remove_source(db: Database, source_key: str, root: Path | None = None) -> dict[str, Any]:
    ensure_schema(db)
    try:
        return apply_materials(db, [{"source_key": source_key, "label": source_key}], "remove", root=root)
    except ApplyError as exc:
        raise CorpusImportError(str(exc)) from exc


def _compile_and_apply(db: Database, materials, mode: str, root: Path | None) -> dict[str, Any]:
    ensure_schema(db)
    try:
        bundles = [compile_material(item) for item in materials]
        report = apply_materials(db, bundles, mode, root=root)
    except (RecordError, ApplyError) as exc:
        raise CorpusImportError(str(exc)) from exc
    report["materials"] = [bundle.get("stats") or {} for bundle in bundles]
    report["issues"] = [issue for bundle in bundles for issue in bundle.get("issues") or []][:80]
    return report
