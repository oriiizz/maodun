"""问答流程的测试。

这里的模型都是假的，只验证程序这一侧：去向、检索和材料的交付、检查与重写、
备用句按原因区分、处理经过的记录。回答读起来好不好，要用真实模型跑
docs/验收问题.txt 来看，不能用这里的通过数量代替。
"""

import json

from app.knowledge.guard import (
    DISPUTE_HOLD,
    GAP,
    POLITICS_AGAIN,
    POLITICS_HOLD,
    PRESENT_GAP,
    PREVIOUS_LAST,
    PREVIOUS_PLAIN,
    PRIVATE_LAST,
    TECHNICAL,
    inspect_output,
)
from app.knowledge.index import KnowledgeIndex
from app.knowledge.materials import scope_filter
from app.knowledge.materials import sentence_already_told
from app.knowledge.respond import answer_from_corpus, carry_mode
from app.knowledge.understand import answer_aim, prior_judgment
from app.knowledge.understand import heuristic_reading, person_angle, understand
from app.llm.errors import ModelCallError


class FakeModel:
    """按用途返回预设的内容。没有给“理解”预设时，这一步报错，流程改用程序里的简单规则。"""

    def __init__(self, reading=None, **replies):
        self.reading = reading
        self.replies = replies
        self.calls = []

    def configured(self):
        return True

    def complete(self, messages, *, purpose="answer", **options):
        self.calls.append((purpose, messages, options))
        if purpose == "understand":
            if self.reading is None:
                raise ModelCallError("测试里不做语义理解")
            return json.dumps(self.reading, ensure_ascii=False)
        if purpose not in self.replies:
            raise AssertionError(f"这一问不应有 {purpose} 调用")
        reply = self.replies[purpose]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, list):
            return reply.pop(0)
        return reply

    def purposes(self):
        return [purpose for purpose, _messages, _options in self.calls]

    def sent(self, purpose="answer"):
        return next(messages for name, messages, _options in reversed(self.calls) if name == purpose)[-1]["content"]


class NoCall:
    def configured(self):
        return True

    def complete(self, messages, **options):
        raise AssertionError("这一问不应调用模型")


def node(entry_id, *, title="", summary="", quote="", people="", direct_self=0, speaker="无独立发言者", nature="编者整理（年谱正文叙述）", book="《茅盾年谱》", **extra):
    base = {
        "kind": "evidence",
        "entry_id": entry_id,
        "searchable": " ".join([title, summary, quote, people]),
        "title": {"effective": title},
        "summary": {"effective": summary},
        "quote": {"effective": quote},
        "source_book": book,
        "speaker": speaker,
        "info_nature": nature,
        "direct_self": direct_self,
        "chronology_unresolved": 0,
        "style_ok": direct_self,
        "event_time": "",
        "place": "",
        "people": people,
        "visitor_ok": 1,
        "limit_theme": "",
        "limit_chapter": "",
    }
    base.update(extra)
    return base


def basics(unit_id, title, body):
    return {"kind": "basics", "unit_id": unit_id, "title": title, "body": body, "searchable": f"{title} {body}", "is_source_list": 0}


def ask(index, question, *, model=None, history=None, focus=None):
    return answer_from_corpus(index, question, model=model, history=history or [], focus=focus or {}, provider_name="local_corpus")


def turn(question, public, audit):
    return {"question": question, "spoken": public["spoken_text"], "usable": True, "mode": audit["mode"]}


def reading(*parts, person="", work="", premise="", search=()):
    return {
        "部分": [{"类型": kind, "对象": obj, "所求": want} for kind, obj, want in parts],
        "人物": person,
        "作品": work,
        "未确认前提": premise,
        "针对上一句": False,
        "检索词": list(search),
        "需要澄清": "",
    }


TRAVEL = node(
    "NP-A",
    title="化名与林晚秋东渡日本",
    summary="化名赴日，同林晚秋先到神户，次日到东京。",
    quote="化名东渡日本。",
    people="茅盾；林晚秋（同行）",
    event_time="1928年7月",
)
RELATION = node(
    "NP-B",
    title="与林晚秋由相恋同居而迁居京都",
    summary="年谱记二人由相恋到同居，决定离开东京赴京都。",
    quote="茅盾和林晚秋由相恋到同居，决定离开东京赴京都。",
    people="茅盾；林晚秋（本条所记关系）",
    limit_theme="私人关系依年谱原记，不能增写。",
)
RETURN = node(
    "NP-C",
    title="返沪",
    summary="返沪，林晚秋回四川；友人到码头迎接。与林晚秋结束在日生活。",
    quote="从日本返沪，林晚秋则回四川。",
    people="茅盾；林晚秋",
    event_time="1930年4月",
)
NOVEL = node(
    "FUG-1",
    title="《虹》写梅女士",
    summary="《虹》写梅女士的性格有阶段地发展。",
    quote="这是我第一次写人物性格有发展。",
    people="梅女士",
    direct_self=1,
    speaker="茅盾",
    nature="茅盾直接自述",
    book="《我走过的道路》",
)
PEOPLE_INDEX = KnowledgeIndex([TRAVEL, RELATION, RETURN, NOVEL], [], [])


# ---- 问候、身份和日常 ----


def test_greeting_and_identity_are_answered_by_the_program():
    index = KnowledgeIndex([], [], [])
    hello, audit, _focus = ask(index, "你好", model=NoCall())
    assert "数字化身" not in hello["spoken_text"]
    assert "说不准" not in hello["spoken_text"]
    assert audit["mode"] == "social"
    assert audit["trace"]["final_source"] == "fixed"
    assert hello["model_called"] is False

    real, real_audit, _focus = ask(index, "你是茅盾本人吗", model=NoCall())
    assert "数字化身" in real["spoken_text"] and "不是茅盾本人" in real["spoken_text"]
    assert real_audit["mode"] == "social"

    probe, probe_audit, _focus = ask(index, "测试一下", model=NoCall())
    assert probe_audit["mode"] == "social"
    assert "说不准" not in probe["spoken_text"]


def test_self_introduction_uses_basic_facts_and_first_person():
    units = [basics("B01", "姓名", "原名沈德鸿。"), basics("B03", "编辑", "主持《小说月报》的革新。")]
    index = KnowledgeIndex([], [], units)
    model = FakeModel(answer="我原名沈德鸿。写过小说，也编过《小说月报》。")
    who, audit, _focus = ask(index, "你是谁", model=model)
    assert "数字化身" not in who["spoken_text"]
    assert "沈德鸿" in who["spoken_text"]
    assert audit["mode"] == "generated"
    assert model.purposes() == ["answer"]

    mixed, _audit, _focus = ask(index, "测试一下，你的原名是什么", model=model)
    assert mixed["spoken_text"] == "我原名沈德鸿。写过小说，也编过《小说月报》。"
    assert "测试一下，你的原名" not in model.sent()
    assert "只回答原名" in model.sent()


def test_advice_needs_no_material_and_an_empty_hedge_is_rewritten():
    index = KnowledgeIndex([], [], [])
    model = FakeModel(answer="先从一个具体场面写起。这一点，我说不准。", rewrite="先从一个具体场面写起，写顺了再回头改开头。")
    result, audit, _focus = ask(index, "我写不下去，开头很难，有什么办法", model=model)
    assert audit["mode"] == "generated"
    assert result["spoken_text"] == "先从一个具体场面写起，写顺了再回头改开头。"
    assert audit["trace"]["final_source"] == "rewrite"
    assert audit["trace"]["retrieval"] == {"ran": False}
    assert "可用材料" not in model.sent()
    assert "空泛" in "".join(item["kind"] for item in audit["trace"]["drafts"][0]["findings"])


# ---- 当代政治 ----


def test_politics_is_held_and_then_released():
    index = KnowledgeIndex([NOVEL], [], [])
    politics, _audit, focus = ask(index, "你怎么看现任领导人")
    assert politics["spoken_text"] == POLITICS_HOLD
    follow, follow_audit, focus = ask(index, "那他呢", focus=focus, history=[{"question": "你怎么看现任领导人", "spoken": POLITICS_HOLD, "usable": True, "mode": "politics"}])
    assert follow_audit["mode"] == "politics"
    assert follow["spoken_text"] == POLITICS_AGAIN
    released, released_audit, focus = ask(index, "《虹》讲什么", focus=focus)
    assert released_audit["mode"] != "politics"
    assert released["spoken_text"] not in {POLITICS_HOLD, POLITICS_AGAIN}
    assert focus["last_restricted"] == ""


