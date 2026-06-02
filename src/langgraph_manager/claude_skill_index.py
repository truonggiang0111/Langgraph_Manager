from __future__ import annotations

import json
import os
import re
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import db


_INDEX_CACHE: dict[str, dict[str, Any]] = {}


def claude_home() -> Path:
    return Path(os.getenv("CLAUDE_HOME_PATH", "/claude-home"))


def executor_claude_home() -> str:
    return os.getenv("CLAUDE_EXECUTOR_HOME_PATH", "/home/node/.claude").rstrip("/")


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _executor_path(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(claude_home().resolve()).as_posix()
        return f"{executor_claude_home()}/{rel}"
    except Exception:
        return path.as_posix()


def _text_excerpt(path: Path, limit: int = 1200) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _read_skill_frontmatter(path: Path) -> dict[str, str]:
    text = _text_excerpt(path, 4000)
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    data: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _component_priority(component_type: str) -> int:
    order = {"skill": 0, "command": 1, "agent": 2, "mcp": 3}
    return order.get(component_type, 9)


def _entry_component(components: list[dict[str, Any]]) -> dict[str, Any]:
    if not components:
        return {}
    return sorted(
        components,
        key=lambda item: (_component_priority(str(item.get("type") or "")), str(item.get("name") or "")),
    )[0]


def scan_claude_skill_index() -> dict[str, Any]:
    now = time.time()
    home = claude_home()
    codegraph_enabled = os.getenv("CODEGRAPH_ENABLED", "1").lower() not in {"0", "false", "no"}
    cache_ttl = float(os.getenv("SKILL_INDEX_CACHE_TTL_SECONDS", "20"))
    cache_key = "|".join(
        [
            str(home.resolve()),
            executor_claude_home(),
            "codegraph=1" if codegraph_enabled else "codegraph=0",
        ]
    )
    cache_row = _INDEX_CACHE.get(cache_key) or {}
    cached = cache_row.get("data")
    if cached is not None and (now - float(cache_row.get("ts") or 0.0)) < cache_ttl:
        return cached

    root = home / "plugins" / "cache" / "claude-plugins-official"
    tools: list[dict[str, Any]] = []
    if root.exists():
        for plugin_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
            versions = sorted([p for p in plugin_dir.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
            if not versions:
                continue
            version_dir = versions[0]
            manifest = _safe_read_json(version_dir / ".claude-plugin" / "plugin.json")
            name = str(manifest.get("name") or plugin_dir.name)
            description = str(manifest.get("description") or "")
            author = manifest.get("author") if isinstance(manifest.get("author"), dict) else {}
            components: list[dict[str, str]] = []
            for folder, component_type in (("skills", "skill"), ("commands", "command"), ("agents", "agent")):
                for path in sorted((version_dir / folder).rglob("*.md")) if (version_dir / folder).exists() else []:
                    components.append(
                        {
                            "type": component_type,
                            "name": path.parent.name if path.name.upper() == "SKILL.MD" else path.stem,
                            "path": _executor_path(path),
                            "excerpt": _text_excerpt(path, 900),
                        }
                    )
            mcp_json = version_dir / ".mcp.json"
            if mcp_json.exists():
                components.append({"type": "mcp", "name": name, "path": _executor_path(mcp_json), "excerpt": _text_excerpt(mcp_json, 900)})
            components = components[:20]
            tools.append(
                {
                    "name": name,
                    "description": description,
                    "author": str(author.get("name") or ""),
                    "version": version_dir.name,
                    "path": _executor_path(version_dir),
                    "components": components,
                    "_search_blob": " ".join(
                        [
                            str(name or ""),
                            str(description or ""),
                            str(author.get("name") or ""),
                            " ".join(str(component.get("name") or "") for component in components),
                            " ".join(str(component.get("excerpt") or "") for component in components[:6]),
                        ]
                    ).lower(),
                }
            )
    local_skills_root = home / "skills"
    if local_skills_root.exists():
        for skill_dir in sorted([p for p in local_skills_root.iterdir() if p.is_dir()], key=lambda p: p.name):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta = _read_skill_frontmatter(skill_md)
            name = str(meta.get("name") or skill_dir.name)
            description = str(meta.get("description") or "")
            components = [
                {
                    "type": "skill",
                    "name": name,
                    "path": _executor_path(skill_md),
                    "excerpt": _text_excerpt(skill_md, 900),
                }
            ]
            for path in sorted((skill_dir / "references").rglob("*.md")) if (skill_dir / "references").exists() else []:
                components.append(
                    {
                        "type": "reference",
                        "name": path.stem,
                        "path": _executor_path(path),
                        "excerpt": _text_excerpt(path, 900),
                    }
                )
            components = components[:20]
            tools.append(
                {
                    "name": name,
                    "description": description,
                    "author": "local",
                    "version": "local",
                    "path": _executor_path(skill_dir),
                    "components": components,
                    "_search_blob": " ".join(
                        [
                            str(name or ""),
                            str(description or ""),
                            " ".join(str(component.get("name") or "") for component in components),
                            " ".join(str(component.get("excerpt") or "") for component in components[:6]),
                        ]
                    ).lower(),
                }
            )
    if codegraph_enabled:
        tools.append(
            {
                "name": "codegraph",
                "description": "Local pre-indexed code knowledge graph for structural repo search, symbol context, callers/callees, impact analysis, and MCP code exploration.",
                "author": "colbymchenry",
                "version": "npx/global",
                "path": "codegraph",
                "components": [{"type": "mcp", "name": "codegraph", "path": "codegraph serve --mcp", "excerpt": "Use for repository structure, symbols, call graph, impact analysis, and fewer grep/read tool calls."}],
                "_search_blob": "codegraph structural repo search symbol context callers callees impact mcp",
            }
        )
    data = {"root": str(home), "tools": tools}
    _INDEX_CACHE[cache_key] = {"ts": now, "data": data}
    return data


def _has_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def _query_terms(text: str) -> list[str]:
    terms = re.findall(r"[a-z0-9][a-z0-9._-]{1,}", str(text or "").lower())
    stop = {
        "anh", "chi", "em", "toi", "giup", "help", "please", "with", "that",
        "this", "need", "dung", "hay", "cho", "cua", "tren", "trong", "repo",
        "file", "code", "plugin", "skill", "mcp", "tool",
    }
    filtered = [item for item in terms if item not in stop]
    return list(dict.fromkeys(filtered))


def classify_intent(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").lower())
    if _has_any(value, ["claude.md", "claude md", "memory file", "project memory"]):
        return "claude_memory"
    if _has_any(value, ["security", "secret", "token", "password", "xss", "ssrf", "vulnerability", "bảo mật"]):
        return "security_review"
    if _has_any(value, ["ui", "frontend", "giao diện", "scroll", "button", "form", "css", "html"]):
        return "frontend_bugfix"
    if _has_any(value, ["docs", "documentation", "version", "latest", "tài liệu", "mới nhất"]):
        return "docs_lookup"
    if _has_any(value, ["refactor", "simplify", "clean", "đơn giản", "dọn code"]):
        return "code_simplification"
    if _has_any(value, ["find", "search", "trace", "callers", "callees", "impact", "hàm", "class", "route", "repo", "source", "mò repo", "luồng"]):
        return "repo_navigation"
    if _has_any(value, ["feature", "build", "implement", "thêm chức năng", "tạo chức năng"]):
        return "feature_development"
    if _has_any(value, ["review", "kiểm tra code", "bug", "debug", "fix", "lỗi", "sửa"]):
        return "code_review_or_debug"
    return "general_code"


INTENT_TOOLS = {
    "frontend_bugfix": ["frontend-design", "codegraph", "git-safety-recovery", "serena", "code-review"],
    "repo_navigation": ["codegraph", "serena", "git-safety-recovery", "code-review"],
    "docs_lookup": ["context7", "serena"],
    "security_review": ["security-guidance", "code-review", "serena"],
    "claude_memory": ["claude-md-management", "claude-code-setup"],
    "feature_development": ["feature-dev", "codegraph", "git-safety-recovery", "serena", "code-review"],
    "code_simplification": ["code-simplifier", "git-safety-recovery", "code-review", "serena"],
    "code_review_or_debug": ["codegraph", "git-safety-recovery", "serena", "code-review"],
    "general_code": ["codegraph", "serena", "git-safety-recovery", "code-review"],
}

INTENT_INITIAL_TOOL_COUNT = {
    "frontend_bugfix": 2,
    "repo_navigation": 2,
    "docs_lookup": 1,
    "security_review": 2,
    "claude_memory": 1,
    "feature_development": 2,
    "code_simplification": 2,
    "code_review_or_debug": 2,
    "general_code": 1,
}

COMPLEXITY_TOOL_COUNT = {
    "simple": 1,
    "composite": 2,
    "complex_composite": 2,
}

BASELINE_TOOLS = {"codegraph"}

INTENT_FALLBACK_QUERIES = {
    "frontend_bugfix": [
        "frontend bug debug fix regression scroll state",
        "code review bug regression ui",
        "git checkpoint recovery rollback rescue branch stash",
    ],
    "repo_navigation": [
        "repo trace callers callees impact debug",
        "semantic code navigation route handler",
        "git checkpoint recovery rollback branch",
    ],
    "docs_lookup": [
        "docs version latest library framework",
    ],
    "security_review": [
        "security vulnerability auth secret token review",
    ],
    "claude_memory": [
        "claude md memory project context",
    ],
    "feature_development": [
        "feature implementation architecture code review",
        "repo trace semantic code navigation",
        "git checkpoint rollback before risky refactor",
    ],
    "code_simplification": [
        "simplify refactor clean code review",
    ],
    "code_review_or_debug": [
        "bug debug fix error regression code review",
        "semantic code navigation trace repo",
        "git checkpoint recovery rollback rescue branch stash",
    ],
    "general_code": [
        "bug debug fix review code",
        "git checkpoint recovery rollback",
    ],
}


def search_claude_skill_index(query: str, limit: int = 6) -> dict[str, Any]:
    index = scan_claude_skill_index()
    terms = _query_terms(query)
    results: list[dict[str, Any]] = []
    if not terms:
        return {"query": query, "terms": [], "results": []}
    for item in index.get("tools", []):
        score = 0
        matched: list[str] = []
        name = str(item.get("name") or "").lower()
        description = str(item.get("description") or "").lower()
        author = str(item.get("author") or "").lower()
        components = item.get("components", []) if isinstance(item.get("components"), list) else []
        component_names = " ".join(str(component.get("name") or "").lower() for component in components)
        search_blob = str(item.get("_search_blob") or "")
        fields = {
            "name": name,
            "description": description,
            "author": author,
            "components": component_names,
            "excerpt": search_blob,
        }
        field_hits = Counter()
        for term in terms:
            if term == name:
                score += 12
                matched.append(term)
                field_hits["name"] += 1
                continue
            if term in name:
                score += 8
                matched.append(term)
                field_hits["name"] += 1
            if term in description:
                score += 5
                matched.append(term)
                field_hits["description"] += 1
            if term in component_names:
                score += 4
                matched.append(term)
                field_hits["components"] += 1
            if term in search_blob:
                score += 2
                matched.append(term)
                field_hits["excerpt"] += 1
        if not score:
            continue
        entry = _entry_component(components)
        results.append(
            {
                "name": item.get("name", ""),
                "score": score,
                "matched_terms": list(dict.fromkeys(matched)),
                "match_summary": ", ".join(f"{field}:{count}" for field, count in field_hits.items()),
                "description": item.get("description", ""),
                "path": item.get("path", ""),
                "entry_path": str(entry.get("path") or item.get("path") or ""),
                "entry_type": str(entry.get("type") or ""),
                "entry_name": str(entry.get("name") or item.get("name") or ""),
            }
        )
    ordered = sorted(results, key=lambda item: (-int(item["score"]), str(item["name"])))
    return {"query": query, "terms": terms, "results": ordered[:limit]}


def classify_task_complexity(text: str, intent: str = "") -> dict[str, Any]:
    value = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not value:
        return {"complexity": "simple", "signals": [], "score": 0}

    score = 0
    signals: list[str] = []

    conjunction_count = len(re.findall(r"\b(và|va|and|rồi|roi|đồng thời|dong thoi|liên quan|lien quan)\b", value))
    if conjunction_count >= 1:
        score += min(conjunction_count, 3)
        signals.append("multi_part_request")

    clauses = [part.strip() for part in re.split(r"[,\n;]+", value) if part.strip()]
    if len(clauses) >= 3:
        score += 2
        signals.append("many_clauses")

    domain_markers = {
        "ui": ["ui", "frontend", "giao diện", "scroll", "css", "html", "panel"],
        "repo": ["repo", "route", "hàm", "class", "callers", "callees", "trace", "luồng"],
        "docs": ["docs", "documentation", "version", "latest", "mới nhất"],
        "runtime": ["realtime", "state", "reload", "stream", "backend", "api", "socket"],
        "files": ["pdf", "file", "upload", "attachment", "artifact"],
    }
    matched_domains = [
        name for name, markers in domain_markers.items()
        if _has_any(value, markers)
    ]
    if len(matched_domains) >= 2:
        score += len(matched_domains) - 1
        signals.append("cross_domain")

    if _has_any(value, ["step by step", "từng bước", "theo thứ tự", "trace", "impact", "end-to-end"]):
        score += 1
        signals.append("workflow_trace")

    if _has_any(value, ["đồng thời", "cùng lúc", "vừa", "multiple", "several", "nhiều phần"]):
        score += 2
        signals.append("parallel_or_multi_target")

    if intent in {"feature_development", "code_review_or_debug"} and len(value) > 90:
        score += 1
        signals.append("broad_request")

    if score >= 5:
        complexity = "complex_composite"
    elif score >= 2:
        complexity = "composite"
    else:
        complexity = "simple"
    return {"complexity": complexity, "signals": signals, "score": score}


def fallback_search_queries(text: str, intent: str, complexity: str) -> list[str]:
    queries: list[str] = []
    for item in INTENT_FALLBACK_QUERIES.get(intent, []):
        if item not in queries:
            queries.append(item)
    lower = str(text or "").lower()
    if _has_any(lower, ["bug", "lỗi", "debug", "error", "fail", "failed", "regression"]):
        for item in [
            "bug debug fix error regression code review",
            "semantic code navigation debug repo",
            "git checkpoint recovery rollback rescue branch stash",
        ]:
            if item not in queries:
                queries.append(item)
    if complexity == "complex_composite":
        for item in [
            "feature implementation review semantic navigation",
            "trace impact callers callees route debug",
        ]:
            if item not in queries:
                queries.append(item)
    return queries[:4]


def effective_read_count(ordered: list[dict[str, Any]], requested_count: int) -> int:
    if requested_count <= 0:
        return 0
    non_baseline = 0
    expanded = 0
    for item in ordered:
        expanded += 1
        if str(item.get("name") or "") not in BASELINE_TOOLS:
            non_baseline += 1
        if non_baseline >= requested_count:
            break
    return min(expanded, len(ordered))


def wants_early_checkpoint(text: str, intent: str, complexity: str) -> bool:
    if intent not in {"frontend_bugfix", "feature_development", "code_simplification", "code_review_or_debug"}:
        return False
    lower = str(text or "").lower()
    if complexity == "complex_composite":
        return True
    return _has_any(
        lower,
        [
            "bug", "debug", "fix", "error", "failed", "failure", "regression",
            "lỗi", "sửa", "refactor", "cleanup", "clean up", "rollback",
            "checkpoint", "recover",
        ],
    )


def recommend_tools_for_request(text: str, limit: int = 4) -> dict[str, Any]:
    index = scan_claude_skill_index()
    by_name = {item["name"]: item for item in index.get("tools", [])}
    intent = classify_intent(text)
    complexity_info = classify_task_complexity(text, intent)
    complexity = str(complexity_info.get("complexity") or "simple")
    initial_tool_count = max(
        INTENT_INITIAL_TOOL_COUNT.get(intent, 1),
        COMPLEXITY_TOOL_COUNT.get(complexity, 1),
    )
    perf_rows = db.list_skill_performance()
    perf_map = {str(item.get("skill_name") or ""): item for item in perf_rows}
    recommendations: list[dict[str, Any]] = []
    preferred_names: list[str] = []
    for name in INTENT_TOOLS.get(intent, INTENT_TOOLS["general_code"]):
        if name not in preferred_names:
            preferred_names.append(name)
    search_hits = search_claude_skill_index(text, limit=limit + 2).get("results", [])
    for hit in search_hits:
        hit_name = str(hit.get("name") or "")
        if hit_name and hit_name not in preferred_names:
            preferred_names.append(hit_name)
    for name in preferred_names:
        item = by_name.get(name)
        if not item:
            continue
        intent_rank = preferred_names.index(name) + 1
        perf = perf_map.get(name, {})
        trials = int(perf.get("trials") or 0)
        score = float(perf.get("score") or 0.0)
        status = str(perf.get("status") or "active")
        # Fairness for new skills: strong exploration bonus when trials are low.
        explore_bonus = 2.4 / math.sqrt(trials + 1)
        route_score = round(score + explore_bonus, 4)
        if status == "retired":
            # Keep visibility for review, but usually skip from default ranking.
            route_score -= 100.0
        entry = _entry_component(item.get("components", []))
        recommendations.append(
            {
                "name": item["name"],
                "confidence": "high" if len(recommendations) < 2 else "medium",
                "priority": intent_rank,
                "intent_rank": intent_rank,
                "reason": _reason_for_tool(intent, item["name"]),
                "path": item.get("path", ""),
                "entry_path": str(entry.get("path") or item.get("path") or ""),
                "entry_type": str(entry.get("type") or ""),
                "entry_name": str(entry.get("name") or item["name"]),
                "learning_score": score,
                "trials": trials,
                "explore_bonus": round(explore_bonus, 4),
                "selection_score": route_score,
                "learning_status": status,
                "needs_review": bool(int(perf.get("needs_review") or 0)),
                "components": [
                    {"type": c.get("type", ""), "name": c.get("name", ""), "path": c.get("path", "")}
                    for c in item.get("components", [])[:4]
                ],
            }
        )
    ordered_pool = sorted(
        [item for item in recommendations if item.get("learning_status") != "retired"],
        key=lambda item: (
            int(item.get("intent_rank") or 999),
            -float(item.get("selection_score") or 0.0),
            str(item.get("name") or ""),
        ),
    )
    ordered = ordered_pool[:limit]
    if wants_early_checkpoint(text, intent, complexity):
        checkpoint_index = next((i for i, item in enumerate(ordered) if item["name"] == "git-safety-recovery"), -1)
        if checkpoint_index >= 0 and checkpoint_index + 1 > initial_tool_count:
            initial_tool_count = min(len(ordered), checkpoint_index + 1)
    initial_tool_read_count = effective_read_count(ordered, initial_tool_count)
    fallback_queries = fallback_search_queries(text, intent, complexity)
    fallback_candidates: list[dict[str, Any]] = []
    seen_fallback = {item["name"] for item in ordered}
    for query in fallback_queries:
        for hit in search_claude_skill_index(query, limit=4).get("results", []):
            hit_name = str(hit.get("name") or "")
            if not hit_name or hit_name in seen_fallback:
                continue
            seen_fallback.add(hit_name)
            fallback_candidates.append(
                {
                    "name": hit_name,
                    "score": int(hit.get("score") or 0),
                    "reason": f"Fallback search match for: {query}",
                    "entry_path": str(hit.get("entry_path") or hit.get("path") or ""),
                    "entry_type": str(hit.get("entry_type") or ""),
                    "entry_name": str(hit.get("entry_name") or hit_name),
                    "matched_terms": hit.get("matched_terms", []),
                }
            )
            if len(fallback_candidates) >= 6:
                break
        if len(fallback_candidates) >= 6:
            break
    return {
        "intent": intent,
        "task_complexity": complexity,
        "complexity_score": int(complexity_info.get("score") or 0),
        "complexity_signals": complexity_info.get("signals", []),
        "recommended_tools": ordered,
        "ordered_tool_recommendations": ordered,
        "recommended_read_order": [item["name"] for item in ordered],
        "ordered_tool_names": [item["name"] for item in ordered],
        "baseline_tools": sorted(BASELINE_TOOLS),
        "baseline_tool_policy": "Baseline tools are always-on helpers and do not consume skill slots.",
        "initial_tool_read_count": initial_tool_read_count,
        "read_until_sufficient": True,
        "continue_to_next_tool_if_needed": True,
        "progressive_tool_loading": True,
        "expand_beyond_initial_if_needed": True,
        "do_not_preload_full_shortlist": True,
        "fallback_search_queries": fallback_queries,
        "fallback_tool_candidates": fallback_candidates,
        "adaptive_skill_search_enabled": True,
        "learning_router_enabled": True,
        "retired_tools_skipped": [item["name"] for item in recommendations if item.get("learning_status") == "retired"],
        "tool_routing_policy": (
            "Recommend tools by relevance + historical effectiveness + exploration bonus for low-trial tools. "
            "Read until sufficient, then stop. "
            "If still insufficient, continue to the next tool in order. "
            "Start with the first tool for simple tasks, or the first two tools for composite tasks. "
            "If those still are not enough, use fallback_search_queries and fallback_tool_candidates to look for more specialized bug/debug/review/domain skills before broad repo wandering. "
            "Do not preload the full shortlist unless the task clearly needs that breadth. Retired tools are skipped by default but still reviewable. "
            "Override the ranking only when code/context proves a better route, and briefly say why."
        ),
        "skill_index_root": index.get("root", ""),
    }


def _reason_for_tool(intent: str, name: str) -> str:
    reasons = {
        "codegraph": "Pre-indexed repo graph helps find symbols, routes, callers/callees, and related files before broad grep/read.",
        "serena": "Semantic code navigation/editing through MCP/LSP-style tools.",
        "frontend-design": "Task touches UI/frontend behavior or visual implementation.",
        "code-review": "Review changes and regressions after implementation.",
        "context7": "Fetch current library/framework documentation when version details matter.",
        "security-guidance": "Check secrets, injection, auth, and other security-sensitive risks.",
        "claude-md-management": "Maintain or audit CLAUDE.md/project memory.",
        "claude-code-setup": "Improve Claude Code project setup, automations, MCP, and plugin usage.",
        "feature-dev": "Structured feature development workflow.",
        "code-simplifier": "Simplify/refactor code while preserving behavior.",
        "git-safety-recovery": "Create a checkpoint before risky edits and recover cleanly when an approach starts compounding failures.",
    }
    return reasons.get(name, f"Recommended for {intent}.")
