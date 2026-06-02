import pytest

from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager.app import app


def test_direct_claude_decision_self_mode_omits_recommendations(monkeypatch):
    monkeypatch.setattr(app_module, "HYBRID_ROUTING_ENABLED", True)
    monkeypatch.setattr(app_module, "SKILL_DISCOVERY_MODE", "self")
    monkeypatch.setattr(
        app_module,
        "build_routing_plan",
        lambda query, task_complexity="simple", limit=6: {
            "primary": [{"name": "serena"}],
            "fallback": [{"name": "code-review"}],
            "ordered_tool_names": ["serena", "code-review"],
            "candidates": [{"name": "serena"}],
            "initial_tool_read_count": 1,
        },
    )
    decision = app_module.direct_claude_decision("fix api timeout", "claude", {"task_complexity": "simple"})
    payload = decision["action"]["payload"]
    assert payload["task_complexity"] == "simple"
    assert "recommended_tools" not in payload
    assert "ordered_tool_recommendations" not in payload


def test_direct_claude_decision_guided_mode_includes_recommendations(monkeypatch):
    monkeypatch.setattr(app_module, "HYBRID_ROUTING_ENABLED", True)
    monkeypatch.setattr(app_module, "SKILL_DISCOVERY_MODE", "guided")
    monkeypatch.setattr(
        app_module,
        "build_routing_plan",
        lambda query, task_complexity="simple", limit=6: {
            "primary": [{"name": "serena"}],
            "fallback": [{"name": "code-review"}],
            "ordered_tool_names": ["serena", "code-review"],
            "candidates": [{"name": "serena", "selection_score": 10}],
            "initial_tool_read_count": 1,
        },
    )
    decision = app_module.direct_claude_decision("fix api timeout", "claude", {"task_complexity": "composite"})
    payload = decision["action"]["payload"]
    assert payload["task_complexity"] == "composite"
    assert payload["recommended_tools"][0]["name"] == "serena"
    assert payload["ordered_tool_recommendations"][0]["name"] == "serena"
    assert payload["fallback_tool_candidates"][0]["name"] == "code-review"


def test_used_skills_feedback_updates_skill_score(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "HYBRID_ROUTING_ENABLED", False)

    def fake_run_tool(name, context):
        if name == "coding_agent_executor":
            return {
                "ok": True,
                "summary": "done",
                "data": {
                    "used_skills": ["serena"],
                    "output": '{"type":"result","result":"ok"}',
                    "command": "codex run",
                },
            }
        return {"ok": True, "summary": "ok", "data": {}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        job = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "sửa lỗi backend"}).json()["job"]
        pending = [item for item in job["pending_actions"] if item["status"] == "pending"]
        assert pending
        client.post(f"/api/actions/{pending[-1]['id']}/approve")

        perf = client.get("/api/skills/performance").json()["skill_performance"]
        row = next((item for item in perf if item.get("skill_name") == "serena"), None)
        assert row is not None
        assert int(row["trials"]) >= 1
        assert float(row["score"]) > 0