def test_a_named_politician_is_held_even_if_the_reading_misses_it():
    index = KnowledgeIndex([], [], [])
    model = FakeModel(reading=reading(("日常", "", "闲谈")))
    result, audit, _focus = ask(index, "聊聊特朗普吧", model=model)
    assert result["spoken_text"] == POLITICS_HOLD
    assert audit["trace"]["reading"]["source"] == "model"
    assert model.purposes() == ["understand"]


# ---- 私人感情 ----


def test_private_question_is_not_searched_and_accepts_no_premise():
    model = None
    private, audit, focus = ask(PEOPLE_INDEX, "你承认和林晚秋同居过吗", model=model, focus={"person": "林晚秋"})
    assert private["spoken_text"] == PRIVATE_LAST
    assert audit["mode"] == "boundary"
    assert audit["trace"]["route"] == "private"
    assert audit["trace"]["retrieval"] == {"ran": False}
    assert audit["hit_ids"] == []
    for banned in ("感情", "关系", "私事", "说不准", "《虹》", "承认"):
        assert banned not in private["spoken_text"]
    assert focus["last_restricted"] == "private"


def test_private_candidates_are_checked_one_by_one():
    candidates = json.dumps({"候选": ["这段感情，就不谈了。", "是不是爱过，我说不准。", "你若有兴趣，我们谈谈《虹》。", "这个断语，我不来下。"]}, ensure_ascii=False)
    model = FakeModel(
        reading=reading(("私人感情", "林晚秋", "要我确认有没有爱意"), person="林晚秋", premise="预设有感情"),
        private_candidates=candidates,
        private_judge=json.dumps({"结果": [{"序号": 1, "问题": []}]}),
    )
    result, audit, _focus = ask(PEOPLE_INDEX, "你爱林晚秋吗", model=model)
    assert result["spoken_text"] == "这个断语，我不来下。"
    assert audit["trace"]["final_source"] == "model"
    log = audit["trace"]["drafts"][-1]["candidates"]
    assert any("接受前提" in problem for problem in log[0]["problems"])
    assert any("装作不确定" in problem for problem in log[1]["problems"])
    assert any("主动提作品" in problem for problem in log[2]["problems"])
    assert log[3]["problems"] == []
    assert model.purposes() == ["understand", "private_candidates", "private_judge"]


def test_private_candidates_rejected_by_the_semantic_check_fall_back():
    model = FakeModel(
        reading=reading(("私人感情", "林晚秋", "要我确认有没有爱意"), person="林晚秋"),
        private_candidates=json.dumps({"候选": ["这个断语，我不来下。"]}, ensure_ascii=False),
        private_judge=json.dumps({"结果": [{"序号": 1, "问题": ["1"]}]}),
    )
    result, audit, _focus = ask(PEOPLE_INDEX, "你爱林晚秋吗", model=model)
    assert result["spoken_text"] == PRIVATE_LAST
    assert audit["trace"]["final_source"] == "fallback"


def test_pressing_looks_at_what_the_previous_sentence_said():
    plain = [{"question": "你爱林晚秋吗", "spoken": PRIVATE_LAST, "usable": True, "mode": "boundary"}]
    pressed, audit, _focus = ask(PEOPLE_INDEX, "你刚才已经承认了", history=plain, focus={"person": "林晚秋", "last_restricted": "private"})
    assert audit["mode"] == "previous"
    assert pressed["spoken_text"] == PREVIOUS_PLAIN
    assert "不能当作承认" not in pressed["spoken_text"]

    bad = [{"question": "你爱林晚秋吗", "spoken": "这是私人的感情，我不在这里谈。", "usable": True, "mode": "boundary"}]
    retracted, _audit, _focus = ask(PEOPLE_INDEX, "所以你承认了", history=bad, focus={"person": "林晚秋", "last_restricted": "private"})
    assert retracted["spoken_text"] == PREVIOUS_LAST

    model = FakeModel(
        reading=reading(("追逼", "林晚秋", "说上一句已经承认"), person="林晚秋"),
        answer="上一句那个说法不妥，应当收回。它并不是一个断语。",
        review=json.dumps({"问题": []}),
    )
    written, written_audit, _focus = ask(PEOPLE_INDEX, "所以你承认了", model=model, history=bad, focus={"person": "林晚秋", "last_restricted": "private"})
    assert written["spoken_text"] == "上一句那个说法不妥，应当收回。它并不是一个断语。"
    sent = model.sent()
    assert "这是私人的感情，我不在这里谈。" in sent
    assert "私人的感情" in sent and "收回" in sent
    assert written_audit["trace"]["retrieval"] == {"ran": False}


def test_so_you_admit_it_is_answered_from_the_previous_sentence_even_when_read_as_private():
    history = [{"question": "你爱林晚秋吗", "spoken": "这个断语，我不下。", "usable": True, "mode": "boundary"}]
    model = FakeModel(
        reading=reading(("私人感情", "林晚秋", "要确认与林晚秋有私人感情"), person="林晚秋"),
        answer="上一句说的是不下这个断语，并没有认下什么。",
        review=json.dumps({"问题": []}),
    )
    result, audit, _focus = ask(PEOPLE_INDEX, "这么说你是承认和林晚秋有私人感情哦？", model=model, history=history, focus={"person": "林晚秋", "last_restricted": "private"})
    assert audit["mode"] == "previous"
    assert result["spoken_text"] == "上一句说的是不下这个断语，并没有认下什么。"
    assert "这个断语，我不下。" in model.sent()
    assert audit["trace"]["retrieval"] == {"ran": False}


def test_why_wont_you_say_after_a_refusal_is_about_the_previous_sentence():
    history = [{"question": "你爱林晚秋吗", "spoken": "这个断语，我不下。", "usable": True, "mode": "boundary"}]
    model = FakeModel(
        reading=reading(("其他史实", "", "问上一句为什么不肯给出明确说法"), person="林晚秋"),
        answer="这样的事不由我来下断语，并不是有话不肯讲。",
        review=json.dumps({"问题": []}),
    )
    result, audit, focus = ask(PEOPLE_INDEX, "你为什么不肯说", model=model, history=history, focus={"person": "林晚秋", "last_restricted": "private"})
    assert audit["mode"] == "previous"
    assert audit["trace"]["retrieval"] == {"ran": False}
    assert result["spoken_text"] == "这样的事不由我来下断语，并不是有话不肯讲。"

    # 上一句不是收住的话时，同样的问法不改去向。
    plain = FakeModel(reading=reading(("其他史实", "", "问为什么不肯说"), person=""), answer="这一问，我还不能肯定。")
    _result, plain_audit, _focus = ask(PEOPLE_INDEX, "你为什么不肯说", model=plain, focus={})
    assert plain_audit["mode"] != "previous"


def test_lodging_details_do_not_stand_in_for_an_introduction():
    first = "林晚秋是怎样的人，我这里没有可靠的介绍可以说。能确知的是，到东京后她住寄宿舍，我住本乡馆。"
    second = "林晚秋是怎样的人，我这里没有可靠的介绍可以说。能确知的只是有过一点来往：当年曾同她一起去日本。"
    model = FakeModel(
        reading=reading(("人物介绍", "林晚秋", "介绍林晚秋是谁"), person="林晚秋"),
        answer=first,
        rewrite=second,
        review=json.dumps({"问题": []}),
    )
    result, audit, _focus = ask(PEOPLE_INDEX, "林晚秋是谁", model=model)
    assert result["spoken_text"] == second
    assert audit["trace"]["final_source"] == "rewrite"
    assert "住处" in model.sent("rewrite")


def test_a_refusal_does_not_lock_the_next_question_but_keeps_who_she_is():
    love, audit, focus = ask(PEOPLE_INDEX, "你爱林晚秋吗", focus={})
    history = [turn("你爱林晚秋吗", love, audit)]
    assert audit["mode"] == "boundary"

    book, book_audit, focus = ask(PEOPLE_INDEX, "《虹》讲什么", focus=focus, history=history)
    assert book_audit["trace"]["route"] == "work"
    assert book_audit["mode"] != "boundary"
    assert focus["last_restricted"] == ""
    assert focus["work"] == "《虹》"

    again, again_audit, _focus = ask(PEOPLE_INDEX, "那你爱她吗", focus={**focus, "person": "林晚秋"}, history=history)
    assert again_audit["mode"] == "boundary"


