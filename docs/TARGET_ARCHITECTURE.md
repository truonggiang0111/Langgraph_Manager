# Target Architecture

## One-Line Decision

Build the assistant around `LangGraph Manager` as the system brain.

Use `Claude CLI` only as a specialized coding executor.

## Why This Is The Chosen Direction

The target is not only an AI coder. The target is a long-running personal assistant on a Linux DevOps machine with:

- memory
- approval
- routing
- verification
- recurring tasks
- background follow-up
- audit trail
- machine actions

That is an orchestration problem, not a CLI problem.

`Claude CLI` is strong at:

- reading code
- editing code
- debugging a repo
- running checks

But `Claude CLI` is not the right place to become the long-term source of truth for:

- job state
- assistant memory
- approval policy
- recurring automation
- action audit
- cross-tool routing

## Core Rule

- `LangGraph decides`
- `Claude executes coding work`
- `Tools do the real machine work`
- `Verify must pass before the system reports done`

## Final System Shape

## 1. LangGraph Core

This is the operating system of the assistant.

Responsibilities:

- state machine
- planning
- task graph orchestration
- approval workflow
- verification gate
- trace/log lifecycle
- background follow-up orchestration

Current home:

- `src/langgraph_manager/native_runtime.py`
- `src/langgraph_manager/agent_runtime.py`
- `src/langgraph_manager/app.py`
- `src/langgraph_manager/db.py`

## 2. Executor Layer

This is where real work is performed.

Responsibilities:

- code implementation
- repo inspection
- shell execution inside workspace
- machine actions on host

Sub-parts:

- `Claude CLI executor` for coding
- `workspace_*` tools for scoped repo work
- future `host_*` tools for Linux/browser/system actions

Rule:

- Executors never become the system brain
- Executors are replaceable workers behind stable LangGraph contracts

## 3. Memory Layer

Memory is not one table. It is several distinct stores with different roles.

Required memory domains:

- `session memory`
- `user memory`
- `project memory`
- `task memory`
- `route memory`
- `action lessons`
- `skill performance`

Rule:

- retrieval must stay structured
- memory write policy must be explicit
- memory cannot be treated as raw chat history only

## 4. Assistant Loop

This is what turns a task runner into an assistant.

Loop:

1. observe
2. decide
3. queue action
4. wait for result
5. verify
6. decide next action
7. stop or continue

Without this loop, the system is still a workflow runner.

## 5. Machine Action Layer

The target machine is a Linux DevOps workstation with near-full power.

That means the assistant must treat machine control as a first-class subsystem, not an ad-hoc shell call.

Required categories:

- shell
- file operations
- Docker
- browser
- screenshots
- app launch
- network-aware tasks

Rule:

- host actions must be policy-gated
- host actions must be auditable
- host actions must produce artifacts or structured evidence

## 6. UI Layer

The UI is not just a dashboard.

It must expose:

- conversation timeline
- action cards
- approve/reject/retry controls
- logs
- artifacts
- verification result
- background status

Rule:

- UI should render assistant state, not invent assistant state

## Hard Guardrails

If future changes violate any of these, they are considered architecture drift.

### Guardrail 1

Do not move primary decision-making into `Claude CLI`.

### Guardrail 2

Do not let report text become the source of truth for success.

Verification state must remain structured.

### Guardrail 3

Do not let ad-hoc shell execution become the default machine control model.

Machine actions need stable tool contracts and policy.

### Guardrail 4

Do not collapse all memory into one generic blob.

Different memory classes must stay separate.

### Guardrail 5

Do not mark tasks done without execution evidence.

Required evidence must come from:

- tool results
- task DAG outcomes
- diff
- logs
- tests
- artifacts

### Guardrail 6

Do not treat full Linux power as permission to skip safety design.

Because this is a DevOps machine, failures can be expensive:

- infra mutation
- secret exposure
- Docker misuse
- filesystem damage
- accidental external side effects

## Anti-Patterns We Explicitly Reject

- `Claude-first brain, LangGraph as thin wrapper`
- `report says OK so task is OK`
- `memory = recent chat history only`
- `retry by repeating the same failing action blindly`
- `shell command strings as the only machine abstraction`
- `assistant autonomy without explicit stop conditions`

## Success Criteria

The architecture is correct when:

- LangGraph owns state and orchestration
- Claude CLI is only one executor among several
- memory is reliable across sessions
- machine actions are structured and auditable
- assistant can follow through multi-step work
- verify blocks fake completion

## Practical Build Order

1. Phase 1: stable runtime, evidence, taxonomy, trace
2. Phase 2: usable assistant memory
3. Phase 3: continuous assistant loop
4. Phase 4: machine action layer
5. Phase 5: assistant UX
6. Phase 6: safe autonomy
