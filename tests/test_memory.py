"""整段会话记忆：保存全量、按预算组织、找回较早原文、纠正与删除状态。"""

from __future__ import annotations

from app.knowledge.compose import build_messages, conversation_messages_view
from app.knowledge.understand import prior_judgment
from app.session.memory import (
    RECENT_LIMIT,
    SUMMARY_AFTER,
    build_context,
    conversation_view,
    recall_turns,
    refresh_segments,
    turns_from_rows,
    update_after_answer,
)


def _turn(order: int, question: str, spoken: str, *, usable: bool = True) -> dict:
    return {
        "id": f"t{order:03d}",
        "question": question,
        "spoken": spoken,
        "usable": usable,
        "mode": "generated" if usable else "model_error",
        "created_at": f"2026-11-22T00:{order:02d}:00",
    }


def test_turns_from_rows_keep_failed_answers_out_of_usable():
    rows = [
        {
            "id": "a",
            "question": "你是谁",
            "result_json": '{"spoken_text":"我是茅盾。"}',
            "mode": "generated",
            "status": "done",
            "created_at": "1",
        },
        {
            "id": "b",
            "question": "再问一次",
            "result_json": '{"spoken_text":"这一次没有答成。"}',
            "mode": "model_error",
            "status": "done",
            "created_at": "2",
        },
    ]
    turns = turns_from_rows(rows)
    assert turns[0]["usable"] is True
    assert turns[1]["usable"] is False


def test_short_session_keeps_full_recent_without_summary():
    turns = [_turn(i, f"问{i}", f"答{i}") for i in range(5)]
    bundle = build_context(turns, "接着问", {})
    assert len(bundle["working"]) == 5
    assert bundle["pack"]["earlier_summary"] == []
    assert bundle["pack"]["meta"]["total_turns"] == 5


def test_long_session_summarizes_older_and_recalls_early_topic():
    turns = []
    for i in range(40):
        if i == 1:
            turns.append(_turn(i, "我母亲常在群里发招聘消息，我该怎么办", "先把家里的情况听清楚，再决定怎么回。"))
        elif i == 12:
            turns.append(_turn(i, "鲁迅是怎样的人", "博闻强记，谈笑风生。"))
        elif i == 20:
            turns.append(_turn(i, "《子夜》主要写什么", "写的是三十年代上海工商业的矛盾。"))
        else:
            turns.append(_turn(i, f"中间话题{i}", f"中间回答{i}关于别处的事。"))
    assert len(turns) >= SUMMARY_AFTER
    bundle = build_context(turns, "我前面说母亲发消息那件事呢", {"person": "鲁迅"})
    pack = bundle["pack"]
    assert pack["meta"]["total_turns"] == 40
    assert pack["earlier_summary"], "长会话应有较早摘要"
    topics = [topic for segment in pack["earlier_summary"] for topic in segment.get("topics") or []]
    assert any("家庭" in topic or "母亲" in topic or topic == "家庭" for topic in topics) or any(
        "母亲" in (segment.get("text") or "") for segment in pack["earlier_summary"]
    )
    recalled_text = " ".join(item.get("问", "") + item.get("答", "") for item in pack["recalled"])
    assert "母亲" in recalled_text and "招聘" in recalled_text
    # 近期窗口外的内容仍可通过找回进入 working
    working_questions = [turn["question"] for turn in bundle["working"]]
    assert any("母亲" in question for question in working_questions)
    assert len(bundle["working"]) <= RECENT_LIMIT + 4


def test_correction_is_kept_and_preferred_in_view():
    turns = [_turn(i, f"问{i}", f"答{i}") for i in range(10)]
    focus = {"memory": {"segments": refresh_segments(turns[:-RECENT_LIMIT], [])}}
    bundle = build_context(turns, "不是这个意思，我说的是沈雁冰的原名", focus)
    assert any("不是这个意思" in item for item in bundle["pack"]["corrections"])
    view = conversation_view(bundle["pack"])
    assert "用户后来的澄清（以澄清后的意思为准）" in view


