from __future__ import annotations

import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from . import db
from .tool_registry import run_tool


StepCallback = Callable[[str, str, str], None]
Risk = Literal["low", "medium", "high"]
PermissionMode = Literal["default_permissions", "auto_review", "full_access"]
CALLBACKS: dict[str, StepCallback] = {}


class NativeState(TypedDict, total=False):
    job_id: str
    request: str
    discussion: str
    _callback: StepCallback
    _callback_key: str
    _step: str
    intent: str
    risk: Risk
    permission_mode: PermissionMode
    tools: list[str]
    plan: list[str]
    task_graph: list[dict[str, Any]]
    task_results: list[dict[str, Any]]
    execution_log: list[str]
    tool_results: list[dict[str, Any]]
    current_task: dict[str, Any]
    agent_status: str
    worker_assignments: list[dict[str, Any]]
    focus_files: list[dict[str, str]]
    verification: dict[str, Any]
    result: str


def _append_log(state: NativeState, line: str) -> NativeState:
    return {**state, "execution_log": [*state.get("execution_log", []), line]}


def _callback(state: NativeState, status: str, detail: str) -> None:
    cb = state.get("_callback")  # type: ignore[typeddict-item]
    if not cb:
        cb = CALLBACKS.get(str(state.get("_callback_key") or state.get("job_id") or ""))
    if cb:
        cb(str(state.get("_step", "langgraph")), status, detail)  # type: ignore[misc]


def classify_intent(text: str) -> str:
    lower = text.lower()
    if any(key in lower for key in ["sửa", "fix", "lỗi", "bug", "test", "code", "docker", "workflow"]):
        return "engineering"
    if any(key in lower for key in ["plan", "kế hoạch", "thảo luận", "thiết kế", "build"]):
        return "planning"
    return "general"


def estimate_risk(text: str) -> Risk:
    lower = text.lower()
    if any(key in lower for key in ["xóa", "delete", "reset", "production", "secret", "token", "deploy"]):
        return "high"
    if any(key in lower for key in ["sửa", "docker", "database", "workflow", "api", "backend"]):
        return "medium"
    return "low"


def choose_tools(intent: str, text: str) -> list[str]:
    lower = text.lower()
    tools = ["memory_log", "plan_builder", "ai_dispatcher", "verifier"]
    if intent == "engineering":
        tools.extend(["workspace_inspect", "repo_inspector", "workspace_executor", "workspace_diff"])
        if wants_coding_agent_executor(text):
            tools.append("coding_agent_executor")
    if "docker" in lower and "docker_observer" not in tools:
        tools.append("docker_observer")
    if "ui" in lower or "giao diện" in lower:
        tools.append("ui_reviewer")
    return tools


def wants_coding_agent_executor(text: str) -> bool:
    lower = text.lower()
    configured = bool(os.getenv("CLAUDE_EXECUTOR_COMMAND") or os.getenv("CODING_AGENT_COMMAND"))
    explicit = any(key in lower for key in ["claude", "coding agent", "code agent", "agent tự code", "agent tu code"])
    return configured or explicit


def is_onboarding_agent_flow(text: str) -> bool:
    lower = text.lower()
    return (
        any(marker in lower for marker in ["onboarding agent", "onboard agent", "flow onboarding", "luồng onboarding"])
        and any(marker in lower for marker in ["backend", "ui", "test", "deploy", "checklist"])
    )


def make_plan(intent: str, risk: Risk, tools: list[str], request: str = "") -> list[str]:
    if is_onboarding_agent_flow(request):
        return [
            "onboarding_scope_and_acceptance_evidence",
            "backend_flow_and_state_contract",
            "ui_entrypoint_and_report_visibility",
            "backend_ui_contract_tests",
            "anti_fake_report_verification_gate",
            "deploy_checklist_with_rollback",
            "final_verified_report",
        ]
    base = [
        "intake_request",
        "clarify_constraints",
        "build_task_graph",
        "select_internal_tools",
        "execute_worker_steps",
        "verify_result",
        "final_report",
    ]
    if risk == "high":
        base.insert(4, "require_explicit_safety_gate")
    if intent == "engineering":
        base.insert(-1, "run_checks")
    return base


