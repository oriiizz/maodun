"""百度实时语音识别 WebSocket 转发。

浏览器只连接本项目；百度 AppID 和 API Key 只在后端 START 帧中使用。
err_no=0 的 FIN_TEXT 才进入最终句子。-3005 只表示当前片段没有有效语音，
不清除已经确认的句子；鉴权和参数错误仍然结束整轮。
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import ssl
import uuid
from contextlib import suppress
from typing import Any, Callable

import certifi
from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect as websocket_connect

from app.config import Settings
from app.speech.correction import correct_transcript

logger = logging.getLogger(__name__)

PCM_FRAME_BYTES = 5120
MAX_PCM_FRAME_BYTES = 6400
# 官方说明这些错误只影响当前一句，连接应继续。
CONTINUABLE_ERRORS = {-3003, -3005, -3013}
# 片段时间戳和前后句子允许 200ms 的边界重叠，避免把句尾切分误判成中途丢字。
BOUNDARY_MS = 200


class RealtimeAsrError(Exception):
    pass


class RecognitionTurn:
    """分开保存临时文字、已确认句子，以及不能自动提交的片段。"""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.sentences: list[dict[str, Any]] = []
        self.interim = ""
        self.no_speech: list[dict[str, Any]] = []
        self.faults: list[dict[str, Any]] = []
        self.seq = 0
        self.ended = ""
        self.fatal_message = ""

    def observe(self, payload: dict[str, Any], *, finish_requested: bool) -> str:
        message_type = str(payload.get("type") or "")
        if message_type == "HEARTBEAT":
            return "ignore"
        self.seq += 1
        error_number = _error_number(payload.get("err_no"))
        if error_number == -3005:
            segment = _segment(payload, self.request_id, self.seq, finish_requested)
            self.no_speech.append(segment)
            logger.info(
                "百度识别片段无有效语音 sn=%s start=%s end=%s after_finish=%s err=%s",
                segment["sn"],
                segment["start_time"],
                segment["end_time"],
                segment["after_finish"],
                segment["err_msg"],
            )
            return "no_speech"
        if error_number in CONTINUABLE_ERRORS:
            self.faults.append(_segment(payload, self.request_id, self.seq, finish_requested))
            return "fault"
        if error_number != 0:
            self.ended = "fatal"
            self.fatal_message = _baidu_error_message(error_number, str(payload.get("err_msg") or ""))
            return "fatal"
        result = _result_text(payload.get("result"))
        if message_type == "MID_TEXT":
            self.interim = result
            return "interim"
        if message_type == "FIN_TEXT":
            self.interim = ""
            if result:
                self.sentences.append(
                    {
                        "text": result,
                        "start_time": _optional_int(payload.get("start_time")),
                        "end_time": _optional_int(payload.get("end_time")),
                        "sn": _request_label(payload, self.request_id),
                        "log_id": payload.get("log_id"),
                        "seq": self.seq,
                    }
                )
            return "final"
        return "ignore"

    def final_text(self) -> str:
        return "".join(str(item["text"]) for item in self.sentences)


def build_start_message(settings: Settings, *, cuid: str | None = None) -> dict[str, Any]:
    return {
        "type": "START",
        "data": {
            "appid": int(settings.baidu_asr_app_id),
            "appkey": settings.baidu_asr_api_key,
            "dev_pid": 15372,
            "cuid": cuid or _device_id(),
            "format": "pcm",
            "sample": 16000,
        },
    }


def _finish_wait_seconds(settings: Settings) -> float:
    return max(5.0, min(settings.request_timeout_seconds, 30.0))


async def relay_baidu_realtime(
    browser: WebSocket,
    settings: Settings,
    lexicon: set[str],
    *,
    connector: Callable[..., Any] = websocket_connect,
) -> None:
    await browser.accept()
    if not settings.baidu_realtime_asr_configured:
        await _send_json(
            browser,
            {
                "type": "ERROR",
                "message": "百度实时语音识别未配置。请填写 BAIDU_ASR_APP_ID 和 BAIDU_ASR_API_KEY。",
            },
        )
        await browser.close(code=1008)
        return

    request_id = str(uuid.uuid4())
    separator = "&" if "?" in settings.baidu_asr_ws_url else "?"
    url = f"{settings.baidu_asr_ws_url}{separator}sn={request_id}"
    finish_requested = asyncio.Event()
    cancel_requested = asyncio.Event()
    audio_stats = {"bytes": 0, "frames": 0, "peak": 0}
    turn = RecognitionTurn(request_id)
    notified = False
    sender: asyncio.Task[str] | None = None
    receiver: asyncio.Task[None] | None = None

    async def notify(payload: dict[str, Any]) -> None:
        nonlocal notified
        if notified:
            return
        notified = True
        await _send_json(browser, payload)

    try:
        async with connector(
            url,
            open_timeout=min(settings.request_timeout_seconds, 15),
            close_timeout=3,
            max_size=1024 * 1024,
            proxy=None,
            ssl=ssl.create_default_context(cafile=certifi.where()),
        ) as baidu:
            await baidu.send(json.dumps(build_start_message(settings), ensure_ascii=False))
            await _send_json(browser, {"type": "READY"})
            sender = asyncio.create_task(
                _browser_to_baidu(browser, baidu, finish_requested, cancel_requested, audio_stats)
            )
            receiver = asyncio.create_task(
                _baidu_to_browser(baidu, browser, turn, finish_requested, cancel_requested)
            )
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)

            if sender in done and not sender.cancelled():
                action = sender.result()
                if action == "finish":
                    try:
                        await asyncio.wait_for(receiver, timeout=_finish_wait_seconds(settings))
                    except TimeoutError:
                        receiver.cancel()
                        with suppress(asyncio.CancelledError):
                            await receiver
                        await notify(build_outcome(turn, lexicon, audio_stats, ended="timeout"))
                        return
                else:
                    receiver.cancel()
                    with suppress(asyncio.CancelledError):
                        await receiver
                    if action == "cancel":
                        await notify({"type": "CANCELLED"})
                    return
            elif not receiver.done():
                await receiver

            if sender is not None and not sender.done():
                with suppress(Exception):
                    await baidu.send(json.dumps({"type": "CANCEL"}))
                sender.cancel()
                with suppress(asyncio.CancelledError):
                    await sender
            if turn.ended == "cancelled" or cancel_requested.is_set():
                await notify({"type": "CANCELLED"})
                return
            if turn.ended == "fatal":
                await notify({"type": "ERROR", "message": turn.fatal_message})
                return
            ended = turn.ended if turn.ended in {"interrupted", "finished"} else "interrupted"
            await notify(build_outcome(turn, lexicon, audio_stats, ended=ended))
    except WebSocketDisconnect:
        return
    except RealtimeAsrError as exc:
        await notify({"type": "ERROR", "message": str(exc)})
    except TimeoutError:
        await notify({"type": "ERROR", "message": "连接百度语音识别超时，请检查网络后重试。"})
    except OSError:
        await notify({"type": "ERROR", "message": "无法连接百度语音识别，请检查网络后重试。"})
    except Exception:
        if not notified:
            await notify({"type": "ERROR", "message": "百度实时语音识别连接异常，请稍后重试。"})
    finally:
        for task in (sender, receiver):
            if task is not None and not task.done():
                task.cancel()
        for task in (sender, receiver):
            if task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await task


async def _browser_to_baidu(
    browser: WebSocket,
    baidu: Any,
    finish_requested: asyncio.Event,
    cancel_requested: asyncio.Event,
    audio_stats: dict[str, int],
) -> str:
    while True:
        packet = await browser.receive()
        if packet["type"] == "websocket.disconnect":
            with suppress(Exception):
                await baidu.send(json.dumps({"type": "CANCEL"}))
            return "disconnect"
        audio = packet.get("bytes")
        if audio is not None:
            if not audio:
                continue
            if len(audio) % 2 or len(audio) > MAX_PCM_FRAME_BYTES:
                raise RealtimeAsrError("浏览器发送的 PCM 音频帧格式不正确。")
            audio_stats["bytes"] += len(audio)
            audio_stats["frames"] += 1
            audio_stats["peak"] = max(audio_stats["peak"], _pcm_peak(audio))
            await baidu.send(audio)
            continue
        text = packet.get("text")
        if not text:
            continue
        try:
            command = json.loads(text)
        except json.JSONDecodeError:
            continue
        message_type = str(command.get("type") or "").upper()
        if message_type == "FINISH":
            finish_requested.set()
            await baidu.send(json.dumps({"type": "FINISH"}))
            return "finish"
        if message_type == "CANCEL":
            cancel_requested.set()
            await baidu.send(json.dumps({"type": "CANCEL"}))
            return "cancel"


async def _baidu_to_browser(
    baidu: Any,
    browser: WebSocket,
    turn: RecognitionTurn,
    finish_requested: asyncio.Event,
    cancel_requested: asyncio.Event,
) -> None:
    try:
        async for raw in baidu:
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            kind = turn.observe(payload, finish_requested=finish_requested.is_set())
            final_text = turn.final_text()
            if kind == "fatal":
                return
            if kind == "interim":
                await _send_json(
                    browser,
                    {"type": "MID_TEXT", "text": turn.interim, "final_text": final_text},
                )
            elif kind == "final":
                added = ""
                if turn.sentences and turn.sentences[-1]["seq"] == turn.seq:
                    added = str(turn.sentences[-1]["text"])
                await _send_json(
                    browser,
                    {"type": "FIN_TEXT", "text": added, "final_text": final_text},
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("百度识别下行中断 sn=%s", turn.request_id, exc_info=True)
        turn.ended = "cancelled" if cancel_requested.is_set() else "interrupted"
        return
    if cancel_requested.is_set():
        turn.ended = "cancelled"
    else:
        turn.ended = "finished" if finish_requested.is_set() else "interrupted"


def build_outcome(
    turn: RecognitionTurn,
    lexicon: set[str],
    audio_stats: dict[str, int],
    *,
    ended: str,
) -> dict[str, Any]:
    """决定这轮是正常提交、留给用户修改，还是直接报错。"""
    segments = [_annotated_segment(item, turn.sentences) for item in turn.no_speech]
    for segment in segments:
        logger.info(
            "百度识别无有效语音片段归类 sn=%s stage=%s start=%s end=%s after_finish=%s",
            segment["sn"],
            segment["stage"],
            segment["start_time"],
            segment["end_time"],
            segment["after_finish"],
        )
    raw_text = turn.final_text().strip()
    fixed = (
        correct_transcript(raw_text, lexicon)
        if raw_text
        else {"raw_text": "", "corrected_text": "", "changes": []}
    )
    question = str(fixed["corrected_text"] or fixed["raw_text"]).strip()
    holes = [item for item in segments if item["stage"] == "mid"]
    if ended == "timeout" and not question:
        return {"type": "ERROR", "message": "发送结束标记后等待识别结果超时，请重新说一次。"}
    if ended == "interrupted" and not question:
        return {"type": "ERROR", "message": "百度语音识别连接意外中断，请检查网络后重试。"}
    if turn.faults and not question:
        fault = turn.faults[0]
        return {
            "type": "ERROR",
            "message": _baidu_error_message(int(fault["err_no"]), str(fault["err_msg"])),
        }
    if not question:
        reason = "no_speech"
        submit = False
    elif ended == "timeout":
        reason = "timeout"
        submit = False
    elif ended == "interrupted":
        reason = "interrupted"
        submit = False
    elif holes or turn.faults:
        reason = "incomplete"
        submit = False
    else:
        reason = "ok"
        submit = True
    return {
        "type": "COMPLETE",
        "raw_text": fixed["raw_text"],
        "corrected_text": fixed["corrected_text"],
        "changes": fixed["changes"],
        "submit": submit,
        "reason": reason,
        "no_speech_segments": segments,
        "audio_bytes": audio_stats["bytes"],
        "audio_frames": audio_stats["frames"],
        "audio_peak": audio_stats["peak"],
    }


def no_speech_stage(segment: dict[str, Any], sentences: list[dict[str, Any]]) -> str:
    """判断无有效语音片段是开头、结束尾巴，还是夹在已识别句子中间。"""
    start = segment.get("start_time")
    end = segment.get("end_time")
    timed = [
        item
        for item in sentences
        if item.get("start_time") is not None and item.get("end_time") is not None
    ]
    if start is not None and end is not None and sentences and len(timed) == len(sentences):
        first_start = min(int(item["start_time"]) for item in timed)
        last_end = max(int(item["end_time"]) for item in timed)
        if int(end) <= first_start + BOUNDARY_MS:
            return "leading"
        if int(start) + BOUNDARY_MS >= last_end:
            return "end"
        return "mid"
    earlier = [item for item in sentences if int(item["seq"]) < int(segment["seq"])]
    later = [item for item in sentences if int(item["seq"]) > int(segment["seq"])]
    if not earlier:
        return "leading"
    if not later and segment.get("after_finish"):
        return "end"
    return "mid"


def _device_id() -> str:
    name = socket.gethostname() or "maodun"
    return f"maodun-{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex}"


def _result_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(str(item) for item in value).strip()
    return ""


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _request_label(payload: dict[str, Any], request_id: str) -> str:
    label = payload.get("sn")
    if label is None or label == "":
        return request_id
    return str(label)


def _segment(payload: dict[str, Any], request_id: str, seq: int, after_finish: bool) -> dict[str, Any]:
    return {
        "err_no": _error_number(payload.get("err_no")),
        "err_msg": str(payload.get("err_msg") or ""),
        "start_time": _optional_int(payload.get("start_time")),
        "end_time": _optional_int(payload.get("end_time")),
        "sn": _request_label(payload, request_id),
        "log_id": payload.get("log_id"),
        "after_finish": after_finish,
        "seq": seq,
    }


def _annotated_segment(segment: dict[str, Any], sentences: list[dict[str, Any]]) -> dict[str, Any]:
    noted = dict(segment)
    noted["stage"] = no_speech_stage(segment, sentences)
    return noted


def _error_number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return -1


def _pcm_peak(audio: bytes) -> int:
    peak = 0
    for offset in range(0, len(audio), 2):
        sample = int.from_bytes(audio[offset : offset + 2], "little", signed=True)
        peak = max(peak, abs(sample))
    return peak


def _baidu_error_message(error_number: int, message: str) -> str:
    lowered = message.lower()
    if error_number == -3004 or "auth" in lowered or "appkey" in lowered:
        return "百度语音识别鉴权失败，请检查 BAIDU_ASR_APP_ID 和 BAIDU_ASR_API_KEY。"
    if error_number == -3008:
        return "百度语音识别参数错误，请检查开始帧配置。"
    detail = message.strip() or "未知错误"
    return f"百度语音识别错误（{error_number}）：{detail}"


async def _send_json(browser: WebSocket, payload: dict[str, Any]) -> None:
    with suppress(RuntimeError, WebSocketDisconnect):
        await browser.send_json(payload)