def test_update_after_answer_refreshes_segments_without_extra_model():
    turns = [_turn(i, f"问{i}", f"答{i}") for i in range(9)]
    focus = update_after_answer({}, turns, question="再问一句", spoken="新的回答。", mode="generated")
    assert focus["memory"]["segments"]
    assert focus["memory"]["total_turns"] == 10


def test_conversation_messages_enter_build_messages():
    pack = {
        "recent": [{"问": "家里怎样", "答": "母亲很体贴。"}],
        "earlier_summary": [{"from": "t1", "to": "t4", "count": 4, "topics": ["家庭"], "text": "问母亲→答体贴"}],
        "recalled": [{"问": "母亲发消息", "答": "先听清楚。"}],
        "corrections": ["我说的是家里的事"],
    }
    view = conversation_messages_view(pack)
    messages = build_messages("规则", "风格", "那件事后来怎样了", asks=["接着先前的家事"], conversation=view)
    body = messages[-1]["content"]
    assert "近期问答" in body
    assert "更早内容摘要" in body
    assert "本轮找回的较早原文" in body
    assert "用户后来的澄清" in body


def test_recall_does_not_mix_unrelated_topics():
    older = [
        _turn(0, "母亲发招聘消息", "先听清楚家里的情况。"),
        _turn(1, "《子夜》写什么", "写上海工商业。"),
        _turn(2, "鲁迅印象", "博闻强记。"),
    ]
    picked = recall_turns(older, "前面母亲发消息那件事", {}, limit=2)
    assert len(picked) == 1
    assert "母亲" in picked[0]["question"]


def test_user_earlier_topic_becomes_advice_not_history_search():
    from app.knowledge.understand import understand

    turns = [
        _turn(0, "我母亲常在群里发招聘消息，我很烦，你怎么看", "可以私下跟她说清楚。"),
        _turn(1, "《子夜》主要写什么", "写都市经济关系。"),
    ]
    bundle = build_context(turns, "我前面说母亲发消息那件事呢", {})
    reading = understand("我前面说母亲发消息那件事呢", bundle["working"], bundle["focus"], call=None, memory=bundle["pack"])
    assert reading["parts"][0]["type"] == "advice"
    assert reading.get("resume_user", {}).get("question", "").startswith("我母亲")


def test_attitude_confirm_marks_aim_without_denying_judgment():
    from app.knowledge.understand import understand

    turns = [
        _turn(0, "鲁迅是怎样的人", "我说他较真到底，对自己认作诬蔑的东西不肯轻轻放过。"),
    ]
    reading = understand("所以你是赞成他的吗", turns, {"person": "鲁迅"}, call=None)
    assert reading.get("aim") == "attitude"
    assert reading["parts"][0]["type"] in {"explain", "press"}
    judgment = reading.get("judgment") or prior_judgment(turns, "所以你是赞成他的吗", "鲁迅")
    assert "较真" in (judgment.get("phrase") or "") or "较真" in (judgment.get("sentence") or "")


def test_delete_session_clears_focus_memory_with_row(tmp_path):
    """删除会话时连 focus_json（含 memory）一起删掉；不另建聊天表。"""

    from app.database import Database
    from app.knowledge.store import ensure_schema
    from app.session.manager import SessionManager

    db = Database(tmp_path / "memory.db")
    ensure_schema(db)
    sessions = SessionManager(db)
    created = sessions.create("visitor-memory-test")
    session_id = created["id"]
    db.execute(
        "UPDATE sessions SET focus_json = ? WHERE id = ?",
        ('{"person":"鲁迅","memory":{"segments":[{"from":"a","to":"b"}],"total_turns":12}}', session_id),
    )
    assert sessions.delete(session_id, "visitor-memory-test") is True
    assert db.query_one("SELECT id FROM sessions WHERE id = ?", (session_id,)) is None
    db.close()
