from __future__ import annotations

import os
import json
import re
import shlex
import subprocess
import hashlib
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    fn: ToolFn


def _run(args: list[str], cwd: str | None = None, timeout: int = 20) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        output = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": output[-6000:],
        }
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"command not found: {exc.filename}"}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": f"timeout after {timeout}s", "output": (exc.stdout or "")[-2000:]}


def workspace_root() -> Path:
    return Path(os.getenv("LANGGRAPH_WORKSPACE_ROOT", "/workspace/LangGraph_Manager")).resolve()


def resolve_workspace_cwd(cwd: str | None = None) -> Path:
    root = workspace_root()
    raw = str(cwd or "").strip()
    if raw in {"/app", "/workspace/app"}:
        raw = ""
    candidate = Path(raw).expanduser() if raw else root
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"cwd escapes workspace root: {resolved}")
    return resolved


IGNORED_WORKSPACE_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}


def is_ignored_workspace_path(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part in IGNORED_WORKSPACE_DIRS for part in parts)


def resolve_workspace_path(path: str) -> Path:
    root = workspace_root()
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path is required")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes workspace root: {resolved}")
    return resolved


def relative_workspace_path(path: Path) -> str:
    root = workspace_root()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def project_markers(root: Path) -> list[str]:
    markers = [
        "pyproject.toml", "package.json", "pnpm-lock.yaml", "package-lock.json",
        "yarn.lock", "requirements.txt", "Dockerfile", "docker-compose.yml",
        "README.md", ".env.example",
    ]
    return [name for name in markers if (root / name).exists()]


def file_entry(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


READ_ONLY_PATTERNS = [
    r"^\s*(pwd|ls|dir|find|rg|grep|cat|sed|head|tail|wc|tree)\b",
    r"^\s*git\s+(status|diff|log|show|branch|rev-parse)\b",
    r"^\s*docker\s+(ps|images|version|info|inspect|logs)\b",
    r"^\s*python\s+-m\s+(pytest|compileall)\b",
    r"^\s*node\s+--check\b",
]

MUTATING_PATTERNS = [
    r"\b(rm|del|erase|rmdir|mv|move|cp|copy|chmod|chown)\b",
    r"\b(git\s+(reset|clean|checkout|switch|restore|commit|push|pull|merge|rebase))\b",
    r"\b(docker\s+(rm|rmi|prune|stop|kill|restart|compose\s+down|compose\s+up|run|exec|build))\b",
    r"\b(pip|npm|pnpm|yarn|uv)\s+(install|add|remove|update)\b",
    r"[>|]\s*[^|]",
]

DESTRUCTIVE_PATTERNS = [
    r"rm\s+-rf\s+(/|\*|\.|~)",
    r"(?<!-)\b(format|shutdown|reboot)\b",
    r"docker\s+(system\s+)?prune",
    r"docker\s+volume\s+rm",
]


def is_read_only_command(command: str) -> bool:
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in READ_ONLY_PATTERNS)


def is_mutating_command(command: str) -> bool:
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in MUTATING_PATTERNS)


def is_destructive_command(command: str) -> bool:
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in DESTRUCTIVE_PATTERNS)


def check_command_policy(command: str, permission_mode: str, risk: str) -> tuple[bool, str]:
    if not command.strip():
        return False, "empty command"
    if "\x00" in command:
        return False, "command contains NUL byte"
    if is_destructive_command(command):
        return False, "blocked destructive command"
    if permission_mode == "default_permissions":
        return (True, "read-only command allowed") if is_read_only_command(command) else (False, "default_permissions allows read-only commands only")
    if permission_mode == "auto_review":
        if risk == "high" and is_mutating_command(command):
            return False, "auto_review blocks high-risk mutating commands"
        return True, "auto_review allowed command after policy check"
    if permission_mode == "full_access":
        return True, "full_access allowed command after destructive guard"
    return False, f"unknown permission_mode: {permission_mode}"


