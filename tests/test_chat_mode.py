import json
import pytest

from fastapi.testclient import TestClient

from langgraph_manager import app as app_module
from langgraph_manager.app import app, casual_reply, is_casual_chat


def test_casual_chat_detection():
    assert is_casual_chat("alo bạn nghe tôi nói k")
    assert casual_reply("alo bạn nghe tôi nói k").startswith("Mình nghe đây")
    assert "lập plan" in casual_reply("bạn có thể làm được gì")
    assert not is_casual_chat("sửa lỗi docker giúp tôi")


def test_create_casual_chat_job(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": False, "data": {}})
    with TestClient(app) as client:
        res = client.post("/api/jobs", json={"request": "alo bạn nghe tôi nói k"})
        assert res.status_code == 200
        job = res.json()["job"]
        assert job["status"] == "chat"
        detail = client.get(f"/api/jobs/{job['id']}").json()["job"]
        assert "Mình nghe đây" in detail["messages"][-1]["content"]


def test_chat_followup_answers_capability_question(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": False, "data": {}})
    with TestClient(app) as client:
        res = client.post("/api/jobs", json={"request": "alo bạn nghe tôi nói k"})
        job = res.json()["job"]
        followup = client.post(f"/api/jobs/{job['id']}/messages", json={"content": "bạn có thể làm được gì"})
        assert followup.status_code == 200
        messages = followup.json()["job"]["messages"]
        assert "Claude executor" in messages[-1]["content"]


def test_chat_mode_uses_model_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        assert "chat mode" in system_prompt
        assert messages[-1]["content"] == "alo bạn nghe tôi nói k"
        return {"ok": True, "data": {"content": "Mình nghe rõ, bạn cứ nói tiếp."}}

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)
    with TestClient(app) as client:
        res = client.post("/api/jobs", json={"request": "alo bạn nghe tôi nói k"})
        assert res.status_code == 200
        detail = client.get(f"/api/jobs/{res.json()['job']['id']}").json()["job"]
        assert detail["messages"][-1]["content"] == "Mình nghe rõ, bạn cứ nói tiếp."


def test_main_chat_is_continuous_and_uses_model(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    calls = []

    def fake_chat(messages, system_prompt=""):
        calls.append(messages[-1]["content"])
        return {"ok": True, "data": {"content": f"reply:{messages[-1]['content']}"}}

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)
    with TestClient(app) as client:
        main = client.get("/api/chat").json()["job"]
        assert main["id"] == "main_chat"
        assert main["status"] == "chat"
        assert main["messages"][0]["role"] == "langgraph"

        first = client.post("/api/chat/messages", json={"content": "câu 1"}).json()["job"]
        second = client.post("/api/chat/messages", json={"content": "câu 2"}).json()["job"]

        assert first["id"] == "main_chat"
        assert second["id"] == "main_chat"
        assert calls == ["câu 1", "câu 2"]
        assert [m["content"] for m in second["messages"][-4:]] == ["câu 1", "reply:câu 1", "câu 2", "reply:câu 2"]


def test_plan_from_chat_builds_onboarding_agent_flow_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("Onboarding agent flow should use the deterministic planner")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        planned = client.post(
            f"/api/jobs/{chat['id']}/plan-from-chat",
            json={"content": "Xay dung flow onboarding agent end-to-end gom backend UI test deploy checklist"},
        ).json()["job"]

        assert planned["status"] == "planned"
        assert planned["plan"] == app_module.ONBOARDING_AGENT_PLAN
        assert any("fake report" in step.lower() for step in planned["plan"])


def test_chat_agent_creates_pending_workspace_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        assert "trợ lý cá nhân" in system_prompt
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Mình cần chạy lệnh để kiểm tra.",'
                    '"action":{"kind":"workspace_command","title":"Check docker",'
                    '"preview":"docker ps --format ...",'
                    '"payload":{"command":"docker ps --format \'{{.Names}}\'","risk":"low","timeout":30}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "kiểm tra docker"}).json()["job"]

        assert updated["pending_actions"][0]["kind"] == "workspace_command"
        assert updated["pending_actions"][0]["status"] == "pending"
        assert "Action chờ duyệt" in updated["messages"][-1]["content"]


def test_chat_agent_parses_json_decision_wrapped_in_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        return {
            "ok": True,
            "data": {
                "content": (
                    'Được thôi anh. '
                    '{"mode":"pending_action","reply":"Em sẽ kiểm tra workspace.",'
                    '"action":{"kind":"workspace_command","title":"Kiểm tra workspace",'
                    '"preview":"pwd","payload":{"command":"pwd","risk":"medium"}}}\n\n'
                    "```json\n"
                    '{\n  "mode": "pending_action",\n  "reply": "Em sẽ kiểm tra workspace.",\n'
                    '  "action": {"kind": "workspace_command", "title": "Kiểm tra workspace", "preview": "pwd", "payload": {"command": "pwd", "risk": "medium"}}\n'
                    "}\n```"
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "kiểm tra workspace thử"}).json()["job"]

        assert updated["pending_actions"][0]["kind"] == "workspace_command"
        assert "```json" not in updated["messages"][-1]["content"]
        assert '"mode"' not in updated["messages"][-1]["content"]
        assert "Em sẽ kiểm tra workspace." in updated["messages"][-1]["content"]


def test_message_mentioning_claude_routes_directly_to_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not be called for direct Claude route")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "hãy gọi Claude kiểm tra repo giúp tôi"},
        ).json()["job"]

        action = updated["pending_actions"][0]
        assert action["kind"] == "coding_agent_executor"
        assert action["payload"]["request"] == "hãy gọi Claude kiểm tra repo giúp tôi"
        assert "chuyển thẳng" in updated["messages"][-1]["content"]


def test_cli_proxy_model_check_routes_directly_to_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not answer CLI proxy checks directly")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "check giúp tôi xem đang sài model gì trong CLI proxy"},
        ).json()["job"]

        action = updated["pending_actions"][0]
        assert action["kind"] == "coding_agent_executor"
        assert action["payload"]["request"] == "check giúp tôi xem đang sài model gì trong CLI proxy"
        assert "chuyển thẳng" in updated["messages"][-1]["content"]


@pytest.mark.parametrize(
    "content",
    [
        "dùng Serena MCP tìm hàm xử lý chat",
        "hãy dùng context7 xem docs FastAPI mới nhất",
        "task này cần plugin code-review kiểm tra",
        "cấu hình mcp/plugin cho executor",
    ],
)
def test_plugin_or_mcp_requests_route_directly_to_executor(tmp_path, monkeypatch, content):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not be called for plugin/MCP direct route")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": content}).json()["job"]

        action = updated["pending_actions"][0]
        assert action["kind"] == "coding_agent_executor"
        assert action["payload"]["request"] == content
        assert "plugin/MCP" in updated["messages"][-1]["content"]


