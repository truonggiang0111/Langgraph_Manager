from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager.app import app
from langgraph_manager import db


def test_failed_action_creates_lesson_and_blocks_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    calls = {"count": 0}

    def fake_run_tool(name, context):
        calls["count"] += 1
        return {"ok": False, "error": "command not found", "data": {"stderr": "not found"}}

    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": True, "data": {"content": '{"mode":"answer","content":"ok"}'}})
    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        job = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        for _ in range(2):
            action = db.add_pending_action(
                job["id"],
                "workspace_command",
                "Run bad cmd",
                "run",
                {"command": "badcmd", "cwd": "/workspace"},
            )
            client.post(f"/api/actions/{action['id']}/approve")

        action3 = db.add_pending_action(
            job["id"],
            "workspace_command",
            "Run bad cmd",
            "run",
            {"command": "badcmd", "cwd": "/workspace"},
        )
        client.post(f"/api/actions/{action3['id']}/approve")
        latest = client.get(f"/api/jobs/{job['id']}").json()["job"]
        action3 = latest["pending_actions"][-1]
        assert action3["status"] == "failed"

        lessons = client.get("/api/action-lessons").json()["action_lessons"]
        assert lessons
        assert lessons[0]["fail_count"] >= 2
        # third approval should be blocked by lesson and not call run_tool again
        assert calls["count"] == 2
        assert "tránh lặp lỗi cũ" in str(action3.get("error") or "")


def test_success_resolves_lesson(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    from langgraph_manager import db

    sig = "abc123"
    db.init_db()
    db.upsert_action_lesson_failure(sig, "workspace_command", "error", "change strategy")
    assert db.get_action_lesson(sig)["status"] == "active"
    db.resolve_action_lesson(sig)
    assert db.get_action_lesson(sig)["status"] == "resolved"
