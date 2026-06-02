from langgraph_manager.native_runtime import verifier_node


def test_verify_hard_gate_blocks_missing_diff(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_STRICT_VERIFY_GATE", "1")
    state = {
        "intent": "engineering",
        "risk": "medium",
        "permission_mode": "full_access",
        "plan": ["a"],
        "task_graph": [{"id": "T1"}],
        "task_results": [{"id": "T1", "name": "delegate_to_coding_agent", "status": "done"}],
        "tool_results": [
            {"name": "coding_agent_executor", "ok": True, "summary": "ok"},
            {"name": "verifier", "ok": True, "summary": "ok"},
        ],
        "worker_assignments": [{"worker": "coder", "mission": "implement"}],
        "request": "fix backend bug",
    }
    out = verifier_node(state)  # type: ignore[arg-type]
    v = out.get("verification", {})
    assert v.get("ok") is False
    assert v.get("hard_gate_ok") is False
    assert any("diff_evidence" in item for item in v.get("missing_requirements", []))