@pytest.mark.parametrize(
    "content",
    [
        "tìm hàm xử lý chat trong repo này",
        "sửa lỗi UI scroll bị nhảy xuống cuối",
        "giao diện chat bị văng xuống cuối khi cuộn lên",
        "đọc PDF này rồi phân tích giúp tôi",
        "xem docs FastAPI version mới nhất cho endpoint upload file",
    ],
)
def test_mcp_suitable_work_routes_directly_to_executor_without_keyword(tmp_path, monkeypatch, content):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not be called for Claude/MCP-suitable work")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": content}).json()["job"]

        action = updated["pending_actions"][0]
        assert action["kind"] == "coding_agent_executor"
        assert action["payload"]["request"] == content


def test_manager_model_prompt_delegates_workspace_work_to_claude_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        assert "router nhẹ" in system_prompt
        assert "luôn trả về pending_action kind coding_agent_executor" in system_prompt
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Em sẽ giao việc này cho Claude executor.",'
                    '"action":{"kind":"coding_agent_executor","title":"Sửa lỗi giao diện",'
                    '"preview":"Sửa lỗi giao diện theo yêu cầu",'
                    '"payload":{"request":"sửa vấn đề hiển thị panel bên phải"}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "phần hiển thị panel bên phải chưa đúng mong muốn"},
        ).json()["job"]

        assert updated["pending_actions"][0]["kind"] == "coding_agent_executor"


def test_direct_claude_full_access_returns_claude_message_without_manager_model(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LANGGRAPH_INLINE_ACTIONS", "1")

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not summarize Claude executor output")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)
    monkeypatch.setattr(
        app_module,
        "run_tool",
        lambda name, context: {
            "ok": True,
            "summary": "Claude executor completed",
            "data": {"output": "Claude executor đang hoạt động."},
        },
    )

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "Claude hãy trả lời ping"},
        ).json()["job"]

        assert updated["pending_actions"][0]["status"] == "done"
        assert updated["messages"][-1]["role"] == "claude"
        assert updated["messages"][-1]["content"] == "Claude executor đang hoạt động."


def test_code_mode_endpoint_routes_to_claude_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    cache_root = tmp_path / "claude_home"
    for name in ["frontend-design", "serena", "code-review"]:
        plugin = cache_root / "plugins" / "cache" / "claude-plugins-official" / name / "1.0.0"
        manifest = plugin / ".claude-plugin"
        manifest.mkdir(parents=True)
        (manifest / "plugin.json").write_text(
            __import__("json").dumps({"name": name, "description": name}),
            encoding="utf-8",
        )
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(cache_root))
    monkeypatch.setenv("CODEGRAPH_ENABLED", "1")

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not be called for code mode")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/code",
            json={"content": "sửa lỗi UI chat bị nhảy scroll"},
        ).json()["job"]

        action = updated["pending_actions"][0]
        assert action["kind"] == "coding_agent_executor"
        assert action["payload"]["request"] == "sửa lỗi UI chat bị nhảy scroll"
        assert "intent" not in action["payload"]
        assert "task_complexity" not in action["payload"]
        assert "ordered_tool_names" not in action["payload"]
        assert "recommended_tools" not in action["payload"]
        assert "recommended_read_order" not in action["payload"]
        assert "baseline_tools" not in action["payload"]
        assert "Claude executor" in updated["messages"][-1]["content"]


def test_code_mode_marks_composite_task_when_request_spans_ui_and_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    cache_root = tmp_path / "claude_home"
    for name in ["frontend-design", "serena", "code-review"]:
        plugin = cache_root / "plugins" / "cache" / "claude-plugins-official" / name / "1.0.0"
        manifest = plugin / ".claude-plugin"
        manifest.mkdir(parents=True)
        (manifest / "plugin.json").write_text(
            __import__("json").dumps({"name": name, "description": name}),
            encoding="utf-8",
        )
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(cache_root))
    monkeypatch.setenv("CODEGRAPH_ENABLED", "1")

    def fail_if_called(messages, system_prompt=""):
        raise AssertionError("LangGraph model should not be called for code mode")

    monkeypatch.setattr(app_module, "cliproxy_chat", fail_if_called)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/code",
            json={"content": "sửa lỗi UI chat bị nhảy scroll realtime và trace route gây auto jump"},
        ).json()["job"]

        action = updated["pending_actions"][0]
        assert action["kind"] == "coding_agent_executor"
        assert "intent" not in action["payload"]
        assert "task_complexity" not in action["payload"]
        assert "complexity_score" not in action["payload"]


def test_skill_search_endpoint_returns_ranked_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    cache_root = tmp_path / "claude_home"
    plugin = cache_root / "plugins" / "cache" / "claude-plugins-official" / "frontend-design" / "1.0.0"
    manifest = plugin / ".claude-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(
        __import__("json").dumps({"name": "frontend-design", "description": "Frontend design skill"}),
        encoding="utf-8",
    )
    skill_dir = plugin / "skills" / "frontend-design"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Frontend Design\nUse this for frontend implementation.", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(cache_root))
    monkeypatch.setenv("CODEGRAPH_ENABLED", "0")

    with TestClient(app) as client:
        res = client.get("/api/claude/skill-search", params={"q": "frontend implementation"})

        assert res.status_code == 200
        body = res.json()
        assert body["results"][0]["name"] == "frontend-design"


def test_full_access_workspace_action_auto_runs_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã kiểm tra xong."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Cần chạy command.",'
                    '"action":{"kind":"workspace_command","title":"List files","preview":"ls",'
                    '"payload":{"command":"ls","cwd":"","risk":"low"}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "ran", "data": {"stdout": "ok"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "list file"}).json()["job"]

        assert captured["name"] == "workspace_executor"
        assert captured["context"]["permission_mode"] == "full_access"
        assert updated["pending_actions"][0]["status"] == "done"
        assert "Đã kiểm tra xong" in updated["messages"][-1]["content"]


def test_workspace_inspect_action_auto_runs_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã inspect workspace."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Mình sẽ inspect workspace trước.",'
                    '"action":{"kind":"workspace_inspect","title":"Inspect workspace","preview":"List markers/files",'
                    '"payload":{"max_files":50}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "inspected", "data": {"files": []}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "đọc repo này"}).json()["job"]

        assert captured["name"] == "workspace_inspect"
        assert captured["context"]["max_files"] == 50
        assert updated["pending_actions"][0]["status"] == "done"
        assert "Đã inspect workspace" in updated["messages"][-1]["content"]


