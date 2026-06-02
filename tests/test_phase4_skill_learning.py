from pathlib import Path

from langgraph_manager import db
from langgraph_manager.claude_skill_index import recommend_tools_for_request


def _make_local_skill(root: Path, name: str, description: str = "") -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description or name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_learning_metadata_present_and_new_skill_not_starved(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "0")
    db.init_db()

    _make_local_skill(tmp_path / "claude", "serena")
    _make_local_skill(tmp_path / "claude", "code-review")
    _make_local_skill(tmp_path / "claude", "git-safety-recovery")

    # Heavily used old skill
    for _ in range(12):
        db.record_skill_outcome("serena", ok=True, notes="good")
    # New skill should still have exploration bonus and stay selectable.
    routing = recommend_tools_for_request("fix bug route handler trong repo", limit=4)
    assert routing["learning_router_enabled"] is True
    assert routing["recommended_tools"]
    assert any("explore_bonus" in item for item in routing["recommended_tools"])
    assert any(item["name"] == "code-review" for item in routing["recommended_tools"])


def test_retired_skill_is_skipped_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_HOME_PATH", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude")
    monkeypatch.setenv("CODEGRAPH_ENABLED", "0")
    db.init_db()

    _make_local_skill(tmp_path / "claude", "code-review")
    _make_local_skill(tmp_path / "claude", "serena")
    _make_local_skill(tmp_path / "claude", "git-safety-recovery")

    # Push code-review to retired.
    for _ in range(14):
        db.record_skill_outcome("code-review", ok=False, notes="bad")
    perf = db.get_skill_performance("code-review")
    assert perf and perf["status"] in {"deprioritized", "retired"}

    routing = recommend_tools_for_request("fix bug route handler trong repo", limit=4)
    if perf["status"] == "retired":
        assert "code-review" not in routing["ordered_tool_names"]
