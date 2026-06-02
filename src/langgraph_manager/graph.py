from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .native_runtime import choose_tools, classify_intent, estimate_risk, make_user_plan


class ManagerState(TypedDict, total=False):
    request: str
    plan: list[str]
    status: str
    result: str


def plan_node(state: ManagerState) -> ManagerState:
    request = state.get("request", "").strip()
    intent = classify_intent(request)
    risk = estimate_risk(request)
    tools = choose_tools(intent, request)
    plan = make_user_plan(request, intent, risk, tools)
    return {**state, "request": request, "plan": plan, "status": "planned"}


def execute_node(state: ManagerState) -> ManagerState:
    request = state.get("request") or "(empty request)"
    return {
        **state,
        "status": "done",
        "result": f"LangGraph manager scaffold received: {request}",
    }


def build_graph():
    graph = StateGraph(ManagerState)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", END)
    return graph.compile()