@pytest.mark.skip(reason="legacy platform action removed from Claude-executor core")
def test_workspace_apply_patch_action_runs_tool_in_full_access(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã sửa file và sẽ chạy kiểm tra tiếp."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Mình sẽ patch file bằng exact replacement.",'
                    '"action":{"kind":"workspace_apply_patch","title":"Patch app.py","preview":"Replace old text",'
                    '"payload":{"path":"app.py","expected_sha256":"abc","edits":[{"old_text":"old","new_text":"new"}]}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "patched", "data": {"path": "app.py", "diff": "diff --git"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "sửa app.py"}).json()["job"]

        assert captured["name"] == "workspace_apply_patch"
        assert captured["context"]["path"] == "app.py"
        assert captured["context"]["edits"][0]["new_text"] == "new"
        assert updated["pending_actions"][0]["status"] == "done"


def test_large_action_result_is_stored_as_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã xử lý output lớn."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Chạy command.",'
                    '"action":{"kind":"workspace_command","title":"Large output","preview":"large",'
                    '"payload":{"command":"large","cwd":"","risk":"low"}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)
    monkeypatch.setattr(
        app_module,
        "run_tool",
        lambda name, context: {"ok": True, "summary": "large ok", "data": {"stdout": "x" * 5000}},
    )

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "large"}).json()["job"]

        result = updated["pending_actions"][0]["result"]
        parsed = __import__("json").loads(result)
        assert parsed["artifact"] is True
        assert parsed["artifact_url"].startswith("/artifacts/action_artifacts/action_")
        assert (tmp_path / "action_artifacts").exists()
        assert "x" * 200 not in result


def test_raw_html_manager_reply_is_sanitized():
    raw = "<!DOCTYPE html><html><head><title>clawflow.shop | 524: A timeout occurred</title></head><body>Error code: 524</body></html>"
    cleaned = app_module.sanitize_manager_content(raw)

    assert "<!DOCTYPE html>" not in cleaned
    assert "<html" not in cleaned
    assert "524" in cleaned
    assert "timeout" in cleaned.lower()


def test_pending_action_json_reply_is_sanitized():
    raw = (
        'prefix {"mode":"pending_action","reply":"Em sẽ gọi Claude kiểm tra.",'
        '"action":{"kind":"coding_agent_executor","title":"Gọi Claude","payload":{}}}'
    )
    cleaned = app_module.sanitize_manager_content(raw)

    assert cleaned == "Em sẽ gọi Claude kiểm tra."


