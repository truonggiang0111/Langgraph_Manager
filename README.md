# LangGraph Manager

Local LangGraph scaffold for the new AI management layer.

n8n should stay as a dispatcher for Telegram/media automation. The old `/dev` orchestration in n8n has been removed from the active Telegram bridge.

## Run

Docker:

```powershell
cd D:\User\File\LangGraph_Manager
docker compose up -d --build
```

Open:

```text
http://localhost:8899
```

Health:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8899/api/health
```

Local Python:

```powershell
cd D:\User\File\LangGraph_Manager
python -m venv .venv
.\.venv\Scripts\pip install -e .[dev]
.\.venv\Scripts\python -m langgraph_manager.cli "kiem tra he thong"
```

## Shape

- `src/langgraph_manager/graph.py`: minimal graph and state.
- `src/langgraph_manager/app.py`: FastAPI + dashboard entrypoint.
- `src/langgraph_manager/db.py`: SQLite job/message/step log store.
- `src/langgraph_manager/cli.py`: local entry point for smoke tests.
- `.env.example`: local paths/config.

## Current UI Flow

1. Create a job from the dashboard.
2. Discuss or add constraints while the job is still `planned`.
3. Regenerate the plan if the discussion changes the task.
4. Approve the plan.
5. Run the approved job through the native LangGraph backend and inspect step logs/results.

The Run button is only shown after approval, so daily use can stay: discuss first, approve only when ready, then execute.

## Claude-Executor Native Backend

The main execution path is intentionally small. LangGraph manages job state, approval, logging, and routing; Claude Code CLI is the coding executor, routed through free-claude-code and CLIProxy.

- `intake`: read request/discussion, classify intent/risk.
- `plan`: build a short execution plan.
- `inspect_workspace`: inspect mounted project context.
- `coding_agent_executor`: delegate code work to the configured Claude executor when needed.
- `verify`: collect diff/status signals without pretending to be the coder.
- `report`: save a structured result back to the job.

The native graph is compiled with an in-memory LangGraph checkpointer and invoked with `thread_id = job_id`, so each run has a serializable state timeline. Runtime callbacks stay outside graph state and are looked up by job id, because checkpointed state must not contain Python functions.

External systems such as n8n, browser automation, web research, or OpenClaw are no longer part of the coding core. CLIProxy remains behind free-claude-code as the model router, not as a direct LangGraph coding worker.

Current native tools:

- `memory_log`: capture request/discussion context.
- `plan_builder`: inspect the active plan and task graph.
- `verifier`: verify task graph structure.
- `repo_inspector`: inspect workspace files and git status.
- `workspace_inspect`: inspect the mounted workspace root, project markers, visible file sample, and git status without asking the model to invent shell commands.
- `workspace_read_file`: safely read a text file inside the workspace with path escape protection, size metadata, SHA-256, and truncation.
- `workspace_diff`: read the current git diff for the whole workspace or one file.
- `workspace_executor`: run gated read-only shell/docker/file inspection commands inside the mounted workspace.
- `coding_agent_executor`: run the configured Claude executor in the mounted workspace, passing the job prompt on stdin.
- `docker_observer`: read Docker status when Docker access is available; otherwise reports that Docker is not mounted.
- `ui_reviewer`: check the manager UI/API health endpoint.

List tools:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8899/api/tools
```

Optional external connectivity check:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8899/api/tools/health
```

Configured Claude executor:

```powershell
CLAUDE_EXECUTOR_COMMAND="docker run --rm -i --user node -e HOME=/home/node -e ANTHROPIC_BASE_URL=http://host.docker.internal:8082 -e ANTHROPIC_AUTH_TOKEN=freecc -e ANTHROPIC_API_KEY=freecc -e CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1 -v D:/User/File:/workspace -v D:/User/File/ClaudeHome:/home/node/.claude -v D:/User/File/ClaudeHome/.claude.json:/home/node/.claude.json -w /workspace/LangGraph_Manager claude-executor-mcp:local -p --model claude-sonnet-4-5-20250929 --permission-mode bypassPermissions --tools default --add-dir /workspace --output-format text"
CLAUDE_EXECUTOR_TIMEOUT=900
```

LangGraph chat/routing uses `CLIPROXY_CHAT_MODEL=gpt-5.4`. Plan mode uses `CLIPROXY_PLAN_MODEL=gpt-5.5`. Code mode sends work directly to the Dockerized Claude Code executor, currently `--model gpt-5.4`, for code/workspace work. It reads the task prompt from stdin, edits the mounted workspace, and keeps Claude Code config/plugins under `D:/User/File/ClaudeHome` for reuse across executor runs. LangGraph mounts that same ClaudeHome read-only at `/claude-home`, builds a shared skill/plugin index from it, and sends ranked routing to Claude with progressive loading hints: start with the first tool for simple tasks, or the first two for composite tasks, then expand only if those are insufficient. The executor runs as the image's non-root `node` user because Claude Code refuses `bypassPermissions` under root. `D:/User/File/ClaudeHome/.claude.json` is also mounted because Claude Code stores marketplace/plugin metadata there. The native engineering graph will use it when the command is configured or the request explicitly asks for Claude/coding-agent delegation.

Skill/plugin discovery endpoints:

- `GET /api/claude/skill-index`: list installed ClaudeHome tools/components.
- `GET /api/claude/skill-search?q=...`: ranked search over tool names, descriptions, component names, and skill excerpts so LangGraph can find likely matches without reading the whole skill set.

Routing payload semantics:

- `ordered_tool_recommendations` / `ordered_tool_names`: ranked tool shortlist.
- `read_until_sufficient`: stop reading new tools once the current set is enough.
- `continue_to_next_tool_if_needed`: if the current tools are not enough, continue in order instead of wandering.

Shared ClaudeHome can also host lightweight local skills. One important example
is `git-safety-recovery`: before risky multi-file edits, debugging, or
refactors, Claude should create a reversible git checkpoint and use that
checkpoint as the fallback path if the current attempt starts compounding
failures.

The compatibility path is:

```text
LangGraph -> claude-executor-mcp:local -> free-claude-code:8082 -> CLIProxy:8317 -> model pool
```

## Agent Core Checklist

These are the baseline pieces required before adding more MCP servers or external skills:

- Workspace awareness: know the mounted root, project markers, candidate source files, and git status.
- Safe file reading: read explicit files through `workspace_read_file` instead of dumping binary/raw content into chat.
- Safe file editing: delegate edits to `coding_agent_executor`; LangGraph should not patch files directly.
- Controlled execution: use `workspace_executor` only for read-only inspection unless a separate executor policy is added.
- Coding delegation: run Claude Code only through `coding_agent_executor`, with the workspace cwd, permission mode, and risk metadata passed in.
- Diff visibility: inspect changes through `workspace_diff` before reporting code work as complete.
- Verification loop: prefer executor-reported checks; LangGraph may inspect diff/status afterward.
- Realtime status: show working action/step events while tools run, then replace them with a final answer/report.
- Recovery: when a tool fails, summarize the failure, avoid repeating the exact same action, and try a safer inspection path first.

Do not treat GitHub/Gmail/browser MCP as the core. They are extension layers after the workspace/action/realtime loop is stable.
