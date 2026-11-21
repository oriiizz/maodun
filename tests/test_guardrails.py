import json

import httpx

from app.llm.errors import ModelCallError
from app.llm.providers import DeepSeekChatModel
from app.speech.correction import correct_transcript
from app.speech.interfaces import PlaybackItem, PlaybackQueue


def test_correction_does_not_replace_every_maodun_homophone():
    lexicon = {"子夜", "茅盾"}
    assert correct_transcript("他的论述自相矛盾。", lexicon)["corrected_text"] == "他的论述自相矛盾。"
    assert correct_transcript("文章前后矛盾。", lexicon)["corrected_text"] == "文章前后矛盾。"
    assert correct_transcript("矛盾", lexicon)["corrected_text"] == "矛盾"
    fixed = correct_transcript("矛盾先生写过《子叶》。", lexicon)
    assert fixed["corrected_text"] == "茅盾先生写过《子夜》。"
    assert fixed["raw_text"] == "矛盾先生写过《子叶》。"
    assert correct_transcript("地上有一片子叶。", lexicon)["corrected_text"] == "地上有一片子叶。"
    assert correct_transcript("请谈谈职业。", lexicon)["corrected_text"] == "请谈谈子夜。"
    assert correct_transcript("你怎么看待职业这篇文章", lexicon)["corrected_text"] == "你怎么看待子夜这篇文章"
    assert correct_transcript("你怎么看待职业这篇文章？", lexicon)["corrected_text"] == "你怎么看待子夜这篇文章？"
    assert correct_transcript("你怎么看待职业规划", lexicon)["corrected_text"] == "你怎么看待职业规划"
    assert correct_transcript("《职业》", lexicon)["corrected_text"] == "《子夜》"
    assert correct_transcript("职业这部小说的主人公是谁", lexicon)["corrected_text"] == "子夜这部小说的主人公是谁"
    assert correct_transcript("职业是怎么写的", lexicon)["corrected_text"] == "子夜是怎么写的"
    assert correct_transcript("请谈谈子叶", lexicon)["corrected_text"] == "请谈谈子夜"
    assert correct_transcript("你的职业是什么", lexicon)["corrected_text"] == "你的职业是什么"
    assert correct_transcript("茅盾的职业是作家", lexicon)["corrected_text"] == "茅盾的职业是作家"
    assert correct_transcript("当时大家很关心职业选择", lexicon)["corrected_text"] == "当时大家很关心职业选择"
    assert correct_transcript("社会上的职业很多", lexicon)["corrected_text"] == "社会上的职业很多"
    assert correct_transcript("您为什么写职业", lexicon)["corrected_text"] == "您为什么写子夜"
    assert correct_transcript("请谈谈茅盾的职业", lexicon)["corrected_text"] == "请谈谈茅盾的子夜"
    assert correct_transcript("职业的作者是谁", lexicon)["corrected_text"] == "子夜的作者是谁"
    assert correct_transcript("我想了解一下职业", lexicon)["corrected_text"] == "我想了解一下子夜"
    assert correct_transcript("职业和春蚕有什么关系", lexicon)["corrected_text"] == "子夜和春蚕有什么关系"
    assert correct_transcript("以写作为职业", lexicon)["corrected_text"] == "以写作为职业"
    assert correct_transcript("您的职业是什么", lexicon)["corrected_text"] == "您的职业是什么"


def test_stale_turn_is_discarded(runtime):
    first = runtime.sessions.begin_turn("", "第一个问题")
    second = runtime.sessions.begin_turn(first["session_id"], "第二个问题")
    kept = runtime.sessions.finish_turn(first["session_id"], first["turn_id"], first["epoch"], {"spoken_text": "迟到"})
    assert kept is False
    assert second["epoch"] > first["epoch"]
    row = runtime.db.query_one("SELECT status FROM turns WHERE id = ?", (first["turn_id"],))
    assert row["status"] == "discarded"


def test_playback_does_not_overlap_and_drops_old_epoch():
    queue = PlaybackQueue()
    queue.note_epoch("s", 2)
    assert queue.enqueue(PlaybackItem("s", "t1", 1, "u1", "旧的")) == "discarded"
    assert queue.enqueue(PlaybackItem("s", "t2", 2, "u2", "第一句")) == "playing"
    assert queue.enqueue(PlaybackItem("s", "t2", 2, "u3", "第二句")) == "queued"
    assert queue.playing["s"].utterance_id == "u2"
    assert queue.finish_current("s") == "playing"
    assert queue.playing["s"].utterance_id == "u3"


def test_text_answers_call_deepseek_not_qianfan(settings):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "沈雁冰。"}}]})

    model = DeepSeekChatModel(
        settings.__class__(**{**settings.__dict__, "deepseek_api_key": "test-key"}),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert model.complete([{"role": "user", "content": "原名是什么"}]) == "沈雁冰。"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["body"]["model"] == "deepseek-flash"
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "qianfan" not in captured["url"]


def test_deepseek_reports_auth_balance_and_timeout(settings):
    def timeout(_request):
        raise httpx.TimeoutException("slow")

    model = DeepSeekChatModel(
        settings.__class__(**{**settings.__dict__, "deepseek_api_key": "test-key"}),
        httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    try:
        model.complete([{"role": "user", "content": "你好"}])
        raise AssertionError("超时应该被说明")
    except ModelCallError as exc:
        assert "超时" in exc.notice

    def denied(_request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    denied_model = DeepSeekChatModel(
        settings.__class__(**{**settings.__dict__, "deepseek_api_key": "test-key"}),
        httpx.Client(transport=httpx.MockTransport(denied)),
    )
    try:
        denied_model.complete([{"role": "user", "content": "你好"}])
        raise AssertionError("拒绝密钥应该被说明")
    except ModelCallError as exc:
        assert "密钥" in exc.notice

    def broke(_request):
        return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})

    broke_model = DeepSeekChatModel(
        settings.__class__(**{**settings.__dict__, "deepseek_api_key": "test-key"}),
        httpx.Client(transport=httpx.MockTransport(broke)),
    )
    try:
        broke_model.complete([{"role": "user", "content": "你好"}])
        raise AssertionError("余额不足应该被说明")
    except ModelCallError as exc:
        assert "余额" in exc.notice


def test_retired_provider_name_cannot_leave_the_local_corpus(runtime):
    runtime.settings = runtime.settings.__class__(**{**runtime.settings.__dict__, "qa_provider": "qianfan_app"})
    result = runtime.ask("茅盾的原名是什么", None)
    assert result["provider"] == "local_corpus"
    assert result["evidence"] == []
    assert "千帆" not in result["spoken_text"]