# ---- 人物介绍和关联求证 ----


def test_person_intro_without_identity_gets_only_the_contact_sentences():
    model = FakeModel(
        reading=reading(("人物介绍", "林晚秋", "介绍林晚秋是谁"), person="林晚秋"),
        answer=json.dumps({"用到": ["E1"], "缺少": "身份", "正文": "林晚秋是怎样的人，我说不上来。只知道一九二八年去日本时，她是同行的人。"}, ensure_ascii=False),
        review=json.dumps({"问题": []}),
    )
    result, audit, focus = ask(PEOPLE_INDEX, "那你能介绍一下林晚秋吗", model=model, focus={"person": "林晚秋", "last_restricted": "private"})
    assert result["spoken_text"] == "林晚秋是怎样的人，我说不上来。只知道一九二八年去日本时，她是同行的人。"
    assert audit["mode"] == "generated"
    assert focus["last_restricted"] == ""
    sent = model.sent()
    for hidden in ("相恋", "同居", "结束在日生活", "NP-A", "NP-B", "《茅盾年谱》", "本条所记关系", "决定离开东京"):
        assert hidden not in sent
    assert "没有写这个人的身份" in sent
    assert "同林晚秋先到神户" in sent or "林晚秋回四川" in sent
    trace = audit["trace"]
    assert "NP-B" not in audit["hit_ids"]
    assert any(item["id"] == "NP-B" for item in trace["retrieval"]["withheld"])
    assert trace["retrieval"]["used"] and set(trace["retrieval"]["used"]) <= {"NP-A", "NP-C"}
    assert "能说明身份的 0 条" in "".join(trace["sufficiency"])
    assert model.purposes() == ["understand", "answer", "review"]


def test_itinerary_instead_of_an_introduction_is_not_salvaged_by_trimming():
    long = "一九二八年七月我化名去日本，她同行。先到神户，次日到东京。到东京后她住寄宿舍，我住本乡馆。一九三〇年四月我回上海，她回四川。"
    model = FakeModel(
        reading=reading(("人物介绍", "林晚秋", "介绍林晚秋是谁"), person="林晚秋"),
        answer=long,
        rewrite=long,
        review=json.dumps({"问题": []}),
    )
    result, audit, _focus = ask(PEOPLE_INDEX, "林晚秋是谁", model=model)
    assert result["spoken_text"] == "林晚秋是怎样的人，我这里说不上来。"
    assert audit["trace"]["final_source"] == "fallback"
    assert audit["mode"] == "empty"
    assert "罗列行程" in audit["notice"]


def test_person_intro_puts_identity_material_first():
    own = node(
        "OWN-1",
        title="追忆同瞿秋白的约定",
        summary="瞿秋白来辞行。",
        quote="他来向我辞行。",
        people="核心人物：瞿秋白\n与茅盾关系：同志、朋友\n相关人物：沈泽民",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
    )
    others = [node(f"NP-{number}", title="瞿秋白评论刊出", summary="瞿秋白读子夜评论刊出。", quote="瞿秋白评论刊出。", people="瞿秋白") for number in range(7)]
    index = KnowledgeIndex([*others, own], [], [])
    model = FakeModel(
        reading=reading(("人物介绍", "瞿秋白", "瞿秋白是怎样的人"), person="瞿秋白"),
        answer="瞿秋白是我的同志，也是朋友。",
        review=json.dumps({"问题": []}),
    )
    _result, audit, _focus = ask(index, "瞿秋白是怎样的人", model=model)
    assert audit["hit_ids"][0] == "OWN-1"
    assert "同志、朋友" in model.sent()
    assert "能说明身份的 1 条" in "".join(audit["trace"]["sufficiency"])


def test_link_needs_a_record_that_names_both():
    model = FakeModel(
        reading=reading(("关联求证", "林晚秋与《虹》", "两者有没有关系"), person="林晚秋", work="《虹》"),
        answer="林晚秋和《虹》有什么关系，我说不出确实的关联。",
        review=json.dumps({"问题": []}),
    )
    result, audit, focus = ask(PEOPLE_INDEX, "她与《虹》有什么关系", model=model, focus={"person": "林晚秋"})
    assert result["spoken_text"] == "林晚秋和《虹》有什么关系，我说不出确实的关联。"
    assert focus["person"] == "林晚秋"
    sent = model.sent()
    assert "没有一条记录同时写到林晚秋和《虹》" in sent
    assert "神户" not in sent and "同居" not in sent
    assert "0 条" in "".join(audit["trace"]["sufficiency"])


def test_asking_why_a_book_came_up_is_answered_from_the_dialogue_itself():
    history = [
        {"question": "你爱林晚秋吗", "spoken": "有关私人交往的这些说法，就不在这里展开了。你若对《虹》感兴趣，我们可以谈谈这部小说。", "usable": True, "mode": "boundary"},
    ]
    model = FakeModel(
        reading=reading(("追问缘由", "《虹》", "问上一句为什么提《虹》"), work="《虹》"),
        answer="上一句提到《虹》，转得不贴切，和你问的事并不相干。",
        review=json.dumps({"问题": []}),
    )
    result, audit, _focus = ask(PEOPLE_INDEX, "为什么你刚才突然提《虹》", model=model, history=history, focus={"person": "林晚秋", "last_restricted": "private"})
    assert audit["mode"] == "previous"
    assert "贴切" in result["spoken_text"]
    sent = model.sent()
    assert "先前的回答自己提起的" in sent
    assert "可用材料" not in sent

    asked_first = [{"question": "《虹》讲什么", "spoken": "《虹》写梅女士。", "usable": True, "mode": "generated"}]
    model = FakeModel(reading=reading(("追问缘由", "《虹》", "问为什么提《虹》"), work="《虹》"), answer="《虹》是你先问起的，我是接着你的话说的。")
    ask(PEOPLE_INDEX, "你刚才为什么提这部小说", model=model, history=asked_first, focus={"work": "《虹》"})
    assert "对方先问起的" in model.sent()


def test_relation_wording_is_withheld_only_for_other_people_s_accounts():
    kept, withheld = scope_filter([TRAVEL, RELATION, RETURN, NOVEL])
    assert [hit["entry_id"] for hit in kept] == ["NP-A", "NP-C", "FUG-1"]
    assert {item["id"] for item in withheld} == {"NP-B", "NP-C"}
    assert "结束在日生活" not in kept[1]["summary"]["effective"]
    assert "林晚秋回四川" in kept[1]["summary"]["effective"]

    plot = node("PLOT-1", title="《蚀》里的恋人", summary="《追求》写一对恋人同居后的幻灭。", quote="《追求》里的恋人。", people="章秋柳")
    own = node("OWN-2", title="谈恋爱小说", summary="我谈当时的恋爱小说和同居风气。", quote="他们同居了。", people="青年", direct_self=1, speaker="茅盾")
    kept, withheld = scope_filter([plot, own])
    assert len(kept) == 2 and withheld == []


# ---- 史实、追问、材料不足和技术故障 ----


def test_follow_up_uses_the_topic_and_a_failed_turn_is_not_a_fact():
    record = node(
        "NP-1",
        title="编译所",
        summary="后来仍在商务印书馆。",
        quote="仍在商务印书馆编译所。",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
        limit_theme="日期分歧，不能称已解决。",
    )
    index = KnowledgeIndex([record], [], [])
    model = FakeModel(answer="后来我仍在商务印书馆做事。", review=json.dumps({"问题": []}))
    result, audit, _focus = ask(
        index,
        "那之后呢",
        model=model,
        history=[{"question": "你在商务印书馆做了什么", "spoken": "这一次没有答成，请稍后再试。", "usable": False, "mode": "model_error"}],
        focus={"topic": "你在商务印书馆做了什么"},
    )
    assert audit["mode"] == "generated"
    assert "没有答成" not in result["spoken_text"]
    sent = model.sent()
    assert "不能当作事实" in sent
    assert "其他限制" in sent and "不能称已解决" in sent
    assert "《我走过的道路》" not in sent and "NP-1" not in sent


