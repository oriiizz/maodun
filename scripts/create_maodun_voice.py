"""把 md.wma 转成参考 WAV，创建或复用茅盾复刻音色，并生成试听。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.speech.voice_clone import (
    MAX_PROMPT_AUDIO_LENGTH,
    POLL_TIMEOUT_SECONDS,
    TARGET_MODEL,
    VoiceCloneError,
    create_voice,
    find_reusable_voice,
    load_voice_record,
    probe_model,
    save_voice_record,
    synthesize_preview,
    update_env_values,
    wait_until_ok,
)

SOURCE = ROOT / "md.wma"
VOICE_DIR = ROOT / "data" / "voices"
REFERENCE = VOICE_DIR / "md-24k.wav"
RECORD = VOICE_DIR / "maodun.json"
PREVIEW = VOICE_DIR / "maodun-preview.wav"
PREVIEW_TEXT = "欢迎你来。你平时喜欢读些什么？我们可以慢慢聊。"


def main() -> int:
    if not SOURCE.is_file():
        print(f"没有找到参考录音：{SOURCE}", file=sys.stderr)
        return 1
    tools = ROOT / ".tools"
    os.environ["PATH"] = f"{tools}{os.pathsep}{os.environ.get('PATH', '')}"
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("没有找到 ffmpeg 或 ffprobe。", file=sys.stderr)
        return 1

    settings = load_settings()
    if not settings.dashscope_api_key or not settings.dashscope_workspace_id:
        print("阿里云 API Key 或业务空间 ID 未配置。", file=sys.stderr)
        return 1

    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    _convert(ffmpeg, SOURCE, REFERENCE)
    report = _analyze(ffmpeg, ffprobe, REFERENCE)
    _print_report(report)
    if report["duration"] < 3 or report["speech_seconds"] < 3:
        print("有效人声不足 3 秒，不能提交声音复刻。原始 WMA 已保留。", file=sys.stderr)
        return 1

    try:
        with httpx.Client(timeout=settings.dashscope_tts_timeout_seconds) as client:
            print(f"正在确认模型 {TARGET_MODEL} 可以调用。")
            probe_model(settings, client, TARGET_MODEL)
            record = load_voice_record(RECORD)
            voice_id = find_reusable_voice(settings, client, TARGET_MODEL, record)
            if voice_id:
                print(f"复用已有音色：{voice_id}")
            else:
                print("没有可复用音色，正在创建。")
                created = create_voice(settings, client, REFERENCE.read_bytes(), TARGET_MODEL)
                voice_id = str(created.get("output", {}).get("voice_id") or "")
                if not voice_id:
                    raise VoiceCloneError("创建音色没有返回 voice_id。", request_id=str(created.get("request_id") or ""))
                print(f"已创建音色：{voice_id}")
            ready = wait_until_ok(settings, client, voice_id, timeout=POLL_TIMEOUT_SECONDS)
            target_model = str(ready.get("output", {}).get("target_model") or TARGET_MODEL)
            save_voice_record(
                RECORD,
                {
                    "voice_id": voice_id,
                    "target_model": target_model,
                    "status": "OK",
                    "prefix": "maodun",
                    "reference_wav": str(REFERENCE.relative_to(ROOT)),
                    "request_id": str(ready.get("request_id") or ""),
                },
            )
            print("正在合成试听。")
            preview = synthesize_preview(settings, client, target_model, voice_id, PREVIEW_TEXT)
            PREVIEW.write_bytes(preview)
    except VoiceCloneError as exc:
        _print_api_error(exc)
        return 2 if exc.model_not_enabled else 1

    update_env_values(
        ROOT / ".env",
        {
            "DASHSCOPE_TTS_VOICE_SOURCE": "clone",
            "DASHSCOPE_CLONE_MODEL": target_model,
            "DASHSCOPE_CLONE_VOICE_ID": voice_id,
        },
    )
    with wave.open(str(PREVIEW), "rb") as audio:
        seconds = audio.getnframes() / audio.getframerate()
        print(
            f"试听文件：{PREVIEW}，{audio.getnchannels()} 声道，"
            f"{audio.getframerate()} Hz，{audio.getsampwidth() * 8} bit，{seconds:.2f} 秒"
        )
    print(f"音色 ID：{voice_id}")
    print(f"绑定模型：{target_model}")
    print("已写入 .env 的复刻配置。请重启服务后再提问。")
    return 0


def _convert(ffmpeg: str, source: Path, target: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _analyze(ffmpeg: str, ffprobe: str, target: Path) -> dict[str, float]:
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,bits_per_sample,duration",
            "-of",
            "default=nw=1:nk=1",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    sample_rate, channels, bits, duration = probe.stdout.splitlines()[:4]
    volume = subprocess.run(
        [ffmpeg, "-i", str(target), "-af", "volumedetect", "-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    silence = subprocess.run(
        [ffmpeg, "-i", str(target), "-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    mean = _decibel(volume.stderr, "mean_volume")
    peak = _decibel(volume.stderr, "max_volume")
    silent = sum(float(item) for item in re.findall(r"silence_duration: ([0-9.]+)", silence.stderr))
    return {
        "sample_rate": float(sample_rate),
        "channels": float(channels),
        "bits": float(bits),
        "duration": float(duration),
        "mean_volume": mean,
        "peak_volume": peak,
        "speech_seconds": max(0.0, float(duration) - silent),
    }


def _print_report(report: dict[str, float]) -> None:
    print(
        "参考 WAV："
        f"{int(report['channels'])} 声道，{int(report['sample_rate'])} Hz，{int(report['bits'])} bit，"
        f"时长 {report['duration']:.2f} 秒。"
    )
    print(
        f"音量均值 {report['mean_volume']:.1f} dB，峰值 {report['peak_volume']:.1f} dB，"
        f"估计有效人声 {report['speech_seconds']:.2f} 秒。"
    )
    print("24 kHz 只统一接口要求的采样率，不能改善原始录音里的杂音或方言。预处理会尝试降噪。")
    if report["duration"] > MAX_PROMPT_AUDIO_LENGTH:
        print(f"录音长于 {MAX_PROMPT_AUDIO_LENGTH:.0f} 秒，复刻只使用预处理后的前 {MAX_PROMPT_AUDIO_LENGTH:.0f} 秒。")


def _print_api_error(exc: VoiceCloneError) -> None:
    print(exc.summary, file=sys.stderr)
    print(f"code: {exc.code or '无'}", file=sys.stderr)
    print(f"message: {exc.message or '无'}", file=sys.stderr)
    print(f"request_id: {exc.request_id or '无'}", file=sys.stderr)
    if exc.model_not_enabled:
        print(
            f"需要在业务空间开通 {TARGET_MODEL} 的声音复刻和语音合成权限。当前系统音色配置未改动。",
            file=sys.stderr,
        )


def _decibel(log: str, name: str) -> float:
    match = re.search(rf"{name}: (-?[0-9.]+) dB", log)
    return float(match.group(1)) if match else 0.0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        print(detail[-1] if detail else "FFmpeg 处理参考录音失败。", file=sys.stderr)
        raise SystemExit(1) from exc
