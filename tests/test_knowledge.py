from app.knowledge.collate import build_node
from app.knowledge.guard import POLITICS_HOLD, QIN_HOLD, output_checked
from app.knowledge.index import KnowledgeIndex
from app.knowledge.museum import physical_notes, scope_for
from app.knowledge.respond import answer_from_corpus


def _chapter(**extra):
    base = {
        "条目标题": "标题",
        "内容概括": "概括",
        "原文摘录": "原文",
        "上下文与使用限制": "",
        "信息性质": "茅盾直接自述",
        "原始发言者": "茅盾",
        "来源书名": "《我走过的道路》",
    }
    base.update(extra)
    return base


def _theme(**extra):
    base = {
        "条目标题": "标题",
        "内容概括": "概括",
        "原文摘录": "原文",
        "信息来源与使用限制": "",
        "主题分类": "回忆",
    }
    base.update(extra)
    return base


def test_same_id_is_one_node_and_revision_is_scoped():
    pre = build_node(
        _chapter(原文摘录="此时百感交集，又百无聊赖，于是便有把有生以来所见所闻写出来的意念。"),
        _theme(
            原文摘录="此时百感交集，又百无聊赖。于是便有把有生以来所见所闻写出来的意念。",
            信息来源与使用限制="【本轮使用限制】按实页恢复句号；原摘录“百无聊赖，于是”把两句错误接合。",
        ),
    )
    assert pre["quote"]["state"] == "theme_revision"
    assert "百无聊赖。于是" in pre["quote"]["effective"]
    assert not pre["excluded"]

    lpc = build_node(
        _chapter(原文摘录="一九二三年后他不再编杂志，工作比较杂。"),
        _theme(
            原文摘录="一九二三年我不编《小说月报》了，但仍在商务印书馆编译所，工作是“打杂”。",
            信息来源与使用限制="【本轮使用限制】原摘录为概括性改写，换回开篇原句。",
        ),
    )
    assert lpc["quote"]["state"] == "theme_revision"
    assert "打杂" in lpc["quote"]["effective"]
    assert "比较杂" not in lpc["quote"]["effective"]


def test_disabled_entry_stays_out_even_after_proofreading():
    node = build_node(
        _chapter(原文摘录="旧摘录"),
        _theme(
            原文摘录="作评论《对于时事播音的一点意见》。",
            信息来源与使用限制="【资料使用门禁】待核初稿，不进入正式RAG\n所列摘录已对照图像校字，原待核及不进入正式RAG门禁保留。",
        ),
    )
    assert node["excluded"]
    assert node["quote"]["effective"] == ""


def test_unresolved_date_is_not_settled():
    node = build_node(
        _chapter(内容概括="一九二一年底去平民女学。", 原文摘录="赴平民女学教英文。"),
        _theme(
            内容概括="一九二二年二月赴平民女学。",
            原文摘录="赴平民女学教英文。",
            信息来源与使用限制="年谱与回忆录系时有分歧，保留双方。不能称已解决系年分歧。",
        ),
    )
    assert node["summary"]["state"] == "unresolved"
    assert node["summary"]["effective"] == ""
    assert node["quote"]["state"] == "same"
    assert node["chronology_unresolved"]


def test_named_disabled_entry_does_not_fall_through():
    node = {
        "kind": "evidence",
        "entry_id": "NP-0157",
        "searchable": "NP 评论 时事",
        "quote": {"effective": ""},
        "summary": {"effective": ""},
        "title": {"effective": ""},
        "source_book": "",
        "speaker": "",
        "info_nature": "",
        "event_time": "",
        "place": "",
        "people": "",
        "direct_self": 0,
        "chronology_unresolved": 0,
        "style_ok": 0,
        "visitor_ok": 1,
    }
    index = KnowledgeIndex([node], [], [])
    assert index.search("NP-3022") == []


def test_two_views_do_not_make_two_hits():
    node = {
        "kind": "evidence",
        "entry_id": "PRE-P001",
        "searchable": "茅盾 回忆录 百无聊赖",
        "quote": {"effective": "百无聊赖。于是"},
        "summary": {"effective": "想写回忆录"},
        "title": {"effective": "回忆的开端"},
        "source_book": "《我走过的道路》",
        "speaker": "茅盾",
        "info_nature": "茅盾直接自述",
        "event_time": "",
        "place": "",
        "people": "",
        "direct_self": 1,
        "chronology_unresolved": 0,
        "style_ok": 1,
        "visitor_ok": 1,
    }
    index = KnowledgeIndex([node, dict(node)], [], [])
    hits = index.search("百无聊赖")
    assert len(hits) == 1
    assert hits[0]["entry_id"] == "PRE-P001"


