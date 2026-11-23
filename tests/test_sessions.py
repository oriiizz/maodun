from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def _client(tmp_path):
    app = create_app(make_settings(tmp_path))
    return TestClient(app), app.state.runtime


def _finish(runtime, session_id, visitor_id, question, spoken):
    turn = runtime.sessions.begin_turn(session_id, question, visitor_id=visitor_id)
    runtime.sessions.finish_turn(
        turn["session_id"],
        turn["turn_id"],
        turn["epoch"],
        {"spoken_text": spoken, "evidence": [{"secret": "不应导出"}]},
    )


def test_page_identity_keeps_one_session_and_other_visitors_cannot_read_it(tmp_path):
    client, runtime = _client(tmp_path)
    owner = {"X-Visitor-Id": "vis-" + "a" * 24}
    other = {"X-Visitor-Id": "vis-" + "b" * 24}
    created = client.post("/api/sessions", headers=owner)
    assert created.status_code == 200
    session_id = created.json()["id"]
    _finish(runtime, session_id, owner["X-Visitor-Id"], "第一问", "第一答")

    again = client.get(f"/api/sessions/{session_id}", headers=owner)
    assert again.status_code == 200
    assert again.json()["turns"][0]["spoken_text"] == "第一答"
    assert "evidence" not in again.json()["turns"][0]

    assert client.get(f"/api/sessions/{session_id}", headers=other).status_code == 404
    assert client.get("/api/sessions", headers=other).json()["items"] == []
    exported = client.get(f"/api/sessions/{session_id}/export", headers=owner)
    assert exported.status_code == 200
    assert "第一问" in exported.text
    assert "第一答" in exported.text
    assert "不应导出" not in exported.text
    assert client.get(f"/api/sessions/{session_id}", headers=owner).status_code == 200
    runtime.close()


def test_delete_current_and_all_history_stay_deleted(tmp_path):
    client, runtime = _client(tmp_path)
    owner = {"X-Visitor-Id": "vis-" + "c" * 24}
    first = client.post("/api/sessions", headers=owner).json()["id"]
    second = client.post("/api/sessions", headers=owner).json()["id"]
    _finish(runtime, first, owner["X-Visitor-Id"], "留下", "留下的回答")
    _finish(runtime, second, owner["X-Visitor-Id"], "删掉", "删掉的回答")
    assert client.delete(f"/api/sessions/{second}", headers=owner).status_code == 200
    assert client.get(f"/api/sessions/{second}", headers=owner).status_code == 404
    assert client.get(f"/api/sessions/{first}", headers=owner).status_code == 200
    assert client.delete("/api/sessions", headers=owner).json()["deleted"] == 1
    assert client.get("/api/sessions", headers=owner).json()["items"] == []
    assert client.get(f"/api/sessions/{first}", headers=owner).status_code == 404
    runtime.close()
