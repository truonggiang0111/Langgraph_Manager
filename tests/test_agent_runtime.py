import sys

from langgraph_manager.agent_runtime import run_agent_job_state


def test_agent_runtime_reports_missing_coding_executor_for_explicit_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("CLAUDE_EXECUTOR_COMMAND", raising=False)
    monkeypatch.delenv("CODING_AGENT_COMMAND", raising=False)

    events = []
    state = run_agent_job_state(
        "agent_missing_claude",
        "dùng claude sửa code trong workspace",
        "",
        lambda step, status, detail: events.append((step, status, detail)),
    )

    assert state["verification"]["ok"] is False
    assert any(item.get("name") == "coding_agent_executor" and not item.get("ok") for item in state["tool_results"])
    assert "not configured" in state["result"]
    assert any(step == "coding_agent_executor" and status == "failed" for step, status, _ in events)


def test_agent_runtime_can_call_configured_coding_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_COMMAND", f"{sys.executable} -c \"import sys; print('executor got', len(sys.stdin.read()))\"")

    events = []
    state = run_agent_job_state(
        "agent_configured_claude",
        "dùng claude kiểm tra repo",
        "",
        lambda step, status, detail: events.append((step, status, detail)),
    )

    assert any(item.get("name") == "coding_agent_executor" and item.get("ok") for item in state["tool_results"])
    assert any(step == "coding_agent_executor" and status == "done" for step, status, _ in events)