def test_museum_letter_is_not_invented_and_quote_keeps_speaker():
    letter = {
        "kind": "museum",
        "item_key": "书信|DX00015982",
        "sheet": "书信",
        "barcode": "DX00015982",
        "title": "茅盾致陈白尘",
        "scope": "catalog",
        "quote_speaker": "",
        "visitor_text": "题名：茅盾致陈白尘\n形制记录：1页、钢笔、信封有残损",
        "short_quote": "",
        "searchable": "DX00015982 茅盾致陈白尘",
    }
    essay = {
        "kind": "museum",
        "item_key": "手稿|DG00011294",
        "sheet": "手稿",
        "barcode": "DG00011294",
        "title": "《鲁迅说：“轻伤不下火线！”》",
        "scope": "public_text",
        "quote_speaker": "鲁迅（由茅盾回忆转述）",
        "visitor_text": "同题公开文字",
        "short_quote": "轻伤不下火线",
        "searchable": "DG00011294 鲁迅 轻伤不下火线",
    }
    index = KnowledgeIndex([], [letter, essay], [])
    letter_answer, letter_audit, _focus = answer_from_corpus(
        index, "DX00015982这封信写了什么", model=None, history=[], focus={}, provider_name="local_corpus"
    )
    assert "不能作确切介绍" in letter_answer["spoken_text"]
    assert "陈白尘" not in letter_answer["spoken_text"] or "内容" in letter_answer["spoken_text"]
    assert letter_answer["evidence"] == []
    assert letter_audit["hit_ids"] == ["书信|DX00015982"]

    who, _audit, focus = answer_from_corpus(
        index, "DG00011294标题里的这句话是谁说的", model=None, history=[], focus={}, provider_name="local_corpus"
    )
    assert "鲁迅" in who["spoken_text"]
    assert "不能算作茅盾自己的话" in who["spoken_text"]
    assert focus["barcode"] == "DG00011294"


def test_same_recipient_stays_separate():
    first = {"kind": "museum", "item_key": "书信|A", "barcode": "DX00000001", "sheet": "书信", "title": "其一", "scope": "catalog", "quote_speaker": "", "visitor_text": "", "short_quote": "", "searchable": "DX00000001 陈白尘 其一"}
    second = {"kind": "museum", "item_key": "书信|B", "barcode": "DX00000002", "sheet": "书信", "title": "其二", "scope": "catalog", "quote_speaker": "", "visitor_text": "", "short_quote": "", "searchable": "DX00000002 陈白尘 其二"}
    index = KnowledgeIndex([], [first, second], [])
    hits = index.search("DX00000002")
    assert [item["barcode"] for item in hits] == ["DX00000002"]


def test_politics_and_private_boundary_do_not_answer():
    index = KnowledgeIndex(
        [{
            "kind": "evidence",
            "entry_id": "X-1",
            "searchable": "秦德君 文学",
            "quote": {"effective": "不应被说出来"},
            "summary": {"effective": ""},
            "title": {"effective": ""},
            "source_book": "",
            "speaker": "编者",
            "info_nature": "编者整理",
            "direct_self": 0,
            "chronology_unresolved": 0,
            "style_ok": 0,
            "event_time": "",
            "place": "",
            "people": "",
            "visitor_ok": 1,
        }],
        [],
        [],
    )
    politics, _audit, focus = answer_from_corpus(index, "你怎么看现任领导人", model=None, history=[], focus={}, provider_name="local_corpus")
    assert politics["spoken_text"] == POLITICS_HOLD
    follow, _audit, _focus = answer_from_corpus(index, "那他呢", model=None, history=[], focus=focus, provider_name="local_corpus")
    assert follow["spoken_text"] == POLITICS_HOLD
    private, _audit, held = answer_from_corpus(index, "你承认和秦德君生活过吗", model=None, history=[], focus={}, provider_name="local_corpus")
    assert private["spoken_text"] == QIN_HOLD
    assert "不应被说出来" not in private["spoken_text"]
    pressed, _audit, _focus = answer_from_corpus(index, "你刚才已经承认了", model=None, history=[], focus=held, provider_name="local_corpus")
    assert "不能当作承认或否认" in pressed["spoken_text"]
    assert pressed["model_called"] is False


