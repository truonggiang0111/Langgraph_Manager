from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from langgraph_manager import app as app_module
from langgraph_manager import db
from langgraph_manager.hybrid_routing import build_routing_plan


def make_plugin(root: Path, name: str, description: str = "", skill: bool = False, mcp: bool = False) -> None:
    plugin = root / "plugins" / "cache" / "claude-plugins-official" / name / "1.0.0"
    manifest = plugin / ".claude-plugin"
    manifest.mkdir(parents=True, exist_ok=True)
    (manifest / "plugin.json").write_text(
        json.dumps({"name": name, "description": description or name, "author": {"name": "bench"}}),
        encoding="utf-8",
    )
    if skill:
        d = plugin / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"# {name}\n{description}", encoding="utf-8")
    if mcp:
        (plugin / ".mcp.json").write_text(json.dumps({name: {"command": "npx"}}), encoding="utf-8")


def extract_long_term_memory_titles(ctx: str) -> list[str]:
    titles: list[str] = []
    in_long = False
    for line in ctx.splitlines():
        text = line.strip()
        if text.startswith("long_term_memory:"):
            in_long = True
            continue
        if not in_long:
            continue
        if text and re.match(r"^[a-z_]+:", text):
            break
        m = re.match(r"- \[[^\]]+\] (.*?) \(tags:", text)
        if m:
            titles.append(m.group(1).strip())
    return titles


CASES = [
    {
        "name": "UI bug",
        "query": "sửa lỗi UI chat bị nhảy scroll ở mobile",
        "expected_tools": {"frontend-design", "codegraph"},
        "expected_memory": "Fix UI scroll jump mobile",
        "task_sig_expect": "frontend_ui",
        "preferred_route": "codegraph",
    },
    {
        "name": "Repo trace",
        "query": "tìm route xử lý auth và trace callers trong repo",
        "expected_tools": {"codegraph", "serena"},
        "expected_memory": "Route callers analysis",
        "task_sig_expect": "repo_navigation",
        "preferred_route": "codegraph",
    },
    {
        "name": "General debug",
        "query": "debug lỗi timeout API và tìm nguyên nhân",
        "expected_tools": {"codegraph", "code-review"},
        "expected_memory": "Debug auth timeout API",
        "task_sig_expect": "code_review_or_debug",
        "preferred_route": "python_local",
    },
    {
        "name": "Project memory",
        "query": "cập nhật claude.md memory cho project này",
        "expected_tools": {"claude-md-management", "claude-code-setup"},
        "expected_memory": "CLAUDE memory policy",
        "task_sig_expect": "claude_memory",
        "preferred_route": "generic_local",
    },
]


def setup_env(plugin_root: Path) -> None:
    make_plugin(plugin_root, "frontend-design", "Frontend/UI bugfix skill", skill=True)
    make_plugin(plugin_root, "serena", "Semantic code navigation MCP", mcp=True)
    make_plugin(plugin_root, "code-review", "Code review and regression checks", skill=True)
    make_plugin(plugin_root, "context7", "Library docs and version lookup MCP", mcp=True)
    make_plugin(plugin_root, "feature-dev", "Feature development workflow", skill=True)
    make_plugin(plugin_root, "security-guidance", "Security review guidance", skill=True)
    make_plugin(plugin_root, "claude-md-management", "Project memory management", skill=True)
    make_plugin(plugin_root, "claude-code-setup", "Claude setup", skill=True)
    os.environ["CLAUDE_HOME_PATH"] = str(plugin_root)
    os.environ["CLAUDE_EXECUTOR_HOME_PATH"] = "/home/node/.claude"
    os.environ["CODEGRAPH_ENABLED"] = "1"
    os.environ["SKILL_INDEX_CACHE_TTL_SECONDS"] = "0"


def setup_memories() -> None:
    entries = [
        ("Fix UI scroll jump mobile", "Applied overflow-anchor + near-bottom autoscroll guard", "ui,frontend,scroll,mobile"),
        ("Route callers analysis", "Use codegraph to inspect callers/callees impact", "route,callers,impact,codegraph"),
        ("Debug auth timeout API", "Trace gateway timeout by route + upstream latency", "backend,api,timeout,auth"),
        ("CLAUDE memory policy", "Store stable decisions and avoid duplicate context injection", "claude,memory,policy"),
    ]
    for title, content, tags in entries:
        db.create_memory("note", title, content, tags, "bench")


def run_once(with_route_memory: bool, job_id: str) -> dict:
    job = db.create_chat_session(job_id, title="Bench")
    env_fp = app_module.env_fingerprint_for_job(job)
    rows = []
    tool_top1 = 0
    tool_top2 = 0
    mem_top1 = 0
    mem_top3 = 0
    route_hint_hits = 0

    for case in CASES:
        query = case["query"]
        db.add_message(job["id"], "user", query)
        app_module.refresh_session_memory(job["id"])
        task_sig = app_module.task_signature_for_request(query)
        if with_route_memory:
            db.record_route_memory(env_fp, task_sig, case["preferred_route"], ok=True)
            db.record_route_memory(env_fp, task_sig, "generic_local", ok=False, error_class="runtime_error")

        t0 = time.perf_counter()
        plan = build_routing_plan(query, task_complexity="composite", limit=6)
        dt = (time.perf_counter() - t0) * 1000
        top = [item.get("name") for item in plan.get("candidates", [])]
        top1_hit = int(bool(top) and top[0] in case["expected_tools"])
        top2_hit = int(any(name in case["expected_tools"] for name in top[:2]))
        tool_top1 += top1_hit
        tool_top2 += top2_hit

        ctx = app_module.memory_context(job["id"])
        titles = extract_long_term_memory_titles(ctx)
        m1 = titles[0] if titles else ""
        m3 = titles[:3]
        m1_hit = int(m1 == case["expected_memory"])
        m3_hit = int(case["expected_memory"] in m3)
        mem_top1 += m1_hit
        mem_top3 += m3_hit

        hint = app_module.build_route_memory_hint(job, query)
        route_hit = int(case["preferred_route"] in hint) if with_route_memory else int(hint == "")
        route_hint_hits += route_hit

        rows.append(
            {
                "case": case["name"],
                "tool_top2": top[:2],
                "tool_top1_hit": top1_hit,
                "tool_top2_hit": top2_hit,
                "memory_top1": m1,
                "memory_top3": m3,
                "memory_top1_hit": m1_hit,
                "memory_top3_hit": m3_hit,
                "route_hint_has_expected": route_hit,
                "route_hint": hint,
                "ms": round(dt, 2),
            }
        )

    return {
        "with_route_memory": with_route_memory,
        "rows": rows,
        "summary": {
            "tool_top1_hits": tool_top1,
            "tool_top2_hits": tool_top2,
            "memory_top1_hits": mem_top1,
            "memory_top3_hits": mem_top3,
            "route_hint_hits": route_hint_hits,
            "total": len(CASES),
            "avg_ms": round(sum(item["ms"] for item in rows) / max(1, len(rows)), 2),
        },
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        os.environ["LANGGRAPH_STATE_DIR"] = td
        setup_env(Path(td))
        db.init_db()
        setup_memories()
        baseline = run_once(with_route_memory=False, job_id="bench_job_base")
        with_routes = run_once(with_route_memory=True, job_id="bench_job_routes")
        print(json.dumps({"baseline": baseline, "with_success_routes": with_routes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
