from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager.app import app
from langgraph_manager import db


def test_memory_context_includes_relevant_project_and_successful_route(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": False, "data": {}})

    with TestClient(app) as client:
        job = client.post("/api/chats").json()["job"]
        client.post(f"/api/jobs/{job['id']}/messages", json={"content": "run python tests and fix flaky case"})
        client.post(
            "/api/projects",
            json={
                "name": "Test runner",
                "root_path": "/workspace/LangGraph_Manager",
                "summary": "Python test workflow for flaky pytest failures",
                "memory": "Prefer pytest reproduction before patching code",
            },
        )
        client.post(
            "/api/projects",
            json={
                "name": "Unrelated frontend",
                "root_path": "/workspace/frontend",
                "summary": "Landing page typography",
                "memory": "Use hero gradient",
            },
        )

        env_fp = app_module.env_fingerprint_for_job(db.get_job(job["id"]) or {})
        task_sig = app_module.task_signature_for_request("run python tests and fix flaky case")
        db.record_route_memory(env_fp, task_sig, "python_local", ok=True, error_class="")
        db.record_route_memory(env_fp, task_sig, "python_local", ok=True, error_class="")
        db.record_route_memory(env_fp, task_sig, "python_docker", ok=False, error_class="timeout")

        context = app_module.memory_context(job["id"])
        assert "project Test runner" in context
        assert "Prefer pytest reproduction before patching code" in context
        assert "successful_route task=run_tests route=python_local" in context
        assert "python_docker" not in context


def test_route_memory_hint_prefers_successful_route_for_request(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    job = db.create_chat_session("job_route_hint", permission_mode="full_access")

    env_fp = app_module.env_fingerprint_for_job(job)
    db.record_route_memory(env_fp, "run_python", "python_local", ok=True, error_class="")
    db.record_route_memory(env_fp, "run_python", "python_local", ok=True, error_class="")
    db.record_route_memory(env_fp, "run_python", "python_docker", ok=False, error_class="timeout")

    hint = app_module.build_route_memory_hint(job, "run python script and fix error")
    assert "Prefer route `python_local`" in hint
    assert "python_docker" not in hint or "Avoid route `python_docker`" in hint


def test_due_recurring_task_creates_job_with_pending_next_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        created = client.post(
            "/api/recurring-tasks",
            json={
                "name": "Morning report",
                "prompt": "Send morning report",
                "schedule": "daily",
                "action_kind": "note",
                "payload": {"note": "Compile daily report"},
                "status": "active",
                "next_run_at": "2000-01-01T00:00:00+00:00",
            },
        )
        assert created.status_code == 200

        queued = client.post("/api/recurring-tasks/run-due").json()["queued"]
        assert queued
        due_job = client.get(f"/api/jobs/{queued[0]['job_id']}").json()["job"]

        assert due_job["title"] == "Recurring: Morning report"
        assert due_job["pending_actions"][0]["kind"] == "note"
        assert due_job["pending_actions"][0]["status"] == "pending"
        assert due_job["next_actions"][0]["type"] == "pending_action"
        assert due_job["next_actions"][0]["title"] == "Recurring: Morning report"


def test_recurring_run_updates_next_schedule_after_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        created = client.post(
            "/api/recurring-tasks",
            json={
                "name": "Hourly check",
                "prompt": "Check service health",
                "schedule": "hourly",
                "action_kind": "note",
                "payload": {"note": "Check service health"},
                "status": "active",
                "next_run_at": "2000-01-01T00:00:00+00:00",
            },
        ).json()["recurring_task"]

        queued = client.post("/api/recurring-tasks/run-due").json()["queued"]
        assert queued and queued[0]["task_id"] == created["id"]

        tasks = client.get("/api/recurring-tasks").json()["recurring_tasks"]
        current = next(item for item in tasks if item["id"] == created["id"])
        assert current["last_run_at"]
        assert current["next_run_at"]
        assert current["next_run_at"] != "2000-01-01T00:00:00+00:00"
