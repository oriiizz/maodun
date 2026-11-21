import asyncio
import io
import json
import ssl
import struct
import wave
from contextlib import contextmanager

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.speech.baidu_realtime import PCM_FRAME_BYTES
from app.speech.dashscope_tts import DashScopeCosyVoiceSynthesizer, normalize_wav_sizes, prepare_tts_text


class FakeBaiduSocket:
    def __init__(self, messages, early=None, *, hang=False, drop_after_early=False):
        self.messages = list(messages)
        self.early = list(early or [])
        self.sent = []
        self.finished = asyncio.Event()
        self.hang = hang
        self.drop_after_early = drop_after_early

    async def send(self, payload):
        self.sent.append(payload)
        if isinstance(payload, str) and json.loads(payload).get("type") in {"FINISH", "CANCEL"}:
            self.finished.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.early:
            return json.dumps(self.early.pop(0), ensure_ascii=False)
        if self.drop_after_early:
            raise StopAsyncIteration
        await self.finished.wait()
        if self.hang:
            await asyncio.Event().wait()
        if not self.messages:
            raise StopAsyncIteration
        return json.dumps(self.messages.pop(0), ensure_ascii=False)


class FakeBaiduConnector:
    def __init__(self, socket):
        self.socket = socket
        self.url = ""
        self.kwargs = {}

    def __call__(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *_args):
        return False


def silence_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def test_unconfigured_asr_does_not_invent_text(settings):
    client = TestClient(create_app(settings))
    response = client.post(
        "/api/speech/transcribe",
        files={"file": ("speech.wav", silence_wav(), "audio/wav")},
        data={"session_id": "s", "turn_id": "t"},
    )
    assert response.status_code == 501
    assert response.json()["detail"] == "语音识别服务未配置"
    assert "raw_text" not in response.json()


def test_empty_recording_is_rejected(settings):
    client = TestClient(create_app(settings))
    response = client.post(
        "/api/speech/transcribe",
        files={"file": ("speech.wav", b"", "audio/wav")},
    )
    assert response.status_code == 400
    assert "没有录到有效声音" in response.json()["detail"]