def workspace_executor(context: dict[str, Any]) -> dict[str, Any]:
    command = str(context.get("command") or default_executor_command(context)).strip()
    permission_mode = str(context.get("permission_mode", "auto_review"))
    risk = str(context.get("risk", "low"))
    try:
        cwd = resolve_workspace_cwd(str(context.get("cwd") or ""))
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {"command": command}}
    allowed, reason = check_command_policy(command, permission_mode, risk)
    if not allowed:
        return {
            "ok": False,
            "summary": f"Permission gate blocked command: {reason}",
            "data": {"command": command, "cwd": str(cwd), "permission_mode": permission_mode, "risk": risk},
        }
    result = _run(["sh", "-lc", command], cwd=str(cwd), timeout=int(context.get("timeout", 120)))
    return {
        "ok": result.get("ok", False),
        "summary": f"Executed command via {permission_mode}: {command}" if result.get("ok") else f"Command failed via {permission_mode}: {command}",
        "data": {"command": command, "cwd": str(cwd), "policy": reason, **result},
    }


def default_executor_command(context: dict[str, Any]) -> str:
    task = context.get("current_task") or {}
    task_name = str(task.get("name", "")).lower()
    request = str(context.get("request", "")).lower()
    if "run_checks" in task_name or "test" in request or "pytest" in request:
        return "docker exec langgraph-manager python -m pytest"
    if "docker" in request:
        return "docker ps --format '{{.Names}} {{.Status}}'"
    return "pwd && ls -la"


def executor_environment_notes() -> list[str]:
    root = workspace_root()
    notes = [
        "Executor environment facts:",
        "- Docker daemon is reachable from this executor via /var/run/docker.sock.",
        "- Prefer direct Docker commands (`docker ps`, `docker exec`, `docker logs`, `docker inspect`, `docker restart`) over Compose unless Compose is explicitly required.",
        "- `docker compose` is shimmed to `docker-compose` v1 in this executor; direct Docker commands are still the safer default.",
        "- The LangGraph app is reachable from the executor at `http://host.docker.internal:8899`, not `http://127.0.0.1:8899`.",
        "- Python test dependencies are expected inside the app container, so prefer `docker exec langgraph-manager python -m pytest` for Python tests.",
        "- Node.js checks can run directly in this executor when needed.",
    ]
    if (root / ".git").exists():
        notes.append(f"- The workspace mounted at `{root}` includes `.git`, so git status/diff should work locally.")
    else:
        notes.append(f"- The workspace mounted at `{root}` does not include `.git`; git status/diff/commit may fail even though file edits work.")
    return notes


def memory_log(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "summary": "Captured request/discussion context",
        "data": {
            "request_chars": len(context.get("request", "")),
            "discussion_chars": len(context.get("discussion", "")),
        },
    }


def plan_builder(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "summary": "Plan and task graph are available in LangGraph state",
        "data": {
            "plan_steps": len(context.get("plan", [])),
            "task_nodes": len(context.get("task_graph", [])),
        },
    }


def ai_dispatcher(context: dict[str, Any]) -> dict[str, Any]:
    plan = [str(item) for item in context.get("plan", [])]
    focus_files = context.get("focus_files", [])
    assignments = [
        {
            "worker": "research_ai",
            "mission": "Đọc bối cảnh, file focus và repo hiện tại để xác định phần cần đụng.",
            "inputs": focus_files[:6],
        },
        {
            "worker": "implementation_ai",
            "mission": "Thực hiện các thay đổi nhỏ theo plan, tuân thủ permission mode.",
            "inputs": plan[:6],
        },
        {
            "worker": "verification_ai",
            "mission": "Chạy kiểm tra phù hợp, đọc log lỗi và chỉ xác nhận khi verify OK.",
            "inputs": ["tests", "runtime logs", "result report"],
        },
    ]
    return {
        "ok": True,
        "summary": f"Dispatched work packages to {len(assignments)} AI workers",
        "data": {"assignments": assignments},
    }


