import json

from langgraph_manager.claude_skill_index import (
    classify_task_complexity,
    recommend_tools_for_request,
    scan_claude_skill_index,
    search_claude_skill_index,
)


def make_plugin(root, name, description="", version="1.0.0", skill=False, mcp=False):
    plugin = root / "plugins" / "cache" / "claude-plugins-official" / name / version
    manifest = plugin / ".claude-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(
        json.dumps({"name": name, "description": description, "author": {"name": "Test"}}),
        encoding="utf-8",
    )
    if skill:
        skill_dir = plugin / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\nUse this skill.", encoding="utf-8")
    if mcp:
        (plugin / ".mcp.json").write_text(json.dumps({name: {"command": "npx"}}), encoding="utf-8")


def make_local_skill(root, name, description):
    skill_dir = root / "skills" / name
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "playbook.md").write_text("# playbook\n", encoding="utf-8")


def test_scan_claude_skill_index_reads_shared_claude_home(tmp_path, monkeypatch):
    make_plugin(tmp_path, "frontend-design", "Frontend design skill", skill=True)
    make_plugin(tmp_path, "serena", "Semantic code MCP", mcp=True)
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "0")

    index = scan_claude_skill_index()
    tools = {tool["name"]: tool for tool in index["tools"]}

    assert "frontend-design" in tools
    assert tools["frontend-design"]["components"][0]["type"] == "skill"
    assert tools["frontend-design"]["components"][0]["path"].startswith("/home/node/.claude/")
    assert "serena" in tools
    assert tools["serena"]["components"][0]["type"] == "mcp"


def test_scan_claude_skill_index_reads_local_skills(tmp_path, monkeypatch):
    make_local_skill(
        tmp_path,
        "git-safety-recovery",
        "Create a checkpoint before risky code changes and recover cleanly.",
    )
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "0")

    index = scan_claude_skill_index()
    tools = {tool["name"]: tool for tool in index["tools"]}

    assert "git-safety-recovery" in tools
    components = tools["git-safety-recovery"]["components"]
    assert components[0]["type"] == "skill"
    assert any(item["type"] == "reference" for item in components)
    assert components[0]["path"].endswith("/skills/git-safety-recovery/SKILL.md")


def test_recommend_tools_prefers_codegraph_for_repo_navigation(tmp_path, monkeypatch):
    for name in ["code-review", "serena"]:
        make_plugin(tmp_path, name, f"{name} plugin", mcp=name == "serena")
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "1")

    routing = recommend_tools_for_request("tìm hàm xử lý chat trong repo này")

    assert routing["intent"] == "repo_navigation"
    names = [item["name"] for item in routing["recommended_tools"]]
    assert names[:2] == ["codegraph", "serena"]
    assert routing["ordered_tool_names"][:2] == ["codegraph", "serena"]
    assert routing["recommended_read_order"][:2] == ["codegraph", "serena"]
    assert routing["baseline_tools"] == ["codegraph"]
    assert routing["initial_tool_read_count"] == 3
    assert routing["read_until_sufficient"] is True
    assert routing["continue_to_next_tool_if_needed"] is True
    assert routing["progressive_tool_loading"] is True
    assert routing["expand_beyond_initial_if_needed"] is True
    assert routing["do_not_preload_full_shortlist"] is True
    assert "exploration bonus" in routing["tool_routing_policy"]


def test_recommend_tools_prefers_frontend_stack_for_ui(tmp_path, monkeypatch):
    for name in ["frontend-design", "serena", "code-review"]:
        make_plugin(tmp_path, name, f"{name} plugin", skill=name == "frontend-design")
    make_local_skill(
        tmp_path,
        "git-safety-recovery",
        "Create a checkpoint before risky code changes and recover cleanly.",
    )
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "1")

    routing = recommend_tools_for_request("sửa lỗi UI scroll bị nhảy xuống cuối")

    assert routing["intent"] == "frontend_bugfix"
    names = [item["name"] for item in routing["recommended_tools"]]
    assert names[:4] == ["frontend-design", "codegraph", "git-safety-recovery", "serena"]
    assert routing["baseline_tools"] == ["codegraph"]
    assert routing["initial_tool_read_count"] == 4
    assert routing["recommended_tools"][0]["entry_path"].endswith("/skills/frontend-design/SKILL.md")


def test_recommend_tools_starts_with_single_tool_for_docs_lookup(tmp_path, monkeypatch):
    for name in ["context7", "serena"]:
        make_plugin(tmp_path, name, f"{name} plugin", mcp=True)
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "0")

    routing = recommend_tools_for_request("xem docs react mới nhất")

    assert routing["intent"] == "docs_lookup"
    assert routing["ordered_tool_names"][:2] == ["context7", "serena"]
    assert routing["recommended_read_order"][:2] == ["context7", "serena"]
    assert routing["initial_tool_read_count"] == 1


def test_search_claude_skill_index_matches_skill_excerpt(tmp_path, monkeypatch):
    make_plugin(tmp_path, "frontend-design", "Frontend design skill", skill=True)
    make_plugin(tmp_path, "serena", "Semantic code MCP", mcp=True)
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "0")

    results = search_claude_skill_index("frontend implementation", limit=3)["results"]

    assert results
    assert results[0]["name"] == "frontend-design"
    assert "entry_path" in results[0]
    assert "frontend" in results[0]["matched_terms"]


def test_classify_task_complexity_distinguishes_simple_and_composite():
    simple = classify_task_complexity("xem docs react mới nhất", "docs_lookup")
    composite = classify_task_complexity(
        "sửa lỗi UI chat bị nhảy scroll realtime và trace route nào gây auto jump",
        "frontend_bugfix",
    )

    assert simple["complexity"] == "simple"
    assert composite["complexity"] in {"composite", "complex_composite"}
    assert composite["score"] > simple["score"]


def test_recommend_tools_includes_task_complexity(tmp_path, monkeypatch):
    for name in ["frontend-design", "serena", "code-review"]:
        make_plugin(tmp_path, name, f"{name} plugin", skill=name == "frontend-design")
    make_local_skill(
        tmp_path,
        "git-safety-recovery",
        "Create a checkpoint before risky code changes and recover cleanly.",
    )
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "1")

    routing = recommend_tools_for_request("sửa lỗi UI chat bị nhảy scroll realtime và trace route liên quan")

    assert routing["task_complexity"] in {"composite", "complex_composite"}
    assert routing["complexity_score"] >= 2
    assert routing["initial_tool_read_count"] == 4


def test_recommend_tools_includes_fallback_candidates_for_bug_work(tmp_path, monkeypatch):
    for name in ["frontend-design", "serena", "code-review", "feature-dev"]:
        make_plugin(tmp_path, name, f"{name} plugin", skill=name == "frontend-design")
    make_local_skill(
        tmp_path,
        "git-safety-recovery",
        "Create a checkpoint before risky code changes and recover cleanly.",
    )
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "1")

    routing = recommend_tools_for_request("fix bug scroll realtime và debug route lỗi", limit=4)

    assert routing["adaptive_skill_search_enabled"] is True
    assert routing["fallback_search_queries"]
    assert any("bug debug" in item for item in routing["fallback_search_queries"])
    assert isinstance(routing["fallback_tool_candidates"], list)
    assert "git-safety-recovery" in routing["ordered_tool_names"]
    assert routing["initial_tool_read_count"] == 4
    assert all(item["name"] not in routing["recommended_read_order"] for item in routing["fallback_tool_candidates"])
