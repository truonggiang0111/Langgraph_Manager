from langgraph_manager.native_runtime import execute_task_dag


def test_dag_skips_dependents_when_dependency_fails():
    events = []
    state = {
        "_callback": lambda step, status, detail: events.append((step, status, detail)),
        "task_graph": [
            {"id": "T1", "name": "bad", "depends_on": [], "tool": "missing_tool", "max_attempts": 2},
            {"id": "T2", "name": "dependent", "depends_on": ["T1"], "tool": "memory_log", "max_attempts": 2},
        ],
    }
    results = execute_task_dag(state, {"request": "test", "discussion": ""})

    assert results[0]["status"] == "failed"
    assert results[0]["attempt"] == 2
    assert results[1]["status"] == "skipped"
    assert any(step == "repair:T1" for step, _, _ in events)


def test_dag_runs_independent_tasks():
    state = {
        "task_graph": [
            {"id": "T1", "name": "a", "depends_on": [], "tool": "memory_log", "max_attempts": 2},
            {"id": "T2", "name": "b", "depends_on": [], "tool": "plan_builder", "max_attempts": 2},
        ],
    }
    results = execute_task_dag(state, {"request": "abc", "discussion": "", "plan": [], "task_graph": []})

    assert [r["status"] for r in results] == ["done", "done"]
