from langgraph_manager.tool_registry import describe_tools, run_tool
from langgraph_manager import tool_registry


def test_tool_registry_exposes_core_tools():
    names = {item["name"] for item in describe_tools()}
    assert {
        "memory_log",
        "plan_builder",
        "verifier",
        "repo_inspector",
        "workspace_inspect",
        "workspace_read_file",
        "workspace_diff",
        "workspace_executor",
        "coding_agent_executor",
    }.issubset(names)
    assert "workspace_apply_patch" not in names
    assert "test_runner" not in names
    assert "web_research" not in names
    assert "browser_automation" not in names
    assert "browser_host" not in names
    assert "cliproxy_worker" not in names


def test_memory_log_tool_runs():
    result = run_tool("memory_log", {"request": "abc", "discussion": "note"})
    assert result["ok"] is True
    assert result["name"] == "memory_log"
    assert result["data"]["request_chars"] == 3


def test_unknown_tool_returns_structured_failure():
    result = run_tool("missing_tool", {})
    assert result["ok"] is False
    assert "Unknown tool" in result["summary"]
    assert result["error_class"] == "unknown_tool"
    assert result["evidence"]["has_data"] is False


def test_plan_prompt_uses_plan_model(monkeypatch):
    captured = {}

    monkeypatch.setenv("CLIPROXY_CHAT_MODEL", "gpt-5.4")
    monkeypatch.setenv("CLIPROXY_PLAN_MODEL", "gpt-5.5")
    monkeypatch.setattr(tool_registry, "_cliproxy_api_key", lambda base_url: "key")

    def fake_http_json(method, url, payload=None, token="", timeout=120):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}], "model": payload["model"], "usage": {}}

    monkeypatch.setattr(tool_registry, "_http_json", fake_http_json)

    result = tool_registry.cliproxy_chat(
        [{"role": "user", "content": "plan"}],
        "Bạn là planner trước khi thực thi cho LangGraph Manager.",
    )

    assert result["ok"] is True
    assert captured["payload"]["model"] == "gpt-5.5"


def test_chat_prompt_uses_chat_model(monkeypatch):
    captured = {}

    monkeypatch.setenv("CLIPROXY_CHAT_MODEL", "gpt-5.4")
    monkeypatch.setenv("CLIPROXY_PLAN_MODEL", "gpt-5.5")
    monkeypatch.setattr(tool_registry, "_cliproxy_api_key", lambda base_url: "key")

    def fake_http_json(method, url, payload=None, token="", timeout=120):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}], "model": payload["model"], "usage": {}}

    monkeypatch.setattr(tool_registry, "_http_json", fake_http_json)

    result = tool_registry.cliproxy_chat(
        [{"role": "user", "content": "hello"}],
        "Bạn là LangGraph Manager chat mode.",
    )

    assert result["ok"] is True
    assert captured["payload"]["model"] == "gpt-5.4"


def test_workspace_executor_blocks_mutation_in_default_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    result = run_tool(
        "workspace_executor",
        {"command": "touch should_not_exist", "permission_mode": "default_permissions", "risk": "low"},
    )
    assert result["ok"] is False
    assert "blocked" in result["summary"]
    assert result["error_class"] == "permission_denied"
    assert not (tmp_path / "should_not_exist").exists()


