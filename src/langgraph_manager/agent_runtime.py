from __future__ import annotations

import os
from typing import Any, Callable, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .tool_registry import run_tool


StepCallback = Callable[[str, str, str], None]
Risk = Literal["low", "medium", "high"]
PermissionMode = Literal["default_permissions", "auto_review", "full_access"]
CALLBACKS: dict[str, StepCallback] = {}


class AgentState(TypedDict, total=False):
    job_id: str
    request: str
    discussion: str
    permission_mode: PermissionMode
    focus_files: list[dict[str, str]]
    _callback_key: str
    intent: str
    risk: Risk
    plan: list[str]
    tool_results: list[dict[str, Any]]
    execution_log: list[str]
    verification: dict[str, Any]
    result: str


def callback(state: AgentState, step: str, status: str, detail: str) -> None:
    key = str(state.get("_callback_key") or state.get("job_id") or "")
    cb = CALLBACKS.get(key)
    if cb:
        cb(step, status, detail)


def append_log(state: AgentState, line: str) -> AgentState:
    return {**state, "execution_log": [*state.get("execution_log", []), line]}


def classify_intent(text: str) -> str:
    lower = text.lower()
    if any(key in lower for key in ["sửa", "fix", "lỗi", "bug", "test", "code", "docker", "repo", "workspace", "claude", "create file", "tạo file", "write file", "edit file"]):
        return "engineering"
    if any(key in lower for key in ["plan", "kế hoạch", "thiết kế", "build", "workflow"]):
        return "planning"
    return "general"


def estimate_risk(text: str) -> Risk:
    lower = text.lower()
    if any(key in lower for key in ["xóa", "delete", "reset", "production", "secret", "token", "deploy"]):
        return "high"
    if any(key in lower for key in ["sửa", "docker", "database", "api", "backend", "claude", "create file", "tạo file", "write file", "edit file"]):
        return "medium"
    return "low"


def wants_coding_executor(text: str) -> bool:
    lower = text.lower()
    configured = bool(os.getenv("CLAUDE_EXECUTOR_COMMAND") or os.getenv("CODING_AGENT_COMMAND"))
    explicit = any(key in lower for key in ["claude", "coding agent", "code agent", "agent tự code", "agent tu code"])
    return configured or explicit


def make_plan(state: AgentState) -> list[str]:
    request = state.get("request", "").strip()
    intent = state.get("intent", "general")
    if intent == "engineering":
        steps = [
            "Inspect workspace and project markers.",
            "Read or delegate code work through the configured coding executor.",
            "Inspect diff after execution.",
            "Run the project check command.",
            "Report only verified changes, failures, and next required configuration.",
        ]
        if wants_coding_executor(request):
            steps[1] = "Delegate implementation to the configured Claude coding executor."
        return steps
    return [
        "Capture the request and discussion context.",
        "Choose the smallest safe tool path.",
        "Return a concise answer or ask for the missing workspace/executor detail.",
    ]


def add_tool_result(state: AgentState, result: dict[str, Any]) -> AgentState:
    return {**state, "tool_results": [*state.get("tool_results", []), result]}


def intake_node(state: AgentState) -> AgentState:
    callback(state, "intake", "running", "Classifying request")
    request = state.get("request", "").strip()
    intent = classify_intent(request)
    risk = estimate_risk(request)
    out: AgentState = {**state, "request": request, "intent": intent, "risk": risk}
    callback(out, "intake", "done", f"intent={intent}, risk={risk}")
    return append_log(out, f"intake intent={intent} risk={risk}")


def plan_node(state: AgentState) -> AgentState:
    callback(state, "plan", "running", "Building execution plan")
    plan = make_plan(state)
    out: AgentState = {**state, "plan": plan}
    callback(out, "plan", "done", f"{len(plan)} step(s)")
    return append_log(out, "plan created")


def inspect_node(state: AgentState) -> AgentState:
    callback(state, "inspect_workspace", "running", "Reading workspace shape")
    result = run_tool("workspace_inspect", {"max_files": 160, "permission_mode": state.get("permission_mode", "auto_review")})
    out = add_tool_result(state, result)
    callback(out, "inspect_workspace", "done" if result.get("ok") else "failed", str(result.get("summary", ""))[:1000])
    return append_log(out, f"inspect_workspace {'ok' if result.get('ok') else 'failed'}")


