from app.knowledge.collate import build_node
from app.knowledge.guard import output_checked
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
    assert "不能算作我自己的话" in who["spoken_text"]
    assert focus["barcode"] == "DG00011294"


def test_same_recipient_stays_separate():
    first = {"kind": "museum", "item_key": "书信|A", "barcode": "DX00000001", "sheet": "书信", "title": "其一", "scope": "catalog", "quote_speaker": "", "visitor_text": "", "short_quote": "", "searchable": "DX00000001 陈白尘 其一"}
    second = {"kind": "museum", "item_key": "书信|B", "barcode": "DX00000002", "sheet": "书信", "title": "其二", "scope": "catalog", "quote_speaker": "", "visitor_text": "", "short_quote": "", "searchable": "DX00000002 陈白尘 其二"}
    index = KnowledgeIndex([], [first, second], [])
    hits = index.search("DX00000002")
    assert [item["barcode"] for item in hits] == ["DX00000002"]


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