def summarize_user_goal(request: str) -> str:
    text = request.strip()
    if "Discussion:" in text:
        user_lines = [
            line.split("user:", 1)[1].strip()
            for line in text.splitlines()
            if line.strip().lower().startswith("user:")
        ]
        if user_lines:
            text = user_lines[-1]
    return text[:160]


def make_user_plan(request: str, intent: str, risk: Risk, tools: list[str]) -> list[str]:
    plan = [
        "Làm rõ mục tiêu và phạm vi việc cần xử lý từ cuộc trao đổi.",
        "Kiểm tra bối cảnh hiện có, file/cấu hình liên quan và trạng thái đang chạy.",
        "Chọn đúng công cụ backend cần dùng: " + ", ".join(tools) + ".",
        "Thực hiện thay đổi theo từng bước nhỏ để dễ kiểm soát và dễ rollback.",
        "Chạy kiểm tra phù hợp, đọc log lỗi nếu có và sửa lại trước khi báo xong.",
        "Tổng kết kết quả, đường dẫn/URL cần mở và phần còn cần quyết định tiếp.",
    ]
    if intent == "engineering":
        plan.insert(3, "Đọc code hiện tại trước khi sửa để giữ đúng pattern của project.")
    if risk == "high":
        plan.insert(3, "Dừng lại xin xác nhận trước các thao tác có rủi ro cao.")
    goal = summarize_user_goal(request)
    if goal:
        plan[0] = f"Chốt mục tiêu chính: {goal}"
    return plan


def intake_node(state: NativeState) -> NativeState:
    request = state.get("request", "").strip()
    _callback({**state, "_step": "intake_request"}, "running", "Reading request and discussion")
    intent = classify_intent(request)
    risk = estimate_risk(request)
    out = {**state, "request": request, "intent": intent, "risk": risk}
    _callback({**state, "_step": "intake_request"}, "done", f"intent={intent}, risk={risk}")
    return _append_log(out, f"Intake: intent={intent}, risk={risk}")


def planner_node(state: NativeState) -> NativeState:
    _callback({**state, "_step": "build_task_graph"}, "running", "Creating native LangGraph plan")
    tools = choose_tools(state.get("intent", "general"), state.get("request", ""))
    plan = make_plan(state.get("intent", "general"), state.get("risk", "low"), tools, state.get("request", ""))
    task_graph = build_task_graph_for_state(state, tools)
    out = {**state, "tools": tools, "plan": plan, "task_graph": task_graph}
    _callback({**state, "_step": "build_task_graph"}, "done", f"{len(plan)} steps, tools={', '.join(tools)}")
    return _append_log(out, "Planner: native task graph generated")


def build_task_graph_for_state(state: NativeState, tools: list[str]) -> list[dict[str, Any]]:
    intent = state.get("intent", "general")
    risk = state.get("risk", "low")
    use_coding_agent = "coding_agent_executor" in tools
    if is_onboarding_agent_flow(state.get("request", "")):
        specs = [
            ("onboarding_scope_and_acceptance_evidence", "memory_log", []),
            ("dispatch_onboarding_worker_roles", "ai_dispatcher", ["T1"]),
            ("inspect_workspace_for_onboarding_flow", "workspace_inspect", ["T1"]),
            ("inspect_repository_for_backend_ui_tests", "repo_inspector", ["T3"]),
            ("delegate_onboarding_implementation", "coding_agent_executor", ["T2", "T4"]),
            ("inspect_onboarding_diff", "workspace_diff", ["T5"]),
            ("verify_anti_fake_report_evidence", "verifier", ["T6"]),
            ("prepare_deploy_checklist", "verifier", ["T7"]),
        ]
        return make_task_graph(specs)
    if intent == "engineering":
        specs: list[tuple[str, str, list[str]]] = []
        previous = ""

        def add(name: str, tool: str, deps: list[str] | None = None) -> str:
            task_id = f"T{len(specs) + 1}"
            specs.append((name, tool, deps if deps is not None else ([previous] if previous else [])))
            return task_id

        t1 = add("intake_request", "memory_log", [])
        add("dispatch_worker_roles", "ai_dispatcher", [t1])
        previous = add("inspect_workspace", "workspace_inspect", [t1])
        previous = add("inspect_repository", "repo_inspector", [previous])
        if risk == "high":
            previous = add("require_explicit_safety_gate", "verifier", [previous])
        if use_coding_agent:
            previous = add("delegate_to_coding_agent", "coding_agent_executor", [previous])
        else:
            previous = add("run_workspace_probe", "workspace_executor", [previous])
        previous = add("inspect_diff", "workspace_diff", [previous])
        add("verify_result", "verifier", [previous])
        return make_task_graph(specs)

    specs = [
        ("intake_request", "memory_log", []),
        ("build_task_graph", "plan_builder", ["T1"]),
        ("dispatch_worker_roles", "ai_dispatcher", ["T2"]),
        ("verify_result", "verifier", ["T3"]),
    ]
    return make_task_graph(specs)