def verifier(context: dict[str, Any]) -> dict[str, Any]:
    task_graph = context.get("task_graph", [])
    missing_tools = [task for task in task_graph if not task.get("tool")]
    return {
        "ok": not missing_tools,
        "summary": "Verified task graph structure",
        "data": {"missing_tools": missing_tools, "task_nodes": len(task_graph)},
    }


def repo_inspector(context: dict[str, Any]) -> dict[str, Any]:
    root = workspace_root()
    if not root.exists():
        return {"ok": False, "summary": "Workspace root does not exist", "data": {"root": str(root)}}
    files = [str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    interesting = [f for f in files if f.endswith((".py", ".js", ".ts", ".md", ".yml", ".yaml", ".toml"))][:80]
    git = _run(["git", "status", "--short"], cwd=str(root), timeout=10)
    return {
        "ok": True,
        "summary": f"Inspected repo root with {len(files)} files",
        "data": {
            "root": str(root),
            "interesting_files": interesting,
            "git_status": git.get("output", "") if git.get("ok") else git.get("error", git.get("output", "")),
        },
    }


def workspace_inspect(context: dict[str, Any]) -> dict[str, Any]:
    root = workspace_root()
    if not root.exists():
        return {"ok": False, "summary": "Workspace root does not exist", "data": {"root": str(root)}}
    max_files = max(20, min(300, int(context.get("max_files") or 120)))
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if len(entries) >= max_files:
            break
        if not path.is_file() or is_ignored_workspace_path(path, root):
            continue
        entries.append(file_entry(path, root))
    git_status = _run(["git", "status", "--short"], cwd=str(root), timeout=10)
    git_root = _run(["git", "rev-parse", "--show-toplevel"], cwd=str(root), timeout=10)
    return {
        "ok": True,
        "summary": f"Workspace has {len(entries)} visible file(s) in sample",
        "data": {
            "root": str(root),
            "git_root": (git_root.get("output") or "").strip() if git_root.get("ok") else "",
            "markers": project_markers(root),
            "files": entries,
            "truncated": len(entries) >= max_files,
            "git_status": git_status.get("output", "") if git_status.get("ok") else git_status.get("error", git_status.get("output", "")),
        },
    }


def workspace_read_file(context: dict[str, Any]) -> dict[str, Any]:
    try:
        path = resolve_workspace_path(str(context.get("path") or ""))
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {}}
    if not path.exists():
        return {"ok": False, "summary": "File does not exist", "data": {"path": str(path)}}
    if not path.is_file():
        return {"ok": False, "summary": "Path is not a file", "data": {"path": str(path)}}
    max_chars = max(1000, min(120000, int(context.get("max_chars") or 40000)))
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return {
            "ok": False,
            "summary": "File appears to be binary; use attachment/PDF extraction or a binary-specific tool",
            "data": {"path": relative_workspace_path(path), "bytes": len(raw)},
        }
    text = raw.decode("utf-8", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "ok": True,
        "summary": f"Read {relative_workspace_path(path)} ({len(raw)} bytes)",
        "data": {
            "path": relative_workspace_path(path),
            "absolute_path": str(path),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": text,
            "truncated": truncated,
        },
    }


def workspace_diff(context: dict[str, Any]) -> dict[str, Any]:
    root = workspace_root()
    if not root.exists():
        return {"ok": False, "summary": "Workspace root does not exist", "data": {"root": str(root)}}
    pathspec = str(context.get("path") or "").strip()
    args = ["git", "diff", "--"]
    if pathspec:
        try:
            target = resolve_workspace_path(pathspec)
            args.append(relative_workspace_path(target))
        except ValueError as exc:
            return {"ok": False, "summary": str(exc), "data": {}}
    result = _run(args, cwd=str(root), timeout=int(context.get("timeout") or 20))
    return {
        "ok": bool(result.get("ok")),
        "summary": "Read workspace git diff" if result.get("ok") else "git diff failed",
        "data": {"root": str(root), **result},
    }