def execute_node(state: AgentState) -> AgentState:
    if state.get("intent") != "engineering":
        callback(state, "execute", "done", "No coding executor needed")
        return append_log(state, "execute skipped for non-engineering request")

    if wants_coding_executor(state.get("request", "")):
        callback(state, "coding_agent_executor", "running", "Delegating to configured Claude executor")
        result = run_tool(
            "coding_agent_executor",
            {
                "request": state.get("request", ""),
                "discussion": state.get("discussion", ""),
                "permission_mode": state.get("permission_mode", "auto_review"),
                "risk": state.get("risk", "low"),
                "plan": state.get("plan", []),
                "focus_files": state.get("focus_files", []),
            },
        )
        out = add_tool_result(state, result)
        callback(out, "coding_agent_executor", "done" if result.get("ok") else "failed", str(result.get("summary", ""))[:1000])
        return append_log(out, f"coding_agent_executor {'ok' if result.get('ok') else 'failed'}")

    callback(state, "workspace_probe", "running", "No coding executor configured; running read-only workspace probe")
    result = run_tool(
        "workspace_executor",
        {
            "command": "git status --short",
            "permission_mode": "default_permissions",
            "risk": state.get("risk", "low"),
            "timeout": 30,
        },
    )
    out = add_tool_result(state, result)
    callback(out, "workspace_probe", "done" if result.get("ok") else "failed", str(result.get("summary", ""))[:1000])
    return append_log(out, "workspace_probe completed")


def verify_node(state: AgentState) -> AgentState:
    callback(state, "verify", "running", "Checking diff and tests")
    results = list(state.get("tool_results", []))
    diff = run_tool("workspace_diff", {"permission_mode": state.get("permission_mode", "auto_review")})
    results.append(diff)

    executor_ok = any(item.get("name") == "coding_agent_executor" and item.get("ok") for item in results)

    failures = [item for item in results if not item.get("ok") and not is_nonfatal_diff_result(item)]
    verification = {
        "ok": not failures and (state.get("intent") != "engineering" or bool(executor_ok) or not wants_coding_executor(state.get("request", ""))),
        "intent": state.get("intent", "general"),
        "risk": state.get("risk", "low"),
        "permission_mode": state.get("permission_mode", "auto_review"),
        "executor_ok": executor_ok,
        "diff_ok": bool(diff.get("ok")),
        "diff_available": not is_nonfatal_diff_result(diff),
        "tests_ok": None,
        "failures": [str(item.get("summary", item.get("name", "unknown"))) for item in failures],
    }
    out: AgentState = {**state, "tool_results": results, "verification": verification}
    callback(out, "verify", "done" if verification["ok"] else "failed", f"ok={verification['ok']}")
    return append_log(out, "verification completed")


def is_nonfatal_diff_result(result: dict[str, Any]) -> bool:
    if result.get("name") != "workspace_diff" or result.get("ok"):
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    output = str(data.get("output") or data.get("error") or "").lower()
    return (
        "not a git repository" in output
        or "not a git repo" in output
        or (result.get("summary") == "git diff failed" and int(data.get("exit_code") or 0) in {1, 129})
    )


def report_node(state: AgentState) -> AgentState:
    callback(state, "report", "running", "Composing final report")
    verification = state.get("verification", {})
    tool_lines = [
        f"- {item.get('name')}: {'OK' if item.get('ok') else 'FAIL'} - {item.get('summary', '')}"
        for item in state.get("tool_results", [])
    ]
    failure_lines = [f"- {item}" for item in verification.get("failures", [])] or ["- None"]
    result = "\n".join(
        [
            "LangGraph agent runtime finished." if verification.get("ok") else "LangGraph agent runtime needs attention.",
            f"Intent: {state.get('intent', 'general')}",
            f"Risk: {state.get('risk', 'low')}",
            f"Permission mode: {state.get('permission_mode', 'auto_review')}",
            f"Verify: {'OK' if verification.get('ok') else 'NOT OK'}",
            f"Executor OK: {verification.get('executor_ok')}",
            f"Tests OK: {verification.get('tests_ok')}",
            "",
            "Plan:",
            *[f"- {step}" for step in state.get("plan", [])],
            "",
            "Tool results:",
            *(tool_lines or ["- No tools executed"]),
            "",
            "Failures:",
            *failure_lines,
            "",
            "Execution log:",
            *[f"- {line}" for line in state.get("execution_log", [])],
        ]
    )
    out: AgentState = {**state, "result": result}
    callback(out, "report", "done", "Report ready")
    return out


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("intake", intake_node)
    graph.add_node("plan", plan_node)
    graph.add_node("inspect", inspect_node)
    graph.add_node("execute", execute_node)
    graph.add_node("verify", verify_node)
    graph.add_node("report", report_node)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "plan")
    graph.add_edge("plan", "inspect")
    graph.add_edge("inspect", "execute")
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=InMemorySaver())


def run_agent_job_state(
    job_id: str,
    request: str,
    discussion: str,
    callback_fn: StepCallback,
    permission_mode: PermissionMode = "auto_review",
    focus_files: list[dict[str, str]] | None = None,
) -> AgentState:
    CALLBACKS[job_id] = callback_fn
    app = build_agent_graph()
    try:
        return app.invoke(
            {
                "job_id": job_id,
                "request": request,
                "discussion": discussion,
                "permission_mode": permission_mode,
                "focus_files": focus_files or [],
                "_callback_key": job_id,
            },
            config={"configurable": {"thread_id": job_id}},
        )
    finally:
        CALLBACKS.pop(job_id, None)