def make_task_graph(specs: list[tuple[str, str, list[str]]]) -> list[dict[str, Any]]:
    task_graph: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    for index, (name, tool, deps) in enumerate(specs, start=1):
        task_id = f"T{index}"
        id_map[f"T{index}"] = task_id
        task_graph.append(
            {
                "id": task_id,
                "name": name,
                "depends_on": [id_map.get(dep, dep) for dep in deps],
                "tool": tool,
                "status": "pending",
                "attempt": 0,
                "max_attempts": 2,
            }
        )
    return task_graph


def policy_node(state: NativeState) -> NativeState:
    _callback({**state, "_step": "select_internal_tools"}, "running", "Checking execution policy")
    risk = state.get("risk", "low")
    mode = state.get("permission_mode", "auto_review")
    if mode == "full_access":
        detail = "Full access mode: CLIProxy may execute approved machine actions when configured"
    elif mode == "auto_review":
        detail = "Auto-review mode: CLIProxy should review risk and require confirmation for writes"
    else:
        detail = "Default permissions mode: read-only/check tools only unless explicitly escalated"
    if risk == "high" and mode != "full_access":
        detail += "; high-risk actions remain blocked"
    _callback({**state, "_step": "select_internal_tools"}, "done", detail)
    return _append_log(state, f"Policy: {detail}")


def worker_node(state: NativeState) -> NativeState:
    _callback({**state, "_step": "execute_worker_steps"}, "running", "Executing native task DAG with retry")
    request = state.get("request", "")
    findings = []
    findings.append(f"Request length: {len(request)} chars")
    findings.append(f"Intent: {state.get('intent', 'general')}")
    findings.append(f"Risk: {state.get('risk', 'low')}")
    findings.append(f"Tools selected: {', '.join(state.get('tools', []))}")
    urls = re.findall(r"https?://[^\s<>)\"']+", request)
    if urls:
        findings.append(f"URLs detected: {len(urls)}")
    if state.get("discussion"):
        findings.append("Discussion context included")
    for item in findings:
        _callback({**state, "_step": "execute_worker_steps"}, "running", item)

    context: dict[str, Any] = {
        "job_id": state.get("job_id", ""),
        "request": request,
        "discussion": state.get("discussion", ""),
        "intent": state.get("intent", "general"),
        "risk": state.get("risk", "low"),
        "permission_mode": state.get("permission_mode", "auto_review"),
        "plan": state.get("plan", []),
        "task_graph": state.get("task_graph", []),
        "focus_files": state.get("focus_files", []),
    }
    task_results = execute_task_dag(state, context)
    results = [r["tool_result"] for r in task_results if r.get("tool_result")]
    dispatch_results = [
        r for r in results
        if r.get("name") == "ai_dispatcher" and r.get("ok") and r.get("data", {}).get("assignments")
    ]
    worker_assignments = dispatch_results[-1]["data"]["assignments"] if dispatch_results else []
    failed = [r for r in task_results if r.get("status") != "done"]
    out = _append_log(
        {**state, "tool_results": results, "task_results": task_results, "worker_assignments": worker_assignments},
        "Worker: " + " | ".join(findings),
    )
    status = "failed" if failed else "done"
    detail = f"DAG completed: {len(task_results) - len(failed)} done, {len(failed)} failed"
    _callback({**state, "_step": "execute_worker_steps"}, status, detail)
    return out