def test_attachment_read_extracts_simple_pdf_text(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    pdf = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 72 720 Td (Hello PDF CV text) Tj ET
endstream
endobj
%%EOF
"""
    encoded = __import__("base64").b64encode(pdf).decode("ascii")
    with TestClient(app) as client:
        res = client.post(
            "/api/attachments/read",
            json={"name": "cv.pdf", "mime_type": "application/pdf", "content_base64": encoded},
        )
        assert res.status_code == 200
        body = res.json()
        assert "Hello PDF CV text" in body["text"]
        assert body["artifact_url"].startswith("/artifacts/session_attachments/")
        assert body["workspace_path"].startswith("/workspace/LangGraph_Manager/data/session_attachments/")
        assert (tmp_path / "session_attachments").exists()


def test_attachment_read_stores_unextractable_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    encoded = __import__("base64").b64encode(b"%PDF-1.4\nnot extractable\n%%EOF").decode("ascii")
    with TestClient(app) as client:
        res = client.post(
            "/api/attachments/read",
            json={"name": "scan.pdf", "mime_type": "application/pdf", "content_base64": encoded},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["text"] == ""
        assert body["warning"]
        assert body["artifact_url"].startswith("/artifacts/session_attachments/")


def test_attachment_delete_removes_stored_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    encoded = __import__("base64").b64encode(b"hello").decode("ascii")
    with TestClient(app) as client:
        res = client.post(
            "/api/attachments/read",
            json={"name": "note.txt", "mime_type": "text/plain", "content_base64": encoded, "job_id": "job_abc"},
        )
        path = res.json()["stored_path"]
        assert __import__("pathlib").Path(path).exists()
        deleted = client.post("/api/attachments/delete", json={"stored_path": path})
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True
        assert not __import__("pathlib").Path(path).exists()


def test_host_browser_ocr_extract_parses_multimodal_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_vision(prompt, image_base64, mime_type="image/png", system_prompt=""):
        assert image_base64 == "ZmFrZQ=="
        assert mime_type == "image/png"
        assert "user query" in prompt
        return {
            "ok": True,
            "data": {
                "content": '{"text":"Tuyển DevOps Intern tại HCM","confidence":"high","reason":"dom-thieu-nen-doc-anh"}'
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_vision", fake_vision)
    with TestClient(app) as client:
        res = client.post(
            "/api/host-browser/ocr-extract",
            json={
                "image_base64": "ZmFrZQ==",
                "mime_type": "image/png",
                "query": "tìm bài tuyển intern devops ở hcm",
                "title": "Facebook",
                "url": "https://facebook.com/search/posts?q=devops",
                "excerpt": "DOM ít chữ",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["data"]["text"] == "Tuyển DevOps Intern tại HCM"
        assert body["data"]["confidence"] == "high"


def test_delete_job_removes_session_attachments(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": False, "data": {}})
    encoded = __import__("base64").b64encode(b"hello").decode("ascii")
    with TestClient(app) as client:
        job = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        res = client.post(
            "/api/attachments/read",
            json={"name": "note.txt", "mime_type": "text/plain", "content_base64": encoded, "job_id": job["id"]},
        )
        path = __import__("pathlib").Path(res.json()["stored_path"])
        assert path.exists()
        assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
        assert not path.exists()


def test_full_access_follow_up_action_auto_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    calls = []

    def fake_chat(messages, system_prompt=""):
        latest = messages[-1]["content"]
        if "Action vừa được approve" in latest and len(calls) == 1:
            return {
                "ok": True,
                "data": {
                    "content": (
                        '{"mode":"pending_action","reply":"Chạy bước tiếp.",'
                        '"action":{"kind":"workspace_command","title":"Second","preview":"pwd",'
                        '"payload":{"command":"pwd","cwd":"","risk":"low"}}}'
                    )
                },
            }
        if "Action vừa được approve" in latest:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Hoàn tất cả hai bước."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Chạy bước đầu.",'
                    '"action":{"kind":"workspace_command","title":"First","preview":"ls",'
                    '"payload":{"command":"ls","cwd":"","risk":"low"}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        calls.append(context["command"])
        return {"ok": True, "summary": f"ran {context['command']}", "data": {"stdout": "ok"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "làm 2 bước"}).json()["job"]

        assert calls == ["ls", "pwd"]
        assert [a["status"] for a in updated["pending_actions"]] == ["done", "done"]
        assert "Hoàn tất cả hai bước" in updated["messages"][-1]["content"]


@pytest.mark.skip(reason="legacy browser action removed from Claude-executor core")
def test_safe_browser_action_auto_runs_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã mở web xong."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Cần mở trình duyệt.",'
                    '"action":{"kind":"browser_automation","title":"Open site","preview":"open example",'
                    '"payload":{"url":"https://example.com","steps":[{"action":"goto","url":"https://example.com"},{"action":"screenshot"}],"timeout":10}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "browser ok", "data": {"screenshots": ["/data/state/browser_artifacts/a.png"]}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        approved = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "mở example.com"}).json()["job"]

        assert captured["name"] == "browser_automation"
        assert captured["context"]["url"] == "https://example.com"
        assert captured["context"]["permission_mode"] == "auto_review"
        assert approved["pending_actions"][0]["status"] == "done"


@pytest.mark.skip(reason="legacy browser research fallback removed from Claude-executor core")
def test_url_research_request_falls_back_to_browser_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã đọc web và báo cáo."}'}}
        return {"ok": True, "data": {"content": '{"mode":"answer","content":"Mình đây. Bạn cứ nhắn tiếp."}'}}

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "browser ok", "data": {"text": "Careers JD"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "full_access"}).json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "tìm hiểu JD trong https://goldenowl.asia/careers rồi báo cáo"},
        ).json()["job"]

        assert captured["name"] == "browser_automation"
        assert captured["context"]["url"] == "https://goldenowl.asia/careers"
        assert updated["pending_actions"][0]["status"] == "done"


@pytest.mark.skip(reason="legacy web research action removed from Claude-executor core")
def test_web_research_request_without_url_auto_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã tìm được nguồn."}'}}
        return {"ok": True, "data": {"content": '{"mode":"answer","content":"Mình đây."}'}}

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {
            "ok": True,
            "summary": "Found 2 result(s), read 1 page(s)",
            "data": {"results": [{"title": "Result", "url": "https://example.com"}], "text": "Research text"},
        }

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "lên web research công ty Golden Owl giúp tôi"},
        ).json()["job"]

        assert captured["name"] == "web_research"
        assert "Golden Owl" in captured["context"]["query"]
        assert updated["pending_actions"][0]["kind"] == "web_research"
        assert updated["pending_actions"][0]["status"] == "done"


@pytest.mark.skip(reason="legacy web research action removed from Claude-executor core")
def test_agent_mcp_research_phrase_auto_runs_web_research(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Agent MCP có ích khi cần tool bridge."}'}}
        return {"ok": True, "data": {"content": '{"mode":"answer","content":"Mình đây."}'}}

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "data": {"results": [], "answer_context": "mcp context"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "tìm hiểu kĩ về agent mcp có giúp gì cho bạn không"},
        ).json()["job"]

        assert captured["name"] == "web_research"
        assert "agent mcp" in captured["context"]["query"].lower()
        assert updated["pending_actions"][0]["status"] == "done"
        assert "Agent MCP có ích" in updated["messages"][-1]["content"]


@pytest.mark.skip(reason="legacy web research action removed from Claude-executor core")
def test_do_it_followup_after_public_research_suggestion_runs_web_research(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã đọc sâu các repo."}'}}
        return {"ok": True, "data": {"content": '{"mode":"answer","content":"Mình đây."}'}}

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "data": {"results": [], "answer_context": "repo context"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        app_module.db.add_message(
            chat["id"],
            "manager",
            "Bước tiếp theo nên làm: đọc sâu 3 repo GitHub về Agent MCP rồi báo cáo.",
        )
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "làm đi"}).json()["job"]

        assert captured["name"] == "web_research"
        assert "Agent MCP" in captured["context"]["query"]
        assert updated["pending_actions"][0]["kind"] == "web_research"


def test_sensitive_browser_action_stays_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Cần đăng nhập.",'
                    '"action":{"kind":"browser_automation","title":"Login","preview":"login gmail",'
                    '"payload":{"url":"https://mail.google.com","steps":[{"action":"goto","url":"https://mail.google.com"},{"action":"fill","selector":"input[type=email]","value":"x"}],"timeout":10}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)
    monkeypatch.setattr(app_module, "run_tool", lambda name, context: captured.setdefault("name", name))

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "đăng gmail"}).json()["job"]

        assert updated["pending_actions"][0]["status"] == "pending"
        assert captured == {}


@pytest.mark.skip(reason="legacy browser action removed from Claude-executor core")
def test_quality_control_job_page_auto_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã đọc các JD intern."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Mình sẽ đọc JD công khai.",'
                    '"action":{"kind":"browser_automation","title":"Mở chi tiết JD Intern","preview":"Đọc Quality Control Intern và các JD intern",'
                    '"payload":{"url":"https://goldenowl.asia/careers","steps":[{"action":"goto","url":"https://goldenowl.asia/careers"},{"action":"click","selector":"text=Quality Control Intern"},{"action":"extract_text","selector":"body"}],"timeout":20}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "read jd", "data": {"text": "Quality Control Intern JD"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "Mở chi tiết JD các vị trí Intern Golden Owl"}).json()["job"]

        assert captured["name"] == "browser_automation"
        assert updated["pending_actions"][0]["status"] == "done"


@pytest.mark.skip(reason="legacy browser action removed from Claude-executor core")
def test_public_browser_enter_navigation_auto_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã mở chi tiết JD."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Mở trang JD công khai.",'
                    '"action":{"kind":"browser_automation","title":"Mở JD","preview":"Mở JD bằng Enter",'
                    '"payload":{"url":"https://goldenowl.asia/careers","steps":[{"action":"goto","url":"https://goldenowl.asia/careers"},{"action":"press","selector":"text=View details","key":"Enter"},{"action":"extract_text","selector":"body"}],"timeout":20}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "opened jd", "data": {"text": "JD detail"}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "mở chi tiết JD Golden Owl"}).json()["job"]

        assert captured["name"] == "browser_automation"
        assert updated["pending_actions"][0]["status"] == "done"


@pytest.mark.skip(reason="legacy host browser action removed from Claude-executor core")
def test_safe_host_browser_action_auto_runs_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {"ok": True, "data": {"content": '{"mode":"answer","content":"Đã kiểm tra browser host."}'}}
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Cần kiểm tra browser trên máy host.",'
                    '"action":{"kind":"browser_host","title":"List host browsers","preview":"list",'
                    '"payload":{"action":"list","timeout":5}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    def fake_run_tool(name, context):
        captured["name"] = name
        captured["context"] = context
        return {"ok": True, "summary": "found browsers", "data": {"browsers": []}}

    monkeypatch.setattr(app_module, "run_tool", fake_run_tool)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        approved = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "xem browser host"}).json()["job"]

        assert captured["name"] == "browser_host"
        assert captured["context"]["action"] == "list"
        assert captured["context"]["permission_mode"] == "auto_review"
        assert approved["pending_actions"][0]["status"] == "done"


def test_natural_language_facebook_research_routes_to_host_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_host_browser_call(path, payload=None, method="POST", timeout=30):
        captured["path"] = path
        captured["payload"] = payload or {}
        return {
            "ok": True,
            "result": {
                "total_found": 2,
                "filtered_count": 1,
                "items": [
                    {
                        "author": "DevOps VietNam",
                        "url": "https://facebook.com/example",
                        "excerpt": "Tuyen DevOps Intern tai HCM",
                    }
                ],
            },
        }

    monkeypatch.setattr(app_module, "host_browser_call", fake_host_browser_call)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "lên facebook tìm bài tuyển devops intern hcm và trả link cho mình"},
        ).json()["job"]
        assert updated["pending_actions"][0]["kind"] == "host_browser_facebook_research"
        assert "devops intern hcm" in str(updated["pending_actions"][0]["payload"].get("query") or "").lower()
        assert updated["pending_actions"][0]["status"] == "pending"

        approved = client.post(f"/api/actions/{updated['pending_actions'][0]['id']}/approve").json()["job"]

        assert captured["path"] == "/facebook-research"
        assert "devops intern hcm" in str(captured["payload"].get("query") or "").lower()
        assert not any(action["kind"] == "host_browser_facebook_research" and action["status"] == "pending" for action in approved["pending_actions"])


def test_natural_language_face_alias_routes_to_host_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_host_browser_call(path, payload=None, method="POST", timeout=30):
        captured["path"] = path
        captured["payload"] = payload or {}
        return {"ok": True, "result": {"total_found": 0, "filtered_count": 0, "items": []}}

    monkeypatch.setattr(app_module, "host_browser_call", fake_host_browser_call)

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "auto_review"}).json()["job"]
        updated = client.post(
            f"/api/jobs/{chat['id']}/messages",
            json={"content": "tìm cho tôi trên face 5 bài tuyển dụng devops hcm"},
        ).json()["job"]
        assert updated["pending_actions"][0]["kind"] == "host_browser_facebook_research"
        approved = client.post(f"/api/actions/{updated['pending_actions'][0]['id']}/approve").json()["job"]
        assert captured["path"] == "/facebook-research"
        assert "devops hcm" in str(captured["payload"].get("query") or "").lower()
        assert approved["messages"][-1]["content"]


def test_face_query_is_sanitized_before_host_browser_search():
    decision = app_module.direct_host_browser_decision(
        "tìm cho tôi trên face 5 bài tuyển dụng devops hcm 1 tháng đổ lại đây"
    )
    assert decision is not None
    payload = decision["action"]["payload"]
    assert payload["query"] == "tuyển dụng devops hcm 1 tháng đổ lại đây"
    assert "tìm cho tôi" not in payload["query"].lower()
    assert "trên face" not in payload["query"].lower()


def test_host_browser_facebook_rerank_uses_model_to_reorder_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        assert "xếp hạng candidate bài Facebook" in system_prompt
        assert "tìm trọ quận 7 dưới 5 triệu" in messages[-1]["content"]
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"intent":"tìm trọ quận 7 dưới 5 triệu",'
                    '"criteria":["đúng nhu cầu thuê trọ","ưu tiên giá và khu vực rõ","ưu tiên bài mới"],'
                    '"summary":"Ưu tiên bài cho thuê thật có giá và vị trí cụ thể.",'
                    '"ranked_items":['
                    '{"index":1,"score":9.8,"verdict":"strong","reason":"Bài cho thuê thật, có giá và quận cụ thể."},'
                    '{"index":0,"score":2.1,"verdict":"weak","reason":"Đây là người đang đi tìm phòng, không phải bài cho thuê."}'
                    ']}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        response = client.post(
            "/api/host-browser/facebook-rerank",
            json={
                "query": "tìm trọ quận 7 dưới 5 triệu",
                "top_k": 2,
                "items": [
                    {
                        "url": "https://facebook.com/post-seeking-room",
                        "author": "Minh",
                        "text": "Mình đang tìm phòng trọ quận 7, ngân sách dưới 5 triệu.",
                        "excerpt": "Mình đang tìm phòng trọ quận 7.",
                        "time_hint": "1 giờ",
                    },
                    {
                        "url": "https://facebook.com/post-rental",
                        "author": "Cho thuê trọ Quận 7",
                        "text": "Cho thuê phòng trọ quận 7, gần Lotte, giá 4.8 triệu, có nội thất cơ bản.",
                        "excerpt": "Cho thuê phòng trọ quận 7 giá 4.8 triệu.",
                        "time_hint": "2 giờ",
                    },
                ],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["data"]["intent"] == "tìm trọ quận 7 dưới 5 triệu"
        assert body["data"]["ranked_items"][0]["index"] == 1
        assert body["data"]["ranked_items"][0]["verdict"] == "strong"
        assert body["data"]["ranked_items"][1]["index"] == 0


def test_host_browser_facebook_search_plan_expands_query_variants(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        assert "search planner cho trợ lý Facebook research" in system_prompt
        assert "tuyển intern devops hcm" in messages[-1]["content"]
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"intent":"tìm bài tuyển dụng devops intern tại HCM",'
                    '"criteria":["đúng bài tuyển thật","đúng role tương đương","đúng location/seniority"],'
                    '"query_variants":["devops intern hcm","thực tập sinh devops tphcm","cloud intern ho chi minh","sre fresher sai gon"],'
                    '"negative_signals":["bài ứng viên đi tìm việc","bài lệch chủ đề"],'
                    '"profile":{"original_query":"tuyển intern devops hcm","normalized_query":"tuyển intern devops hcm","roleTerms":["devops","sre","cloud"],"seniorityTerms":["intern","fresher"],"locationTerms":["hcm","tphcm","ho chi minh"],"mustIncludeGroups":[["devops","sre","cloud"],["intern","fresher"],["hcm","tphcm","ho chi minh"]]},'
                    '"summary":"Mở rộng query theo role tương đương và alias location."}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        response = client.post(
            "/api/host-browser/facebook-search-plan",
            json={"query": "tuyển intern devops hcm", "max_queries": 5},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["data"]["intent"] == "tìm bài tuyển dụng devops intern tại HCM"
        assert "devops intern hcm" in body["data"]["query_variants"]
        assert "cloud intern ho chi minh" in body["data"]["query_variants"]
        assert "sre" in " ".join(body["data"]["profile"]["roleTerms"]).lower()


def test_host_browser_search_plan_sanitizes_natural_language_query(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        app_module,
        "cliproxy_chat",
        lambda messages, system_prompt="": {"ok": False, "summary": "planner unavailable"},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/host-browser/facebook-search-plan",
            json={"query": "tìm cho tôi trên face 5 bài tuyển dụng devops hcm 1 tháng đổ lại đây", "max_queries": 5},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["data"]["query_variants"][0] == "tuyển dụng devops hcm 1 tháng đổ lại đây"
        assert "tìm cho tôi" not in body["data"]["query_variants"][0].lower()
        assert "trên face" not in body["data"]["query_variants"][0].lower()


def test_format_facebook_research_message_returns_clean_ranked_list():
    payload = {
        "ok": True,
        "data": {
            "summary": {
                "recent_confirmed_count": 2,
                "time_unknown_count": 1,
            },
            "results": [
                {
                    "author": "DevOps VietNam",
                    "summary": "[Q7 - HCM] Cloudteam tuyển System & DevOps (Intern)",
                    "url": "https://facebook.com/post1",
                    "time_status": "recent_confirmed",
                    "keep_reason": "Đúng DevOps intern tại HCM, có mô tả rõ.",
                },
                {
                    "author": "Tuyển dụng DevOps",
                    "summary": "DEVOPS INTERN - TMA Tech Group",
                    "url": "https://facebook.com/post2",
                    "time_status": "time_unknown",
                    "keep_reason": "Đúng role nhưng chưa xác nhận được ngày.",
                },
            ],
        },
    }

    text = app_module.format_facebook_research_message(json.dumps(payload, ensure_ascii=False))
    assert "2 bài xác nhận còn mới" in text
    assert "1. DevOps VietNam" in text
    assert "Nội dung chính: [Q7 - HCM] Cloudteam tuyển System & DevOps (Intern)" in text
    assert "Lý do giữ: Đúng DevOps intern tại HCM, có mô tả rõ." in text
    assert "Link: https://facebook.com/post1" in text
    assert "Thời gian: đã xác nhận còn mới" in text


def test_format_facebook_research_message_empty_explains_why():
    payload = {
        "ok": True,
        "data": {
            "total_found": 12,
            "filtered_count": 0,
            "summary": {
                "time_window_label": "1m",
                "recent_confirmed_count": 0,
                "time_unknown_count": 4,
                "stale_confirmed_count": 2,
                "planner_summary": "Đã mở rộng query và lọc theo thời gian.",
            },
            "items": [],
            "results": [],
        },
    }
    text = app_module.format_facebook_research_message(json.dumps(payload, ensure_ascii=False))
    assert "Đã quét 12 candidate" in text
    assert "Điều kiện thời gian đang bật: `1m`" in text


def test_format_facebook_research_message_accepts_live_result_shape():
    payload = {
        "ok": True,
        "result": {
            "total_found": 40,
            "filtered_count": 23,
            "summary": {
                "recent_confirmed_count": 0,
                "time_unknown_count": 0,
                "stale_confirmed_count": 0,
            },
            "results": [
                {
                    "author": "Devops tuyển dụng",
                    "summary": "Tuyển DevOps Engineer tại Thủ Đức TP.HCM, onsite, up to 28M NET.",
                    "url": "https://facebook.com/post-live",
                    "time_status": "none",
                    "keep_reason": "Có địa điểm HCM rất rõ và JD khá cụ thể.",
                }
            ],
        },
    }

    text = app_module.format_facebook_research_message(json.dumps(payload, ensure_ascii=False))
    assert "Trả về 1 bài phù hợp nhất" in text
    assert "1. Devops tuyển dụng" in text
    assert "Nội dung chính: Tuyển DevOps Engineer tại Thủ Đức TP.HCM, onsite, up to 28M NET." in text
    assert "Lý do giữ: Có địa điểm HCM rất rõ và JD khá cụ thể." in text
    assert "Link: https://facebook.com/post-live" in text


def test_approve_action_can_queue_next_agent_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        if "Action vừa được approve" in messages[-1]["content"]:
            return {
                "ok": True,
                "data": {
                    "content": (
                        '{"mode":"pending_action","reply":"Cần kiểm tra tiếp.",'
                        '"action":{"kind":"workspace_command","title":"Run tests","preview":"python -m pytest",'
                        '"payload":{"command":"python -m pytest","risk":"low"}}}'
                    )
                },
            }
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Cần list file.",'
                    '"action":{"kind":"workspace_command","title":"List files","preview":"ls",'
                    '"payload":{"command":"ls","risk":"low"}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)
    monkeypatch.setattr(app_module, "run_tool", lambda name, context: {"ok": True, "summary": "ran", "data": {"stdout": "ok"}})

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "kiểm tra project"}).json()["job"]
        action_id = updated["pending_actions"][0]["id"]
        approved = client.post(f"/api/actions/{action_id}/approve").json()["job"]

        assert [a["status"] for a in approved["pending_actions"]] == ["done", "pending"]
        assert approved["pending_actions"][1]["title"] == "Run tests"


def test_failed_action_can_queue_recovery_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_chat(messages, system_prompt=""):
        latest = messages[-1]["content"]
        if "chạy lỗi hoặc bị policy/permission chặn" in latest:
            return {
                "ok": True,
                "data": {
                    "content": (
                        '{"mode":"pending_action","reply":"Command bị chặn, mình thử hướng chỉ đọc trước.",'
                        '"action":{"kind":"workspace_command","title":"Inspect current user","preview":"id && pwd",'
                        '"payload":{"command":"id && pwd","risk":"low"}}}'
                    )
                },
            }
        return {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Cần cài package.",'
                    '"action":{"kind":"workspace_command","title":"Install package","preview":"apt install",'
                    '"payload":{"command":"apt-get install -y ffmpeg","risk":"high"}}}'
                )
            },
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)
    monkeypatch.setattr(
        app_module,
        "run_tool",
        lambda name, context: {"ok": False, "summary": "Permission gate blocked command: default_permissions allows read-only commands only"},
    )

    with TestClient(app) as client:
        chat = client.post("/api/chats", json={"permission_mode": "default_permissions"}).json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "cài ffmpeg"}).json()["job"]
        action_id = updated["pending_actions"][0]["id"]
        approved = client.post(f"/api/actions/{action_id}/approve").json()["job"]

        assert [a["status"] for a in approved["pending_actions"]] == ["failed", "pending"]
        recovery = approved["pending_actions"][1]
        assert recovery["title"] == "Inspect current user"
        assert recovery["payload"]["recovery_of"] == action_id
        assert "hướng chỉ đọc" in approved["messages"][-1]["content"]


def test_failed_action_recovery_guard_stops_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        app_module,
        "cliproxy_chat",
        lambda messages, system_prompt="": {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Thử recovery.",'
                    '"action":{"kind":"workspace_command","title":"Retry","preview":"retry",'
                    '"payload":{"command":"retry","risk":"low"}}}'
                )
            },
        },
    )
    monkeypatch.setattr(app_module, "run_tool", lambda name, context: {"ok": False, "summary": "permission denied"})

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        job = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "làm việc cần quyền"}).json()["job"]

        for _ in range(app_module.MAX_ACTION_RECOVERY_DEPTH + 1):
            pending = [a for a in job["pending_actions"] if a["status"] == "pending"]
            assert pending
            job = client.post(f"/api/actions/{pending[-1]['id']}/approve").json()["job"]

        assert len([a for a in job["pending_actions"] if a["status"] == "pending"]) == 0
        assert "tránh lặp vô hạn" in job["messages"][-1]["content"]


def test_chat_routes_work_to_coding_executor_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        first = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "nhớ repo này"}).json()["job"]
        pending = [a for a in first["pending_actions"] if a["status"] == "pending"]
        assert pending
        assert pending[-1]["kind"] == "coding_agent_executor"


def test_reject_pending_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        app_module,
        "cliproxy_chat",
        lambda messages, system_prompt="": {
            "ok": True,
            "data": {
                "content": (
                    '{"mode":"pending_action","reply":"Cần gửi email.",'
                    '"action":{"kind":"send_email","title":"Send draft","preview":"to user",'
                    '"payload":{"to":"a@example.com","subject":"Hi","body":"Hello"}}}'
                )
            },
        },
    )

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "soạn email"}).json()["job"]
        action_id = updated["pending_actions"][0]["id"]
        rejected = client.post(f"/api/actions/{action_id}/reject").json()["job"]

        assert rejected["pending_actions"][0]["status"] == "rejected"
        assert "Đã reject action" in rejected["messages"][-1]["content"]


def test_plan_from_existing_chat_session_keeps_context(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_chat(messages, system_prompt=""):
        captured["request"] = messages[-1]["content"]
        return {
            "ok": True,
            "data": {"content": '{"mode":"plan","plan":["Inspect context","Run requested work"],"summary":"ready"}'},
        }

    monkeypatch.setattr(app_module, "cliproxy_chat", fake_chat)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "mình muốn sửa UI"})
        planned = client.post(f"/api/jobs/{chat['id']}/plan-from-chat", json={"content": "giờ lên plan làm việc này"}).json()["job"]

        assert planned["id"] == chat["id"]
        assert planned["status"] == "planned"
        assert planned["plan"] == ["Inspect context", "Run requested work"]
        assert "mình muốn sửa UI" in captured["request"]
        assert "mình muốn sửa UI" in planned["request"]
        assert "giờ lên plan làm việc này" in planned["request"]
        assert planned["messages"][-1]["role"] == "langgraph"


def test_plan_from_chat_asks_questions_when_model_needs_input(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    monkeypatch.setattr(
        app_module,
        "cliproxy_chat",
        lambda messages, system_prompt="": {
            "ok": True,
            "data": {"content": '{"mode":"questions","questions":["Đã có project chưa?","Muốn dùng stack nào?"],"reason":"missing"}'},
        },
    )
    monkeypatch.setattr(app_module, "fetch_source_excerpt", lambda source: "repo readme excerpt")

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        planned = client.post(f"/api/jobs/{chat['id']}/plan-from-chat", json={"content": "tạo tool multi device"}).json()["job"]

        assert planned["status"] == "needs_input"
        assert planned["plan"] == []
        assert "Đã có project chưa" in planned["messages"][-1]["content"]
        assert "Muốn dùng stack nào" in planned["messages"][-1]["content"]


def test_run_chat_plan_uses_discussion_when_original_request_was_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_run(job_id, request, discussion, callback, permission_mode="auto_review", focus_files=None):
        captured["request"] = request
        captured["discussion"] = discussion
        captured["permission_mode"] = permission_mode
        callback("execute_worker_steps", "done", "ok")
        return {"result": "ran\nVerify: OK", "verification": {"ok": True}}

    monkeypatch.setattr(
        app_module,
        "cliproxy_chat",
        lambda messages, system_prompt="": {"ok": True, "data": {"content": '{"mode":"plan","plan":["Plan from chat"]}'}},
    )
    monkeypatch.setattr(app_module, "run_native_job_state", fake_run)

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "tạo tool multi device"})
        client.post(f"/api/jobs/{chat['id']}/plan-from-chat", json={"content": "lên plan rồi chạy"})
        client.post(f"/api/jobs/{chat['id']}/approve")
        ran = client.post(f"/api/jobs/{chat['id']}/run").json()["job"]

        assert ran["status"] == "done"
        assert captured["permission_mode"] == "auto_review"
        assert "tạo tool multi device" in captured["request"]
        assert "lên plan rồi chạy" in captured["request"]
        assert "tạo tool multi device" in captured["discussion"]


def test_chat_stream_emits_delta_and_persists_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "cliproxy_chat_stream", lambda messages, system_prompt="": iter(["Xin ", "chào"]))

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        with client.stream("POST", f"/api/jobs/{chat['id']}/messages/stream", json={"content": "alo"}) as res:
            body = "".join(res.iter_text())

        assert res.status_code == 200
        assert "event: delta" in body
        assert '"delta": "Xin "' in body
        assert "event: done" in body
        detail = client.get(f"/api/jobs/{chat['id']}").json()["job"]
        assert detail["messages"][-1]["content"] == "Xin chào"


def test_delete_chat_session_removes_it_from_sidebar_list(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        assert client.get(f"/api/jobs/{chat['id']}").status_code == 200

        deleted = client.delete(f"/api/jobs/{chat['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True
        assert client.get(f"/api/jobs/{chat['id']}").status_code == 404
        ids = [job["id"] for job in client.get("/api/jobs").json()["jobs"]]
        assert chat["id"] not in ids


def test_chat_session_can_be_renamed_and_auto_titled(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": True, "data": {"content": '{"mode":"answer","content":"ok"}'}})

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        assert chat["title"] == "New session"

        auto_titled = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "Build trợ lý Gmail"}).json()["job"]
        assert auto_titled["title"] == "Build trợ lý Gmail"

        renamed = client.patch(f"/api/jobs/{chat['id']}/title", json={"title": "Gmail assistant"}).json()["job"]
        assert renamed["title"] == "Gmail assistant"

        detail = client.get(f"/api/jobs/{chat['id']}").json()["job"]
        assert detail["title"] == "Gmail assistant"


def test_job_permission_mode_can_be_set_and_passed_to_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_run(job_id, request, discussion, callback, permission_mode="auto_review", focus_files=None):
        captured["permission_mode"] = permission_mode
        callback("execute_worker_steps", "done", permission_mode)
        return {"result": f"mode:{permission_mode}\nVerify: OK", "verification": {"ok": True}}

    monkeypatch.setattr(app_module, "run_native_job_state", fake_run)

    with TestClient(app) as client:
        created = client.post(
            "/api/jobs",
            json={"request": "build testable workflow", "permission_mode": "full_access"},
        ).json()["job"]
        assert created["permission_mode"] == "full_access"

        updated = client.patch(
            f"/api/jobs/{created['id']}/permission-mode",
            json={"permission_mode": "default_permissions"},
        ).json()["job"]
        assert updated["permission_mode"] == "default_permissions"

        client.post(f"/api/jobs/{created['id']}/approve")
        ran = client.post(f"/api/jobs/{created['id']}/run").json()["job"]

        assert ran["status"] == "done"
        assert captured["permission_mode"] == "default_permissions"
        assert "mode:default_permissions" in ran["result"]


def test_run_marks_failed_when_native_verification_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    def fake_run(job_id, request, discussion, callback, permission_mode="auto_review", focus_files=None):
        callback("verify_result", "done", "ok=False")
        return {"result": "Verify: NOT OK", "verification": {"ok": False}}

    monkeypatch.setattr(app_module, "run_native_job_state", fake_run)

    with TestClient(app) as client:
        created = client.post("/api/jobs", json={"request": "build testable workflow"}).json()["job"]
        client.post(f"/api/jobs/{created['id']}/approve")
        ran = client.post(f"/api/jobs/{created['id']}/run").json()["job"]

        assert ran["status"] == "failed"
        assert "Verify: NOT OK" in ran["result"]
        assert "verification did not pass" in ran["error"]


def test_focus_file_is_included_when_running_chat_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_run(job_id, request, discussion, callback, permission_mode="auto_review", focus_files=None):
        captured["discussion"] = discussion
        captured["focus_files"] = focus_files
        callback("verify_result", "done", "ok=True")
        return {"result": "Verify: OK", "verification": {"ok": True}}

    monkeypatch.setattr(app_module, "run_native_job_state", fake_run)
    monkeypatch.setattr(
        app_module,
        "cliproxy_chat",
        lambda messages, system_prompt="": {"ok": True, "data": {"content": '{"mode":"plan","plan":["Use focus file"]}' }},
    )

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        focused = client.post(
            f"/api/jobs/{chat['id']}/focus-file",
            json={"name": "notes.txt", "content": "important local context"},
        ).json()["job"]
        assert focused["focus_files"][0]["name"] == "notes.txt"
        client.post(f"/api/jobs/{chat['id']}/plan-from-chat", json={"content": "làm theo file này"})
        client.post(f"/api/jobs/{chat['id']}/approve")
        client.post(f"/api/jobs/{chat['id']}/run")

        assert "important local context" in captured["discussion"]
        assert captured["focus_files"] == [{"name": "notes.txt", "content": "important local context"}]


def test_memory_and_skill_crud_and_runtime_context(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    captured = {}

    def fake_run(job_id, request, discussion, callback, permission_mode="auto_review", focus_files=None):
        captured["discussion"] = discussion
        callback("verify_result", "done", "ok=True")
        return {"result": "Verify: OK", "verification": {"ok": True}}

    monkeypatch.setattr(app_module, "run_native_job_state", fake_run)

    with TestClient(app) as client:
        memory = client.post(
            "/api/memories",
            json={"kind": "project", "title": "LangGraph path", "content": "Project lives at D:/User/File/LangGraph_Manager", "tags": "langgraph"},
        ).json()["memory"]
        skill = client.post(
            "/api/skills",
            json={
                "name": "Cloudflare Tunnel",
                "description": "Publish local apps through Cloudflare Tunnel",
                "triggers": "cloudflare,tunnel,domain",
                "source": "manual",
                "body": "# Cloudflare Tunnel\nUse existing tunnel before creating a new one.",
            },
        ).json()["skill"]
        assert memory["title"] == "LangGraph path"
        assert skill["name"] == "Cloudflare Tunnel"

        created = client.post("/api/jobs", json={"request": "fix docker workflow"}).json()["job"]
        client.post(f"/api/jobs/{created['id']}/approve")
        client.post(f"/api/jobs/{created['id']}/run")

        assert "long_term_memory" in captured["discussion"]
        assert "Project lives at D:/User/File/LangGraph_Manager" in captured["discussion"]
        assert "available_skills" not in captured["discussion"]

        assert client.delete(f"/api/memories/{memory['id']}").status_code == 200
        assert client.delete(f"/api/skills/{skill['id']}").status_code == 200


def test_slash_command_creates_reusable_pending_action(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        command = client.post(
            "/api/commands",
            json={
                "name": "check",
                "description": "Run check command",
                "action_kind": "workspace_command",
                "payload": {"command": "echo {{input}}", "risk": "low"},
            },
        ).json()["command"]
        assert command["name"] == "check"

        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "/check hello"}).json()["job"]

        assert updated["pending_actions"][0]["kind"] == "workspace_command"
        assert updated["pending_actions"][0]["payload"]["command"] == "echo hello"
        assert "Action chờ duyệt" in updated["messages"][-1]["content"]


def test_project_memory_and_role_plugins_are_in_context(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    with TestClient(app) as client:
        client.post(
            "/api/projects",
            json={"name": "Main project", "root_path": "D:/repo", "summary": "Build assistant", "memory": "Run pytest before done"},
        )
        client.post(
            "/api/role-plugins",
            json={"name": "Ops cowork", "role": "ops", "instructions": "Check Docker and Cloudflare first", "tool_hints": "docker,cloudflare"},
        )

        context = app_module.memory_context()
        assert "project_memory" in context
        assert "Run pytest before done" in context
        assert "role_plugins" in context
        assert "Ops cowork" in context


@pytest.mark.skip(reason="legacy recurring task action removed from Claude-executor core")
def test_create_recurring_task_action_and_due_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LANGGRAPH_DISABLE_SCHEDULER", "1")
    responses = iter(
        [
            (
                '{"mode":"pending_action","reply":"Tạo task định kỳ.",'
                '"action":{"kind":"create_recurring_task","title":"Morning report","preview":"daily",'
                '"payload":{"name":"Morning report","prompt":"Tổng hợp mỗi sáng","schedule":"daily","action_kind":"note","payload":{"note":"run report"},"next_run_at":"2000-01-01T00:00:00+00:00"}}}'
            ),
            '{"mode":"answer","content":"Đã tạo task định kỳ."}',
        ]
    )
    monkeypatch.setattr(app_module, "cliproxy_chat", lambda messages, system_prompt="": {"ok": True, "data": {"content": next(responses)}})

    with TestClient(app) as client:
        chat = client.post("/api/chats").json()["job"]
        updated = client.post(f"/api/jobs/{chat['id']}/messages", json={"content": "tạo báo cáo định kỳ"}).json()["job"]
        client.post(f"/api/actions/{updated['pending_actions'][0]['id']}/approve")
        tasks = client.get("/api/recurring-tasks").json()["recurring_tasks"]
        assert tasks[0]["name"] == "Morning report"

        queued = client.post("/api/recurring-tasks/run-due").json()["queued"]
        assert queued[0]["task_id"] == tasks[0]["id"]
        due_job = client.get(f"/api/jobs/{queued[0]['job_id']}").json()["job"]
        assert due_job["pending_actions"][0]["title"] == "Recurring: Morning report"


def test_skill_research_creates_skill_and_memory_suggestion(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))

    monkeypatch.setattr(
        app_module,
        "cliproxy_chat",
        lambda messages, system_prompt="": {
            "ok": True,
            "data": {
                "content": (
                    '{"name":"Repo Reviewer","description":"Review repos","triggers":"review,repo",'
                    '"body":"# Repo Reviewer\\nRead files then test.",'
                    '"memory_suggestions":[{"kind":"skill","title":"Review habit","content":"Always run tests after review","tags":"review"}]}'
                )
            },
        },
    )

    with TestClient(app) as client:
        res = client.post("/api/skills/research", json={"prompt": "make repo review skill", "source": "https://github.com/example/repo"})
        assert res.status_code == 200
        body = res.json()
        assert body["skill"]["name"] == "Repo Reviewer"
        assert body["memories"][0]["title"] == "Review habit"


def test_running_jobs_are_failed_on_startup_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path))
    app_module.db.init_db()
    app_module.db.create_job("lg_running", "Running", "do work", ["step"])
    app_module.db.update_job_status("lg_running", "running")

    assert app_module.db.fail_running_jobs_on_startup() == 1

    job = app_module.db.get_job("lg_running")
    assert job["status"] == "failed"
    assert "server restarted" in job["error"]

