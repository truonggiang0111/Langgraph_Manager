from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager.app import app


def test_simple_chat_routes_direct_to_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not be called for direct executor route")

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        job = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{job['id']}/messages", json={"content": "kiểm tra giúp repo đang ở trạng thái nào"}).json()["job"]
        assert updated["pending_actions"]
        action = updated["pending_actions"][-1]
        assert action["kind"] == "coding_agent_executor"
        assert action["payload"]["request"] == "kiểm tra giúp repo đang ở trạng thái nào"


def test_composite_chat_routes_direct_to_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        raise AssertionError("Composite request should not call assistant model first")

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        job = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(
            f"/api/jobs/{job['id']}/messages",
            json={"content": "sửa bug UI chat và debug route scroll realtime giúp tôi"},
        ).json()["job"]
        assert updated["pending_actions"]
        action = updated["pending_actions"][-1]
        assert action["kind"] == "coding_agent_executor"
        assert "recommended_tools" not in action["payload"]
        assert "ordered_tool_names" not in action["payload"]
