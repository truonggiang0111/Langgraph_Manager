import time

from langgraph_manager import native_runtime


def test_parallel_dag_executes_independent_tasks_concurrently(monkeypatch):
    original = native_runtime.run_tool
    call_order: list[str] = []

    def fake_run_tool(name, context):
        if name in {"memory_log", "plan_builder"}:
            call_order.append(name)
            time.sleep(0.25)
            return {"name": name, "ok": True, "summary": f"{name} ok", "data": {}}
        return original(name, context)

    monkeypatch.setattr(native_runtime, "run_tool", fake_run_tool)
    monkeypatch.setenv("LANGGRAPH_MAX_PARALLEL_TASKS", "2")

    state = {
        "task_graph": [
            {"id": "T1", "name": "a", "depends_on": [], "tool": "memory_log", "max_attempts": 1},
            {"id": "T2", "name": "b", "depends_on": [], "tool": "plan_builder", "max_attempts": 1},
        ],
    }
    started = time.perf_counter()
    results = native_runtime.execute_task_dag(state, {"request": "abc", "discussion": "", "plan": [], "task_graph": []})
    elapsed = time.perf_counter() - started

    assert [r["status"] for r in results] == ["done", "done"]
    assert sorted(call_order) == ["memory_log", "plan_builder"]
    # If sequential it would be ~0.50s+, parallel should be noticeably lower.
    assert elapsed < 0.45, elapsed


def test_native_graph_uses_parallel_worker_mode_by_default(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_DAG_EXECUTION_MODE", raising=False)
    events = []

    def cb(step, status, detail):
        events.append((step, status, detail))

    result = native_runtime.run_native_job(
        "lg_parallel_default",
        "Build backend onboarding flow with tests",
        "user: keep verify strict",
        cb,
    )

    assert "LangGraph native backend" in result
    assert any(step == "execute_worker_steps" and status in {"running", "done"} for step, status, _ in events)
