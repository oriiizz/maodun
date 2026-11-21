"""语音识别、语音合成和播放队列。

没有配置地址时状态是“未接入”，不会返回一段假的语音。
播放队列保证同一会话里旧一轮的声音不会和新一轮叠在一起。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.contracts import AudioSegment, ModuleStatus, Transcript
from app.speech.correction import correct_transcript


class SpeechNotConnected(Exception):
    def __init__(self, notice: str):
        super().__init__(notice)
        self.notice = notice


class NotConnectedRecognizer:
    provider_id = "none"

    def status(self) -> ModuleStatus:
        return ModuleStatus("asr", self.provider_id, "not_connected", "语音识别服务未配置")

    def transcribe(self, audio: bytes, *, mime: str, session_id: str, turn_id: str, lexicon: set[str]) -> Transcript:
        del audio, mime, session_id, turn_id, lexicon
        raise SpeechNotConnected("语音识别服务未配置")


class HttpRecognizer:
    """把音频交给外部识别服务。返回文字后，再做有上下文的纠错。"""

    provider_id = "http"

    def __init__(self, url: str, client: httpx.Client):
        self.url = url
        self.client = client

    def status(self) -> ModuleStatus:
        return ModuleStatus("asr", self.provider_id, "ready", "已配置外部语音识别地址。")

    def transcribe(self, audio: bytes, *, mime: str, session_id: str, turn_id: str, lexicon: set[str]) -> Transcript:
        if not audio:
            raise SpeechNotConnected("没有收到音频数据。")
        try:
            response = self.client.post(self.url, content=audio, headers={"Content-Type": mime or "application/octet-stream"})
        except httpx.TimeoutException as exc:
            raise SpeechNotConnected("语音识别超时。请再说一次，或改用文字提问。") from exc
        except httpx.HTTPError as exc:
            raise SpeechNotConnected("连接语音识别服务失败。") from exc
        if response.status_code >= 400:
            raise SpeechNotConnected(f"语音识别服务返回错误（状态码 {response.status_code}）。")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpeechNotConnected("语音识别服务返回的内容无法解析。") from exc
        raw = str(payload.get("text") or payload.get("result") or "").strip()
        if not raw:
            raise SpeechNotConnected("语音识别没有返回文字。")
        fixed = correct_transcript(raw, lexicon)
        return Transcript(
            raw_text=str(fixed["raw_text"]),
            corrected_text=str(fixed["corrected_text"]),
            changes=list(fixed["changes"]),  # type: ignore[arg-type]
            provider=self.provider_id,
            session_id=session_id,
            turn_id=turn_id,
        )


class BaiduRealtimeRecognizer:
    """实时识别由 WebSocket 接口处理；保留统一状态接口。"""

    provider_id = "baidu_realtime"

    def __init__(self, configured: bool):
        self.configured = configured

    def status(self) -> ModuleStatus:
        if self.configured:
            return ModuleStatus("asr", self.provider_id, "ready", "百度实时语音识别已配置。")
        return ModuleStatus(
            "asr",
            self.provider_id,
            "not_connected",
            "已选择百度实时语音识别，但 BAIDU_ASR_APP_ID 或 BAIDU_ASR_API_KEY 未填完整。",
        )

    def transcribe(self, audio: bytes, *, mime: str, session_id: str, turn_id: str, lexicon: set[str]) -> Transcript:
        del audio, mime, session_id, turn_id, lexicon
        raise SpeechNotConnected("百度实时识别只接受浏览器 WebSocket 音频流。")


class NotConnectedSynthesizer:
    provider_id = "none"

    def status(self) -> ModuleStatus:
        return ModuleStatus("tts", self.provider_id, "not_connected", "语音合成尚未接入。当前只显示文字，没有语音播放。")

    def synthesize(self, text: str, *, utterance_id: str, turn_id: str, session_id: str, epoch: int) -> AudioSegment:
        del text, utterance_id, turn_id, session_id, epoch
        raise SpeechNotConnected("语音合成尚未接入。当前只显示文字，没有语音播放。")


class HttpSynthesizer:
    """把正文交给外部合成服务，例如另一台电脑上的 CosyVoice 或 GPT-SoVITS。"""

    provider_id = "http"

    def __init__(self, url: str, client: httpx.Client):
        self.url = url
        self.client = client

    def status(self) -> ModuleStatus:
        return ModuleStatus("tts", self.provider_id, "ready", "已配置外部语音合成地址。声音克隆尚未在本机运行。")

    def synthesize(self, text: str, *, utterance_id: str, turn_id: str, session_id: str, epoch: int) -> AudioSegment:
        if not text.strip():
            raise SpeechNotConnected("没有需要朗读的正文。")
        try:
            response = self.client.post(self.url, json={"text": text, "utterance_id": utterance_id})
        except httpx.TimeoutException as exc:
            raise SpeechNotConnected("语音合成超时。这一句没有加入播放队列。") from exc
        except httpx.HTTPError as exc:
            raise SpeechNotConnected("连接语音合成服务失败。") from exc
        if response.status_code >= 400:
            raise SpeechNotConnected(f"语音合成服务返回错误（状态码 {response.status_code}）。")
        mime = response.headers.get("content-type", "audio/wav").split(";")[0]
        if not response.content:
            raise SpeechNotConnected("语音合成服务没有返回音频。")
        return AudioSegment(
            utterance_id=utterance_id,
            turn_id=turn_id,
            session_id=session_id,
            epoch=epoch,
            text=text,
            audio=response.content,
            mime=mime or "audio/wav",
        )


def build_recognizer(settings: Settings, client: httpx.Client):
    if settings.asr_provider == "baidu_realtime":
        return BaiduRealtimeRecognizer(settings.baidu_realtime_asr_configured)
    if settings.asr_provider != "none" and settings.asr_base_url:
        return HttpRecognizer(settings.asr_base_url, client)
    return NotConnectedRecognizer()


def build_synthesizer(settings: Settings, client: httpx.Client):
    if settings.tts_provider == "dashscope_cosyvoice":
        from app.speech.dashscope_tts import DashScopeCosyVoiceSynthesizer

        return DashScopeCosyVoiceSynthesizer(settings, client)
    if settings.tts_provider != "none" and settings.tts_base_url:
        return HttpSynthesizer(settings.tts_base_url, client)
    return NotConnectedSynthesizer()


@dataclass
class PlaybackItem:
    session_id: str
    turn_id: str
    epoch: int
    utterance_id: str
    text: str


@dataclass
class PlaybackQueue:
    """同一时间只播放一段。取消或新一轮会丢掉旧的排队音频。"""

    epochs: dict[str, int] = field(default_factory=dict)
    playing: dict[str, PlaybackItem | None] = field(default_factory=dict)
    pending: dict[str, list[PlaybackItem]] = field(default_factory=dict)

    def note_epoch(self, session_id: str, epoch: int) -> None:
        self.epochs[session_id] = epoch
        self.pending[session_id] = [item for item in self.pending.get(session_id, []) if item.epoch == epoch]
        current = self.playing.get(session_id)
        if current is not None and current.epoch != epoch:
            self.playing[session_id] = None

    def enqueue(self, item: PlaybackItem) -> str:
        current_epoch = self.epochs.get(item.session_id)
        if current_epoch is not None and item.epoch != current_epoch:
            return "discarded"
        if self.playing.get(item.session_id) is not None:
            self.pending.setdefault(item.session_id, []).append(item)
            return "queued"
        self.playing[item.session_id] = item
        return "playing"

    def finish_current(self, session_id: str) -> str:
        self.playing[session_id] = None
        while self.pending.get(session_id):
            nxt = self.pending[session_id].pop(0)
            if self.epochs.get(session_id) not in (None, nxt.epoch):
                continue
            self.playing[session_id] = nxt
            return "playing"
        return "idle"
