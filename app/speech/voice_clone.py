"""阿里云百炼声音复刻。创建、查询和复用都走业务空间的 HTTP 接口。"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.config import Settings

CLONE_MODEL = "voice-enrollment"
TARGET_MODEL = "cosyvoice-v3-flash"
VOICE_PREFIX = "maodun"
LANGUAGE_HINTS = ["zh"]
MAX_PROMPT_AUDIO_LENGTH = 20.0
POLL_TIMEOUT_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 5.0


class VoiceCloneError(Exception):
    def __init__(
        self,
        summary: str,
        *,
        status_code: int = 0,
        code: str = "",
        message: str = "",
        request_id: str = "",
    ):
        super().__init__(summary)
        self.summary = summary
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id

    @property
    def model_not_enabled(self) -> bool:
        return self.code == "AccessDenied.Unpurchased"


def probe_model(settings: Settings, client: httpx.Client, model: str = TARGET_MODEL) -> None:
    """用系统音色确认目标模型已开通。失败时不创建复刻音色。"""

    _post_json(
        client,
        settings.dashscope_tts_url,
        settings,
        {
            "model": model,
            "input": {
                "text": "模型权限检查。",
                "voice": "longanyang",
                "format": "wav",
                "sample_rate": 24000,
            },
        },
    )


def create_voice(settings: Settings, client: httpx.Client, wav: bytes, model: str = TARGET_MODEL) -> dict[str, Any]:
    audio = "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")
    return _post_json(
        client,
        settings.dashscope_voice_url,
        settings,
        {
            "model": CLONE_MODEL,
            "input": {
                "action": "create_voice",
                "target_model": model,
                "prefix": VOICE_PREFIX,
                "audio": audio,
                "language_hints": LANGUAGE_HINTS,
                "enable_preprocess": True,
                "max_prompt_audio_length": MAX_PROMPT_AUDIO_LENGTH,
            },
        },
    )


def list_voices(settings: Settings, client: httpx.Client) -> list[dict[str, Any]]:
    payload = _post_json(
        client,
        settings.dashscope_voice_url,
        settings,
        {
            "model": CLONE_MODEL,
            "input": {
                "action": "list_voice",
                "prefix": VOICE_PREFIX,
                "page_index": 0,
                "page_size": 10,
            },
        },
    )
    voices = payload.get("output", {}).get("voice_list", [])
    return [item for item in voices if isinstance(item, dict)]


def query_voice(settings: Settings, client: httpx.Client, voice_id: str) -> dict[str, Any]:
    return _post_json(
        client,
        settings.dashscope_voice_url,
        settings,
        {"model": CLONE_MODEL, "input": {"action": "query_voice", "voice_id": voice_id}},
    )


def find_reusable_voice(settings: Settings, client: httpx.Client, model: str, record: dict[str, Any] | None) -> str:
    """返回可继续等待或使用的音色。没有已有音色时返回空字符串。"""

    recorded_id = str((record or {}).get("voice_id") or "")
    recorded_model = str((record or {}).get("target_model") or "")
    if recorded_id and recorded_model == model:
        try:
            detail = query_voice(settings, client, recorded_id)
        except VoiceCloneError as exc:
            if exc.status_code in {401, 403} or exc.model_not_enabled:
                raise
        else:
            status = str(detail.get("output", {}).get("status") or "")
            if status in {"OK", "DEPLOYING"}:
                return recorded_id
            if status == "UNDEPLOYED":
                raise VoiceCloneError(
                    "已有复刻音色审核未通过。本次不会自动再次创建。",
                    request_id=str(detail.get("request_id") or ""),
                )
    for item in list_voices(settings, client):
        voice_id = str(item.get("voice_id") or "")
        status = str(item.get("status") or "")
        if not voice_id or status not in {"OK", "DEPLOYING"}:
            continue
        detail = query_voice(settings, client, voice_id)
        if str(detail.get("output", {}).get("target_model") or "") == model:
            return voice_id
    return ""


def wait_until_ok(
    settings: Settings,
    client: httpx.Client,
    voice_id: str,
    *,
    timeout: float = POLL_TIMEOUT_SECONDS,
    interval: float = POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    deadline = now() + timeout
    last_status = ""
    while True:
        payload = query_voice(settings, client, voice_id)
        status = str(payload.get("output", {}).get("status") or "")
        last_status = status
        if status == "OK":
            return payload
        if status == "UNDEPLOYED":
            raise VoiceCloneError(
                "复刻音色审核未通过，不能用于朗读。",
                request_id=str(payload.get("request_id") or ""),
            )
        if status and status != "DEPLOYING":
            raise VoiceCloneError(
                f"复刻音色状态为 {status}，不能用于朗读。",
                request_id=str(payload.get("request_id") or ""),
            )
        if now() >= deadline:
            raise VoiceCloneError(f"等待复刻音色超时，最后状态为 {last_status or '未知'}。")
        sleep(interval)


def synthesize_preview(settings: Settings, client: httpx.Client, model: str, voice_id: str, text: str) -> bytes:
    from app.speech.dashscope_tts import normalize_wav_sizes

    payload = _post_json(
        client,
        settings.dashscope_tts_url,
        settings,
        {
            "model": model,
            "input": {"text": text, "voice": voice_id, "format": "wav", "sample_rate": 24000},
        },
    )
    audio_url = str(payload.get("output", {}).get("audio", {}).get("url") or "")
    if not audio_url.startswith(("http://", "https://")):
        raise VoiceCloneError("试听合成没有返回音频地址。", request_id=str(payload.get("request_id") or ""))
    try:
        response = client.get(audio_url, timeout=settings.dashscope_tts_timeout_seconds, follow_redirects=True)
    except httpx.TimeoutException as exc:
        raise VoiceCloneError("下载试听音频超时。") from exc
    except httpx.HTTPError as exc:
        raise VoiceCloneError("下载试听音频失败。") from exc
    if response.status_code >= 400 or not response.content:
        raise VoiceCloneError(f"下载试听音频失败（状态码 {response.status_code}）。")
    return normalize_wav_sizes(response.content)


def load_voice_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_voice_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def update_env_values(path: Path, updates: dict[str, str]) -> None:
    """只替换指定配置项，保留密钥和其他配置。"""

    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if stripped and not stripped.startswith("#") and "=" in stripped else ""
        if key in updates:
            updated.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in updates.items():
        if key not in seen:
            updated.append(f"{key}={value}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _post_json(client: httpx.Client, url: str, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=settings.dashscope_tts_timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        raise VoiceCloneError("连接阿里云语音接口超时。") from exc
    except httpx.HTTPError as exc:
        raise VoiceCloneError("无法连接阿里云语音接口。") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if response.status_code >= 400:
        raise _error_from_response(response.status_code, body)
    return body


def _error_from_response(status_code: int, payload: dict[str, Any]) -> VoiceCloneError:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(payload.get("code") or error.get("code") or "")
    message = str(payload.get("message") or error.get("message") or "")
    request_id = str(payload.get("request_id") or error.get("request_id") or "")
    if code == "AccessDenied.Unpurchased":
        summary = "目标语音模型尚未开通。"
    else:
        summary = "阿里云声音复刻请求失败。"
    return VoiceCloneError(
        summary,
        status_code=status_code,
        code=code,
        message=message,
        request_id=request_id,
    )