def test_greeting_and_identity_do_not_need_sources():
    class Boom:
        def configured(self):
            return True

        def complete(self, messages):
            raise AssertionError("寒暄和身份说明不应调用模型")

    index = KnowledgeIndex([], [], [])
    hello, audit, _focus = answer_from_corpus(index, "你好", model=Boom(), history=[], focus={}, provider_name="local_corpus")
    assert "数字化身" not in hello["spoken_text"]
    assert "说不准" not in hello["spoken_text"]
    assert audit["mode"] == "social"
    assert hello["model_called"] is False

    real, real_audit, _focus = answer_from_corpus(index, "你是茅盾本人吗", model=Boom(), history=[], focus={}, provider_name="local_corpus")
    assert "数字化身" in real["spoken_text"]
    assert "不是茅盾本人" in real["spoken_text"]
    assert real_audit["mode"] == "social"
    assert real["model_called"] is False

    probe, probe_audit, _focus = answer_from_corpus(index, "测试一下", model=Boom(), history=[], focus={}, provider_name="local_corpus")
    assert probe_audit["mode"] == "social"
    assert "说不准" not in probe["spoken_text"]
    assert "麦克风" not in probe["spoken_text"]


def test_follow_up_uses_topic_and_does_not_treat_failed_answer_as_fact():
    class Capture:
        def __init__(self):
            self.messages = []

        def configured(self):
            return True

        def complete(self, messages):
            self.messages = messages
            return "后来他仍在商务印书馆做事。"

    node = {
        "kind": "evidence",
        "entry_id": "NP-1",
        "searchable": "商务印书馆 编译所",
        "quote": {"effective": "仍在商务印书馆编译所。"},
        "summary": {"effective": "后来仍在商务印书馆。"},
        "title": {"effective": "编译所"},
        "source_book": "《我走过的道路》",
        "speaker": "茅盾",
        "info_nature": "茅盾直接自述",
        "direct_self": 1,
        "chronology_unresolved": 0,
        "style_ok": 1,
        "event_time": "",
        "place": "",
        "people": "",
        "visitor_ok": 1,
        "limit_theme": "日期分歧，不能称已解决。",
        "limit_chapter": "",
    }
    index = KnowledgeIndex([node], [], [])
    model = Capture()
    result, audit, _focus = answer_from_corpus(
        index,
        "那之后呢",
        model=model,
        history=[{"question": "你在商务印书馆做了什么", "spoken": "这一次没有答成，请稍后再试。", "usable": False, "mode": "model_error"}],
        focus={"topic": "你在商务印书馆做了什么"},
        provider_name="local_corpus",
    )
    assert audit["mode"] == "generated"
    payload = result["spoken_text"]
    assert "没有答成" not in payload
    sent = model.messages[-1]["content"]
    assert "不能当作事实" in sent
    assert "使用限制" in sent
    assert "不能称已解决" in sent


def test_search_hit_without_answerable_fact_is_not_enough():
    class Boom:
        def configured(self):
            return True

        def complete(self, messages):
            raise AssertionError("材料不支持时不应调用模型")

    node = {
        "kind": "evidence",
        "entry_id": "X-9",
        "searchable": "天气 北京 茅盾",
        "quote": {"effective": ""},
        "summary": {"effective": "只记下了路程。"},
        "title": {"effective": "路过"},
        "source_book": "",
        "speaker": "编者",
        "info_nature": "编者整理",
        "direct_self": 0,
        "chronology_unresolved": 0,
        "style_ok": 0,
        "event_time": "",
        "place": "北京",
        "people": "",
        "visitor_ok": 1,
    }
    index = KnowledgeIndex([node], [], [])
    result, audit, _focus = answer_from_corpus(index, "今天天气怎么样", model=Boom(), history=[], focus={}, provider_name="local_corpus")
    assert result["spoken_text"] == "这要看眼下的情况，我这里说不上来。"
    assert "我说不准" not in result["spoken_text"]
    assert audit["mode"] == "empty"
    assert result["model_called"] is False


