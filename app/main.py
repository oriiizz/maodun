"""本地网页和接口。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings, load_settings
from app.runtime import Runtime, WorkbookImportError
from app.speech.baidu_realtime import relay_baidu_realtime, websocket_connect
from app.speech.correction import correct_transcript
from app.speech.interfaces import SpeechNotConnected

WEB = Path(__file__).resolve().parent.parent / "web"


class PathImport(BaseModel):
    path: str


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    session_id: str | None = None


class ReviewBody(BaseModel):
    entry_id: str
    status: str


class CorrectBody(BaseModel):
    text: str


class SpeakBody(BaseModel):
    text: str = ""
    session_id: str = ""
    turn_id: str = ""
    epoch: int = 0


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = Runtime(settings or load_settings())
    app = FastAPI(title="茅盾数字人")
    app.state.runtime = runtime
    app.state.baidu_asr_connector = websocket_connect

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/admin")
    def admin() -> FileResponse:
        return FileResponse(WEB / "admin.html")

    @app.get("/api/status")
    def status() -> dict:
        return runtime.status()

    @app.get("/api/sources")
    def sources() -> dict:
        return {"items": runtime.sources()}

    @app.get("/api/batches")
    def batches() -> dict:
        return {"items": runtime.batches()}

    @app.get("/api/batches/{batch_id}/issues")
    def issues(batch_id: str) -> dict:
        return {"items": runtime.batch_issues(batch_id)}

    @app.get("/api/entries")
    def entries(q: str = "", source_id: str = "", limit: int = 20, offset: int = 0) -> dict:
        try:
            return runtime.list_entries(query=q, source_id=source_id, limit=limit, offset=offset)
        except Exception as exc:
            raise HTTPException(502, _public_error(exc)) from exc

    @app.get("/api/entry")
    def entry_detail(entry_id: str) -> dict:
        item = runtime.get_entry(entry_id)
        if item is None:
            raise HTTPException(404, "没有找到这个条目。")
        return item

    @app.post("/api/entry/review")
    def review(body: ReviewBody) -> dict:
        try:
            runtime.set_review(body.entry_id, body.status)
        except KeyError as exc:
            raise HTTPException(404, "没有找到这个条目。") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "status": body.status}

    @app.post("/api/search")
    def search(body: AskBody) -> dict:
        return {"items": runtime.search(body.question)}

    @app.post("/api/ask")
    def ask(body: AskBody) -> dict:
        try:
            return runtime.ask(body.question, body.session_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/sessions")
    def create_session() -> dict:
        return runtime.sessions.create()

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: str) -> dict:
        session = runtime.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "没有找到这个会话。")
        return {"session": session, "turns": runtime.sessions.history(session_id)}

    @app.post("/api/sessions/{session_id}/cancel")
    def cancel(session_id: str) -> dict:
        session = runtime.sessions.cancel(session_id)
        if session is None:
            raise HTTPException(404, "没有找到这个会话。")
        runtime.playback.note_epoch(session_id, int(session["epoch"]))
        return session

    @app.post("/api/import")
    async def upload(file: UploadFile = File(...)) -> dict:
        name = Path(file.filename or "upload.xlsx").name
        if not name.lower().endswith(".xlsx"):
            raise HTTPException(400, "请上传 .xlsx 文件。")
        folder = Path(tempfile.mkdtemp(prefix="maodun-import-"))
        target = folder / name
        try:
            target.write_bytes(await file.read())
            return runtime.import_path(target)
        except WorkbookImportError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    @app.post("/api/import/path")
    def import_from_path(body: PathImport) -> dict:
        path = Path(body.path).expanduser()
        if path.suffix.lower() != ".xlsx":
            raise HTTPException(400, "请选择 .xlsx 文件。")
        try:
            return runtime.import_path(path)
        except WorkbookImportError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/import/demo")
    def import_demo() -> dict:
        return runtime.import_demo()

    @app.post("/api/correct")
    def correct(body: CorrectBody) -> dict:
        return correct_transcript(body.text, runtime.lexicon)

    @app.get("/api/modules")
    def modules() -> dict:
        return {
            "asr": "百度实时识别由浏览器发送 16kHz 单声道 PCM 到本项目，再由后端转发；API Key 不进入前端。",
            "correction": "纠错保留原始识别结果。称谓只在上下文里改，不会把所有“矛盾”换成“茅盾”；排除“你的职业”“职业规划”等职业搭配后，其余听成“职业”或“子叶”的《子夜》会改回来。",
            "tts": "回答完成后，正文由后端送到阿里云百炼 CosyVoice SpeechSynthesizer；API Key 不进入前端，依据和调试信息不朗读。",
            "avatar": "2D 和 UE 都接收同一份事件：字幕、动作、音频。音频格式和嘴型插件要在实际接入时确认，不能假设只发文字就会对上口型。",
            "playback": "同一会话按句排队，取消或新一轮会丢掉旧结果，避免多段声音一起播。",
        }

    @app.post("/api/speech/transcribe")
    async def transcribe(
        file: UploadFile = File(...),
        session_id: str = Form(""),
        turn_id: str = Form(""),
    ) -> dict:
        audio = await file.read()
        if len(audio) < 100:
            raise HTTPException(400, "没有录到有效声音。请靠近麦克风再说一次。")
        try:
            transcript = runtime.recognizer.transcribe(
                audio,
                mime=file.content_type or "application/octet-stream",
                session_id=session_id,
                turn_id=turn_id,
                lexicon=runtime.lexicon,
            )
        except SpeechNotConnected as exc:
            raise HTTPException(501, exc.notice) from exc
        return {
            "raw_text": transcript.raw_text,
            "corrected_text": transcript.corrected_text,
            "changes": transcript.changes,
            "provider": transcript.provider,
            "provisional": False,
        }

    @app.websocket("/api/speech/realtime")
    async def realtime_transcribe(websocket: WebSocket) -> None:
        await relay_baidu_realtime(
            websocket,
            runtime.settings,
            runtime.lexicon,
            connector=app.state.baidu_asr_connector,
        )

    @app.post("/api/speech/synthesize")
    def synthesize(body: SpeakBody) -> Response:
        spoken = body.text.strip()
        if not spoken:
            raise HTTPException(400, "没有需要朗读的正文。")
        try:
            clip = runtime.synthesizer.synthesize(
                spoken,
                utterance_id=f"{body.turn_id or 'utt'}-spoken",
                turn_id=body.turn_id,
                session_id=body.session_id,
                epoch=body.epoch,
            )
        except SpeechNotConnected as exc:
            raise HTTPException(501, exc.notice) from exc
        return Response(content=clip.audio, media_type=clip.mime or "audio/wav")

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


def _public_error(exc: Exception) -> str:
    notice = getattr(exc, "notice", None)
    if isinstance(notice, str) and notice:
        return notice
    return "检索时出现问题。请看服务是否已配置，然后重试。"


app = create_app()