def test_an_elliptical_question_is_searched_with_the_previous_one():
    index = KnowledgeIndex([], [], [basics("B01", "姓名", "原名沈德鸿，字雁冰。")])
    model = FakeModel(answer="我字雁冰。")
    result, audit, _focus = ask(index, "字呢？", model=model, focus={"topic": "你的原名是什么"})
    assert result["spoken_text"] == "我字雁冰。"
    assert audit["hit_ids"] == ["B01"]
    assert "只回答字" in model.sent()


def test_present_day_question_missing_material_and_technical_failure_read_differently():
    passing = node("X-9", title="路过", summary="只记下了路程。", people="", place="北京")
    passing["searchable"] = "天气 北京 茅盾"
    index = KnowledgeIndex([passing, NOVEL], [], [])

    weather, weather_audit, _focus = ask(index, "今天天气怎么样", model=FakeModel())
    assert weather["spoken_text"] == PRESENT_GAP
    assert weather_audit["mode"] == "empty"
    assert weather["model_called"] is False

    missing, missing_audit, _focus = ask(index, "《霜叶红似二月花》讲什么", model=FakeModel())
    assert missing_audit["mode"] == "empty"
    assert missing_audit["trace"]["final_source"] == "fixed"
    assert "还不能作确切的介绍" in missing["spoken_text"]

    broken = FakeModel(answer=ModelCallError("调用超时。"))
    failed, failed_audit, _focus = ask(index, "《虹》讲什么", model=broken)
    assert failed["spoken_text"] == TECHNICAL
    assert failed_audit["mode"] == "model_error"
    assert failed_audit["notice"] == "调用超时。"
    assert failed_audit["trace"]["calls"][-1]["ok"] is False

    assert len({weather["spoken_text"], missing["spoken_text"], failed["spoken_text"], PRIVATE_LAST, POLITICS_HOLD}) == 5
    assert "说不准" not in weather["spoken_text"] + missing["spoken_text"] + failed["spoken_text"]


def test_backstage_wording_is_rewritten_as_a_whole_sentence():
    index = KnowledgeIndex([], [], [basics("B01", "姓名", "原名沈德鸿。")])
    model = FakeModel(answer="根据年谱编者整理的记述，我原名沈德鸿。", rewrite="我原名沈德鸿。")
    result, audit, _focus = ask(index, "茅盾的原名叫什么", model=model)
    assert result["spoken_text"] == "我原名沈德鸿。"
    assert audit["trace"]["final_source"] == "rewrite"
    kinds = [item["kind"] for item in audit["trace"]["drafts"][0]["findings"]]
    assert "来源说明" in kinds
    assert model.purposes() == ["understand", "answer", "rewrite"]
    assert "年谱" in model.sent("rewrite")


def test_a_failed_rewrite_keeps_only_whole_sentences_or_says_it_plainly():
    index = KnowledgeIndex([], [], [basics("B01", "姓名", "原名沈德鸿。")])
    bad = "我原名沈德鸿。这些是年谱编者整理的记述。"
    model = FakeModel(answer=bad, rewrite=bad)
    result, audit, _focus = ask(index, "茅盾的原名叫什么", model=model)
    assert result["spoken_text"] == "我原名沈德鸿。"
    assert audit["trace"]["final_source"] == "kept_part"

    worse = "这些是年谱编者整理的记述。"
    model = FakeModel(answer=worse, rewrite=worse)
    result, audit, _focus = ask(index, "茅盾的原名叫什么", model=model)
    assert result["spoken_text"] == GAP
    assert audit["trace"]["final_source"] == "fallback"


def test_basic_fact_is_not_crowded_out_and_a_buried_fact_is_still_found():
    noise = node("NP-9", title="原名", summary="原名一事另见他书。", quote="原名一事另见他书。")
    noise["searchable"] = "原名 原名 原名"
    index = KnowledgeIndex([noise], [], [basics("B01", "原名", "原名沈德鸿。")])
    model = FakeModel(answer="我的原名是沈德鸿。")
    result, audit, _focus = ask(index, "茅盾的原名叫什么", model=model)
    assert "沈德鸿" in model.sent()
    assert result["spoken_text"] == "我的原名是沈德鸿。"
    assert audit["mode"] == "generated"

    buried = node("NP-Y", title="姓名", summary="原名沈德鸿。", quote="原名沈德鸿。", direct_self=1, speaker="茅盾", nature="茅盾直接自述")
    buried["searchable"] = "一段不相干的路程"
    other = node("NP-X", title="路过", summary="这里只记了别的事。", quote="这里只记了别的事。")
    other["searchable"] = "原名 原名 原名"
    index = KnowledgeIndex([other, buried], [], [])
    model = FakeModel(answer="我原名沈德鸿。")
    result, audit, _focus = ask(index, "茅盾的原名叫什么", model=model)
    assert "NP-Y" in audit["hit_ids"]
    assert result["spoken_text"] == "我原名沈德鸿。"


def test_outline_plot_is_not_sent_as_the_novel():
    draft = node(
        "ZYB-F032",
        title="提要里的罢工",
        summary="旧提要中吴荪甫用武力解决罢工。",
        quote="吴荪甫用武力解决，暂时停厂。",
        people="吴荪甫",
        speaker="茅盾",
        nature="茅盾引述本人旧提要并评论",
    )
    final = basics("B07", "小说的几个基本入口", "《子夜》以吴荪甫等人物及都市经济关系展开。")
    index = KnowledgeIndex([draft], [], [final])
    model = FakeModel(answer="吴荪甫是《子夜》里的人物。")
    ask(index, "吴荪甫是怎样的人", model=model)
    sent = model.sent()
    assert "都市经济关系" in sent
    assert "武力解决" not in sent


def test_vague_continuation_after_a_plot_question_asks_first():
    index = KnowledgeIndex([], [], [])
    result, audit, _focus = ask(index, "那之后呢", model=NoCall(), focus={"topic": "《子夜》主要写什么"})
    assert audit["mode"] == "clarify"
    assert "《子夜》" in result["spoken_text"] and "写完以后" in result["spoken_text"]


def test_correction_is_answered_from_the_previous_sentence():
    index = KnowledgeIndex([], [], [basics("B01", "原名", "原名沈德鸿。")])
    model = FakeModel(answer="上一句说的是眼下的行情我说不上来，并没有讲到原名。")
    result, audit, _focus = ask(
        index,
        "你刚才讲原名时如果说得像亲眼记得，就请收回。",
        model=model,
        history=[{"question": "今天股市怎么样", "spoken": PRESENT_GAP, "usable": True, "mode": "empty"}],
        focus={"topic": "今天股市怎么样"},
    )
    assert audit["mode"] == "previous"
    sent = model.sent()
    assert PRESENT_GAP in sent
    assert "不要用“我没有”" in sent
    assert not result["spoken_text"].startswith(("刚才并没有", "我没有"))


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
    result, audit, _focus = ask(index, "你为什么给他写这封信", model=NoCall(), focus={"barcode": "DX00015982", "sheet": "书信"})
    assert audit["mode"] == "museum"
    assert "为什么要写" in result["spoken_text"] and "钢笔" in result["spoken_text"]
    assert result["model_called"] is False

    again, _audit, _focus = ask(
        index,
        "你为什么这样措辞",
        model=NoCall(),
        history=[{"question": "这封信写了什么", "spoken": result["spoken_text"], "usable": True, "mode": "museum"}],
        focus={"barcode": "DX00015982", "sheet": "书信"},
    )
    assert "措辞" in again["spoken_text"]
    assert "钢笔" not in again["spoken_text"]


# ---- 理解这一步 ----


def test_reading_falls_back_to_rules_and_does_not_inherit_a_refusal():
    focus = {"person": "林晚秋", "last_restricted": "private"}
    assert [part["type"] for part in heuristic_reading("《虹》讲什么", focus)["parts"]] == ["work"]
    assert [part["type"] for part in heuristic_reading("你爱读书吗", {})["parts"]] != ["private"]
    assert [part["type"] for part in heuristic_reading("那你爱她吗", focus)["parts"]] == ["private"]
    link = heuristic_reading("她与《虹》有什么关系", focus)
    assert [part["type"] for part in link["parts"]] == ["link"]
    assert link["person"] == "林晚秋" and link["work"] == "《虹》"

    def broken(messages, **options):
        raise ModelCallError("超时")

    assert understand("《虹》讲什么", [], {}, broken)["source"] == "fallback"

    def garbled(messages, **options):
        return "不是 JSON"

    assert understand("《虹》讲什么", [], {}, garbled)["source"] == "fallback"


