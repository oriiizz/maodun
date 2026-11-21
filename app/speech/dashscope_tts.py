"""阿里云百炼 CosyVoice 非实时语音合成。"""

from __future__ import annotations

import html
import re
import struct
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.contracts import AudioSegment, ModuleStatus
from app.speech.interfaces import SpeechNotConnected


class DashScopeCosyVoiceSynthesizer:
    provider_id = "dashscope_cosyvoice"

    def __init__(self, settings: Settings, client: httpx.Client):
        self.settings = settings
        self.client = client

    def status(self) -> ModuleStatus:
        if self.settings.dashscope_tts_configured:
            if self.settings.dashscope_tts_voice_source == "clone":
                message = (
                    "阿里云百炼语音合成已配置（复刻音色）："
                    f"{self.settings.dashscope_clone_voice_id} / {self.settings.dashscope_tts_sample_rate}Hz。"
                    f"优先使用 {self.settings.dashscope_clone_model}；"
                    f"该模型未开通时改用 {self.settings.dashscope_tts_model}，音色保持不变。"
                )
            else:
                message = (
                    "阿里云百炼语音合成已配置（系统音色）："
                    f"{self.settings.dashscope_tts_model} / {self.settings.dashscope_tts_voice} / "
                    f"{self.settings.dashscope_tts_sample_rate}Hz。"
                )
            return ModuleStatus("tts", self.provider_id, "ready", message)
        if self.settings.dashscope_tts_voice_source == "clone" and not self.settings.dashscope_clone_voice_id:
            message = "已选择复刻音色，但还没有可用的 DASHSCOPE_CLONE_VOICE_ID。"
        else:
            message = "已选择阿里云百炼语音合成，但 API Key、业务空间 ID、API Host 或音色未填完整。"
        return ModuleStatus("tts", self.provider_id, "not_connected", message)

    def synthesize(
        self,
        text: str,
        *,
        utterance_id: str,
        turn_id: str,
        session_id: str,
        epoch: int,
    ) -> AudioSegment:
        spoken = prepare_tts_text(text)
        if not spoken:
            raise SpeechNotConnected("回答中没有适合朗读的正文。")
        if not self.settings.dashscope_tts_configured:
            raise SpeechNotConnected("阿里云百炼语音合成配置未填写完整。")

        response = self._request_synthesis(spoken)
        try:
            payload = response.json()
            audio_url = str(payload["output"]["audio"]["url"]).strip()
        except (ValueError, KeyError, TypeError) as exc:
            raise SpeechNotConnected("阿里云语音合成返回内容缺少音频地址。") from exc
        if not _is_download_url(audio_url):
            raise SpeechNotConnected("阿里云语音合成返回了无效的音频地址。")

        try:
            audio_response = self.client.get(
                audio_url,
                timeout=self.settings.dashscope_tts_timeout_seconds,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise SpeechNotConnected("下载阿里云合成音频超时，请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise SpeechNotConnected("无法下载阿里云合成音频。") from exc
        if audio_response.status_code >= 400:
            raise SpeechNotConnected(f"下载合成音频失败（状态码 {audio_response.status_code}）。")
        if not audio_response.content:
            raise SpeechNotConnected("阿里云语音合成没有返回音频数据。")

        mime = audio_response.headers.get("content-type", "").split(";")[0].strip()
        if not mime.startswith("audio/"):
            mime = _format_mime(self.settings.dashscope_tts_format)
        return AudioSegment(
            utterance_id=utterance_id,
            turn_id=turn_id,
            session_id=session_id,
            epoch=epoch,
            text=spoken,
            audio=normalize_wav_sizes(audio_response.content),
            mime=mime,
        )

    def _request_synthesis(self, spoken: str) -> httpx.Response:
        models = [self.settings.dashscope_active_model]
        fallback = self.settings.dashscope_tts_model
        if (
            self.settings.dashscope_tts_voice_source == "clone"
            and fallback
            and fallback not in models
        ):
            models.append(fallback)
        response: httpx.Response | None = None
        for index, model in enumerate(models):
            try:
                response = self.client.post(
                    self.settings.dashscope_tts_url,
                    headers={
                        "Authorization": f"Bearer {self.settings.dashscope_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "input": {
                            "text": spoken,
                            "voice": self.settings.dashscope_active_voice,
                            "format": self.settings.dashscope_tts_format,
                            "sample_rate": self.settings.dashscope_tts_sample_rate,
                        },
                    },
                    timeout=self.settings.dashscope_tts_timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise SpeechNotConnected("阿里云语音合成超时，请稍后重试。") from exc
            except httpx.HTTPError as exc:
                raise SpeechNotConnected("无法连接阿里云语音合成服务。") from exc
            if response.status_code < 400:
                return response
            if index + 1 < len(models) and _is_unpurchased(response):
                continue
            raise SpeechNotConnected(_service_error(response))
        raise SpeechNotConnected("阿里云语音合成没有返回结果。")


def normalize_wav_sizes(audio: bytes) -> bytes:
    """百炼返回的 WAV 可能把数据长度写成占位最大值，播放器会因此显示错误时长。"""

    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    fixed = bytearray(audio)
    declared_riff = struct.unpack_from("<I", fixed, 4)[0]
    actual_riff = len(fixed) - 8
    if declared_riff > actual_riff:
        struct.pack_into("<I", fixed, 4, actual_riff)
    data_at = fixed.find(b"data", 12)
    if data_at < 0 or data_at + 8 > len(fixed):
        return bytes(fixed)
    declared_data = struct.unpack_from("<I", fixed, data_at + 4)[0]
    actual_data = len(fixed) - (data_at + 8)
    if declared_data > actual_data:
        struct.pack_into("<I", fixed, data_at + 4, actual_data)
    return bytes(fixed)


def prepare_tts_text(text: str) -> str:
    """只保留回答正文，去掉不适合朗读的 Markdown 表记。"""

    cleaned = html.unescape(text or "")
    cleaned = re.sub(r"```[\s\S]*?```", " ", cleaned)
    cleaned = re.sub(r"!\[[^\]]*]\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*]|\d+[.)])\s*", "", cleaned)
    cleaned = re.sub(r"[*_~`]+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*[-:| ]{3,}\s*$", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _is_unpurchased(response: httpx.Response) -> bool:
    code, _message, _request_id = _error_fields(response)
    return code == "AccessDenied.Unpurchased"


def _service_error(response: httpx.Response) -> str:
    code, message, request_id = _error_fields(response)
    if code == "AccessDenied.Unpurchased":
        return (
            f"阿里云模型未开通（{code}）：{message or '没有使用该模型的权限'}。"
            f"请求 ID：{request_id or '无'}。请在当前业务空间开通对应语音模型。"
        )
    if response.status_code in {401, 403}:
        detail = f"（{code}）{message}" if code or message else ""
        return f"阿里云语音合成鉴权失败{detail}。请求 ID：{request_id or '无'}。"
    detail = f"（{code}）{message}" if code or message else ""
    return f"阿里云语音合成返回错误（状态码 {response.status_code}）{detail}。请求 ID：{request_id or '无'}。"


def _error_fields(response: httpx.Response) -> tuple[str, str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "", "", ""
    if not isinstance(payload, dict):
        return "", "", ""
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(payload.get("code") or error.get("code") or "").strip()
    message = str(payload.get("message") or error.get("message") or "").strip()
    request_id = str(payload.get("request_id") or error.get("request_id") or "").strip()
    return code, message, request_id


def _is_download_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _format_mime(value: str) -> str:
    return {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "pcm": "audio/L16",
        "opus": "audio/opus",
    }.get(value.lower(), "application/octet-stream")
