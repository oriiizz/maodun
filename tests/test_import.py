from openpyxl import Workbook, load_workbook

from app.ingest.demo_data import write_demo_workbook
from app.runtime import WorkbookImportError


def test_repeat_import_does_not_duplicate(runtime, tmp_path):
    path = write_demo_workbook(tmp_path / "演示资料.xlsx")
    first = runtime.import_path(path, is_demo=True)
    second = runtime.import_path(path, is_demo=True)
    count = runtime.db.query_one("SELECT COUNT(*) AS count FROM entries")["count"]
    assert first["inserted"] == 4
    assert second["inserted"] == 0
    assert second["unchanged"] == 4
    assert count == 4


def test_changed_row_updates_same_entry(runtime, tmp_path):
    path = write_demo_workbook(tmp_path / "演示资料.xlsx")
    runtime.import_path(path, is_demo=True)
    book = load_workbook(path)
    sheet = book.active
    headers = [cell.value for cell in sheet[1]]
    column = headers.index("原文引用") + 1
    sheet.cell(2, column).value = "【演示资料，不是馆藏原文，也不能当作茅盾原话】修改后的文字仍然包含演示乌镇。"
    book.save(path)
    book.close()
    report = runtime.import_path(path, is_demo=True)
    count = runtime.db.query_one("SELECT COUNT(*) AS count FROM entries")["count"]
    updated = runtime.db.query_one("SELECT original_text, summary FROM entries WHERE external_entry_id = 'DEMO-P001'")
    assert report["updated"] == 1
    assert report["inserted"] == 0
    assert count == 4
    assert "修改后的文字" in updated["original_text"]
    assert updated["summary"] not in updated["original_text"]


def test_duplicate_id_is_recorded(runtime, tmp_path):
    path = write_demo_workbook(tmp_path / "重复.xlsx")
    book = load_workbook(path)
    sheet = book.active
    duplicate = [cell.value for cell in sheet[2]]
    sheet.append(duplicate)
    book.save(path)
    book.close()
    report = runtime.import_path(path, is_demo=True)
    assert report["errors"] == 1
    assert any("重复" in item["message"] for item in report["issues"])
    assert runtime.db.query_one("SELECT COUNT(*) AS count FROM entries")["count"] == 4


def test_empty_row_and_damaged_file(runtime, tmp_path):
    path = tmp_path / "残缺.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["条目ID", "原文引用", "客观内容概括"])
    sheet.append(["BAD-1", "", ""])
    sheet.append(["BAD-2", "这里有原文。", "这里是概括，不应替换原文。"])
    book.save(path)
    book.close()
    report = runtime.import_path(path)
    saved = runtime.db.query_one("SELECT original_text, summary FROM entries WHERE external_entry_id = 'BAD-2'")
    assert report["errors"] == 1
    assert saved["original_text"] == "这里有原文。"
    assert saved["summary"] == "这里是概括，不应替换原文。"

    broken = tmp_path / "损坏.xlsx"
    broken.write_bytes(b"this is not an excel file")
    try:
        runtime.import_path(broken)
        raise AssertionError("损坏文件不应该导入成功")
    except WorkbookImportError as exc:
        assert "损坏" in str(exc)

    missing = tmp_path / "不存在.xlsx"
    try:
        runtime.import_path(missing)
        raise AssertionError("缺失文件不应该导入成功")
    except WorkbookImportError as exc:
        assert "找不到文件" in str(exc)


def test_packed_qianfan_columns(runtime, tmp_path):
    path = tmp_path / "打包.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "千帆导入"
    sheet.append(["条目ID", "人物信息", "信息来源性质", "原文引用", "客观内容概括", "来源定位", "检索标签", "证据状态"])
    sheet.append(
        [
            "PACK-1",
            "核心人物：茅盾；与茅盾关系：本人",
            "叙述层级：茅盾直接自述；信息提供者：茅盾；原始发言者：茅盾",
            "打包格式里的原文提到演示乌镇。",
            "这只是概括。",
            "来源：《我走过的道路》；册次：上册；页码：打包-1；段落编号：PK-1",
            "事实：演示；写作缘起；人格：自省；风格：短句",
            "语言风格证据：是；证据类型：直接自述；证据强度：高；核验状态：文本内部已核对",
        ]
    )
    book.save(path)
    book.close()
    runtime.import_path(path)
    row = runtime.db.query_one("SELECT * FROM entries WHERE external_entry_id = 'PACK-1'")
    assert row["speaker"] == "茅盾"
    assert row["page"] == "打包-1"
    assert row["source_title"] == "《我走过的道路》"
    assert row["fact_tags"] == "演示；写作缘起"
    assert row["persona_tags"] == "自省"
    assert row["review_status"] == "reviewed"
    assert row["speech_kind"] == "self"
    assert row["original_text"] != row["summary"]