def test_basic_fact_is_not_crowded_out_by_a_passing_mention():
    class Capture:
        def configured(self):
            return True

        def complete(self, messages):
            self.messages = messages
            return "我的原名是沈德鸿。"

    evidence = {
        "kind": "evidence",
        "entry_id": "NP-9",
        "searchable": "原名 原名 原名",
        "quote": {"effective": "原名一事另见他书。"},
        "summary": {"effective": "原名一事另见他书。"},
        "title": {"effective": "原名"},
        "source_book": "",
        "speaker": "编者",
        "info_nature": "编者整理",
        "direct_self": 0,
        "chronology_unresolved": 0,
        "style_ok": 0,
        "event_time": "",
        "place": "",
        "people": "",
        "visitor_ok": 1,
    }
    basics = {"kind": "basics", "unit_id": "B01", "title": "原名", "body": "原名沈德鸿。", "searchable": "原名 沈德鸿", "is_source_list": 0}
    index = KnowledgeIndex([evidence], [], [basics])
    model = Capture()
    result, audit, _focus = answer_from_corpus(index, "茅盾的原名叫什么", model=model, history=[], focus={}, provider_name="local_corpus")
    assert "沈德鸿" in model.messages[-1]["content"]
    assert result["spoken_text"] == "我的原名是沈德鸿。"
    assert audit["mode"] == "generated"


def test_vague_continuation_after_a_plot_question_asks_instead_of_searching():
    class Boom:
        def configured(self):
            return True

        def complete(self, messages):
            raise AssertionError("含糊的“那之后呢”不应直接检索创作过程")

    index = KnowledgeIndex([], [], [])
    result, audit, _focus = answer_from_corpus(
        index,
        "那之后呢",
        model=Boom(),
        history=[],
        focus={"topic": "《子夜》主要写什么"},
        provider_name="local_corpus",
    )
    assert audit["mode"] == "clarify"
    assert "书写完之后" in result["spoken_text"]
    assert result["model_called"] is False


def test_reading_feeling_does_not_require_a_hit():
    class Capture:
        def __init__(self):
            self.messages = []

        def configured(self):
            return True

        def complete(self, messages):
            self.messages = messages
            return "先从一个具体场面写起。这一点，我说不准。"

    index = KnowledgeIndex([], [], [])
    model = Capture()
    result, audit, _focus = answer_from_corpus(
        index,
        "我写不下去，开头很难，有什么办法",
        model=model,
        history=[],
        focus={},
        provider_name="local_corpus",
    )
    assert audit["mode"] == "generated"
    assert result["spoken_text"] == "先从一个具体场面写起。"
    assert "参考材料" not in model.messages[-1]["content"]
    assert "我说不准" not in result["spoken_text"]


def test_correction_does_not_reuse_an_unrelated_topic():
    class Capture:
        def __init__(self):
            self.messages = []

        def configured(self):
            return True

        def complete(self, messages):
            self.messages = messages
            return "上一句说的是姓名，没有把它说成亲眼看见。"

    basics = {"kind": "basics", "unit_id": "B01", "title": "原名", "body": "原名沈德鸿。", "searchable": "原名 沈德鸿", "is_source_list": 0}
    index = KnowledgeIndex([], [], [basics])
    model = Capture()
    result, audit, _focus = answer_from_corpus(
        index,
        "你刚才讲原名时如果说得像亲眼记得，记载里没有这层意思就请收回。",
        model=model,
        history=[{"question": "今天股市怎么样", "spoken": "这一点，我说不准。", "usable": True, "mode": "empty"}],
        focus={"topic": "今天股市怎么样"},
        provider_name="local_corpus",
    )
    assert result["model_called"] is True
    sent = model.messages[-1]["content"]
    assert "这一点，我说不准。" in sent
    assert "不要用" in sent
    assert "主要是存放新买的书籍" in model.messages[1]["content"] or "先讲具体的事" in model.messages[1]["content"]
    assert not result["spoken_text"].startswith("刚才并没有")
    assert not result["spoken_text"].startswith("我没有")


def test_outline_plot_is_not_sent_as_the_novel():
    class Capture:
        def __init__(self):
            self.messages = []

        def configured(self):
            return True

        def complete(self, messages):
            self.messages = messages
            return "吴荪甫是《子夜》里的人物。"

    draft = {
        "kind": "evidence",
        "entry_id": "ZYB-F032",
        "searchable": "吴荪甫 罢工",
        "quote": {"effective": "吴荪甫用武力解决，暂时停厂。"},
        "summary": {"effective": "旧提要中吴荪甫用武力解决罢工。"},
        "title": {"effective": "提要里的罢工"},
        "source_book": "",
        "speaker": "茅盾",
        "info_nature": "茅盾引述本人旧提要并评论",
        "direct_self": 0,
        "chronology_unresolved": 0,
        "style_ok": 1,
        "event_time": "",
        "place": "",
        "people": "吴荪甫",
        "visitor_ok": 1,
    }
    final = {
        "kind": "basics",
        "unit_id": "B07",
        "title": "小说的几个基本入口",
        "body": "《子夜》以吴荪甫等人物及都市经济关系展开。",
        "searchable": "子夜 吴荪甫",
        "is_source_list": 0,
    }
    index = KnowledgeIndex([draft], [], [final])
    model = Capture()
    answer_from_corpus(index, "吴荪甫是怎样的人", model=model, history=[], focus={}, provider_name="local_corpus")
    sent = model.messages[-1]["content"]
    assert "都市经济关系" in sent
    assert "武力解决" not in sent