def test_asking_why_he_wrote_something_is_not_a_question_about_the_last_sentence():
    def said(messages, **options):
        return json.dumps(reading(("追问缘由", "吴荪甫", "为什么这样写")), ensure_ascii=False)

    result = understand("你为什么把他写成这样", [], {"work": "《子夜》"}, said)
    assert [part["type"] for part in result["parts"]] == ["process"]
    assert result["work"] == "《子夜》"

    result = understand("你刚才为什么提这部小说", [], {"work": "《子夜》"}, said)
    assert [part["type"] for part in result["parts"]] == ["meta"]


# ---- 输出检查 ----


def test_output_check_names_the_problem_and_the_sentence():
    def kinds(text, **options):
        return {item["kind"] for item in inspect_output(text, **options)[1]}

    assert "接受前提" in kinds("这是私人的感情，我不在这里谈。", private=True)
    assert "接受前提" in kinds("我们之间的事，就不谈了。", private=True)
    assert "辩解套话" in kinds("这不能当作承认，也不能当作否认。")
    assert "辩解套话" in kinds("我不作第一人称的确认或解释。")
    assert "虚构隐情" in kinds("有些旧伤，我不愿再回忆。", private=True)
    assert "装作不确定" in kinds("是不是爱过，我说不准。", private=True, closing=True)
    assert "私人断言" in kinds("我和她相恋过。")
    assert "来源说明" in kinds("这些都是年谱编者整理的行迹。")
    assert "后台说明" in kinds("手头没有写到它的发表记录。")
    assert "后台说明" in kinds("知识库显示我原名沈德鸿。")
    assert "空泛的不确定" in kinds("这一点，我说不准。")
    assert kinds("这个断语，我不来下。", private=True, closing=True) == set()
    assert kinds("《追求》写一对恋人的幻灭。") == set()
    assert kinds("年谱里记的是一九一八年八月。", sources_ok=True) == set()
    assert kinds("先把手头的材料里最熟的一段写出来。", advice=True) == set()
    assert "后台说明" in kinds("叶圣陶与新文学事业有具体材料可查。")
    assert "人称" in kinds("这句话我无法确认是出自茅盾。")
    assert "人称" in kinds("不能把它当作茅盾说过的话来引用。")
    assert kinds("发表时署名茅盾，是叶圣陶改的。") == set()
    assert kinds("这一章收在《茅盾回忆录》里。", sources_ok=True) == set()


def test_person_intro_drops_only_the_unsupported_sentence_and_still_names_the_person():
    own = node(
        "OWN-1",
        title="追忆同瞿秋白的约定",
        summary="瞿秋白来辞行。",
        quote="他来向我辞行。",
        people="核心人物：瞿秋白\n与茅盾关系：同志、朋友\n相关人物：沈泽民",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
    )
    index = KnowledgeIndex([own], [], [])
    text = "他是那个时代最重要的人物之一。他去江西以前来向我辞行。"
    flagged = json.dumps({"问题": [{"项": "D", "句子": "他是那个时代最重要的人物之一。", "说明": "材料里没有这个评断"}]}, ensure_ascii=False)
    model = FakeModel(
        reading=reading(("人物介绍", "瞿秋白", "瞿秋白是怎样的人"), person="瞿秋白"),
        answer=text,
        rewrite=text,
        review=[flagged, flagged],
    )
    result, audit, _focus = ask(index, "瞿秋白是怎样的人", model=model)
    assert audit["trace"]["final_source"] == "kept_part"
    assert result["spoken_text"] == "说到瞿秋白：他去江西以前来向我辞行。"


def test_follow_up_looks_first_for_what_came_after_the_last_answer():
    def record(entry_id, year, text):
        return node(entry_id, title=f"商务印书馆编译所{text}", summary=f"在商务印书馆编译所{text}。", quote=f"商务印书馆编译所{text}。", direct_self=1, speaker="茅盾", nature="茅盾直接自述", book="《我走过的道路》", event_time=f"{year}年")

    early = record("E-1916", 1916, "入馆改卷")
    middle = record("E-1923", 1923, "不编月报改做杂事")
    leave = record("E-1926", 1926, "辞职离馆")
    index = KnowledgeIndex([early, middle, leave], [], [])
    model = FakeModel(
        reading=reading(("接着说", "商务印书馆之后", "接着讲后来的事"), search=["商务印书馆", "编译所", "之后"]),
        answer=json.dumps({"用到": ["E1"], "缺少": "", "正文": "一九二六年我辞了职，离开了商务印书馆。"}, ensure_ascii=False),
        review=json.dumps({"问题": []}),
    )
    result, audit, _focus = ask(
        index,
        "那之后呢",
        model=model,
        history=[{"question": "你在商务印书馆做过什么", "spoken": "一九二三年我不编月报，改做杂事。", "usable": True, "mode": "generated"}],
        focus={"topic": "你在商务印书馆做过什么", "used": ["E-1923"]},
    )
    adopted = [item["id"] for item in audit["trace"]["retrieval"]["adopted"]]
    assert adopted[0] == "E-1926"
    assert "E-1916" not in adopted[:1]
    assert result["spoken_text"] == "一九二六年我辞了职，离开了商务印书馆。"


def test_follow_up_query_leaves_out_the_words_that_only_mean_continue():
    from app.knowledge.materials import gather

    reading = {"parts": [{"type": "follow", "object": "", "ask": ""}], "person": "", "work": "", "search": ["商务印书馆", "离开", "之后"]}
    info = gather(None, "那之后呢", reading, reading["parts"], topic="你在商务印书馆做过什么")
    assert "之后" not in info["query"] and "商务印书馆" in info["query"]
    bare = {"parts": [{"type": "follow", "object": "", "ask": ""}], "person": "", "work": "", "search": []}
    assert gather(None, "后来呢", bare, bare["parts"], topic="你在商务印书馆做过什么")["query"] == "你在商务印书馆做过什么"


# ---- 处理经过只给维护者 ----


def test_trace_is_stored_for_maintainers_and_never_returned(settings):
    from app.runtime import Runtime

    local = Runtime(settings)
    try:
        result = local.ask("你好", None)
        rows = local.db.query("SELECT trace_json FROM answer_trace WHERE turn_id = ?", (result["turn_id"],))
    finally:
        local.close()
    assert "trace" not in result and "trace_json" not in json.dumps(result, ensure_ascii=False)
    assert result["evidence"] == [] and result["citations"] == []
    trace = json.loads(rows[0]["trace_json"])
    assert trace["question"] == "你好"
    assert trace["final"] == result["spoken_text"]
    assert trace["final_source"] == "fixed"
    assert trace["rules_version"] and trace["style_version"]
    assert "api_key" not in rows[0]["trace_json"].lower()


def _mao_index():
    scene = node(
        "ZSI-1",
        title="默坐相陪",
        summary="工友离开后他沉思，我不敢打扰。",
        quote="他默然沉思，我不敢打扰，也默坐相陪。",
        people="核心人物：毛泽东\n与茅盾关系：中央代表、党内同志",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
        place="广州",
        event_time="1926年3月19日",
    )
    meeting = node(
        "LPC-1",
        title="第一次见面",
        summary="这是我第一次见到他，地点在上海。",
        quote="这是我第一次见到他。",
        people="核心人物：毛泽东\n与茅盾关系：中央委员",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
        place="上海",
        event_time="1923年8月5日",
    )
    dispute = node(
        "REV-1",
        title="农运传闻",
        summary="对农民运动的过火传闻存疑，包括砸祖宗牌位和强迫剪发。",
        quote="农民运动里所谓过火，有砸祖宗牌位、强迫剪发的说法。",
        people="毛泽东",
        place="武汉",
    )
    stance = node(
        "LPC-2",
        title="后来的评断",
        summary="由此感到他早年就注意掌握枪杆子，后来更能指挥千军万马。",
        quote="注意掌握枪杆子，指挥千军万马。",
        people="毛泽东",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
    )
    return KnowledgeIndex([dispute, stance, scene, meeting], [], [])


