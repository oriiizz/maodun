from pathlib import Path

import pytest
from openpyxl import Workbook

from app.database import Database
from app.knowledge.apply import ApplyError, apply_materials
from app.knowledge.import_sources import CorpusImportError, import_corpus, remove_source
from app.knowledge.package import export_pack, load_pack
from app.knowledge.store import ensure_schema
from app.session.manager import SessionManager


def _write_book(folder: Path, prefix: str, entry_id: str, sentence: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    chapter = Workbook()
    sheet = chapter.active
    sheet.title = "章节"
    sheet.append(["条目ID", "条目标题", "内容概括", "原文摘录", "来源书名"])
    sheet.append([entry_id, "一件事", sentence, sentence, prefix])
    chapter.save(folder / f"{prefix}-章节.xlsx")
    theme = Workbook()
    theme_sheet = theme.active
    theme_sheet.title = "主题"
    theme_sheet.append(["条目ID", "条目标题", "内容概括", "原文摘录", "主题分类", "信息来源与使用限制"])
    theme_sheet.append([entry_id, "一件事", sentence, sentence, "回忆", ""])
    theme.save(folder / f"{prefix}-主题.xlsx")


def test_two_books_may_share_an_entry_id_and_partial_update_keeps_the_other(tmp_path):
    first = tmp_path / "甲"
    second = tmp_path / "乙"
    _write_book(first, "甲书", "BK-F001", "甲书里的原句。")
    _write_book(second, "乙书", "BK-F001", "乙书里的原句。")
    db = Database(tmp_path / "knowledge.db")
    ensure_schema(db)
    import_corpus(db, first, "update", root=tmp_path)
    import_corpus(db, second, "update", root=tmp_path)
    rows = db.query("SELECT source_key, entry_id, quote_json FROM corpus_nodes WHERE entry_id = ?", ("BK-F001",))
    assert len(rows) == 2
    assert {row["source_key"] for row in rows} != {"BK-F001"}
    assert "甲书里的原句" in rows[0]["quote_json"] or "甲书里的原句" in rows[1]["quote_json"]
    kept = rows[0]["source_key"]
    other = rows[1]["source_key"]
    _write_book(second, "乙书", "BK-F001", "乙书修改后的原句。")
    import_corpus(db, second, "update", root=tmp_path)
    kept_row = db.query_one("SELECT quote_json FROM corpus_nodes WHERE source_key = ?", (kept,))
    changed = db.query_one("SELECT quote_json FROM corpus_nodes WHERE source_key = ?", (other,))
    assert "甲书里的原句" in kept_row["quote_json"] or "乙书修改后的原句" in kept_row["quote_json"]
    assert "乙书修改后的原句" in changed["quote_json"] or "甲书里的原句" in changed["quote_json"]
    session = SessionManager(db).create()
    remove_source(db, other, root=tmp_path)
    assert db.query_one("SELECT id FROM sessions WHERE id = ?", (session["id"],)) is not None
    assert db.query_one("SELECT COUNT(*) AS count FROM corpus_nodes WHERE source_key = ?", (other,))["count"] == 0
    assert db.query_one("SELECT COUNT(*) AS count FROM corpus_nodes WHERE source_key = ?", (kept,))["count"] == 1
    db.close()


def test_failed_update_keeps_the_previous_records(tmp_path):
    folder = tmp_path / "书"
    _write_book(folder, "甲书", "BK-F002", "原来的句子。")
    db = Database(tmp_path / "knowledge.db")
    imported = import_corpus(db, folder, "update", root=tmp_path)
    source_key = imported["sources"][0]
    with pytest.raises(ApplyError):
        apply_materials(db, [{"source_key": source_key, "nodes": [("坏记录",)], "museum": [], "basics": []}], "update", root=tmp_path)
    row = db.query_one("SELECT quote_json FROM corpus_nodes WHERE source_key = ?", (source_key,))
    assert "原来的句子" in row["quote_json"]
    db.close()


def test_knowledge_pack_has_no_chat_and_can_start_another_database(tmp_path):
    folder = tmp_path / "书"
    _write_book(folder, "甲书", "BK-F003", "可以打包的句子。")
    db = Database(tmp_path / "knowledge.db")
    import_corpus(db, folder, "update", root=tmp_path)
    SessionManager(db).create()
    pack = tmp_path / "pack"
    manifest = export_pack(db, pack)
    assert manifest["format"] == "maodun-knowledge-pack"
    names = {path.name for path in pack.iterdir()}
    assert names == {"manifest.json", "nodes.jsonl", "museum.jsonl", "basics.jsonl"}
    blob = (pack / "manifest.json").read_text(encoding="utf-8") + (pack / "nodes.jsonl").read_text(encoding="utf-8")
    assert "sessions" not in blob
    assert "DEEPSEEK" not in blob
    other = Database(tmp_path / "other.db")
    load_pack(other, pack, "replace", root=tmp_path)
    assert other.query_one("SELECT COUNT(*) AS count FROM corpus_nodes")["count"] == 1
    assert other.query_one("SELECT COUNT(*) AS count FROM sessions")["count"] == 0
    assert db.query_one("SELECT COUNT(*) AS count FROM sessions")["count"] == 1
    db.close()
    other.close()


def test_missing_catalog_does_not_invent_a_desktop_path(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCES_DIR", raising=False)
    from app.knowledge.import_sources import import_registered

    with pytest.raises(CorpusImportError) as caught:
        import_registered(Database(tmp_path / "empty.db"), "rebuild", catalog_path=tmp_path / "没有登记.json")
    assert "桌面" not in str(caught.value)
    assert "登记" in str(caught.value)