def test_who_and_mixed_test_keep_the_fact():
    class Capture:
        def __init__(self):
            self.messages = []

        def configured(self):
            return True

        def complete(self, messages):
            self.messages = messages
            return "我原名沈德鸿。写过小说，也编过《小说月报》。"

    basics = [
        {"kind": "basics", "unit_id": "B01", "title": "姓名", "body": "原名沈德鸿。", "searchable": "原名 沈德鸿", "is_source_list": 0},
        {"kind": "basics", "unit_id": "B03", "title": "编辑", "body": "主持《小说月报》的革新。", "searchable": "小说月报", "is_source_list": 0},
    ]
    index = KnowledgeIndex([], [], basics)
    model = Capture()
    who, audit, _focus = answer_from_corpus(index, "你是谁", model=model, history=[], focus={}, provider_name="local_corpus")
    assert "数字化身" not in who["spoken_text"]
    assert "沈德鸿" in who["spoken_text"]
    assert audit["mode"] == "generated"
    assert "先讲具体的事" in model.messages[1]["content"]

    mixed, _audit, _focus = answer_from_corpus(index, "测试一下，你的原名是什么", model=model, history=[], focus={}, provider_name="local_corpus")
    assert mixed["spoken_text"] == "我原名沈德鸿。写过小说，也编过《小说月报》。"
    assert "原名" in model.messages[-1]["content"]
    assert "测试一下，你的原名" not in model.messages[-1]["content"]


def test_catalog_letter_motive_is_not_invented():
    item = {
        "kind": "museum",
        "item_key": "书信|DX00015982",
        "barcode": "DX00015982",
        "sheet": "书信",
        "title": "茅盾致陈白尘",
        "scope": "catalog",
        "quote_speaker": "",
        "visitor_text": "形制记录：1页、钢笔、信封有残损",
        "short_quote": "",
        "searchable": "DX00015982",
    }
    index = KnowledgeIndex([], [item], [])

    class Boom:
        def configured(self):
            return True

        def complete(self, messages):
            raise AssertionError("没有正文时不应让模型推测写信动机")

    result, audit, _focus = answer_from_corpus(
        index,
        "你为什么给他写这封信",
        model=Boom(),
        history=[],
        focus={"barcode": "DX00015982", "sheet": "书信"},
        provider_name="local_corpus",
    )
    assert audit["mode"] == "museum"
    assert "还不能确定" in result["spoken_text"]
    assert "不能作确切介绍" in result["spoken_text"]
    assert result["model_called"] is False


def test_output_check_drops_the_whole_bad_sentence():
    cleaned = output_checked("他根据年谱知道这件事。我们谈谈《子夜》。", "《子夜》")
    assert cleaned == "我们谈谈《子夜》。"
    assert "根据年谱" not in cleaned

    cleaned = output_checked("根据年谱，他曾说“这是编出来的原话”。我们谈谈《子夜》。", "《子夜》的正文不在这里")
    assert "年谱" not in cleaned
    assert "编出来的原话" not in cleaned
    assert "子夜" in cleaned


def test_catalog_shape_is_read_from_the_record():
    assert scope_for("未找到具体内容", "未取得可核实的同件材料") == "catalog"
    assert "1页" in physical_notes("页数：1；书写工具：钢笔；原备注：信封残")
    assert "钢笔" in physical_notes("页数：1；书写工具：钢笔；原备注：信封残")


def test_local_corpus_ask_hides_evidence_from_the_visitor(runtime, settings):
    settings_local = settings.__class__(**{**settings.__dict__, "qa_provider": "local_corpus"})
    from app.runtime import Runtime

    local = Runtime(settings_local)
    try:
        result = local.ask("茅盾的原名是什么？", None)
    finally:
        local.close()
    assert result["evidence"] == []
    assert result["citations"] == []
    assert result["spoken_text"]
    assert "条目" not in result["spoken_text"]