def test_person_questions_are_told_apart_without_another_model_call():
    assert person_angle("你怎么看待毛泽东") == ("impression", False)
    assert person_angle("你认为他是一个什么样的人") == ("impression", False)
    assert person_angle("你第一次见到他是什么时候") == ("meeting", True)
    assert person_angle("你们在广州相处时，有什么事情可以讲") == ("episode", False)
    assert person_angle("叶圣陶是谁") == ("identity", False)
    assert person_angle("你怎么看待《子夜》") == ("", False)
    assert person_angle("吴荪甫是怎样的人") == ("", False)
    assert person_angle("这句话是谁说的") == ("", False)

    misread = json.dumps(reading(("当代政治", "毛泽东", "评价毛泽东"), person="毛泽东"), ensure_ascii=False)

    def said(messages, **options):
        return misread

    result = understand("你怎么看待毛泽东", [], {}, said)
    assert result["angle"] == "impression"
    assert result["date_precise"] is False
    assert [part["type"] for part in result["parts"]] == ["person_intro"]

    current = understand("你怎么看现任领导人", [], {}, said)
    assert current["angle"] == ""
    assert "politics" in [part["type"] for part in current["parts"]]


def test_impression_keeps_the_scene_and_leaves_out_the_dispute():
    model = FakeModel(
        reading=reading(("文学分析", "毛泽东", "怎么看待"), person="毛泽东"),
        answer="我第一次见他，是在二十年代初的上海。后来在广州，有一晚他默然沉思，我不敢打扰，便也默坐相陪。",
    )
    result, audit, _focus = ask(_mao_index(), "你怎么看待毛泽东", model=model)
    assert result["spoken_text"].startswith("我第一次见他")
    assert "默坐相陪" in result["spoken_text"]
    assert audit["mode"] == "generated"
    sent = model.sent()
    assert "默坐相陪" in sent and "第一次见到他" in sent
    assert "农民运动" not in sent and "牌位" not in sent and "枪杆子" not in sent and "千军万马" not in sent
    assert "与茅盾关系" not in sent and "中央代表" not in sent
    assert "不要用职务作开头" in sent
    assert "二十年代初" in sent or "大致" in sent
    assert model.purposes() == ["understand", "answer"]
    omitted = audit["trace"]["retrieval"]["omitted"]
    assert {item["id"] for item in omitted} >= {"REV-1", "LPC-2"}


def test_a_dated_meeting_question_keeps_the_material_date():
    model = FakeModel(
        reading=reading(("其他史实", "", "什么时候")),
        answer="我第一次见到他，是一九二三年八月五日，在上海。",
    )
    result, audit, _focus = ask(_mao_index(), "你第一次见到他是什么时候", model=model, focus={"person": "毛泽东"})
    assert "八月五日" in result["spoken_text"]
    assert audit["trace"]["reading"]["angle"] == "meeting"
    assert audit["trace"]["reading"]["date_precise"] is True
    assert "材料里写明" in model.sent()
    assert "会上讨论" in model.sent()
    assert "REV-1" not in audit["hit_ids"]
    assert model.purposes() == ["understand", "answer"]


def test_an_episode_stays_on_that_episode():
    model = FakeModel(
        reading=reading(("人物介绍", "毛泽东", "广州相处的事"), person="毛泽东"),
        answer="在广州，有一晚他默然沉思，我不敢打扰，便也默坐相陪。",
    )
    result, audit, _focus = ask(_mao_index(), "你们在广州相处时，有什么事情可以讲？", model=model, focus={"person": "毛泽东"})
    assert "默坐相陪" in result["spoken_text"]
    assert "只讲这件事" in model.sent()
    assert audit["trace"]["reading"]["angle"] == "episode"
    assert audit["hit_ids"][0] == "ZSI-1"
    assert "农民运动" not in model.sent()


def test_output_check_catches_a_catalog_opening_added_feeling_and_a_stock_ending():
    def kinds(text, allowed="", **options):
        return {item["kind"] for item in inspect_output(text, allowed, **options)[1]}

    assert "资料卡开头" in kinds("毛泽东是中央委员，也做过中央代表。", angle="impression")
    assert "添加心理" in kinds("他黯然沉思，我很敬仰。", "他默然沉思", angle="impression")
    assert "主动夹带争议" in kinds("后来又谈到农民运动和过火。", angle="impression")
    assert "日期过密" in kinds("那是1923年8月5日的事。", angle="impression")
    assert "日期过密" not in kinds("那是1923年8月5日的事。", angle="meeting", date_precise=True)
    assert "多余收束" in kinds("我见过他。至于其他方面，我不能介绍。", angle="impression")
    assert "多余收束" not in kinds("这个断语我不下。", angle="impression", private=True)
    assert "资料卡开头" not in kinds("我第一次见到他时，他是中央代表。", angle="meeting")
    assert kinds("叶圣陶是作家，也是编辑。", angle="identity") == set()


def _stay_nodes(*, later=False):
    scene = node(
        "ZSI-1",
        title="默坐相陪",
        summary="广州寓所同住。毛泽东夫妇住楼上。",
        quote="毛泽东夫妇住楼上，肖楚女和我住楼下。有一晚他默然沉思，我不敢打扰，也默坐相陪。",
        people="核心人物：毛泽东\n与茅盾关系：党内同志",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
        place="广州",
        event_time="1926年3月19日",
    )
    meeting = node(
        "LPC-1",
        title="第一次见面",
        summary="这是我第一次见到毛泽东，地点在上海。",
        quote="这是我第一次见到毛泽东。",
        people="核心人物：毛泽东",
        direct_self=1,
        speaker="茅盾",
        nature="茅盾直接自述",
        book="《我走过的道路》",
        place="上海",
        event_time="1923年8月5日",
    )
    docs = [scene, meeting]
    if later:
        docs.append(
            node(
                "LYA-1",
                title="后来送书",
                summary="后来又见面，毛泽东亲手把一本诗集交给我。",
                quote="后来又见面，他亲手把一本诗集交给我。",
                people="核心人物：毛泽东",
                direct_self=1,
                speaker="茅盾",
                nature="茅盾直接自述",
                book="《我走过的道路》",
                place="延安",
                event_time="1936年",
            )
        )
    return docs


def _story():
    return "我第一次见到毛泽东，是在上海。后来在广州的寓所，有一晚他默然沉思，我不敢打扰，也默坐相陪。"


def _story_history():
    return [{"question": "你怎么看待毛泽东", "spoken": _story(), "usable": True, "mode": "generated"}]


def _covered_history():
    """上一轮把同住的上下楼也说了，记录里就没有还没讲的句子。"""

    spoken = _story() + "毛泽东夫妇住楼上，肖楚女和我住楼下。"
    return [{"question": "你怎么看待毛泽东", "spoken": spoken, "usable": True, "mode": "generated"}]


def _user_json(model, purpose="answer"):
    return json.loads(model.sent(purpose))


def _conversation_blob(payload: dict) -> str:
    parts = []
    for key, value in payload.items():
        if key.startswith("先前的对话") or "近期问答" in key or "更早内容" in key or "找回的较早原文" in key:
            parts.append(json.dumps(value, ensure_ascii=False))
    assert parts, "生成请求里应有会话上下文"
    return "\n".join(parts)


def test_carry_modes_follow_what_this_turn_is_asking():
    previous = _story()
    assert carry_mode("你怎么看待毛泽东", "", "毛泽东") == ""
    assert carry_mode("那你对他这个人是什么印象", previous, "毛泽东") == "rephrase"
    assert carry_mode("那你认为他是一个什么样的人", previous, "毛泽东") == "rephrase"
    assert carry_mode("你认为他是谁", previous, "毛泽东") == ""
    assert carry_mode("那你觉得你的家庭是幸福的吗", "父亲看重新学，母亲操持家里。", "", "你自己的家庭是什么样子的", aim="feeling") == ""
    assert carry_mode("你为什么这样评价他", previous, "毛泽东", kinds={"explain"}) == "explain"
    assert carry_mode("还有别的事情吗", previous, "毛泽东") == "more"
    assert carry_mode("我听说他为人很严厉，你怎么看", previous, "毛泽东") == "challenge"
    assert carry_mode("那晚楼上住的是谁", previous, "毛泽东") == "detail"
    assert carry_mode("你把这件事再讲一遍", previous, "毛泽东") == "retell"
    assert carry_mode("你的原名是什么", previous, "毛泽东") == "fact"
    assert carry_mode("那之后呢", previous, "毛泽东") == ""
    assert carry_mode("你第一次见到他是什么时候", previous, "毛泽东") == ""
    assert carry_mode("我听说他很严厉，你怎么看", "", "毛泽东") == ""


