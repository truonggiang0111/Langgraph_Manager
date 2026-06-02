from langgraph_manager import native_runtime


def test_task_memory_context_prefers_relevant_entries(monkeypatch):
    fake_memories = [
        {"id": 1, "kind": "note", "title": "UI scroll fix", "content": "Fix chat scroll to bottom and avoid jump"},
        {"id": 2, "kind": "note", "title": "Docker health", "content": "Check container health endpoint"},
    ]
    monkeypatch.setattr(native_runtime.db, "list_memories", lambda: fake_memories)
    task = {"id": "T1", "name": "fix_chat_scroll", "tool": "workspace_diff"}
    context = {"intent": "engineering", "request": "fix chat scroll jump in UI"}

    picked = native_runtime._build_task_memory_context(task, context, limit=2)  # type: ignore[attr-defined]

    assert picked
    assert picked[0]["title"] == "UI scroll fix"


def test_task_skill_candidates_prefers_relevant_and_scored(monkeypatch):
    fake_skills = [
        {"name": "frontend-scroll-fix", "description": "Fix scroll behavior", "triggers": "scroll,ui,chat", "status": "active"},
        {"name": "docker-health-check", "description": "Container health checks", "triggers": "docker,health", "status": "active"},
    ]
    fake_perf = [
        {"skill_name": "frontend-scroll-fix", "score": 12.0, "trials": 8},
        {"skill_name": "docker-health-check", "score": 20.0, "trials": 20},
    ]
    monkeypatch.setattr(native_runtime.db, "list_skills", lambda include_inactive=False: fake_skills)
    monkeypatch.setattr(native_runtime.db, "list_skill_performance", lambda: fake_perf)
    task = {"id": "T1", "name": "fix_chat_scroll", "tool": "coding_agent_executor"}
    context = {"intent": "engineering", "request": "fix chat scroll jump in UI"}

    picked = native_runtime._build_task_skill_candidates(task, context, limit=2)  # type: ignore[attr-defined]

    assert picked
    assert picked[0]["name"] == "frontend-scroll-fix"
