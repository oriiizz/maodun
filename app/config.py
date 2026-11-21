"""读取本机配置。密钥只来自环境变量或 .env，不会写进代码。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """把 .env 读进环境变量。已经存在的变量优先，避免盖住真正的部署配置。"""
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    root: Path
    database_path: Path
    originals_dir: Path
    persona_path: Path
    lexicon_path: Path
    request_timeout_seconds: float
    retriever: str
    qa_provider: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    asr_provider: str
    asr_base_url: str
    baidu_asr_app_id: str
    baidu_asr_api_key: str
    baidu_asr_ws_url: str
    tts_provider: str
    tts_base_url: str
    dashscope_api_key: str
    dashscope_workspace_id: str
    dashscope_api_host: str
    dashscope_tts_model: str
    dashscope_tts_voice: str
    dashscope_tts_format: str
    dashscope_tts_sample_rate: int
    dashscope_tts_timeout_seconds: float
    dashscope_tts_voice_source: str
    dashscope_clone_model: str
    dashscope_clone_voice_id: str
    avatar_provider: str

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key and self.deepseek_base_url and self.deepseek_model)

    @property
    def baidu_realtime_asr_configured(self) -> bool:
        return bool(
            self.asr_provider == "baidu_realtime"
            and self.baidu_asr_app_id.isdigit()
            and self.baidu_asr_api_key
            and self.baidu_asr_ws_url
        )

    @property
    def dashscope_api_origin(self) -> str:
        host = self.dashscope_api_host.rstrip("/")
        workspace = self.dashscope_workspace_id
        for placeholder in ("{workspace_id}", "{WorkspaceId}", "YOUR_WORKSPACE_ID"):
            host = host.replace(placeholder, workspace)
        marker = "/api/v1/"
        if marker in host:
            host = host.split(marker, 1)[0]
        return host

    @property
    def dashscope_tts_url(self) -> str:
        return f"{self.dashscope_api_origin}/api/v1/services/audio/tts/SpeechSynthesizer"

    @property
    def dashscope_voice_url(self) -> str:
        return f"{self.dashscope_api_origin}/api/v1/services/audio/tts/customization"

    @property
    def dashscope_active_model(self) -> str:
        if self.dashscope_tts_voice_source == "clone":
            return self.dashscope_clone_model
        return self.dashscope_tts_model

    @property
    def dashscope_active_voice(self) -> str:
        if self.dashscope_tts_voice_source == "clone":
            return self.dashscope_clone_voice_id
        return self.dashscope_tts_voice

    @property
    def dashscope_tts_configured(self) -> bool:
        return bool(
            self.tts_provider == "dashscope_cosyvoice"
            and self.dashscope_api_key
            and self.dashscope_workspace_id
            and self.dashscope_api_host
            and self.dashscope_active_model
            and self.dashscope_active_voice
            and "{workspace_id}" not in self.dashscope_tts_url
            and "YOUR_WORKSPACE_ID" not in self.dashscope_tts_url
        )


def _voice_source(value: str) -> str:
    source = value.strip().lower()
    return source if source in {"system", "clone"} else "system"


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file)
    root = ROOT

    def text(name: str, default: str = "") -> str:
        return os.environ.get(name, default).strip()

    database = Path(text("DATABASE_PATH", "data/maodun.db"))
    originals = Path(text("ORIGINALS_DIR", "data/originals"))
    if not database.is_absolute():
        database = root / database
    if not originals.is_absolute():
        originals = root / originals
    try:
        timeout = float(text("REQUEST_TIMEOUT_SECONDS", "30") or "30")
    except ValueError:
        timeout = 30.0
    try:
        tts_sample_rate = int(text("DASHSCOPE_TTS_SAMPLE_RATE", "24000") or "24000")
    except ValueError:
        tts_sample_rate = 24000
    try:
        tts_timeout = float(text("DASHSCOPE_TTS_TIMEOUT_SECONDS", "60") or "60")
    except ValueError:
        tts_timeout = 60.0
    return Settings(
        root=root,
        database_path=database,
        originals_dir=originals,
        persona_path=root / "data" / "persona" / "maodun.yaml",
        lexicon_path=root / "data" / "persona" / "lexicon.txt",
        request_timeout_seconds=timeout,
        retriever=text("RETRIEVER", "local") or "local",
        qa_provider=text("QA_PROVIDER", "local_corpus") or "local_corpus",
        deepseek_api_key=text("DEEPSEEK_API_KEY"),
        deepseek_base_url=text("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        deepseek_model=text("DEEPSEEK_MODEL", "deepseek-flash") or "deepseek-flash",
        asr_provider=text("ASR_PROVIDER", "none") or "none",
        asr_base_url=text("ASR_BASE_URL").rstrip("/"),
        baidu_asr_app_id=text("BAIDU_ASR_APP_ID"),
        baidu_asr_api_key=text("BAIDU_ASR_API_KEY"),
        baidu_asr_ws_url=text("BAIDU_ASR_WS_URL", "wss://vop.baidu.com/realtime_asr"),
        tts_provider=text("TTS_PROVIDER", "none") or "none",
        tts_base_url=text("TTS_BASE_URL").rstrip("/"),
        dashscope_api_key=text("DASHSCOPE_API_KEY"),
        dashscope_workspace_id=text("DASHSCOPE_WORKSPACE_ID"),
        dashscope_api_host=text(
            "DASHSCOPE_API_HOST",
            "https://{workspace_id}.cn-beijing.maas.aliyuncs.com",
        ),
        dashscope_tts_model=text("DASHSCOPE_TTS_MODEL", "cosyvoice-v3-flash"),
        dashscope_tts_voice=text("DASHSCOPE_TTS_VOICE", "longanyang"),
        dashscope_tts_format=text("DASHSCOPE_TTS_FORMAT", "wav"),
        dashscope_tts_sample_rate=tts_sample_rate,
        dashscope_tts_timeout_seconds=tts_timeout,
        dashscope_tts_voice_source=_voice_source(text("DASHSCOPE_TTS_VOICE_SOURCE", "system")),
        dashscope_clone_model=text("DASHSCOPE_CLONE_MODEL", "cosyvoice-v3-flash"),
        dashscope_clone_voice_id=text("DASHSCOPE_CLONE_VOICE_ID"),
        avatar_provider=text("AVATAR_PROVIDER", "none") or "none",
    )