def test_a_rephrased_impression_does_not_retell_the_same_story():
    model = FakeModel(reading=reading(("人物介绍", "毛泽东", "还有什么印象"), person="毛泽东"))
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(), [], []),
        "那你对他这个人是什么印象？",
        model=model,
        history=_covered_history(),
        focus={"person": "毛泽东"},
    )
    assert result["spoken_text"] == "印象就是刚才说的那样。"
    assert "默坐相陪" not in result["spoken_text"] and "寓所" not in result["spoken_text"]
    assert "answer" not in model.purposes()
    continuity = audit["trace"]["continuity"]
    assert continuity["mode"] == "rephrase"
    assert continuity["previous_chars"] > 0 and continuity["previous_in_request"] is False
    assert "默坐相陪" in continuity["told_anchors"]


def test_a_rephrased_person_question_drops_the_retold_opening_and_keeps_the_new_point():
    model = FakeModel(
        reading=reading(("人物介绍", "毛泽东", "他是什么样的人"), person="毛泽东"),
        answer="我第一次见到毛泽东，是在上海。后来在广州的寓所，有一晚他默然沉思，我不敢打扰，也默坐相陪。后来他又把一本诗集交给我。",
    )
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(later=True), [], []),
        "那你认为他是一个什么样的人？",
        model=model,
        history=_story_history(),
        focus={"person": "毛泽东"},
    )
    assert "诗集" in result["spoken_text"]
    assert "默坐相陪" not in result["spoken_text"] and "第一次见到" not in result["spoken_text"]
    pointed = FakeModel(
        reading=reading(("人物介绍", "毛泽东", "他是什么样的人"), person="毛泽东"),
        answer="有一晚他默然沉思，我不敢打扰，也默坐相陪。这是我当时对鲁迅说的看法。",
    )
    pointed_result, _audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(), [], []),
        "那你认为他是一个什么样的人？",
        model=pointed,
        history=[{"question": "你怎么看待毛泽东", "spoken": "有一晚他默然沉思，我不敢打扰，也默坐相陪。鲁迅问起时，我说他博闻强记。", "usable": True, "mode": "generated"}],
        focus={"person": "毛泽东"},
    )
    assert pointed_result["spoken_text"] == "印象就是刚才说的那样。"
    assert "默坐相陪" not in pointed_result["spoken_text"]
    payload = _user_json(model)
    materials = json.dumps(payload["可用材料"], ensure_ascii=False)
    assert "默坐相陪" not in materials and "诗集" in materials
    assert audit["trace"]["continuity"]["mode"] == "rephrase"
    assert audit["trace"]["continuity"]["previous_in_request"] is True
    assert audit["trace"]["reading"]["angle"] == "impression"


def test_already_spoken_wording_is_recognized_when_a_date_is_inserted():
    previous = "我头一回见到他，是在一九二三年上海地委的一次会议上，他代表中央来出席。后来在广州的寓所，有一晚他默然沉思，我不敢打扰，也默坐相陪。"
    assert sentence_already_told("我头一回见到他，是一九二三年八月五日上海地委第六次会议，他代表中央来出席指导。", previous)
    assert sentence_already_told("有一晚他默然沉思，显然是在考虑问题，我不敢打扰，也默坐相陪。", previous)
    assert not sentence_already_told("后来他又把一本诗集交给我。", previous)


def test_asking_for_something_else_uses_a_new_scene_and_keeps_the_old_answer_in_the_request():
    model = FakeModel(
        reading=reading(("其他史实", "毛泽东", "还有别的事"), person="毛泽东"),
        answer="后来又见面，他亲手把一本诗集交给我。",
    )
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(later=True), [], []),
        "还有别的事情吗？",
        model=model,
        history=_story_history(),
        focus={"person": "毛泽东"},
    )
    assert "诗集" in result["spoken_text"]
    assert "默坐相陪" not in result["spoken_text"]
    payload = _user_json(model)
    prior = _conversation_blob(payload)
    materials = json.dumps(payload["可用材料"], ensure_ascii=False)
    assert "默坐相陪" in prior
    assert "默坐相陪" not in materials and "诗集" in materials
    assert "另外的事" in json.dumps(payload["这一问怎样答"], ensure_ascii=False)
    assert audit["trace"]["continuity"]["previous_in_request"] is True
    assert model.purposes() == ["understand", "answer"]


def test_a_repeated_story_is_dropped_when_there_is_nothing_new_to_add():
    model = FakeModel(reading=reading(("其他史实", "毛泽东", "还有别的事"), person="毛泽东"))
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(), [], []),
        "还有别的事情吗？",
        model=model,
        history=_covered_history(),
        focus={"person": "毛泽东"},
    )
    assert result["spoken_text"] == "此外没有别的可以补的。"
    assert "默坐相陪" not in result["spoken_text"]
    assert "answer" not in model.purposes()
    assert audit["mode"] == "continue"
    assert audit["trace"]["continuity"]["previous_in_request"] is False


def test_a_different_view_closes_without_replaying_the_story():
    model = FakeModel(reading=reading(("人物介绍", "毛泽东", "怎么看这个说法"), person="毛泽东"), answer="不该再讲一遍。")
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(), [], []),
        "我听说他为人很严厉，你怎么看？",
        model=model,
        history=_story_history(),
        focus={"person": "毛泽东"},
    )
    assert result["spoken_text"] == DISPUTE_HOLD
    assert "默坐相陪" not in result["spoken_text"] and "寓所" not in result["spoken_text"] and "严厉" not in result["spoken_text"]
    assert model.purposes() == ["understand"]
    assert audit["trace"]["continuity"]["mode"] == "challenge"


def test_a_detail_keeps_the_previous_answer_and_does_not_restart_the_story():
    model = FakeModel(
        reading=reading(("其他史实", "", "楼上是谁")),
        answer="毛泽东夫妇住楼上。",
    )
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(), [], []),
        "那晚楼上住的是谁？",
        model=model,
        history=_story_history(),
        focus={"person": "毛泽东"},
    )
    assert result["spoken_text"] == "毛泽东夫妇住楼上。"
    assert "第一次见到" not in result["spoken_text"]
    payload = _user_json(model)
    prior = _conversation_blob(payload)
    materials = json.dumps(payload["可用材料"], ensure_ascii=False)
    assert "默坐相陪" in prior and "默坐相陪" in materials and "第一次见到" not in materials
    assert "只补对方问的那一点" in json.dumps(payload["这一问怎样答"], ensure_ascii=False)
    assert audit["trace"]["continuity"]["previous_in_request"] is True


def test_a_new_session_does_not_inherit_another_sessions_just_now():
    model = FakeModel(
        reading=reading(("人物介绍", "毛泽东", "印象"), person="毛泽东"),
        answer="刚才谈到同住寓所，他默然沉思，我不敢打扰，也默坐相陪。",
        rewrite="有一晚他默然沉思，我不敢打扰，也默坐相陪。",
    )
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(), [], []),
        "你怎么看待毛泽东？",
        model=model,
        history=[],
        focus={"person": "毛泽东", "told": ["ZSI-1", "LPC-1"], "hits": ["ZSI-1"], "used": ["ZSI-1"]},
    )
    assert result["spoken_text"].startswith("有一晚")
    assert "刚才" not in result["spoken_text"]
    assert "印象就是刚才" not in result["spoken_text"]
    first = model.calls[1][1][-1]["content"]
    assert "先前的对话" not in first
    assert audit["trace"]["continuity"]["mode"] == "first"
    assert "rewrite" in model.purposes()


