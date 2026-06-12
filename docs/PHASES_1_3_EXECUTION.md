# Phases 1-3 Execution

## Scope

This document is the build contract for the first three phases.

If a change does not help one of these three phases, it is not the current priority.

## Phase 1

## Name

Stable Core Assistant

## Goal

Turn the current backend into a trustworthy execution core with real evidence, not optimistic reporting.

## Build Areas

- runtime state correctness
- verify gate correctness
- action/job artifact handling
- error taxonomy
- callback and trace coverage
- retry/skip/failure observability

## Must-Have Outcomes

- structured verification
- structured tool failure classification
- action artifacts
- job artifacts
- stable callbacks for sequential and parallel paths
- test coverage for run success and run failure

## Done Gate

Phase 1 is done only when all are true:

- verify never depends on prose alone
- important fail paths emit structured error metadata
- action and job outputs can be audited after the fact
- targeted backend tests pass consistently

## Test Gate

Minimum regression command:

```bash
. .venv/bin/activate
python -m pytest -q \
  tests/test_tool_registry.py \
  tests/test_phase1_memory.py \
  tests/test_native_runtime.py \
  tests/test_native_runtime_parallel.py \
  tests/test_verify_hard_gate.py
```

Additional end-to-end checks for this phase:

```bash
. .venv/bin/activate
python -m pytest -q tests/test_chat_mode.py -k \
  'run_marks_failed_when_native_verification_fails or \
   large_native_job_result_is_stored_as_artifact or \
   large_native_job_result_is_hydrated_for_ui or \
   failed_action_exposes_structured_error_payload'
```

## Debug Gate

When something fails, debug in this order:

1. `verification`
2. `tool_results`
3. `task_results`
4. `pending_actions`
5. `trace_event` logs
6. artifact payloads

## Phase 2

## Name

Real Assistant Memory

## Goal

Make memory actually useful for a personal assistant, not just stored records.

## Build Areas

- session compaction
- user profile memory
- project memory retrieval
- task memory selection
- route memory ranking
- memory write rules

## Must-Have Outcomes

- long sessions remain coherent
- important decisions survive across runs
- user preferences reliably reappear
- memory retrieval is relevance-aware and bounded

## Done Gate

Phase 2 is done only when:

- user memory, project memory, and session memory are clearly separated
- memory retrieval is ranked, budgeted, and repeatable
- session compaction prevents context bloat
- assistant recalls meaningful prior context without manual copy-paste

## Test Gate

Required tests to add or keep green:

- session memory compaction
- memory retrieval ranking
- user preference persistence
- project memory injection
- route memory reuse

Suggested command target:

```bash
. .venv/bin/activate
python -m pytest -q tests/test_phase1_memory.py tests/test_phase2_lessons.py tests/test_task_context_selection.py
```

## Debug Gate

When memory looks wrong, inspect:

1. `memory_context()`
2. `session_memory_context()`
3. DB rows for `memory_entries`, `session_memory`, `route_memory`
4. token-budget trimming logic
5. relevance scoring

## Phase 3

## Name

Continuous Assistant Loop

## Goal

Turn the system from a one-shot runner into a real assistant loop that can continue work after actions finish.

## Build Areas

- observe/decide/queue/wait/resume loop
- next-action queue
- open task state
- recurring follow-up
- stop conditions
- safe continuation logic

## Must-Have Outcomes

- assistant can continue a multi-step task without user nudging every step
- assistant can resume after action completion
- assistant can stop cleanly when confidence or policy blocks progress

## Done Gate

Phase 3 is done only when:

- next action is explicit and structured
- the system can resume from waiting states
- recurring/background follow-up is visible in state and UI
- assistant stops when policy says stop

## Test Gate

Required test categories:

- queue next action after success
- queue recovery after failure
- stop after retry budget exhausted
- recurring task creates pending action
- resume loop after action completion

Suggested command target:

```bash
. .venv/bin/activate
python -m pytest -q tests/test_chat_mode.py tests/test_native_runtime.py
```

This suite must be narrowed to reliable Phase 3 coverage if the current broad file still contains legacy failures.

## Debug Gate

When loop behavior is wrong, inspect:

1. job status
2. pending action status
3. recovery depth
4. assistant follow-up routing
5. recurring task selection
6. traces for `action_finish` and `job_run_finish`

## Rules For All Three Phases

- No fake completion
- No architecture drift toward Claude-first control
- No unstructured machine mutation path
- No broad feature work without tests
- No background autonomy without stop conditions

## Current Build Order

1. Keep Phase 1 regression suite green
2. Keep Phase 2 memory ranking/compaction suite green
3. Keep Phase 3 follow-up/recurring loop suite green

## Current Status Snapshot

Phase 1 completed:

- callback stability in parallel native runtime
- normalized tool error classification
- richer verification failure details
- job result artifact storage
- job artifact hydration for UI
- structured pending-action failure payloads
- hydrated verification and next-action UI response contract
- full regression coverage across runtime, tool, and verify paths

Phase 2 completed:

- session memory compaction
- user preference persistence
- project memory injection into context
- route memory hinting and ranked successful-route recall
- bounded, repeatable memory-context assembly

Phase 3 completed:

- explicit `next_actions` on hydrated jobs
- verification follow-up action queue on failed runs
- recurring task queue creates visible pending actions/jobs
- action/result resume visibility via job SSE hydration
- stop conditions preserved for unsupported or legacy action paths

Current regression command used for sign-off:

```bash
. .venv/bin/activate
python -m pytest -q \
  tests/test_tool_registry.py \
  tests/test_phase1_memory.py \
  tests/test_phase2_lessons.py \
  tests/test_task_context_selection.py \
  tests/test_phase23_completion.py \
  tests/test_native_runtime.py \
  tests/test_native_runtime_parallel.py \
  tests/test_verify_hard_gate.py \
  tests/test_chat_mode.py
```
