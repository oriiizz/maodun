from pathlib import Path

import pytest

from app.config import ROOT, Settings


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        root=ROOT,
        database_path=tmp_path / "test.db",
        originals_dir=tmp_path / "originals",
        persona_path=ROOT / "data" / "persona" / "maodun.yaml",
        lexicon_path=ROOT / "data" / "persona" / "lexicon.txt",
        request_timeout_seconds=5,
        retriever="local",
        qa_provider="local_corpus",
        deepseek_api_key="",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-flash",
        asr_provider="none",
        asr_base_url="",
        baidu_asr_app_id="",
        baidu_asr_api_key="",
        baidu_asr_ws_url="wss://vop.baidu.com/realtime_asr",
        tts_provider="none",
        tts_base_url="",
        dashscope_api_key="",
        dashscope_workspace_id="",
        dashscope_api_host="https://{workspace_id}.cn-beijing.maas.aliyuncs.com",
        dashscope_tts_model="cosyvoice-v3-flash",
        dashscope_tts_voice="longanyang",
        dashscope_tts_format="wav",
        dashscope_tts_sample_rate=24000,
        dashscope_tts_timeout_seconds=60,
        dashscope_tts_voice_source="system",
        dashscope_clone_model="cosyvoice-v3-flash",
        dashscope_clone_voice_id="",
        avatar_provider="none",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def runtime(settings):
    from app.runtime import Runtime

    item = Runtime(settings)
    yield item
    item.close()