def test_a_basic_fact_can_stay_the_same_and_an_explicit_retell_may_repeat():
    index = KnowledgeIndex(_stay_nodes(), [], [basics("B01", "姓名", "原名沈德鸿。")])
    fact_model = FakeModel(reading=reading(("本人生平", "", "原名")), answer="我原名沈德鸿。")
    fact, _audit, _focus = ask(index, "你的原名是什么", model=fact_model, history=_story_history(), focus={"person": "毛泽东"})
    assert fact["spoken_text"] == "我原名沈德鸿。"
    retell_model = FakeModel(
        reading=reading(("人物介绍", "毛泽东", "再讲一遍"), person="毛泽东"),
        answer="上海那次是第一次见到毛泽东。广州寓所里，他默然沉思，我不敢打扰，也默坐相陪。",
    )
    retold, audit, _focus = ask(index, "你把这件事再讲一遍", model=retell_model, history=_story_history(), focus={"person": "毛泽东"})
    assert "默坐相陪" in retold["spoken_text"]
    assert audit["trace"]["continuity"]["mode"] == "retell"
    assert audit["trace"]["continuity"]["previous_in_request"] is True


def test_asking_for_more_is_not_turned_into_small_talk():
    model = FakeModel(reading=reading(("日常", "", "还有没有别的")), answer="我们来谈一部作品吧。")
    result, audit, _focus = ask(
        KnowledgeIndex(_stay_nodes(), [], []),
        "还有别的事情吗？",
        model=model,
        history=_covered_history(),
        focus={"person": "毛泽东"},
    )
    assert result["spoken_text"] == "此外没有别的可以补的。"
    assert "作品" not in result["spoken_text"]
    assert "answer" not in model.purposes()
    assert audit["trace"]["continuity"]["mode"] == "more"


def test_an_explicit_request_to_repeat_a_basic_fact_may_say_it_again():
    index = KnowledgeIndex([], [], [basics("B01", "姓名", "原名沈德鸿。")])
    model = FakeModel(reading=reading(("本人生平", "", "再讲一遍原名")), answer="我原名沈德鸿。")
    history = [{"question": "你的原名是什么", "spoken": "我原名沈德鸿。", "usable": True, "mode": "generated"}]
    result, audit, _focus = ask(index, "你的原名再讲一遍", model=model, history=history)
    assert result["spoken_text"] == "我原名沈德鸿。"
    assert audit["trace"]["continuity"]["mode"] == "fact"
    assert "rewrite" not in model.purposes()


def test_family_aim_and_prior_judgment_are_read_without_another_model_call():
    assert answer_aim("那你自己的家庭是什么样子的？", {"self_fact"}) == "overview"
    assert answer_aim("那你觉得你的家庭是幸福的吗？", {"self_fact"}) == "feeling"
    assert answer_aim("你家都有谁？", {"self_fact"}) == "members"
    turns = [
        {
            "question": "你怎么看待毛泽东",
            "spoken": "一九三一年秋在鲁迅家里，我说他是共产党里的大学问家，博闻强记，谈笑风生。",
            "usable": True,
        }
    ]
    judged = prior_judgment(turns, "你为什么这样评价他", "毛泽东")
    assert "大学问家" in judged["phrase"] and "博闻强记" in judged["search"]
    assert "鲁迅" in judged["sentence"]

    misread = json.dumps(reading(("追问缘由", "上一句回答", "为什么这么说"), person="毛泽东"), ensure_ascii=False)
    explained = understand(
        "我的意思就是你为什么这么评价他？",
        turns,
        {"person": "毛泽东"},
        lambda messages, **options: misread,
    )
    assert [part["type"] for part in explained["parts"]] == ["explain"]
    assert explained["premise"] == ""
    assert "大学问家" in (explained.get("judgment") or {}).get("phrase", "")


def test_a_family_overview_does_not_start_from_the_courtesy_name_card():
    index = KnowledgeIndex(
        [
            node(
                "FAM-1",
                title="母亲照管家里",
                summary="幼年丧父后，家里的事多由母亲照管；孩子们幼年也常由母亲看着。",
                quote="家里的事多由母亲照管。",
                people="母亲",
                direct_self=1,
                speaker="茅盾",
                nature="茅盾直接自述",
                book="《我走过的道路》",
            )
        ],
        [],
        [basics("B01", "姓名", "原名沈德鸿，字雁冰。")],
    )
    model = FakeModel(
        reading=reading(("本人生平", "", "想知道家里是什么样子"), search=("家庭", "母亲")),
        answer="我幼年丧父，家里的事多由母亲照管。孩子们小时候，也常是母亲看着。",
    )
    result, audit, _focus = ask(index, "那你自己的家庭是什么样子的？", model=model)
    assert not result["spoken_text"].startswith("我原名")
    assert "母亲" in result["spoken_text"]
    assert audit["trace"]["reading"]["aim"] == "overview"
    materials = model.sent()
    assert "原名沈德鸿" not in materials


def test_explaining_a_judgment_keeps_the_matching_record_and_does_not_collapse():
    index = KnowledgeIndex(
        [
            node(
                "LFE-1",
                title="鲁迅问起毛泽东",
                summary="一九三一年秋在鲁迅家里，鲁迅问起毛泽东和朱德；茅盾说毛泽东是共产党里的大学问家，博闻强记，谈笑风生。",
                quote="毛泽东是共产党里的大学问家，博闻强记，谈笑风生。",
                people="核心人物：毛泽东",
                direct_self=1,
                speaker="茅盾",
                nature="茅盾直接自述",
                book="《我走过的道路》",
            ),
            node(
                "LPC-2",
                title="掌握枪杆子",
                summary="会议依毛泽东建议作出决议；茅盾由此说他早年就注意掌握枪杆子。",
                quote="由此可见毛泽东早年就注意共产党掌握枪杆子的问题了。",
                people="毛泽东",
                direct_self=1,
                speaker="茅盾",
                nature="茅盾直接自述",
                book="《我走过的道路》",
            ),
        ],
        [],
        [],
    )
    history = [
        {
            "question": "你怎么看待毛泽东",
            "spoken": "一九三一年秋在鲁迅家里，我说他是共产党里的大学问家，博闻强记，谈笑风生。",
            "usable": True,
            "mode": "generated",
        }
    ]
    model = FakeModel(
        reading=reading(("其他史实", "上一句回答", "为什么这样评价"), person="毛泽东"),
        answer="这些是经历和当时说过的话，不是评价。枪杆子那件事另说。",
        rewrite="鲁迅问起时，我因五卅以前就相识、又在广州共过事，便说他是共产党里的大学问家，博闻强记，谈笑风生。",
    )
    result, audit, _focus = ask(index, "你为什么这样评价他？", model=model, history=history, focus={"person": "毛泽东"})
    assert "大学问家" in result["spoken_text"] or "博闻强记" in result["spoken_text"]
    assert "不是评价" not in result["spoken_text"]
    assert "刚才说的就是这些" not in result["spoken_text"]
    assert "枪杆子" not in result["spoken_text"]
    assert audit["trace"]["continuity"]["mode"] == "explain"
    materials = model.sent()
    assert "大学问家" in materials and "枪杆子" not in materials


def test_ordinary_life_stays_in_the_first_person_and_today_questions_are_not_sent_to_the_archive():
    def kinds(text, **options):
        return {item["kind"] for item in inspect_output(text, **options)[1]}

    assert "研究者口吻" in kinds("这只能说明添过一次书，不能推成我一向如此。")
    assert "研究者口吻" in kinds("孩子回忆说，我那时给他添过一本书。")
    assert "研究者口吻" in kinds("没有可靠的直接表述，我不能替自己补出当时的心情。")
    assert "研究者口吻" not in kinds("我原名沈德鸿。")
    assert "研究者口吻" not in kinds("这一层，不在这里展开。")
    assert "研究者口吻" not in kinds("这只能说明添过一次书。", sources_ok=True)

    misread = json.dumps(reading(("其他史实", "", "家长该怎么办")), ensure_ascii=False)
    today = understand("现在的孩子不肯好好吃饭，做家长的该怎么办？", [], {}, lambda messages, **options: misread)
    assert [part["type"] for part in today["parts"]] == ["advice"]
    past = understand("你父母对读书是什么态度？", [], {}, lambda messages, **options: misread)
    assert [part["type"] for part in past["parts"]] == ["other"]


def test_an_explicit_dispute_question_is_not_retold():
    model = FakeModel(answer="不该写到这里。")
    result, audit, _focus = ask(_mao_index(), "农民运动那些过火的说法，你怎么看？", model=model)
    assert result["spoken_text"] == DISPUTE_HOLD
    assert audit["mode"] == "boundary"
    assert audit["trace"]["final_source"] == "fixed"
    assert model.purposes() == []
    assert result["model_called"] is False
