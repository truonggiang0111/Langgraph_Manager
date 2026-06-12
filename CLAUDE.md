# CLAUDE.md

You are the Claude Code executor for LangGraph Manager.

Keep this file short. Use it as the routing and safety map for this project, not
as a full knowledge dump. Read deeper docs only when the task needs them.

## Project Snapshot

- App: local FastAPI + static web UI for LangGraph Manager.
- Purpose: manage chat sessions, actions, logs, memory, file attachments, and
  delegate code/workspace tasks to Claude executor.
- Runtime: Docker service `langgraph-manager` on port `8899`.
- Workspace in container: `/workspace/LangGraph_Manager/LangGraph_Manager`.
- Host repo path: `/home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager`.
- State DB/artifacts: `data/` on host, `/data/state` in container.
- Claude home/config: `/home/giang/Work/AgentStack/ClaudeHome/ClaudeHome`.

## Read When Needed

- Project map and commands: `docs/claude/project-map.md`
- Coding workflow: `docs/claude/workflow.md`
- Safety and secrets: `docs/claude/security.md`
- Decision logging: `docs/claude/decisions.md`

Do not read all docs by default. Pick the smallest doc that answers the current
task. If unsure, inspect the relevant file or run a cheap read-only command.

## Installed Claude Code Plugins

LangGraph may send `TOOL ROUTING` with `intent`, `task_complexity`,
`ordered_tool_recommendations`, `ordered_tool_names`,
`read_until_sufficient`, `continue_to_next_tool_if_needed`,
`initial_tool_read_count`, and a policy. Treat these as high-priority routing
recommendations chosen from the shared ClaudeHome plugin/skill index.

Read tools progressively, not all at once:

- For simple tasks, start with the first ranked tool.
- For composite tasks, start with the first two ranked tools.
- Stop reading new tool docs/skills as soon as you have enough context to do
  the work well.
- Read later recommended tools only when the earlier ones are insufficient.
- If the first ranked tools still are not enough, use
  `fallback_search_queries` and `fallback_tool_candidates` to try specialized
  bug/debug/review/domain skills before broad wandering.
- Do not preload the full shortlist unless the task clearly needs that breadth.
- You may override the ranking when real code/context proves a better route, but
  say why briefly.

Use installed plugins only when they fit the task:

- `claude-code-setup`: inspect/recommend project-specific Claude Code setup.
- `claude-md-management`: audit and improve project memory files.
- `security-guidance`: review generated changes for security issues.
- `code-review`: review code changes and regressions.
- `feature-dev`: structured feature implementation workflow.
- `frontend-design`: frontend/UI implementation support.
- `code-simplifier`: simplify code while preserving behavior.
- `context7`: fetch current library/framework docs when version details matter.
- `serena`: semantic code navigation/editing through an MCP server and LSP-style
  symbol tools.
- `codegraph`: local pre-indexed code knowledge graph for repo search, symbols,
  callers/callees, impact analysis, and fewer broad grep/read calls.
- `git-safety-recovery`: create a git checkpoint before risky edits and recover
  cleanly when a bug hunt or refactor starts compounding failures.

Do not invoke git/public/account workflow plugins unless the user explicitly
asks. The executor image is `claude-executor-mcp:local`; it includes `uvx`,
Python, git, and npx so MCP plugins like Serena and Context7 can start.

## Operating Rules

1. Use real evidence. Do not guess, imply success, or say something is fixed
   until a command, test, API response, or file inspection supports it.
2. Keep changes scoped. Do not refactor unrelated files.
3. Read before editing. Match existing patterns, naming, and style.
4. Prefer focused tests/checks over broad expensive runs unless the change
   affects shared behavior.
5. After edits, verify with the smallest meaningful check and report exactly
   what passed or failed.
6. If a tool/action fails, report the actual error and next recovery step.
7. If output is raw JSON/log/HTML, summarize it for the user instead of dumping
   it into chat.

## Token Discipline

- Do not scan the whole repo unless the task needs it.
- Start with `rg`, targeted file reads, and existing tests.
- Avoid re-reading unchanged files.
- Use summaries for long logs and artifacts.
- If a task explicitly mentions Claude, treat it as direct executor work. Do not
  ask LangGraph to reason first.
- Before risky multi-file debugging or refactor work, prefer a reversible git
  checkpoint so you can recover instead of patching deeper into a bad path.
- For large work, create a short checklist, then execute only the next useful
  step.

## Safety Guard

This is a trusted local dev machine with broad local permissions, but keep these
guards:

- Do not upload, publish, push, email, share, or transmit workspace/personal
  data outside the local machine unless the user explicitly asks for that exact
  action.
- Never reveal, print, log, commit, or send API keys, tokens, passwords, cookies,
  SSH keys, private keys, `.env` values, or credential-store contents.
- Do not touch personal accounts, Gmail, payments, billing, or login flows unless
  the user clearly requested that action.
- Destructive operations need explicit user intent or a narrow, reversible local
  scope.

## Preferred Commands

Use POSIX shell on the host and inside containers.

- Health: `curl http://127.0.0.1:8899/api/health`
- Tool health: `curl http://127.0.0.1:8899/api/tools/health`
- Restart service: `docker compose up -d --no-build --force-recreate langgraph-manager`
- Rebuild Claude MCP executor: `docker build -f Dockerfile.claude-executor-mcp -t claude-executor-mcp:local .`
- App tests: `docker run --rm -v /home/giang/Work/AgentStack/LangGraph_Manager/LangGraph_Manager:/app -w /app langgraph_manager-langgraph-manager python -m pytest tests -q`
- JS check: `node --check src/langgraph_manager/static/app.js`

## Completion Standard

Final response should be concise and concrete:

- What changed
- What was verified
- What remains risky or unverified
- Any exact command/output the user needs to know

Do not end with vague "should work" language. Say what evidence you have.
