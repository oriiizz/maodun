from openpyxl import Workbook

from app.qa.answer import answer_with_evidence


class StubModel:
    provider_id = "stub"

    def __init__(self, payload: str):
        self.payload = payload

    def configured(self) -> bool:
        return True

    def missing_notice(self) -> str:
        return "未配置"

    def complete(self, messages):
        self.messages = messages
        return self.payload


def test_visitor_questions_do_not_use_the_demo_table(runtime):
    runtime.import_demo()
    result = runtime.ask("演示乌镇在哪一页？", None)
    assert result["evidence"] == []
    assert result["citations"] == []
    assert result["provider"] == "local_corpus"
    assert "演示-1" not in result["spoken_text"]


def test_old_workbook_cannot_answer_the_visitor(runtime, tmp_path):
    path = tmp_path / "北京.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["条目ID", "原文引用", "来源书名", "页码", "原始发言者", "叙述层级"])
    sheet.append(["BJ-1", "旅途中曾经路过北京，只记下了路程。", "《演示资料·非馆藏》", "北京-1", "茅盾", "演示用的直接自述"])
    book.save(path)
    book.close()
    runtime.import_path(path, is_demo=True)
    result = runtime.ask("今天北京天气怎么样？", None)
    assert result["evidence"] == []
    assert result["citations"] == []
    assert "天气" not in result["spoken_text"]
    assert "路程" not in result["spoken_text"]


def test_fictional_line_is_marked(runtime):
    runtime.import_demo()
    rows = runtime.db.query("SELECT speech_kind, speaker FROM entries WHERE external_entry_id = 'DEMO-P004'")
    assert rows[0]["speech_kind"] == "fictional"
    assert rows[0]["speaker"] == "演示角色吴荪甫"


def test_model_cannot_cite_unknown_entry(runtime):
    runtime.import_demo()
    model = StubModel('{"spoken_text":"这是编造。","used_entry_ids":["不存在的条目"],"control":{"mood":"calm","gesture":"explain"}}')
    result = answer_with_evidence(
        question="演示子夜",
        retriever=runtime.retriever,
        model=model,
        persona=runtime.persona,
        provider_name="stub",
    )
    assert result.spoken_text == ""
    assert "拦住" in result.system_notice
    assert result.citations


def test_model_answer_keeps_citation_separate(runtime):
    runtime.import_demo()
    entry_id = runtime.db.query_one("SELECT id FROM entries WHERE external_entry_id = 'DEMO-P003'")["id"]
    model = StubModel(
        '{"spoken_text":"演示资料里把这件事记成1933年的出版记录。","used_entry_ids":["'
        + entry_id
        + '"],"control":{"mood":"reflective","gesture":"recall"}}'
    )
    result = answer_with_evidence(
        question="演示子夜",
        retriever=runtime.retriever,
        model=model,
        persona=runtime.persona,
        provider_name="stub",
    )
    assert "1933" in result.spoken_text
    assert entry_id not in result.spoken_text
    assert result.citations[0].entry_id == entry_id
    assert result.citations[0].page == "演示-9"
    assert result.control.mood == "reflective"
    assert result.control.gesture == "recall"