def test_workspace_executor_runs_read_only_command(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    result = run_tool(
        "workspace_executor",
        {"command": "ls", "permission_mode": "default_permissions", "risk": "low"},
    )
    assert result["ok"] is True
    assert "a.txt" in result["data"]["output"]
    assert result["error_class"] == ""
    assert result["evidence"]["has_output"] is True


def test_coding_agent_executor_requires_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("CLAUDE_EXECUTOR_COMMAND", raising=False)
    monkeypatch.delenv("CODING_AGENT_COMMAND", raising=False)

    result = run_tool(
        "coding_agent_executor",
        {"request": "fix code", "permission_mode": "auto_review", "risk": "low"},
    )

    assert result["ok"] is False
    assert "not configured" in result["summary"]


def test_coding_agent_executor_prompt_requires_executor_to_finish_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_COMMAND", "fake-claude --stdin")

    captured = {}

    class FakeStdout:
        def __init__(self):
            self._lines = ["done\n"]
            self._idx = 0

        def readline(self):
            if self._idx >= len(self._lines):
                return ""
            line = self._lines[self._idx]
            self._idx += 1
            return line

        def read(self):
            return ""

    class FakeStdin:
        def write(self, value):
            captured["input"] = value
            return None

        def close(self):
            return None

    class FakePopen:
        def __init__(self, _args, **_kwargs):
            self.returncode = 0
            self.stdout = FakeStdout()
            self.stdin = FakeStdin()

        def poll(self):
            return 0 if self.stdout._idx >= 1 else None

        def kill(self):
            self.returncode = 1

    monkeypatch.setattr(tool_registry.subprocess, "Popen", FakePopen)

    result = run_tool(
        "coding_agent_executor",
        {
            "request": "check giúp tôi xem bạn đang sài model gì trong CLI proxy",
            "permission_mode": "full_access",
            "risk": "medium",
        },
    )

    assert result["ok"] is True
    assert "execute the user's request to completion inside this executor" in captured["input"]
    assert "do not return instructions telling LangGraph or the user to run another task first" in captured["input"]


def test_coding_agent_executor_prompt_includes_progressive_tool_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_COMMAND", "fake-claude --stdin")

    captured = {}

    class FakeStdout:
        def __init__(self):
            self._lines = ["ok\n"]
            self._idx = 0

        def readline(self):
            if self._idx >= len(self._lines):
                return ""
            line = self._lines[self._idx]
            self._idx += 1
            return line

        def read(self):
            return ""

    class FakeStdin:
        def write(self, value):
            captured["input"] = value
            return None

        def close(self):
            return None

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["cwd"] = kwargs.get("cwd")
            self.returncode = 0
            self.stdout = FakeStdout()
            self.stdin = FakeStdin()

        def poll(self):
            return 0 if self.stdout._idx >= 1 else None

        def kill(self):
            self.returncode = 1

    monkeypatch.setattr(tool_registry.subprocess, "Popen", FakePopen)

    result = run_tool(
        "coding_agent_executor",
        {
            "request": "find chat route in repo",
            "permission_mode": "full_access",
            "risk": "low",
            "intent": "repo_navigation",
            "recommended_tools": [{"name": "codegraph"}, {"name": "serena"}],
            "ordered_tool_recommendations": [{"name": "codegraph"}, {"name": "serena"}],
            "task_complexity": "composite",
            "complexity_score": 3,
            "complexity_signals": ["multi_part_request", "cross_domain"],
            "recommended_read_order": ["codegraph", "serena"],
            "ordered_tool_names": ["codegraph", "serena"],
            "initial_tool_read_count": 2,
            "read_until_sufficient": True,
            "continue_to_next_tool_if_needed": True,
            "progressive_tool_loading": True,
            "expand_beyond_initial_if_needed": True,
            "do_not_preload_full_shortlist": True,
            "fallback_search_queries": ["bug debug fix error regression code review"],
            "fallback_tool_candidates": [{"name": "code-review"}],
            "adaptive_skill_search_enabled": True,
            "tool_routing_policy": "Read progressively",
            "skill_index_root": "/claude-home",
        },
    )

    assert result["ok"] is True
    assert '"task_complexity": "composite"' in captured["input"]
    assert '"ordered_tool_names"' in captured["input"]
    assert "recommended_read_order" in captured["input"]
    assert '"initial_tool_read_count": 2' in captured["input"]
    assert '"read_until_sufficient": true' in captured["input"]
    assert '"progressive_tool_loading": true' in captured["input"]
    assert '"fallback_search_queries"' in captured["input"]


def test_coding_agent_executor_appends_resume_session_for_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_COMMAND", "claude -p")

    captured = {}

    class FakeStdout:
        def __init__(self):
            self._lines = ['{"type":"session_info","session_id":"sess_new_123"}\n']
            self._idx = 0

        def readline(self):
            if self._idx >= len(self._lines):
                return ""
            line = self._lines[self._idx]
            self._idx += 1
            return line

        def read(self):
            return ""

    class FakeStdin:
        def write(self, _value):
            return None

        def close(self):
            return None

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.returncode = 0
            self.stdout = FakeStdout()
            self.stdin = FakeStdin()

        def poll(self):
            return 0 if self.stdout._idx >= 1 else None

        def kill(self):
            self.returncode = 1

    monkeypatch.setattr(tool_registry.subprocess, "Popen", FakePopen)

    result = run_tool(
        "coding_agent_executor",
        {
            "request": "fix API timeout",
            "permission_mode": "full_access",
            "risk": "low",
            "resume_session_id": "sess_prev_001",
        },
    )

    assert result["ok"] is True
    assert "--resume" in captured["args"]
    assert "sess_prev_001" in captured["args"]
    assert result["data"]["resumed_session_id"] == "sess_prev_001"
    assert result["data"]["session_id"] == "sess_new_123"


def test_coding_agent_executor_retries_without_resume_when_session_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_COMMAND", "claude -p")
    calls = {"count": 0, "argv": []}

    class FakeStdout:
        def __init__(self, lines):
            self._lines = lines
            self._idx = 0

        def readline(self):
            if self._idx >= len(self._lines):
                return ""
            line = self._lines[self._idx]
            self._idx += 1
            return line

        def read(self):
            return ""

    class FakeStdin:
        def write(self, _value):
            return None

        def close(self):
            return None

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls["count"] += 1
            calls["argv"].append(list(args))
            self.stdin = FakeStdin()
            if calls["count"] == 1:
                self.stdout = FakeStdout([
                    "No conversation found with session ID: sess_prev_001\n",
                    '{"type":"result","subtype":"error_during_execution","session_id":"s_old","is_error":true}\n',
                ])
                self.returncode = 1
            else:
                self.stdout = FakeStdout([
                    '{"type":"assistant","message":{"content":[{"type":"text","text":"ok retry"}]}}\n',
                    '{"type":"result","subtype":"success","result":"ok retry","session_id":"s_new","is_error":false}\n',
                ])
                self.returncode = 0

        def poll(self):
            return self.returncode if self.stdout._idx >= len(self.stdout._lines) else None

        def kill(self):
            self.returncode = 1

    monkeypatch.setattr(tool_registry.subprocess, "Popen", FakePopen)

    result = run_tool(
        "coding_agent_executor",
        {
            "request": "retry test",
            "permission_mode": "full_access",
            "risk": "low",
            "resume_session_id": "sess_prev_001",
        },
    )

    assert result["ok"] is True
    assert calls["count"] == 2
    assert "--resume" in calls["argv"][0]
    assert "--resume" not in calls["argv"][1]


def test_workspace_executor_allows_docker_format_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    result = run_tool(
        "workspace_executor",
        {"command": "docker ps --format '{{.Names}}'", "permission_mode": "default_permissions", "risk": "low"},
    )
    assert "blocked destructive command" not in result["summary"]


def test_workspace_executor_rejects_cwd_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    result = run_tool(
        "workspace_executor",
        {"command": "pwd", "cwd": str(tmp_path), "permission_mode": "full_access", "risk": "low"},
    )
    assert result["ok"] is False
    assert "escapes workspace" in result["summary"]


def test_workspace_inspect_and_read_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")

    inspected = run_tool("workspace_inspect", {"max_files": 20})
    assert inspected["ok"] is True
    assert "pyproject.toml" in inspected["data"]["markers"]
    assert any(item["path"] == "src/app.py" for item in inspected["data"]["files"])

    read = run_tool("workspace_read_file", {"path": "src/app.py"})
    assert read["ok"] is True
    assert "print('hello')" in read["data"]["content"]
    assert len(read["data"]["sha256"]) == 64


def test_workspace_read_file_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_WORKSPACE_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    result = run_tool("workspace_read_file", {"path": str(outside)})
    assert result["ok"] is False
    assert "escapes workspace" in result["summary"]


def test_removed_agent_platform_tools_are_not_runnable():
    for name in ["workspace_apply_patch", "test_runner", "web_research", "browser_automation", "browser_host", "cliproxy_worker"]:
        result = run_tool(name, {})
        assert result["ok"] is False
        assert "Unknown tool" in result["summary"]
