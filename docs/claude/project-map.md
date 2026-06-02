# Project Map

Use this file when you need to orient in the repo without scanning everything.

## Important Files

- `src/langgraph_manager/app.py`
  - FastAPI app, chat routes, action routing, attachments, memory, settings APIs.
- `src/langgraph_manager/tool_registry.py`
  - Workspace tools, Claude executor wrapper, command policy, CLIProxy calls.
- `src/langgraph_manager/static/app.js`
  - Main frontend state, chat rendering, polling, action cards, attachments.
- `src/langgraph_manager/static/styles.css`
  - UI layout and visual states.
- `src/langgraph_manager/static/index.html`
  - Static shell and cache-busting asset versions.
- `src/langgraph_manager/db.py`
  - SQLite schema and persistence helpers.
- `src/langgraph_manager/agent_runtime.py`
  - Native LangGraph-ish runtime for manager jobs.
- `src/langgraph_manager/native_runtime.py`
  - DAG/task state runtime and verification.
- `docker-compose.yml`
  - Local service, mounted state, workspace, Claude executor command.
- `tests/test_chat_mode.py`
  - Most backend behavior around chat/actions/UI contracts.
- `tests/test_tool_registry.py`
  - Workspace and executor tool behavior.

## Runtime Shape

Host:

- Repo: `D:/User/File/LangGraph_Manager`
- Workspace root mounted to executor: `D:/User/File` -> `/workspace`
- Claude config: `D:/User/File/ClaudeHome`

Container:

- App code: `/app/src`
- Workspace root: `/workspace/LangGraph_Manager`
- State: `/data/state`

## Common Failure Modes

- Python source changes need service recreate because Uvicorn imports modules at
  startup.
- Static changes usually update via bind mount, but browser may need cache-bust
  or Ctrl+F5.
- `cwd=/workspace/app` is wrong for this app; normalize/use
  `/workspace/LangGraph_Manager`.
- Claude `bypassPermissions` cannot run as root, so executor uses `--user node`.
- `.claude.json` must be readable by the executor user.
