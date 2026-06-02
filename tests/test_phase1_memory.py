from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager.app import app


def test_user_preferences_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    with TestClient(app) as client:
        put = client.put(
            "/api/user-preferences/work_style",
            json={"key": "work_style", "value": "brief-first", "source": "manual"},
        )
        assert put.status_code == 200
        assert put.json()["user_preference"]["key"] == "work_style"

        listed = client.get("/api/user-preferences")
        assert listed.status_code == 200
        prefs = listed.json()["user_preferences"]
        assert any(item["key"] == "work_style" and item["value"] == "brief-first" for item in prefs)

        deleted = client.delete("/api/user-preferences/work_style")
        assert deleted.status_code == 200
        assert client.delete("/api/user-preferences/work_style").status_code == 404


def test_memory_context_includes_user_preferences_and_session_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": False, "data": {}})
    with TestClient(app) as client:
        created = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        job_id = created["id"]
        client.post(f"/api/jobs/{job_id}/messages", json={"content": "nhớ giúp tôi cách làm việc"})
        context = app_module.memory_context(job_id)
        assert "user_preferences:" in context
        assert "last_active_job_id" in context
        assert "last_permission_mode" in context
