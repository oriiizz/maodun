"""人物画面事件。2D 和 UE 都收同一份事件，嘴型要以音频进度为准，不能假设文字会自动对上口型。"""

from __future__ import annotations

from app.contracts import AnswerResult, AvatarEvent, ModuleStatus


def avatar_status(provider: str) -> ModuleStatus:
    if provider and provider != "none":
        return ModuleStatus("avatar", provider, "not_connected", "已填写展示模块名称，但本版还没有接通具体的 2D 或 UE 程序。")
    return ModuleStatus("avatar", "none", "not_connected", "未接入")


def events_for_answer(result: AnswerResult) -> list[AvatarEvent]:
    """先把事件排好。人物程序未接入时，这些事件只在页面上说明，不会真的驱动形象。"""
    base = dict(
        session_id=result.session_id,
        turn_id=result.turn_id,
        epoch=result.epoch,
    )
    return [
        AvatarEvent(
            segment_id=f"{result.turn_id}-caption",
            event_type="caption",
            payload={"text": result.spoken_text, "read_aloud": bool(result.spoken_text)},
            **base,
        ),
        AvatarEvent(
            segment_id=f"{result.turn_id}-gesture",
            event_type="gesture",
            payload={"mood": result.control.mood, "gesture": result.control.gesture},
            **base,
        ),
        AvatarEvent(
            segment_id=f"{result.turn_id}-audio",
            event_type="audio",
            payload={
                "status": "未接入",
                "format_to_confirm": "接入 UE 或 2D 时需要确认插件接受的音频格式，例如 wav 或 pcm。不要假定只发文字就会生成嘴型。",
            },
            **base,
        ),
    ]