def executor_command(executor: str) -> str:
    _ = executor
    return (
        os.getenv("CLAUDE_EXECUTOR_COMMAND")
        or os.getenv("CODING_AGENT_COMMAND")
        or ""
    ).strip()


def executor_timeout_env(executor: str) -> str:
    _ = executor
    return "CLAUDE_EXECUTOR_TIMEOUT"


def _extract_session_id_from_output(output: str) -> str:
    text = str(output or "")
    if not text:
        return ""
    patterns = [
        r'"session_id"\s*:\s*"([^"]+)"',
        r'"sessionId"\s*:\s*"([^"]+)"',
        r"session[_-]?id[:=]\s*([A-Za-z0-9._-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
    return ""


def _run_coding_process(
    args: list[str],
    cwd: Path,
    prompt: str,
    timeout_s: int,
    progress_callback: Any = None,
) -> dict[str, Any]:
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdin is not None
    proc.stdin.write(prompt)
    proc.stdin.close()
    output_chunks: list[str] = []
    started = time.monotonic()
    assert proc.stdout is not None
    while True:
        if time.monotonic() - started > timeout_s:
            proc.kill()
            leftover = proc.stdout.read() or ""
            if leftover:
                output_chunks.append(leftover)
            output = "".join(output_chunks).strip()
            return {"timed_out": True, "returncode": 124, "output": output}
        line = proc.stdout.readline()
        if line:
            output_chunks.append(line)
            if callable(progress_callback):
                try:
                    progress_callback(line, "".join(output_chunks)[-1600:])
                except Exception:
                    pass
            continue
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    tail = proc.stdout.read() or ""
    if tail:
        output_chunks.append(tail)
        if callable(progress_callback):
            try:
                progress_callback(tail, "".join(output_chunks)[-1600:])
            except Exception:
                pass
    output = "".join(output_chunks).strip()
    return {"timed_out": False, "returncode": int(proc.returncode or 0), "output": output}


def run_coding_executor(context: dict[str, Any], executor: str) -> dict[str, Any]:
    executor = "claude"
    command = executor_command(executor)
    if not command:
        env_names = ["CLAUDE_EXECUTOR_COMMAND", "CODING_AGENT_COMMAND"]
        return {
            "ok": False,
            "summary": f"{executor.title()} coding executor is not configured; set {env_names[0]}",
            "data": {
                "executor": executor,
                "env": env_names,
                "hint": "Point this at a coding-agent CLI wrapper that reads the task prompt from stdin and works in the workspace cwd.",
            },
        }

    permission_mode = str(context.get("permission_mode", "auto_review"))
    risk = str(context.get("risk", "low"))
    if permission_mode == "default_permissions":
        return {
            "ok": False,
            "summary": "Permission gate blocked Claude executor: default_permissions is read-only",
            "data": {"permission_mode": permission_mode},
        }
    if permission_mode == "auto_review" and risk == "high":
        return {
            "ok": False,
            "summary": "Permission gate blocked Claude executor: high-risk task needs full_access",
            "data": {"permission_mode": permission_mode, "risk": risk},
        }

    try:
        cwd = resolve_workspace_cwd(str(context.get("cwd") or ""))
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {"command": command}}

    prompt = "\n".join(
        [
            "You are the coding executor for LangGraph Manager.",
            f"You are running through {executor.title()} CLI. Prefer careful reads, focused edits, and concise verification notes.",
            "You must execute the user's request to completion inside this executor whenever local tools can answer it; do not return instructions telling LangGraph or the user to run another task first.",
            "Work only inside the current workspace. Read before editing, keep changes scoped, run relevant checks, and leave a concise result.",
            "Anti-fake-report rule: do not claim a change works unless you cite concrete local evidence from commands, tests, diffs, logs, API responses, or file inspection; explicitly list anything unverified.",
            "This is a trusted local dev machine, so you may use local dev tools, read files, edit files, and run commands needed for the task.",
            "Do not upload, publish, push, email, share, or transmit workspace/personal data outside the local machine unless the user explicitly requested that exact action.",
            "Never reveal API keys, tokens, passwords, cookies, SSH keys, private keys, .env values, credential stores, or personal account data; if relevant, mention only that such data exists and where it is stored.",
            "Only stop early when the required information is unavailable locally or the next step needs external approval/secrets/destructive action; in that case, state the blocker and the exact evidence.",
            f"Permission mode: {permission_mode}",
            f"Risk: {risk}",
            "",
            *executor_environment_notes(),
            "",
            "USER REQUEST:",
            str(context.get("request", "")),
            "",
            "DISCUSSION:",
            str(context.get("discussion", "")),
            "",
            "PLAN:",
            json.dumps(context.get("plan", []), ensure_ascii=False, indent=2),
            "",
            "ROUTE MEMORY HINT:",
            str(context.get("route_memory_hint") or "").strip(),
            "",
            "TOOL ROUTING:",
            json.dumps(
                {
                    "executor": executor,
                    "intent": context.get("intent", ""),
                    "recommended_tools": context.get("recommended_tools", []),
                    "ordered_tool_recommendations": context.get("ordered_tool_recommendations", []),
                    "task_complexity": context.get("task_complexity", "simple"),
                    "complexity_score": context.get("complexity_score", 0),
                    "complexity_signals": context.get("complexity_signals", []),
                    "recommended_read_order": context.get("recommended_read_order", []),
                    "ordered_tool_names": context.get("ordered_tool_names", []),
                    "initial_tool_read_count": context.get("initial_tool_read_count", 1),
                    "read_until_sufficient": context.get("read_until_sufficient", True),
                    "continue_to_next_tool_if_needed": context.get("continue_to_next_tool_if_needed", True),
                    "progressive_tool_loading": context.get("progressive_tool_loading", False),
                    "expand_beyond_initial_if_needed": context.get("expand_beyond_initial_if_needed", True),
                    "do_not_preload_full_shortlist": context.get("do_not_preload_full_shortlist", True),
                    "fallback_search_queries": context.get("fallback_search_queries", []),
                    "fallback_tool_candidates": context.get("fallback_tool_candidates", []),
                    "adaptive_skill_search_enabled": context.get("adaptive_skill_search_enabled", True),
                    "tool_routing_policy": context.get("tool_routing_policy", ""),
                    "skill_index_root": context.get("skill_index_root", ""),
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
            "",
            "FOCUS FILES:",
            json.dumps(context.get("focus_files", []), ensure_ascii=False, indent=2)[:12000],
        ]
    )

    # Normalize a known bad pattern where GIT_SSH_COMMAND value is split into docker flags.
    if "GIT_SSH_COMMAND=ssh -F /home/node/.ssh/config -o StrictHostKeyChecking=accept-new" in command:
        command = command.replace(
            "GIT_SSH_COMMAND=ssh -F /home/node/.ssh/config -o StrictHostKeyChecking=accept-new",
            "GIT_SSH_COMMAND=ssh\\ -F\\ /home/node/.ssh/config\\ -o\\ StrictHostKeyChecking=accept-new",
        )

    try:
        args = shlex.split(command)
        resume_session_id = str(context.get("resume_session_id") or "").strip()
        resumed = False
        if executor == "claude" and resume_session_id and "--resume" not in args:
            args.extend(["--resume", resume_session_id])
            resumed = True
        timeout_s = int(context.get("timeout") or os.getenv(executor_timeout_env(executor), "900"))
        progress_callback = context.get("progress_callback")
        run = _run_coding_process(args, cwd, prompt, timeout_s, progress_callback)
        if run.get("timed_out"):
            output = str(run.get("output") or "")
            return {
                "ok": False,
                "summary": f"{executor.title()} executor timed out after {timeout_s}s",
                "data": {"executor": executor, "command": command, "cwd": str(cwd), "output": output[-4000:]},
            }
        output = str(run.get("output") or "")
        returncode = int(run.get("returncode") or 0)
        resume_missing = (
            executor == "claude"
            and resumed
            and returncode != 0
            and "No conversation found with session ID" in output
        )
        if resume_missing:
            args_retry = [arg for arg in args]
            if "--resume" in args_retry:
                idx = args_retry.index("--resume")
                if idx + 1 < len(args_retry):
                    del args_retry[idx:idx + 2]
            run_retry = _run_coding_process(args_retry, cwd, prompt, timeout_s, progress_callback)
            if run_retry.get("timed_out"):
                output_retry = str(run_retry.get("output") or "")
                return {
                    "ok": False,
                    "summary": f"{executor.title()} executor timed out after {timeout_s}s",
                    "data": {"executor": executor, "command": command, "cwd": str(cwd), "output": output_retry[-4000:]},
                }
            output = str(run_retry.get("output") or "")
            returncode = int(run_retry.get("returncode") or 0)
            args = args_retry
        return {
            "ok": returncode == 0,
            "summary": f"{executor.title()} executor completed" if returncode == 0 else f"{executor.title()} executor failed",
            "data": {
                "executor": executor,
                "command": command,
                "resolved_args": args,
                "cwd": str(cwd),
                "exit_code": returncode,
                "resumed_session_id": resume_session_id if resumed else "",
                "session_id": _extract_session_id_from_output(output),
                "output": output[-12000:],
            },
        }
    except FileNotFoundError as exc:
        return {"ok": False, "summary": f"{executor.title()} executor command not found: {exc.filename}", "data": {"executor": executor, "command": command}}
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")).strip()
        return {
            "ok": False,
            "summary": f"{executor.title()} executor timed out after {context.get('timeout') or os.getenv(executor_timeout_env(executor), '900')}s",
            "data": {"executor": executor, "command": command, "cwd": str(cwd), "output": output[-4000:]},
        }


def coding_agent_executor(context: dict[str, Any]) -> dict[str, Any]:
    return run_coding_executor(context, "claude")


def claude_executor(context: dict[str, Any]) -> dict[str, Any]:
    return run_coding_executor(context, "claude")


def docker_observer(context: dict[str, Any]) -> dict[str, Any]:
    ps = _run(["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Ports}}"], timeout=20)
    if not ps.get("ok") and "command not found" in str(ps.get("error", "")):
        return {
            "ok": True,
            "summary": "Docker CLI is not available inside this container; mount Docker access later to enable live Docker checks",
            "data": ps,
        }
    return {
        "ok": ps.get("ok", False),
        "summary": "Observed running Docker containers" if ps.get("ok") else "Docker observer failed",
        "data": ps,
    }


def ui_reviewer(context: dict[str, Any]) -> dict[str, Any]:
    url = os.getenv("LANGGRAPH_UI_URL", "http://host.docker.internal:8899/api/health")
    try:
        with urlopen(url, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= resp.status < 300,
                "summary": f"HTTP {resp.status} from {url}",
                "data": {"body": body[:1200]},
            }
    except Exception as exc:
        if not os.getenv("LANGGRAPH_UI_URL") or "127.0.0.1:8899" in url:
            return {
                "ok": True,
                "summary": f"UI health skipped; default local UI is not reachable in this runtime: {exc}",
                "data": {"url": url, "skipped": True},
            }
        return {"ok": False, "summary": f"UI health check failed: {exc}", "data": {"url": url}}


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None, token: str = "", timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"content-type": "application/json; charset=utf-8"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = Request(url, data=body, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def _cliproxy_api_key(base_url: str) -> str:
    direct = os.getenv("CLIPROXY_API_KEY", "").strip()
    if direct:
        return direct
    mgmt_password = os.getenv("CLIPROXY_MANAGEMENT_PASSWORD", "").strip()
    if not mgmt_password:
        return ""
    config = _http_json("GET", f"{base_url}/v0/management/config", token=mgmt_password, timeout=20)
    keys = config.get("api-keys") or []
    return str(keys[0]) if keys else ""


def cliproxy_chat(messages: list[dict[str, str]], system_prompt: str = "") -> dict[str, Any]:
    base_url = os.getenv("CLIPROXY_BASE_URL", "http://host.docker.internal:8317").rstrip("/")
    default_model = os.getenv("CLIPROXY_CHAT_MODEL", os.getenv("CLIPROXY_MODEL", "gpt-5.5"))
    if "planner trước khi thực thi" in system_prompt:
        model = os.getenv("CLIPROXY_PLAN_MODEL", default_model)
    else:
        model = default_model
    try:
        api_key = _cliproxy_api_key(base_url)
        if not api_key:
            return {"ok": False, "summary": "Missing CLIProxy API key", "data": {"base_url": base_url, "model": model}}
    except Exception as exc:
        return {"ok": False, "summary": f"CLIProxy API key lookup failed: {exc}", "data": {"base_url": base_url, "model": model}}

    prompt_messages: list[dict[str, str]] = []
    if system_prompt:
        prompt_messages.append({"role": "system", "content": system_prompt})
    prompt_messages.extend(messages[-16:])
    payload = {
        "model": model,
        "messages": prompt_messages,
        "temperature": float(os.getenv("CLIPROXY_CHAT_TEMPERATURE", "0.5")),
        "max_tokens": int(os.getenv("CLIPROXY_CHAT_MAX_TOKENS", "1200")),
    }
    try:
        data = _http_json("POST", f"{base_url}/v1/chat/completions", payload, token=api_key, timeout=int(os.getenv("CLIPROXY_TIMEOUT_SECONDS", "180")))
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return {
            "ok": bool(content),
            "summary": "CLIProxy chat returned content" if content else "CLIProxy chat returned empty content",
            "data": {"model": data.get("model", model), "content": content, "usage": data.get("usage", {})},
        }
    except Exception as exc:
        return {"ok": False, "summary": f"CLIProxy chat failed: {exc}", "data": {"base_url": base_url, "model": model}}


def cliproxy_vision(prompt: str, image_base64: str, mime_type: str = "image/png", system_prompt: str = "") -> dict[str, Any]:
    base_url = os.getenv("CLIPROXY_BASE_URL", "http://host.docker.internal:8317").rstrip("/")
    model = os.getenv(
        "CLIPROXY_VISION_MODEL",
        os.getenv("CLIPROXY_CHAT_MODEL", os.getenv("CLIPROXY_MODEL", "gpt-5.5")),
    )
    try:
        api_key = _cliproxy_api_key(base_url)
        if not api_key:
            return {"ok": False, "summary": "Missing CLIProxy API key", "data": {"base_url": base_url, "model": model}}
    except Exception as exc:
        return {"ok": False, "summary": f"CLIProxy API key lookup failed: {exc}", "data": {"base_url": base_url, "model": model}}

    user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if image_base64:
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
            }
        )
    prompt_messages: list[dict[str, Any]] = []
    if system_prompt:
        prompt_messages.append({"role": "system", "content": system_prompt})
    prompt_messages.append({"role": "user", "content": user_content})
    payload = {
        "model": model,
        "messages": prompt_messages,
        "temperature": float(os.getenv("CLIPROXY_CHAT_TEMPERATURE", "0.2")),
        "max_tokens": int(os.getenv("CLIPROXY_VISION_MAX_TOKENS", os.getenv("CLIPROXY_CHAT_MAX_TOKENS", "1200"))),
    }
    try:
        data = _http_json(
            "POST",
            f"{base_url}/v1/chat/completions",
            payload,
            token=api_key,
            timeout=int(os.getenv("CLIPROXY_TIMEOUT_SECONDS", "180")),
        )
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return {
            "ok": bool(content),
            "summary": "CLIProxy vision returned content" if content else "CLIProxy vision returned empty content",
            "data": {"model": data.get("model", model), "content": content, "usage": data.get("usage", {})},
        }
    except Exception as exc:
        return {"ok": False, "summary": f"CLIProxy vision failed: {exc}", "data": {"base_url": base_url, "model": model}}


def cliproxy_chat_stream(messages: list[dict[str, str]], system_prompt: str = ""):
    base_url = os.getenv("CLIPROXY_BASE_URL", "http://host.docker.internal:8317").rstrip("/")
    model = os.getenv("CLIPROXY_CHAT_MODEL", os.getenv("CLIPROXY_MODEL", "gpt-5.5"))
    api_key = _cliproxy_api_key(base_url)
    if not api_key:
        raise RuntimeError("Missing CLIProxy API key")

    prompt_messages: list[dict[str, str]] = []
    if system_prompt:
        prompt_messages.append({"role": "system", "content": system_prompt})
    prompt_messages.extend(messages[-16:])
    payload = {
        "model": model,
        "messages": prompt_messages,
        "temperature": float(os.getenv("CLIPROXY_CHAT_TEMPERATURE", "0.5")),
        "max_tokens": int(os.getenv("CLIPROXY_CHAT_MAX_TOKENS", "1200")),
        "stream": True,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "content-type": "application/json; charset=utf-8",
        "accept": "text/event-stream",
        "authorization": f"Bearer {api_key}",
    }
    req = Request(f"{base_url}/v1/chat/completions", data=body, headers=headers, method="POST")
    stream_timeout = int(os.getenv("CLIPROXY_STREAM_TIMEOUT_SECONDS", os.getenv("CLIPROXY_TIMEOUT_SECONDS", "45")))
    with urlopen(req, timeout=stream_timeout) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            content = delta.get("content") or ""
            if content:
                yield str(content)


TOOLS: dict[str, ToolSpec] = {
    "memory_log": ToolSpec("memory_log", "Capture request and discussion context", memory_log),
    "plan_builder": ToolSpec("plan_builder", "Inspect current plan/task graph", plan_builder),
    "ai_dispatcher": ToolSpec("ai_dispatcher", "Split work into AI worker assignments", ai_dispatcher),
    "verifier": ToolSpec("verifier", "Verify task graph consistency", verifier),
    "repo_inspector": ToolSpec("repo_inspector", "Inspect repository files and git status", repo_inspector),
    "workspace_inspect": ToolSpec("workspace_inspect", "Inspect workspace root, project markers, file sample, and git status", workspace_inspect),
    "workspace_read_file": ToolSpec("workspace_read_file", "Read a text file inside the workspace with path safety and truncation", workspace_read_file),
    "workspace_diff": ToolSpec("workspace_diff", "Read git diff for the workspace or one file", workspace_diff),
    "workspace_executor": ToolSpec("workspace_executor", "Run gated read-only shell/docker/file inspection commands inside the mounted workspace", workspace_executor),
    "coding_agent_executor": ToolSpec("coding_agent_executor", "Run Claude coding executor against the mounted workspace", coding_agent_executor),
    "claude_executor": ToolSpec("claude_executor", "Run Claude Code against the mounted workspace", claude_executor),
    "docker_observer": ToolSpec("docker_observer", "Read-only Docker status", docker_observer),
    "ui_reviewer": ToolSpec("ui_reviewer", "Read-only UI/API health check", ui_reviewer),
}


def run_tool(name: str, context: dict[str, Any]) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if not spec:
        return {"ok": False, "summary": f"Unknown tool: {name}", "data": {}}
    result = spec.fn(context)
    return {"name": name, **result}


def describe_tools() -> list[dict[str, str]]:
    return [{"name": spec.name, "description": spec.description} for spec in TOOLS.values()]