def _task_ready(task: dict[str, Any], completed: set[str], failed: set[str]) -> bool:
    deps = [str(dep) for dep in task.get("depends_on", [])]
    return all(dep in completed for dep in deps) and not any(dep in failed for dep in deps)


def _refresh_task_graph(tasks: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    refreshed: list[dict[str, Any]] = []
    for task in tasks:
        if str(task.get("id")) == str(result.get("id")):
            refreshed.append(
                {
                    **task,
                    "status": result.get("status", task.get("status", "pending")),
                    "attempt": result.get("attempt", task.get("attempt", 0)),
                }
            )
        else:
            refreshed.append(task)
    return refreshed


def _tokenize(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]{2,}", str(value or "").lower()))


def _overlap_score(query: str, text: str) -> float:
    q = _tokenize(query)
    t = _tokenize(text)
    if not q or not t:
        return 0.0
    common = q & t
    return float(len(common)) / float(max(1, len(q)))


def _task_query(task: dict[str, Any], context: dict[str, Any]) -> str:
    return " ".join(
        [
            str(task.get("name") or ""),
            str(task.get("tool") or ""),
            str(context.get("intent") or ""),
            str(context.get("request") or ""),
        ]
    ).strip()


def _build_task_memory_context(task: dict[str, Any], context: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    query = _task_query(task, context)
    selected: list[dict[str, Any]] = []
    try:
        rows = db.list_memories()
    except Exception:
        return selected
    ranked = []
    for row in rows[:500]:
        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        text = f"{title}\n{content}"
        score = _overlap_score(query, text)
        if score <= 0:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    for score, row in ranked[:limit]:
        selected.append(
            {
                "id": row.get("id"),
                "kind": row.get("kind"),
                "title": str(row.get("title") or ""),
                "excerpt": str(row.get("content") or "")[:600],
                "score": round(float(score), 4),
            }
        )
    return selected


def _build_task_skill_candidates(task: dict[str, Any], context: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    query = _task_query(task, context)
    out: list[dict[str, Any]] = []
    perf_index: dict[str, dict[str, Any]] = {}
    try:
        for row in db.list_skill_performance():
            perf_index[str(row.get("skill_name") or "").lower()] = row
    except Exception:
        perf_index = {}
    try:
        skills = db.list_skills(include_inactive=False)
    except Exception:
        skills = []
    ranked = []
    for row in skills[:500]:
        name = str(row.get("name") or "")
        desc = str(row.get("description") or "")
        triggers = str(row.get("triggers") or "")
        relevance = _overlap_score(query, " ".join([name, desc, triggers]))
        perf = perf_index.get(name.lower(), {})
        perf_score = float(perf.get("score") or 0.0)
        trials = int(perf.get("trials") or 0)
        # Relevance first, performance as a nudge.
        final = (relevance * 10.0) + min(perf_score, 20.0) * 0.05
        if final <= 0:
            continue
        ranked.append((final, relevance, row, perf_score, trials))
    ranked.sort(key=lambda item: item[0], reverse=True)
    for final, relevance, row, perf_score, trials in ranked[:limit]:
        out.append(
            {
                "name": str(row.get("name") or ""),
                "description": str(row.get("description") or "")[:200],
                "triggers": str(row.get("triggers") or "")[:200],
                "relevance": round(float(relevance), 4),
                "performance_score": round(float(perf_score), 4),
                "trials": trials,
                "selection_score": round(float(final), 4),
            }
        )
    return out


def _record_task_outcome_memory(task: dict[str, Any], context: dict[str, Any], result: dict[str, Any], attempt: int) -> None:
    if os.getenv("LANGGRAPH_TASK_OUTCOME_MEMORY_ENABLED", "1").lower() not in {"1", "true", "yes"}:
        return
    try:
        status = "success" if result.get("ok") else "failure"
        task_id = str(task.get("id") or "")
        task_name = str(task.get("name") or "")
        tool = str(task.get("tool") or "")
        summary = str(result.get("summary") or "")[:500]
        content = (
            f"job_id={context.get('job_id','')}\n"
            f"task_id={task_id}\n"
            f"task_name={task_name}\n"
            f"tool={tool}\n"
            f"status={status}\n"
            f"attempt={attempt}\n"
            f"intent={context.get('intent','')}\n"
            f"risk={context.get('risk','')}\n"
            f"summary={summary}\n"
        )
        tags = ",".join(
            [
                "task_outcome",
                f"tool:{tool}" if tool else "tool:unknown",
                f"status:{status}",
            ]
        )
        db.create_memory(
            kind="route",
            title=f"task:{task_id}:{task_name}:{status}",
            content=content,
            tags=tags,
            source="task_runtime",
        )
    except Exception:
        return


def agent_decision_node(state: NativeState) -> NativeState:
    tasks = [dict(task) for task in state.get("task_graph", [])]
    results = [dict(item) for item in state.get("task_results", [])]
    done_ids = {str(item.get("id")) for item in results if item.get("status") == "done"}
    failed_ids = {str(item.get("id")) for item in results if item.get("status") != "done"}
    seen_ids = done_ids | failed_ids

    for task in tasks:
        task_id = str(task.get("id"))
        if task_id in seen_ids:
            continue
        if _task_ready(task, done_ids, failed_ids):
            detail = f"Selected {task_id}:{task.get('name')} via {task.get('tool')}"
            _callback({**state, "_step": "agent_decision"}, "done", detail)
            _callback({**state, "_step": "execute_worker_steps"}, "running", detail)
            return _append_log({**state, "current_task": task, "agent_status": "tool"}, f"Agent: {detail}")

    blocked = [
        task for task in tasks
        if str(task.get("id")) not in seen_ids and not _task_ready(task, done_ids, failed_ids)
    ]
    if blocked:
        skipped: list[dict[str, Any]] = []
        for task in blocked:
            result = {
                **task,
                "status": "skipped",
                "attempt": 0,
                "tool_result": {"ok": False, "summary": "Skipped because dependency failed"},
            }
            skipped.append(result)
            _callback({**state, "_step": f"task:{task['id']}:{task['tool']}"}, "failed", "Skipped because dependency failed")
        results = [*results, *skipped]
        _callback({**state, "_step": "execute_worker_steps"}, "failed", f"{len(skipped)} task(s) skipped")
        return _append_log({**state, "task_results": results, "agent_status": "verify", "current_task": {}}, "Agent: blocked tasks skipped")

    failed = [item for item in results if item.get("status") != "done"]
    status = "failed" if failed else "done"
    detail = f"Agent loop completed: {len(results) - len(failed)} done, {len(failed)} failed"
    _callback({**state, "_step": "execute_worker_steps"}, status, detail)
    return _append_log({**state, "agent_status": "verify", "current_task": {}}, detail)


def route_after_agent(state: NativeState) -> str:
    return "tool" if state.get("agent_status") == "tool" and state.get("current_task") else "verifier"


def tool_node(state: NativeState) -> NativeState:
    task = dict(state.get("current_task") or {})
    if not task:
        return {**state, "agent_status": "verify"}
    context: dict[str, Any] = {
        "job_id": state.get("job_id", ""),
        "request": state.get("request", ""),
        "discussion": state.get("discussion", ""),
        "intent": state.get("intent", "general"),
        "risk": state.get("risk", "low"),
        "permission_mode": state.get("permission_mode", "auto_review"),
        "plan": state.get("plan", []),
        "task_graph": state.get("task_graph", []),
        "focus_files": state.get("focus_files", []),
    }
    callback = state.get("_callback") or CALLBACKS.get(str(state.get("_callback_key") or state.get("job_id") or ""))
    result = execute_single_task(task, context, callback)
    tool_result = result.get("tool_result")
    task_results = [*state.get("task_results", []), result]
    tool_results = [*state.get("tool_results", []), tool_result] if tool_result else state.get("tool_results", [])
    dispatch_results = [
        item for item in tool_results
        if item and item.get("name") == "ai_dispatcher" and item.get("ok") and item.get("data", {}).get("assignments")
    ]
    worker_assignments = dispatch_results[-1]["data"]["assignments"] if dispatch_results else state.get("worker_assignments", [])
    task_graph = _refresh_task_graph([dict(item) for item in state.get("task_graph", [])], result)
    status = result.get("status", "failed")
    detail = f"{result.get('id')} {status} via {result.get('tool')}"
    _callback({**state, "_step": "execute_worker_steps"}, "running", detail)
    return _append_log(
        {
            **state,
            "task_graph": task_graph,
            "task_results": task_results,
            "tool_results": tool_results,
            "worker_assignments": worker_assignments,
            "current_task": {},
            "agent_status": "decide",
        },
        f"Tool: {detail}",
    )


def execute_single_task(task: dict[str, Any], context: dict[str, Any], callback: StepCallback | None = None) -> dict[str, Any]:
    task_id = str(task["id"])
    tool = str(task["tool"])
    max_attempts = int(task.get("max_attempts", 2))
    last_result: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        if callback:
            callback(f"task:{task_id}:{tool}", "running", f"attempt {attempt}/{max_attempts}")
        task_memory_context = _build_task_memory_context(task, context)
        task_skill_candidates = _build_task_skill_candidates(task, context)
        result = run_tool(
            tool,
            {
                **context,
                "current_task": task,
                "attempt": attempt,
                "task_memory_context": task_memory_context,
                "task_skill_candidates": task_skill_candidates,
            },
        )
        last_result = result
        _record_task_outcome_memory(task, context, result, attempt)
        if result.get("ok"):
            if callback:
                callback(f"task:{task_id}:{tool}", "done", str(result.get("summary", ""))[:1000])
            return {**task, "status": "done", "attempt": attempt, "tool_result": result}
        if callback:
            callback(f"task:{task_id}:{tool}", "failed", f"attempt {attempt}: {result.get('summary', 'failed')}")

    repair = {
        "ok": False,
        "summary": "No direct CLIProxy repair worker is configured in the Claude executor core",
        "data": {"failed_tool": tool, "failed_summary": last_result.get("summary", "")},
    }
    if callback:
        callback(f"repair:{task_id}", "done" if repair.get("ok") else "failed", str(repair.get("summary", ""))[:1000])
    return {**task, "status": "failed", "attempt": max_attempts, "tool_result": last_result, "repair_result": repair}


def execute_task_dag(state: NativeState, context: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = [dict(task) for task in state.get("task_graph", [])]
    if not tasks:
        return []
    callback = state.get("_callback")
    completed: set[str] = set()
    failed: set[str] = set()
    running: set[str] = set()
    results: dict[str, dict[str, Any]] = {}
    max_workers = max(1, min(4, int(__import__("os").getenv("LANGGRAPH_MAX_PARALLEL_TASKS", "3"))))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures: dict[Any, dict[str, Any]] = {}
        while len(results) < len(tasks):
            ready = [
                task for task in tasks
                if task["id"] not in completed
                and task["id"] not in failed
                and task["id"] not in running
                and all(dep in completed for dep in task.get("depends_on", []))
                and not any(dep in failed for dep in task.get("depends_on", []))
            ]
            for task in ready:
                running.add(task["id"])
                futures[pool.submit(execute_single_task, task, context, callback)] = task
            if not futures:
                blocked = [
                    task for task in tasks
                    if task["id"] not in completed and task["id"] not in failed and task["id"] not in running
                ]
                for task in blocked:
                    result = {**task, "status": "skipped", "attempt": 0, "tool_result": {"ok": False, "summary": "Skipped because dependency failed"}}
                    results[task["id"]] = result
                    failed.add(task["id"])
                    if callback:
                        callback(f"task:{task['id']}:{task['tool']}", "failed", "Skipped because dependency failed")
                continue
            for future in as_completed(list(futures.keys())):
                task = futures.pop(future)
                running.discard(task["id"])
                result = future.result()
                results[task["id"]] = result
                if result.get("status") == "done":
                    completed.add(task["id"])
                else:
                    failed.add(task["id"])
                break
    return [results[task["id"]] for task in tasks]


def verifier_node(state: NativeState) -> NativeState:
    _callback({**state, "_step": "verify_result"}, "running", "Verifying native run")
    plan = state.get("plan", [])
    task_graph = state.get("task_graph", [])
    task_failures = [r.get("id", "unknown") for r in state.get("task_results", []) if r.get("status") != "done"]
    tool_failures = [r.get("name", "unknown") for r in state.get("tool_results", []) if not r.get("ok")]
    executed_tools = {str(r.get("name", "")) for r in state.get("tool_results", []) if r.get("ok")}
    execution_tools_ok = state.get("intent") != "engineering" or bool(executed_tools & {"workspace_executor", "coding_agent_executor"})
    dispatch_ok = bool(state.get("worker_assignments"))
    is_onboarding = is_onboarding_agent_flow(state.get("request", ""))
    required_onboarding_tools = {"workspace_inspect", "repo_inspector", "coding_agent_executor", "workspace_diff", "verifier"}
    onboarding_tools_ok = not is_onboarding or required_onboarding_tools.issubset(executed_tools)
    evidence_count = len(executed_tools) + len([r for r in state.get("task_results", []) if r.get("status") == "done"])
    evidence_ok = not is_onboarding or evidence_count >= 6
    strict_gate = os.getenv("LANGGRAPH_STRICT_VERIFY_GATE", "1").lower() in {"1", "true", "yes"}
    missing_requirements: list[str] = []
    if strict_gate:
        if state.get("intent") == "engineering":
            if not bool(executed_tools & {"workspace_executor", "coding_agent_executor"}):
                missing_requirements.append("execution_evidence(coding_agent_executor|workspace_executor)")
            if "workspace_diff" not in executed_tools:
                missing_requirements.append("diff_evidence(workspace_diff)")
            if "verifier" not in executed_tools:
                missing_requirements.append("verification_evidence(verifier)")
        if is_onboarding:
            required_task_names = {"prepare_deploy_checklist", "verify_anti_fake_report_evidence"}
            done_task_names = {
                str(item.get("name") or "")
                for item in state.get("task_results", [])
                if item.get("status") == "done"
            }
            for name in sorted(required_task_names):
                if name not in done_task_names:
                    missing_requirements.append(f"task_done:{name}")
            if evidence_count < 6:
                missing_requirements.append("minimum_evidence_count>=6")
    hard_gate_ok = not strict_gate or not missing_requirements
    verification = {
        "ok": bool(plan and task_graph) and not task_failures and not tool_failures and execution_tools_ok and dispatch_ok and onboarding_tools_ok and evidence_ok and hard_gate_ok,
        "plan_steps": len(plan),
        "task_nodes": len(task_graph),
        "tool_calls": len(state.get("tool_results", [])),
        "tool_failures": tool_failures,
        "task_failures": task_failures,
        "execution_tools_ok": execution_tools_ok,
        "dispatch_ok": dispatch_ok,
        "onboarding_tools_ok": onboarding_tools_ok,
        "evidence_count": evidence_count,
        "evidence_ok": evidence_ok,
        "strict_gate": strict_gate,
        "hard_gate_ok": hard_gate_ok,
        "missing_requirements": missing_requirements,
        "risk": state.get("risk", "low"),
        "permission_mode": state.get("permission_mode", "auto_review"),
    }
    _callback({**state, "_step": "verify_result"}, "done", f"ok={verification['ok']}")
    return _append_log({**state, "verification": verification}, "Verifier: checks completed")


def report_node(state: NativeState) -> NativeState:
    _callback({**state, "_step": "final_report"}, "running", "Composing report")
    verification = state.get("verification", {})
    task_lines = [
        f"- {task['id']}: {task['name']} via {task['tool']}"
        for task in state.get("task_graph", [])
    ]
    tool_lines = [
        f"- {r.get('name')}: {'OK' if r.get('ok') else 'FAIL'} - {r.get('summary', '')}"
        for r in state.get("tool_results", [])
    ]
    dag_lines = [
        f"- {r.get('id')}: {r.get('status')} after {r.get('attempt', 0)} attempt(s) via {r.get('tool')}"
        for r in state.get("task_results", [])
    ]
    assignment_lines = [
        f"- {item.get('worker')}: {item.get('mission')}"
        for item in state.get("worker_assignments", [])
    ]
    result = "\n".join(
        [
            "LangGraph native backend đã chạy xong." if verification.get("ok") else "LangGraph native backend chưa hoàn tất.",
            f"Intent: {state.get('intent', 'general')}",
            f"Risk: {state.get('risk', 'low')}",
            f"Permission mode: {state.get('permission_mode', 'auto_review')}",
            f"Verify: {'OK' if verification.get('ok') else 'NOT OK'}",
            f"Verify detail: execution_tools_ok={verification.get('execution_tools_ok')}, dispatch_ok={verification.get('dispatch_ok')}, onboarding_tools_ok={verification.get('onboarding_tools_ok')}, evidence_ok={verification.get('evidence_ok')}, evidence_count={verification.get('evidence_count')}",
            f"Hard gate: {'OK' if verification.get('hard_gate_ok') else 'FAILED'}",
            f"Missing requirements: {', '.join(verification.get('missing_requirements') or []) or 'None'}",
            "",
            "Anti-fake report evidence:",
            "- Report is marked OK only when required tools ran successfully and the verifier saw completed task/tool evidence.",
            "- Failed tools, skipped DAG nodes, or missing onboarding evidence keep Verify as NOT OK.",
            "- Strict verify gate blocks completion when mandatory evidence is missing.",
            "",
            "Deploy checklist:",
            "- Build frontend assets before service rebuild.",
            "- Run backend tests covering plan/approve/run and verification failure paths.",
            "- Recreate langgraph-manager service and check /api/health after deploy.",
            "- Roll back by restoring the previous image/files if health or verification fails.",
            "",
            "Task graph:",
            *task_lines,
            "",
            "AI worker assignments:",
            *(assignment_lines or ["- No AI worker assignments created"]),
            "",
            "Tool results:",
            *(tool_lines or ["- No tools executed"]),
            "",
            "DAG execution:",
            *(dag_lines or ["- No DAG tasks executed"]),
            "",
            "Execution log:",
            *[f"- {line}" for line in state.get("execution_log", [])],
        ]
    )
    _callback({**state, "_step": "final_report"}, "done", "Report ready")
    return {**state, "result": result}


def build_native_graph():
    graph = StateGraph(NativeState)
    graph.add_node("intake", intake_node)
    graph.add_node("planner", planner_node)
    graph.add_node("policy", policy_node)
    graph.add_node("agent", agent_decision_node)
    graph.add_node("tool", tool_node)
    graph.add_node("worker", worker_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("report", report_node)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "planner")
    graph.add_edge("planner", "policy")
    mode = os.getenv("LANGGRAPH_DAG_EXECUTION_MODE", "parallel").strip().lower()
    if mode in {"legacy", "sequential"}:
        graph.add_edge("policy", "agent")
        graph.add_conditional_edges("agent", route_after_agent, {"tool": "tool", "verifier": "verifier"})
        graph.add_edge("tool", "agent")
    else:
        # Parallel DAG executor: run all ready nodes with bounded concurrency.
        graph.add_edge("policy", "worker")
        graph.add_edge("worker", "verifier")
    graph.add_edge("verifier", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=InMemorySaver())


def run_native_job(
    job_id: str,
    request: str,
    discussion: str,
    callback: StepCallback,
    permission_mode: PermissionMode = "auto_review",
) -> str:
    state = run_native_job_state(job_id, request, discussion, callback, permission_mode)
    return state.get("result", "LangGraph native backend finished without a report.")


def run_native_job_state(
    job_id: str,
    request: str,
    discussion: str,
    callback: StepCallback,
    permission_mode: PermissionMode = "auto_review",
    focus_files: list[dict[str, str]] | None = None,
) -> NativeState:
    CALLBACKS[job_id] = callback
    app = build_native_graph()
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
