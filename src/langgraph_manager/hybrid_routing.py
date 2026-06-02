from __future__ import annotations

from typing import Any

from . import db
from .claude_skill_index import recommend_tools_for_request


def _route_priority_score(success_count: int, failure_count: int) -> float:
    s = max(0, int(success_count))
    f = max(0, int(failure_count))
    total = s + f
    if total <= 0:
        return 0.0
    win_rate = (s + 1.0) / (total + 2.0)
    evidence = min(total, 12) / 12.0
    return (win_rate * 2.0 - 1.0) * (0.5 + 0.5 * evidence) + (s * 0.04) - (f * 0.06)


def _route_tool_boosts(intent: str) -> dict[str, float]:
    # Reuse successful route memory as a light ranking prior for tools.
    # Mapping is intentionally small/safe so default ordering stays stable.
    route_to_tools = {
        "codegraph": ["codegraph", "serena"],
        "python_local": ["serena", "code-review"],
        "docker": ["codegraph", "code-review"],
        "python_docker": ["codegraph", "serena", "code-review"],
        "generic_local": ["git-safety-recovery", "code-review"],
    }
    boosts: dict[str, float] = {}
    for row in db.list_successful_routes(limit=30):
        if str(row.get("task_signature") or "") != intent:
            continue
        route = str(row.get("route") or "")
        score = _route_priority_score(
            int(row.get("success_count") or 0),
            int(row.get("failure_count") or 0),
        )
        if score <= 0:
            continue
        for tool_name in route_to_tools.get(route, []):
            boosts[tool_name] = boosts.get(tool_name, 0.0) + min(0.6, score * 0.35)
    return boosts


def build_routing_plan(query: str, task_complexity: str = "simple", limit: int = 6) -> dict[str, Any]:
    base = recommend_tools_for_request(query, limit=max(2, int(limit)))
    intent = str(base.get("intent") or "")
    route_boosts = _route_tool_boosts(intent) if intent else {}
    ranked = list(base.get("recommended_tools") or [])
    if route_boosts:
        boosted: list[dict[str, Any]] = []
        for item in ranked:
            name = str(item.get("name") or "")
            extra = float(route_boosts.get(name, 0.0))
            merged = dict(item)
            merged["route_boost"] = round(extra, 4)
            merged["selection_score"] = round(float(item.get("selection_score") or 0.0) + extra, 4)
            boosted.append(merged)
        # Keep default intent order as primary key; route_boost only nudges
        # within similar-priority candidates.
        ranked = sorted(
            boosted,
            key=lambda row: (
                int(row.get("priority") or 999),
                -float(row.get("selection_score") or 0.0),
                str(row.get("name") or ""),
            ),
        )
    ordered = ranked[: max(1, int(limit))]
    initial_count = int(base.get("initial_tool_read_count") or (1 if task_complexity == "simple" else 2))
    initial_count = max(1, min(initial_count, len(ordered))) if ordered else 1
    primary = ordered[:initial_count]

    fallback: list[dict[str, Any]] = []
    seen = {str(item.get("name") or "") for item in primary}
    for item in ordered[initial_count:]:
        name = str(item.get("name") or "")
        if not name or name in seen:
            continue
        fallback.append(item)
        if len(fallback) >= 4:
            break
    if len(fallback) < 4:
        for item in base.get("fallback_tool_candidates", []):
            name = str(item.get("name") or "")
            if not name or name in seen or any(str(x.get("name") or "") == name for x in fallback):
                continue
            fallback.append(item)
            if len(fallback) >= 4:
                break

    rationale = [
        {
            "name": str(item.get("name") or ""),
            "priority": int(item.get("priority") or 0),
            "selection_score": float(item.get("selection_score") or 0.0),
            "route_boost": float(item.get("route_boost") or 0.0),
            "reason": str(item.get("reason") or ""),
            "learning_score": float(item.get("learning_score") or 0.0),
            "trials": int(item.get("trials") or 0),
        }
        for item in ordered[:6]
    ]
    return {
        "query": query,
        "intent": intent,
        "task_complexity": task_complexity,
        "candidates": ordered,
        "primary": primary,
        "fallback": fallback,
        "initial_tool_read_count": initial_count,
        "ordered_tool_names": [str(item.get("name") or "") for item in ordered],
        "rationale": rationale,
    }
