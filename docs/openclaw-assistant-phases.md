# LangGraph Assistant Phases

## Goal

Nâng `LangGraph_Manager` từ một `approve -> run -> verify` backend thành một trợ lý cá nhân kiểu OpenClaw nhưng vẫn giữ kiểm soát, audit, và safety gate.

## Current State

Đã có:

- LangGraph runtime với `plan`, `task_graph`, `verify`, `report`
- `session_memory`, `memory_entries`, `route_memory`, `action_lessons`, `skill_performance`
- `pending_actions`, `permission_mode`, `workspace_executor`, `coding_agent_executor`
- callback/task DAG và verify gate

Chưa đủ mạnh ở:

- continuous assistant loop
- autonomy/background execution
- machine action layer ngoài workspace
- richer personal memory and recurrence
- assistant UX giống một agent luôn sống

## Phase 1: Stable Core Assistant

Mục tiêu:

- Củng cố runtime hiện tại để mọi action đều có audit trail, callback, verify, retry rõ ràng
- Chuẩn hóa state machine cho `chat`, `draft`, `planned`, `approved`, `running`, `done`, `failed`

Việc chính:

- Hoàn thiện callback/task telemetry cho mọi đường chạy
- Chuẩn hóa error taxonomy cho tool/runtime
- Gắn chặt `pending_actions` với execution result và artifact
- Bổ sung test backend cho các đường fail/retry/skip

Done khi:

- Tất cả action quan trọng có trace và artifact rõ
- Verify không còn phụ thuộc vào report text
- Full backend test pass ổn định

## Phase 2: Real Assistant Memory

Mục tiêu:

- Biến memory hiện tại thành memory dùng được như trợ lý cá nhân, không chỉ là DB lưu records

Việc chính:

- Tách rõ `user profile`, `working style`, `project memory`, `task memory`, `route memory`
- Thêm memory compaction/summarization định kỳ cho session dài
- Thêm memory write policy: khi nào lưu, mức ưu tiên, TTL nếu cần
- Thêm retrieval ranking tốt hơn theo recency + relevance + source trust

Done khi:

- Một phiên chat dài vẫn giữ được mục tiêu, quyết định, file quan trọng
- Assistant nhớ style làm việc và project context nhất quán qua nhiều job

## Phase 3: Continuous Assistant Loop

Mục tiêu:

- Từ job runner thành assistant loop có thể nhận lệnh, lên action tiếp theo, quay lại khi task đang mở

Việc chính:

- Tạo assistant loop riêng: `observe -> decide -> queue action -> wait -> resume`
- Thêm `open loop state` cho các task nhiều bước
- Tạo scheduler cho recurring tasks và follow-up actions
- Cho assistant tự tạo “next action” thay vì dừng ở một run duy nhất

Done khi:

- Một yêu cầu nhiều bước không cần user thúc từng bước nhỏ
- Assistant có thể quay lại task còn dở sau khi action hoàn tất

## Phase 4: Machine Action Layer

Mục tiêu:

- Mở rộng từ `workspace` sang `host machine actions` có kiểm soát

Việc chính:

- Tạo host action bus riêng cho browser, files, shell, screenshots, app launch
- Tách policy engine cho `read`, `write`, `network`, `credentialed action`
- Thêm approval classes theo mức rủi ro
- Lưu artifact/result chuẩn hóa cho mọi machine action

Done khi:

- Assistant thao tác được máy thật nhưng không phá safety model
- Mọi host action đều replay/audit được

## Phase 5: OpenClaw-Like Product UX

Mục tiêu:

- Trải nghiệm thành một trợ lý “luôn sẵn sàng” hơn là một dashboard task runner

Việc chính:

- Assistant inbox/timeline
- Action cards tốt hơn cho approve/reject/retry
- Session summary tự động
- Project-centric assistant view
- Background tasks, notifications, recurring follow-up

Done khi:

- User có cảm giác đang nói chuyện với một assistant đang theo việc, không chỉ đang mở job thủ công

## Phase 6: Safe Autonomy

Mục tiêu:

- Tăng tự chủ nhưng không rơi vào kiểu “agent làm bừa”

Việc chính:

- Policy profiles theo loại task
- Budget limits cho time/tool/action count
- Stop conditions và escalation rules
- Confidence-based approval escalation
- Post-action reflection ghi vào `action_lessons`

Done khi:

- Assistant tự làm được nhiều hơn nhưng vẫn dừng đúng chỗ khi rủi ro tăng

## Recommended Execution Order

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. Phase 5
6. Phase 6

## Recommended Immediate Build Order

Nếu làm thực dụng, nên ưu tiên:

1. Phase 1: ổn định runtime và verify
2. Phase 2: memory usable thật
3. Phase 3: continuous loop

Lúc đó project đã bắt đầu giống một assistant thật. Phase 4-6 mới là phần đưa nó tiến gần OpenClaw.

## Simplified Next Phases

Để dễ hiểu và dễ thực thi, 2 hướng tiếp theo nên được gom lại thành ladder sau:

### Phase 4A: Linux Machine Control Foundation

Mục tiêu:

- làm assistant điều khiển máy Linux tốt hơn theo cách có kiểm soát

Việc chính:

