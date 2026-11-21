import json

import httpx

from app.speech.voice_clone import (
    VoiceCloneError,
    create_voice,
    find_reusable_voice,
    probe_model,
    update_env_values,
    wait_until_ok,
)


def _settings(settings):
    return settings.__class__(
        **{
            **settings.__dict__,
            "dashscope_api_key": "test-dashscope-key",
            "dashscope_workspace_id": "ws-test",
        }
    )


def test_model_probe_reports_unpurchased_and_does_not_create_voice(settings):
    configured = _settings(settings)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            403,
            json={
                "code": "AccessDenied.Unpurchased",
                "message": "Access to model denied. Please make sure you are eligible for using the model.",
                "request_id": "request-denied",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            probe_model(configured, client)
        except VoiceCloneError as exc:
            error = exc
        else:
            raise AssertionError("未开通模型时应该失败")

    assert error.model_not_enabled
    assert error.code == "AccessDenied.Unpurchased"
    assert error.message == "Access to model denied. Please make sure you are eligible for using the model."
    assert error.request_id == "request-denied"
    assert all("customization" not in url for url in seen)
    assert "test-dashscope-key" not in str(error)


def test_create_voice_submits_wav_data_uri(settings):
    configured = _settings(settings)
    bodies = []
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"request_id": "request-create", "output": {"voice_id": "voice-1"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload = create_voice(configured, client, b"RIFF-demo")

    body = bodies[0]
    assert urls == [configured.dashscope_voice_url]
    assert body["model"] == "voice-enrollment"
    assert body["input"]["action"] == "create_voice"
    assert body["input"]["target_model"] == "cosyvoice-v3-flash"
    assert body["input"]["prefix"] == "maodun"
    assert body["input"]["language_hints"] == ["zh"]
    assert body["input"]["enable_preprocess"] is True
    assert body["input"]["max_prompt_audio_length"] == 20.0
    assert body["input"]["audio"].startswith("data:audio/wav;base64,")
    assert payload["output"]["voice_id"] == "voice-1"


def test_existing_voice_is_reused_without_create(settings):
    configured = _settings(settings)
    actions = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        action = body["input"]["action"]
        actions.append(action)
        if action == "query_voice":
            return httpx.Response(
                200,
                json={"request_id": "request-query", "output": {"status": "OK", "target_model": "cosyvoice-v3-flash"}},
            )
        raise AssertionError(action)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        voice_id = find_reusable_voice(
            configured,
            client,
            "cosyvoice-v3-flash",
            {"voice_id": "voice-existing", "target_model": "cosyvoice-v3-flash"},
        )

    assert voice_id == "voice-existing"
    assert actions == ["query_voice"]


def test_wait_until_ok_times_out(settings):
    configured = _settings(settings)
    clock = {"now": 0.0}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"status": "DEPLOYING"}, "request_id": "request-wait"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            wait_until_ok(
                configured,
                client,
                "voice-1",
                timeout=10,
                interval=5,
                sleep=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
                now=lambda: clock["now"],
            )
        except VoiceCloneError as exc:
            assert "超时" in exc.summary
        else:
            raise AssertionError("等待应该超时")


def test_env_update_preserves_existing_secret(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DASHSCOPE_API_KEY=secret-value\nDASHSCOPE_TTS_MODEL=qwen-audio-3.1-tts-flash\n", encoding="utf-8")
    update_env_values(
        env,
        {
            "DASHSCOPE_TTS_VOICE_SOURCE": "clone",
            "DASHSCOPE_CLONE_MODEL": "cosyvoice-v3-flash",
            "DASHSCOPE_CLONE_VOICE_ID": "voice-1",
        },
    )
    text = env.read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY=secret-value" in text
    assert "DASHSCOPE_TTS_MODEL=qwen-audio-3.1-tts-flash" in text
    assert "DASHSCOPE_CLONE_VOICE_ID=voice-1" in text


def test_clone_voice_is_selected_independently(settings):
    from app.speech.dashscope_tts import DashScopeCosyVoiceSynthesizer

    configured = settings.__class__(
        **{
            **_settings(settings).__dict__,
            "tts_provider": "dashscope_cosyvoice",
            "dashscope_tts_voice_source": "clone",
            "dashscope_clone_model": "cosyvoice-v3-flash",
            "dashscope_clone_voice_id": "cloned-voice",
            "dashscope_tts_model": "qwen-audio-3.1-tts-flash",
            "dashscope_tts_voice": "longanhuan_v3.1",
        }
    )
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"output": {"audio": {"url": "https://audio.example/result.wav"}}})
        return httpx.Response(200, content=b"RIFFdata", headers={"Content-Type": "audio/wav"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        DashScopeCosyVoiceSynthesizer(configured, client).synthesize(
            "欢迎你来。",
            utterance_id="utt",
            turn_id="turn",
            session_id="session",
            epoch=0,
        )

    assert bodies[0]["model"] == "cosyvoice-v3-flash"
    assert bodies[0]["input"]["voice"] == "cloned-voice"
    assert "rate" not in bodies[0]["input"]
    assert "pitch" not in bodies[0]["input"]