def test_baidu_realtime_forwards_pcm_and_combines_final_sentences(settings):
    configured = settings.__class__(
        **{
            **settings.__dict__,
            "asr_provider": "baidu_realtime",
            "baidu_asr_app_id": "124580192",
            "baidu_asr_api_key": "test-key",
        }
    )
    upstream = FakeBaiduSocket(
        [
            {"type": "MID_TEXT", "err_no": 0, "result": "茅盾先"},
            {"type": "FIN_TEXT", "err_no": 0, "result": "茅盾先生，"},
            {"type": "FIN_TEXT", "err_no": 0, "result": "请谈谈《子夜》。"},
        ]
    )
    connector = FakeBaiduConnector(upstream)
    app = create_app(configured)
    app.state.baidu_asr_connector = connector

    with TestClient(app) as client:
        with client.websocket_connect("/api/speech/realtime") as websocket:
            assert websocket.receive_json() == {"type": "READY"}
            websocket.send_bytes(b"\x00\x00" * (PCM_FRAME_BYTES // 2))
            websocket.send_json({"type": "FINISH"})
            assert websocket.receive_json()["type"] == "MID_TEXT"
            assert websocket.receive_json()["final_text"] == "茅盾先生，"
            assert websocket.receive_json()["final_text"] == "茅盾先生，请谈谈《子夜》。"
            complete = websocket.receive_json()

    start = json.loads(upstream.sent[0])
    assert connector.url.startswith("wss://vop.baidu.com/realtime_asr?sn=")
    assert connector.kwargs["proxy"] is None
    assert connector.kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert start == {
        "type": "START",
        "data": {
            "appid": 124580192,
            "appkey": "test-key",
            "dev_pid": 15372,
            "cuid": start["data"]["cuid"],
            "format": "pcm",
            "sample": 16000,
        },
    }
    assert upstream.sent[1] == b"\x00\x00" * (PCM_FRAME_BYTES // 2)
    assert json.loads(upstream.sent[2]) == {"type": "FINISH"}
    assert complete["type"] == "COMPLETE"
    assert complete["reason"] == "ok"
    assert complete["submit"] is True
    assert complete["corrected_text"] == "茅盾先生，请谈谈《子夜》。"
    assert complete["raw_text"] == "茅盾先生，请谈谈《子夜》。"
    assert complete["audio_bytes"] == PCM_FRAME_BYTES
    assert complete["audio_frames"] == 1
    assert complete["audio_peak"] == 0
    assert [item for item in upstream.sent if isinstance(item, bytes)] == [
        b"\x00\x00" * (PCM_FRAME_BYTES // 2)
    ]


def test_baidu_realtime_reports_authentication_error(settings):
    configured = settings.__class__(
        **{
            **settings.__dict__,
            "asr_provider": "baidu_realtime",
            "baidu_asr_app_id": "124580192",
            "baidu_asr_api_key": "wrong-key",
        }
    )
    upstream = FakeBaiduSocket(
        [{"type": "FIN_TEXT", "err_no": -3004, "err_msg": "asr authentication failed", "result": ""}]
    )
    app = create_app(configured)
    app.state.baidu_asr_connector = FakeBaiduConnector(upstream)

    with TestClient(app) as client:
        with client.websocket_connect("/api/speech/realtime") as websocket:
            assert websocket.receive_json()["type"] == "READY"
            websocket.send_json({"type": "FINISH"})
            error = websocket.receive_json()

    assert error["type"] == "ERROR"
    assert "鉴权失败" in error["message"]
    assert "wrong-key" not in error["message"]


def _configured(settings):
    return settings.__class__(
        **{
            **settings.__dict__,
            "asr_provider": "baidu_realtime",
            "baidu_asr_app_id": "124580192",
            "baidu_asr_api_key": "test-key",
        }
    )


@contextmanager
def _speak(messages, settings, *, early=None, hang=False, drop_after_early=False, audio=True):
    upstream = FakeBaiduSocket(messages, early, hang=hang, drop_after_early=drop_after_early)
    app = create_app(_configured(settings))
    app.state.baidu_asr_connector = FakeBaiduConnector(upstream)
    with TestClient(app) as client:
        with client.websocket_connect("/api/speech/realtime") as websocket:
            assert websocket.receive_json()["type"] == "READY"
            if audio:
                websocket.send_bytes(b"\x00\x10" * 160)
            yield websocket, upstream


def test_trailing_no_speech_keeps_final_sentence_and_submits_once(settings):
    messages = [
        {"type": "MID_TEXT", "err_no": 0, "result": "请谈谈子夜", "sn": "req-1"},
        {
            "type": "FIN_TEXT",
            "err_no": 0,
            "result": "请谈谈《子夜》。",
            "start_time": 0,
            "end_time": 1600,
            "sn": "req-1",
            "log_id": 11,
        },
        {
            "type": "FIN_TEXT",
            "err_no": -3005,
            "err_msg": "asr server not find effective speech[info:-4]",
            "result": "这段不该进入结果",
            "start_time": 1600,
            "end_time": 2400,
            "sn": "req-1",
            "log_id": 12,
        },
    ]
    with _speak(messages, settings) as (websocket, upstream):
        websocket.send_json({"type": "FINISH"})
        interim = websocket.receive_json()
        final = websocket.receive_json()
        complete = websocket.receive_json()

    assert interim["type"] == "MID_TEXT"
    assert interim["text"] == "请谈谈子夜"
    assert interim["final_text"] == ""
    assert final["final_text"] == "请谈谈《子夜》。"
    assert complete["type"] == "COMPLETE"
    assert complete["submit"] is True
    assert complete["reason"] == "ok"
    assert complete["corrected_text"] == "请谈谈《子夜》。"
    assert "这段不该进入结果" not in complete["corrected_text"]
    assert "请谈谈子夜" not in complete["corrected_text"]
    segment = complete["no_speech_segments"][0]
    assert segment["err_no"] == -3005
    assert segment["stage"] == "end"
    assert segment["start_time"] == 1600
    assert segment["end_time"] == 2400
    assert segment["sn"] == "req-1"
    assert segment["log_id"] == 12
    assert segment["after_finish"] is True
    assert json.loads(upstream.sent[-1]) == {"type": "FINISH"}


def test_multiple_final_sentences_stay_in_order(settings):
    messages = [
        {"type": "FIN_TEXT", "err_no": 0, "result": "第一句。", "start_time": 0, "end_time": 800, "sn": "multi"},
        {"type": "FIN_TEXT", "err_no": 0, "result": "第二句。", "start_time": 900, "end_time": 1700, "sn": "multi"},
        {
            "type": "FIN_TEXT",
            "err_no": -3005,
            "err_msg": "asr server not find effective speech[info:-4]",
            "result": "",
            "start_time": 1700,
            "end_time": 2200,
            "sn": "multi",
        },
    ]
    with _speak(messages, settings) as (websocket, _upstream):
        websocket.send_json({"type": "FINISH"})
        assert websocket.receive_json()["final_text"] == "第一句。"
        assert websocket.receive_json()["final_text"] == "第一句。第二句。"
        complete = websocket.receive_json()

    assert complete["submit"] is True
    assert complete["corrected_text"] == "第一句。第二句。"


def test_silence_only_does_not_submit(settings):
    messages = [
        {
            "type": "FIN_TEXT",
            "err_no": -3005,
            "err_msg": "asr server not find effective speech[info:-4]",
            "result": "噪声",
            "start_time": 0,
            "end_time": 900,
            "sn": "quiet",
        }
    ]
    with _speak(messages, settings) as (websocket, _upstream):
        websocket.send_json({"type": "FINISH"})
        complete = websocket.receive_json()

    assert complete["type"] == "COMPLETE"
    assert complete["submit"] is False
    assert complete["reason"] == "no_speech"
    assert complete["corrected_text"] == ""
    assert complete["raw_text"] == ""
    assert complete["audio_bytes"] == 320
    assert complete["audio_frames"] == 1
    assert complete["no_speech_segments"][0]["err_msg"] == "asr server not find effective speech[info:-4]"
    assert complete["no_speech_segments"][0]["sn"] == "quiet"


def test_mid_stream_no_speech_keeps_text_without_submitting(settings):
    messages = [
        {"type": "FIN_TEXT", "err_no": 0, "result": "第一句。", "start_time": 0, "end_time": 1000, "sn": "hole"},
        {
            "type": "FIN_TEXT",
            "err_no": -3005,
            "err_msg": "asr server not find effective speech[info:-4]",
            "result": "",
            "start_time": 1000,
            "end_time": 1800,
            "sn": "hole",
            "log_id": 77,
        },
        {"type": "FIN_TEXT", "err_no": 0, "result": "第三句。", "start_time": 1800, "end_time": 2600, "sn": "hole"},
    ]
    with _speak(messages, settings) as (websocket, _upstream):
        websocket.send_json({"type": "FINISH"})
        assert websocket.receive_json()["final_text"] == "第一句。"
        assert websocket.receive_json()["final_text"] == "第一句。第三句。"
        complete = websocket.receive_json()

    assert complete["submit"] is False
    assert complete["reason"] == "incomplete"
    assert complete["corrected_text"] == "第一句。第三句。"
    assert complete["no_speech_segments"][0]["stage"] == "mid"
    assert complete["no_speech_segments"][0]["log_id"] == 77


def test_parameter_error_still_fails_the_session(settings):
    messages = [{"type": "FIN_TEXT", "err_no": -3008, "err_msg": "missing field", "result": "不能要"}]
    with _speak(messages, settings) as (websocket, _upstream):
        websocket.send_json({"type": "FINISH"})
        error = websocket.receive_json()

    assert error["type"] == "ERROR"
    assert "参数错误" in error["message"]
    assert "不能要" not in error["message"]


def test_cancel_does_not_complete_a_question(settings):
    upstream = FakeBaiduSocket([])
    app = create_app(_configured(settings))
    app.state.baidu_asr_connector = FakeBaiduConnector(upstream)
    with TestClient(app) as client:
        with client.websocket_connect("/api/speech/realtime") as websocket:
            assert websocket.receive_json()["type"] == "READY"
            websocket.send_bytes(b"\x00\x10" * 160)
            websocket.send_json({"type": "CANCEL"})
            payload = websocket.receive_json()
    assert payload["type"] == "CANCELLED"
    assert payload.get("submit") is not True
    assert "corrected_text" not in payload
    assert json.loads(upstream.sent[-1]) == {"type": "CANCEL"}
    assert not any(isinstance(item, bytes) and set(item) == {0} for item in upstream.sent)


def test_connection_drop_keeps_text_for_review(settings):
    with _speak(
        [],
        settings,
        early=[{"type": "FIN_TEXT", "err_no": 0, "result": "已经听到的句子。", "sn": "drop"}],
        drop_after_early=True,
    ) as (websocket, _upstream):
        final = websocket.receive_json()
        complete = websocket.receive_json()

    assert final["final_text"] == "已经听到的句子。"
    assert complete["submit"] is False
    assert complete["reason"] == "interrupted"
    assert complete["corrected_text"] == "已经听到的句子。"


def test_finish_timeout_keeps_text_without_submitting(settings, monkeypatch):
    monkeypatch.setattr("app.speech.baidu_realtime._finish_wait_seconds", lambda _settings: 0.2)
    with _speak(
        [],
        settings,
        early=[{"type": "FIN_TEXT", "err_no": 0, "result": "超时前的句子。", "sn": "slow"}],
        hang=True,
    ) as (websocket, _upstream):
        assert websocket.receive_json()["final_text"] == "超时前的句子。"
        websocket.send_json({"type": "FINISH"})
        complete = websocket.receive_json()

    assert complete["type"] == "COMPLETE"
    assert complete["submit"] is False
    assert complete["reason"] == "timeout"
    assert complete["corrected_text"] == "超时前的句子。"


def test_unconfigured_tts_does_not_return_audio(settings):
    client = TestClient(create_app(settings))
    response = client.post("/api/speech/synthesize", json={"text": "这是一句回答"})
    assert response.status_code == 501
    assert "没有语音播放" in response.json()["detail"]
    assert not response.content.startswith(b"RIFF")


def test_dashscope_cosyvoice_downloads_wav_with_expected_configuration(settings):
    configured = settings.__class__(
        **{
            **settings.__dict__,
            "tts_provider": "dashscope_cosyvoice",
            "dashscope_api_key": "test-dashscope-key",
            "dashscope_workspace_id": "workspace-123",
        }
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "request_id": "request-1",
                    "output": {"audio": {"url": "https://audio.example/result.wav"}},
                },
            )
        return httpx.Response(200, content=b"RIFF-test-wave", headers={"Content-Type": "audio/wav"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        synthesizer = DashScopeCosyVoiceSynthesizer(configured, client)
        clip = synthesizer.synthesize(
            "# 回答\n**茅盾**的作品见[资料](https://example.com)。",
            utterance_id="utt-1",
            turn_id="turn-1",
            session_id="session-1",
            epoch=0,
        )

    assert str(requests[0].url) == (
        "https://workspace-123.cn-beijing.maas.aliyuncs.com/"
        "api/v1/services/audio/tts/SpeechSynthesizer"
    )
    assert requests[0].headers["authorization"] == "Bearer test-dashscope-key"
    request_body = json.loads(requests[0].content)
    assert request_body == {
        "model": "cosyvoice-v3-flash",
        "input": {
            "text": "回答 茅盾的作品见资料。",
            "voice": "longanyang",
            "format": "wav",
            "sample_rate": 24000,
        },
    }
    assert clip.audio == b"RIFF-test-wave"
    assert clip.mime == "audio/wav"


def test_clone_voice_uses_entitled_model_when_target_model_is_unpurchased(settings):
    configured = settings.__class__(
        **{
            **settings.__dict__,
            "tts_provider": "dashscope_cosyvoice",
            "dashscope_api_key": "test-dashscope-key",
            "dashscope_workspace_id": "workspace-123",
            "dashscope_tts_model": "qwen-audio-3.1-tts-flash",
            "dashscope_tts_voice": "longanhuan_v3.1",
            "dashscope_tts_voice_source": "clone",
            "dashscope_clone_model": "cosyvoice-v3-flash",
            "dashscope_clone_voice_id": "cosyvoice-v3-flash-maodun-test",
        }
    )
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            models.append(body["model"])
            if body["model"] == "cosyvoice-v3-flash":
                return httpx.Response(
                    403,
                    json={
                        "code": "AccessDenied.Unpurchased",
                        "message": "Access to model denied.",
                        "request_id": "denied-1",
                    },
                )
            assert body["input"]["voice"] == "cosyvoice-v3-flash-maodun-test"
            return httpx.Response(
                200,
                json={"output": {"audio": {"url": "https://audio.example/clone.wav"}}},
            )
        return httpx.Response(200, content=b"RIFF-clone-wave", headers={"Content-Type": "audio/wav"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        clip = DashScopeCosyVoiceSynthesizer(configured, client).synthesize(
            "欢迎你来。",
            utterance_id="utt-2",
            turn_id="turn-2",
            session_id="session-2",
            epoch=0,
        )

    assert models == ["cosyvoice-v3-flash", "qwen-audio-3.1-tts-flash"]
    assert clip.audio == b"RIFF-clone-wave"
    assert clip.mime == "audio/wav"


def test_normalize_wav_sizes_replaces_placeholder_lengths():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x01\x00\x02\x00")
    audio = bytearray(buffer.getvalue())
    struct.pack_into("<I", audio, 4, 0x7FFFFFFF)
    struct.pack_into("<I", audio, audio.find(b"data") + 4, 0x7FFFFFFF)

    fixed = normalize_wav_sizes(bytes(audio))
    with wave.open(io.BytesIO(fixed), "rb") as wav:
        assert wav.getnframes() == 2
        assert wav.getframerate() == 24000


def test_prepare_tts_text_removes_code_urls_and_markdown():
    text = "## 正文\n- **第一句**\n```json\n{\"debug\": true}\n```\n详情：https://example.com"
    assert prepare_tts_text(text) == "正文 第一句 详情："
