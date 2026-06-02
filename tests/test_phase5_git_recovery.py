from langgraph_manager import app as app_module
from langgraph_manager import db


def test_coding_executor_includes_git_checkpoint_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    db.create_chat_session("job1", title="New session", permission_mode="full_access")
    job = db.get_job("job1")
    assert job is not None

    action = {
        "id": 1,
        "job_id": "job1",
        "kind": "coding_agent_executor",
        "title": "Run coder",
        "payload": {"request": "fix bug", "git_checkpoint_before_run": True},
    }

    monkeypatch.setattr(
        app_module,
        "create_git_checkpoint_for_job",
        lambda job_id, cwd, label="": (True, "ok", {"id": 9, "commit_hash": "abc12345", "branch": "main", "cwd": "/workspace"}),
    )
    monkeypatch.setattr(
        app_module,
        "run_tool",
        lambda name, context: {"ok": True, "summary": "ok", "data": {"output": "done"}},
    )

    ok, output = app_module.execute_pending_action(action, job)
    assert ok is True
    assert '"git_checkpoint"' in output
    assert '"abc12345"' in output


def test_coding_executor_auto_checkpoint_only_after_repeated_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    db.create_chat_session("jobx", title="New session", permission_mode="full_access")
    job = db.get_job("jobx")
    assert job is not None

    called = {"count": 0}

    def fake_checkpoint(job_id, cwd, label=""):
        called["count"] += 1
        return True, "ok", {"id": 1, "commit_hash": "abc", "branch": "main", "cwd": "/workspace"}

    monkeypatch.setattr(app_module, "create_git_checkpoint_for_job", fake_checkpoint)
    monkeypatch.setattr(app_module, "run_tool", lambda name, context: {"ok": True, "summary": "ok", "data": {"output": "done"}})

    action = {
        "id": 11,
        "job_id": "jobx",
        "kind": "coding_agent_executor",
        "title": "Run coder",
        "payload": {"request": "fix bug", "git_checkpoint_before_run": False, "checkpoint_fail_threshold": 3},
    }
    # fail_count=2 -> not yet checkpoint
    sig = app_module.action_signature(action)
    db.upsert_action_lesson_failure(sig, "coding_agent_executor", "e1", "retry")
    db.upsert_action_lesson_failure(sig, "coding_agent_executor", "e2", "retry")
    ok, output = app_module.execute_pending_action(action, job)
    assert ok is True
    assert called["count"] == 0
    assert '"git_checkpoint"' not in output

    # fail_count=3 -> checkpoint should trigger
    db.upsert_action_lesson_failure(sig, "coding_agent_executor", "e3", "retry")
    ok2, output2 = app_module.execute_pending_action(action, job)
    assert ok2 is True
    assert called["count"] == 1
    assert '"git_checkpoint"' in output2


def test_git_restore_checkpoint_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    cp = db.create_git_checkpoint("job2", "/workspace/LangGraph_Manager", "main", "deadbeef", "test")

    monkeypatch.setattr(app_module, "run_git", lambda args, cwd, timeout=20: (True, "ok"))
    action = {
        "id": 2,
        "job_id": "job2",
        "kind": "git_restore_checkpoint",
        "title": "Restore",
        "payload": {"checkpoint_id": cp["id"]},
    }
    job = {"id": "job2", "permission_mode": "full_access", "focus_files": []}
    ok, output = app_module.execute_pending_action(action, job)
    assert ok is True
    assert "Restored checkpoint" in output
    updated = db.get_git_checkpoint(cp["id"])
    assert updated and updated["status"] == "restored"


def test_execute_action_updates_executor_session_and_route_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    db.create_chat_session("job3", title="New session", permission_mode="full_access")

    action = db.add_pending_action(
        "job3",
        "coding_agent_executor",
        "Run Claude",
        "Fix flaky test",
        {"executor": "claude", "request": "run python tests and fix failing case"},
    )

    monkeypatch.setattr(app_module, "agent_follow_up_after_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        app_module,
        "run_tool",
        lambda name, context: {
            "ok": True,
            "summary": "ok",
            "data": {
                "command": "python -m pytest",
                "session_id": "sess_abc_123",
                "output": "fixed",
            },
        },
    )

    app_module.execute_action_and_follow_up(action["id"])

    linked = db.get_executor_session("job3", "claude")
    assert linked is not None
    assert linked["session_id"] == "sess_abc_123"

    rows = db.list_route_memory(
        app_module.env_fingerprint_for_job(db.get_job("job3") or {}),
        app_module.task_signature_for_request("run python tests and fix failing case"),
        limit=3,
    )
    assert rows
    assert rows[0]["route"] == "python_local"
    assert int(rows[0]["success_count"] or 0) >= 1
    assert rows[0]["last_outcome"] == "success"


def test_execute_action_failure_writes_route_memory_with_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    db.create_chat_session("job4", title="New session", permission_mode="full_access")

    action = db.add_pending_action(
        "job4",
        "coding_agent_executor",
        "Run Claude",
        "Run script",
        {"executor": "claude", "request": "run python script"},
    )

    monkeypatch.setattr(app_module, "agent_follow_up_after_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        app_module,
        "run_tool",
        lambda name, context: {
            "ok": False,
            "summary": "failed",
            "data": {
                "command": "python script.py",
                "output": "python: command not found",
            },
        },
    )

    app_module.execute_action_and_follow_up(action["id"])

    rows = db.list_route_memory(
        app_module.env_fingerprint_for_job(db.get_job("job4") or {}),
        app_module.task_signature_for_request("run python script"),
        limit=3,
    )
    assert rows
    assert int(rows[0].get("failure_count") or 0) >= 1
    assert str(rows[0].get("last_outcome") or "") == "failure"


def test_quality_overview_includes_successful_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    db.init_db()
    db.record_route_memory("os=nt|container=x|workspace=y", "run_python", "python_docker", ok=True, error_class="")
    db.record_route_memory("os=nt|container=x|workspace=y", "run_python", "python_docker", ok=True, error_class="")

    overview = app_module.quality_overview()
    assert "successful_routes" in overview
    assert overview["summary"]["successful_routes_total"] >= 1
    assert any(item.get("route") == "python_docker" for item in overview["successful_routes"])
