Design a professional, modern, responsive AI chat web app interface for desktop, tablet, and mobile.

Product concept:
An AI chat and coding-agent workspace where users can chat, create plans, run actions, inspect logs/results, manage sessions, memory, skills, commands, projects, roles, and recurring tasks.

Visual style:
- Primary palette: gray, orange, and white.
- Main background: white or very light gray.
- Sidebar: soft gray.
- Panels: white with subtle borders.
- Accent color: warm orange.
- Typography: friendly, highly readable, professional.
- Use a modern sans-serif font such as Inter, SF Pro, Geist, or similar.
- Keep the UI clean, calm, and work-focused.
- Use subtle shadows, thin borders, and 8px border radius.
- Avoid heavy gradients or decorative clutter.
- The product should feel like a serious SaaS AI workspace, but still approachable.

Important UX requirement:
Use professional compact controls wherever possible. Add collapse/expand buttons to optimize screen space and make the interface feel efficient.

Examples:
- Collapsible left session sidebar.
- Collapsible right inspector panel.
- Expandable action cards.
- Compact action toolbar.
- Expand/collapse logs.
- Expand/collapse result details.
- Mobile drawers and bottom sheets.
- Icon-only buttons with tooltips.
- “More actions” menu for secondary commands.
- Resizable panels on desktop if possible.

Desktop layout:
1. Left sidebar for sessions/chats.
2. Main center area for the chat thread.
3. Right inspector panel for Result and Logs.
4. Top bar with session title, pin state, permission mode, and settings.
5. Composer fixed at the bottom.

Mobile layout:
- Left sidebar becomes a slide-in drawer.
- Right inspector becomes a bottom sheet or separate tab view.
- Composer stays fixed at the bottom.
- Important buttons must be thumb-friendly.
- Use compact icon buttons with clear tooltips or labels where needed.
- Chat, sessions, settings, and action details must be easy to access on small screens.

Main screens to design:

1. Chat Workspace
- Session sidebar grouped by date:
  Today, Yesterday, Previous 7 Days.
- New session button.
- Multi-select sessions with checkboxes.
- Select all / deselect all.
- Delete multiple sessions.
- Rename session.
- Delete single session.
- Active session state.
- Pin indicator for the current session.
- Remember and restore the last opened session.
- Add a sidebar collapse/expand button.
- Add compact action menu for each session.

2. Chat Area
- Clear user and assistant message bubbles.
- Casual chat replies shown as normal assistant responses.
- Non-casual requests routed directly to coding_agent_executor.
- Messages can create pending actions automatically.
- Show action cards inside the chat.
- Action statuses:
  pending, running, done, failed, rejected.
- Action card buttons:
  Approve, Reject, Run, View Logs, View Result.
- Support safe auto-execute actions based on policy.
- Show follow-up action chains when one action creates the next action.
- If executor fails, show a clear warning that auto-recovery is blocked to prevent uncontrolled execution.
- Action cards should be expandable/collapsible.
- Collapsed action cards should show type, status, short summary, and primary next action.

3. Composer
- Large but compact chat input.
- Send message button.
- Plan mode toggle.
- File attachment button.
- Permission mode dropdown showing:
  full_access
- Attached files appear as chips above the input.
- File chips include file name, size, file-type icon, and remove button.
- Composer should have a compact mode on mobile.
- Add an expand button for advanced composer options.

4. Action System
Action cards must support these action kinds:
- coding_agent_executor
- workspace_command
- workspace_inspect
- workspace_read_file
- workspace_diff
- create_memory
- create_skill
- note
- git_checkpoint
- git_restore_checkpoint

Each action card should include:
- Action kind.
- Short description.
- Status badge.
- Risk / permission indicator.
- View logs button.
- View result button.
- Artifact links or images if available.
- Parsed result preview.
- Expand/collapse detail area.
- Compact toolbar with icon buttons.

5. Right Panel / Inspector
- Right panel with tabs:
  Result
  Logs
- Sync with the selected job/action.
- Result tab shows parsed output, artifact links, images, and file paths.
- Logs tab shows grouped streaming output lines, timestamps, and running/done/failed states.
- Empty state when no action is selected.
- Add collapse/expand button for the entire inspector.
- Logs should have foldable sections.
- Long results should be collapsible with “Show more”.

6. Settings Panel
Create a professional settings modal or dedicated settings page with tabs:
- Quality
- Session
- Memory
- Skills & MCP
- Commands
- Projects
- Roles
- Recurring

Each tab should support CRUD UI for:
- Memories
- Skills
- Commands
- Projects
- Role plugins
- Recurring tasks

CRUD interface should include:
- Search.
- Filter.
- Add new.
- Edit.
- Delete.
- Empty state.
- Confirm delete modal.
- Status badges where useful.
- Compact table layout.
- Expandable row details.
- Overflow menu for secondary actions.

7. Memory / Knowledge
Design a Memory section with:
- Session memory per job.
- Global memory store.
- Skill store with trigger metadata.
- Route-memory / lessons:
  successful route, checkpoint, lesson learned.
- Use cards or compact tables that are easy to scan.
- Add expandable details for long memory entries.

8. Quality & Metrics
Design a Quality dashboard with:
- Route quality overview.
- 1-hop executor rate.
- Non-casual route count.
- Route events total.
- Skill performance summary.
- Use simple charts, metric cards, and progress bars.
- Keep it compact and readable.

9. Attachments
- Upload file.
- Parse text attachment with visible size/character limits.
- Store artifact path or URL.
- Show file chip inside chat.
- Remove attachment.
- Show error state if file is too large.
- Attachment preview should be expandable.

10. Backend Policy & Safety
Design UI states for:
- Permission gate based on mode and risk.
- Default command mode is read-only.
- Warning for destructive commands.
- Workspace path / cwd safety.
- Executor timeout.
- Confirmation modal for risky actions.
- Use clear but calm warning language.

11. Executor / Runtime
Design executor status UI:
- Claude executor via command env.
- Show env labels:
  CLAUDE_EXECUTOR_COMMAND
  CODING_AGENT_COMMAND
- Resume session ID.
- Fallback retry when resume ID no longer exists.
- Streaming output grouped by lines, not real-time token streaming.
- Status states:
  connected, running, retrying, failed.
- Runtime details should be collapsible.

12. API / Developer Surface
Design a panel or tab for API/tools health:
- Jobs/chats CRUD + messages.
- Plan regenerate / approve / run.
- Actions approve/reject.
- Memories/skills/commands/projects/roles/recurring CRUD.
- Tools health.
- Claude skill index + search.
- Attachments read/delete.
- Use compact tables, status indicators, and expandable rows.

Component requirements:
- Primary button: orange.
- Secondary button: white with gray border.
- Danger button: soft red.
- Status badges:
  pending: gray
  running: orange
  done: green
  failed: red
  rejected: dark gray
- Use clear icons for:
  chat, file, settings, logs, result, play, approve, reject, pin, trash, edit, collapse, expand, more actions.
- Use tables, cards, tabs, dropdowns, modals, drawers, toast notifications, tooltips, empty states, and loading skeletons.
- Prefer icon buttons with tooltips for compact professional controls.
- Use “More” menus for secondary or destructive actions.
- Make sure all text is readable and does not overflow on mobile.

Deliverables:
Create these UI frames:
1. Desktop main chat workspace.
2. Mobile main chat workspace.
3. Settings panel.
4. Action detail / inspector state.
5. Empty state.
6. Failed action / error state.

Goal:
The interface should look like a polished professional SaaS AI-agent workspace. It must be practical, compact, responsive, easy to scan, and optimized for daily use with AI chat and coding-agent workflows.