- mở rộng `host_*` actions cho shell, file, Docker, screenshots, app launch
- chuẩn hóa artifact/result cho host actions
- tách rõ lớp policy cho `read`, `write`, `network`, `credentialed`

Done khi:

- assistant thao tác Linux host đáng tin hơn workspace-only path
- action host có audit/evidence/policy rõ

### Phase 4B: Linux Operator Workflows

Mục tiêu:

- biến machine control thành workflow Linux/DevOps dùng thật

Việc chính:

- workflow đọc log/service/process
- workflow Docker health/check/restart có gate
- workflow browser host và screenshot confirm
- recovery path cho các action Linux phổ biến

Done khi:

- assistant hỗ trợ Linux/DevOps như một operator thực dụng

### Phase 5A: Assistant Inbox And Follow-Through UX

Mục tiêu:

- làm assistant giống “người theo việc” hơn là task runner

Việc chính:

- inbox/attention queue
- session summary
- notifications
- recurring follow-up visibility

Done khi:

- user mở lên là biết việc nào cần xử lý trước

### Phase 5B: Project-Centric Assistant UX

Mục tiêu:

- gắn assistant với project/workstream thay vì từng job rời rạc

Việc chính:

- project-centric view
- action grouping theo project
- summary theo project và workstream

Done khi:

- cảm giác dùng gần hơn một assistant product thực sự

### Phase 6A: Safe Autonomy Controls

Mục tiêu:

- cho assistant tự làm nhiều hơn nhưng có giới hạn rõ

Việc chính:

- budget theo action/time/tool count
- stop reasons
- escalation rules
- approval escalation theo risk/confidence

Done khi:

- agent tự chạy thêm bước nhưng dừng đúng chỗ

### Phase 6B: Managed Autonomy Loops

Mục tiêu:

- cho autonomy chạy theo loop dài hơn mà vẫn kiểm soát được

Việc chính:

- background follow-through
- multi-step continuation budgets
- post-action reflection vào `action_lessons`
- attention/inbox update sau mỗi vòng loop

Done khi:

- assistant theo việc dài hơi hơn mà không rơi vào “làm bừa”

## Simplified Execution Order

Nếu nói ngắn gọn theo đúng 2 hướng đã giải thích:

1. `Machine Control`
   - `Phase 4A`
   - `Phase 4B`
2. `Assistant UX + Autonomy`
   - `Phase 5A`
   - `Phase 5B`
   - `Phase 6A`
   - `Phase 6B`

Khuyến nghị thực thi:

1. `Phase 4A`
2. `Phase 4B`
3. `Phase 5A`
4. `Phase 5B`
5. `Phase 6A`
6. `Phase 6B`

## First Concrete Milestone

Milestone gần nhất nên làm:

- `assistant loop MVP`
- `session memory compaction`
- `next-action queue`
- `host action policy abstraction`

Milestone này xong thì hệ thống sẽ chuyển từ “run một job rồi dừng” sang “theo việc như một trợ lý”.

## Current Completion Snapshot

Tính tới hiện tại, 6 phase đầu đã có lát cắt implementation + test trong repo:

- `Phase 1`
  - verify/job/action artifact
  - structured tool/runtime error taxonomy
  - hydrated UI job contract
  - callback/runtime regression coverage

- `Phase 2`
  - session memory compaction
  - user preference persistence
  - project memory injection
  - ranked route memory recall
  - bounded memory context assembly

- `Phase 3`
  - `next_actions`
  - verification follow-up queue
  - recurring task queue to pending actions/jobs
  - resume visibility through hydrated job + SSE state

- `Phase 4`
  - host/browser action policy classification
  - Linux host shell/file action foundation with policy guard
  - Linux operator workflows for Docker inventory, service status, and service logs
  - gated host write actions for container/service restart
  - Linux recovery workflows capture before/after state and logs for restart evidence
  - gated host file mutation actions (write/move/delete)
  - gated host process signaling path
  - host process recovery workflow captures before/signal/after evidence
  - structured machine-action approval classes
  - replayable host action artifacts/results in unified action contract

- `Phase 5`
  - job/session summary field
  - job notifications field
  - recurring/background task visibility in assistant-facing response state
  - inbox entries carry primary project and related project context
  - project-centric assistant views group jobs by project attention

- `Phase 6`
  - autonomy status object
  - action budget accounting
  - stop reasons (`approval_required`, `verification_failed`, `action_budget_exhausted`, `recovery_budget_exhausted`)
  - approval escalation through structured policy metadata
  - managed loop telemetry (`managed_loop_state`, `can_continue_without_user`, continuation loop budget)
  - auto-continuation guard stops extra loop chaining when continuation budget is exhausted

Sign-off suite currently used:

```bash
. .venv/bin/activate
python -m pytest -q \
  tests/test_tool_registry.py \
  tests/test_phase1_memory.py \
  tests/test_phase2_lessons.py \
  tests/test_task_context_selection.py \
  tests/test_phase23_completion.py \
  tests/test_phase456_completion.py \
  tests/test_native_runtime.py \
  tests/test_native_runtime_parallel.py \
  tests/test_verify_hard_gate.py \
  tests/test_chat_mode.py
```

Latest sign-off result:

- `128 passed, 16 skipped, 3 warnings`
