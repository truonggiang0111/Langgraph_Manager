import {
  Action,
  ActionStatus,
  AttachedFile,
  Command,
  MemoryItem,
  Message,
  Project,
  RecurringTask,
  Session,
  Skill,
} from './types';

export type PermissionMode = 'default_permissions' | 'auto_review' | 'full_access';

export interface BackendJob {
  id: string;
  title?: string;
  status?: string;
  request?: string;
  result?: string;
  error?: string;
  plan?: string[];
  messages?: Array<{
    id?: string | number;
    role?: string;
    content?: string;
    created_at?: string;
    attachments?: Array<{
      id?: string;
      name?: string;
      size?: number;
      type?: string;
    }>;
  }>;
  pending_actions?: Array<{
    id: string | number;
    title?: string;
    kind?: string;
    status?: string;
    risk?: string;
    preview?: string;
    result?: string;
    error?: string;
    created_at?: string;
    updated_at?: string;
    payload?: Record<string, unknown>;
  }>;
  steps?: Array<{
    name?: string;
    status?: string;
    detail?: string;
    started_at?: string;
    finished_at?: string;
  }>;
  focus_files?: Array<{ name?: string; content?: string }>;
  created_at?: string;
  updated_at?: string;
  permission_mode?: PermissionMode;
}

export interface WorkspaceData {
  sessions: Session[];
  activeJob: BackendJob | null;
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has('content-type') && init.body) {
    headers.set('content-type', 'application/json');
  }
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

function asDate(value?: string): Date {
  const date = value ? new Date(value) : new Date();
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function actionStatus(value?: string): ActionStatus {
  if (value === 'running' || value === 'done' || value === 'failed' || value === 'rejected') return value;
  return 'pending';
}

function actionRisk(value?: string): Action['risk'] {
  if (value === 'medium' || value === 'high') return value;
  return 'low';
}

function actionKind(value?: string): Action['kind'] {
  const known: Action['kind'][] = [
    'coding_agent_executor',
    'workspace_command',
    'workspace_inspect',
    'workspace_read_file',
    'workspace_diff',
    'create_memory',
    'create_skill',
    'note',
    'git_checkpoint',
    'git_restore_checkpoint',
  ];
  return known.includes(value as Action['kind']) ? (value as Action['kind']) : 'note';
}

function actionSummary(action: BackendJob['pending_actions'][number]): string {
  const payloadPrompt = typeof action.payload?.prompt === 'string' ? action.payload.prompt : '';
  if ((action.kind || '') === 'coding_agent_executor') {
    return action.title || 'Claude executor request';
  }
  return action.preview || action.title || payloadPrompt || action.kind || 'Action';
}

function actionLogs(job: BackendJob, action: BackendJob['pending_actions'][number]): string[] {
  const includePreview = (action.kind || '') !== 'coding_agent_executor';
  const rows = [
    includePreview ? action.preview : '',
    action.result,
    action.error,
    ...(job.steps || []).map(step => [step.name, step.status, step.detail].filter(Boolean).join(' - ')),
  ];
  return rows.filter(Boolean).map(String);
}

function actionKey(action: BackendJob['pending_actions'][number]): string {
  const prompt = typeof action.payload?.prompt === 'string' ? action.payload.prompt : '';
  const seed = action.preview || action.title || prompt || '';
  return `${action.kind || 'note'}::${seed.trim().toLowerCase()}`;
}

function actionTime(action: BackendJob['pending_actions'][number]): number {
  const value = action.updated_at || action.created_at;
  const ms = value ? Date.parse(value) : 0;
  return Number.isNaN(ms) ? 0 : ms;
}

function dedupeActions(actions: BackendJob['pending_actions'] = []): BackendJob['pending_actions'] {
  const byKey = new Map<string, BackendJob['pending_actions'][number]>();
  for (const action of actions) {
    const key = actionKey(action);
    const prev = byKey.get(key);
    if (!prev) {
      byKey.set(key, action);
      continue;
    }
    const newer = actionTime(action) >= actionTime(prev) ? action : prev;
    byKey.set(key, newer);
  }
  return Array.from(byKey.values()).sort((a, b) => actionTime(a) - actionTime(b));
}

function keepRelevantActions(actions: BackendJob['pending_actions'] = []): BackendJob['pending_actions'] {
  const latestByKind = new Map<string, BackendJob['pending_actions'][number]>();
  for (const action of actions) {
    const kind = action.kind || 'note';
    const prev = latestByKind.get(kind);
    if (!prev || actionTime(action) >= actionTime(prev)) {
      latestByKind.set(kind, action);
    }
  }
  return Array.from(latestByKind.values()).sort((a, b) => actionTime(a) - actionTime(b));
}

function parseVerificationFromText(text: string): { hardGateOk?: boolean; missingRequirements: string[] } {
  const value = String(text || '');
  const hardLine = value.split('\n').find(line => line.toLowerCase().startsWith('hard gate:'));
  const missingLine = value.split('\n').find(line => line.toLowerCase().startsWith('missing requirements:'));
  let hardGateOk: boolean | undefined = undefined;
  if (hardLine) {
    const lower = hardLine.toLowerCase();
    if (lower.includes('ok')) hardGateOk = true;
    if (lower.includes('failed')) hardGateOk = false;
  }
  let missingRequirements: string[] = [];
  if (missingLine) {
    const raw = missingLine.split(':').slice(1).join(':').trim();
    if (raw && raw.toLowerCase() !== 'none') {
      missingRequirements = raw.split(',').map(item => item.trim()).filter(Boolean);
    }
  }
  return { hardGateOk, missingRequirements };
}

function planProgress(job: BackendJob): { completed: number; runningDetail: string } {
  const planLen = job.plan?.length || 0;
  if (!planLen) return { completed: 0, runningDetail: '' };
  const steps = (job.steps || []).filter(step => (step.name || '').toLowerCase() !== 'report');
  const completedRaw = steps.filter(step => step.status === 'done').length;
  const completed = Math.max(0, Math.min(planLen, completedRaw));
  const running = steps.find(step => step.status === 'running');
  return { completed, runningDetail: String(running?.detail || '') };
}

export function mapJobToActions(job: BackendJob | null): Action[] {
  if (!job) return [];
  const actions = keepRelevantActions(dedupeActions(job.pending_actions)).map(action => ({
    id: String(action.id),
    kind: actionKind(action.kind),
    description: actionSummary(action),
    status: actionStatus(action.status),
    risk: actionRisk(action.risk),
    result: action.result || action.error,
    logs: actionLogs(job, action),
    timestamp: asDate(action.updated_at || action.created_at),
  }));

  if (job.plan?.length && ['draft', 'planned', 'approved', 'running', 'done', 'failed'].includes(job.status || '')) {
    const progress = planProgress(job);
    let planStatus: ActionStatus = 'pending';
    if (job.status === 'running' || job.status === 'approved') planStatus = 'running';
    if (job.status === 'done') planStatus = 'done';
    if (job.status === 'failed') planStatus = 'failed';
    actions.unshift({
      id: `plan-${job.id}`,
      kind: 'note',
      description: `Plan: ${job.plan.length} step(s)`,
      status: planStatus,
      risk: 'low',
      result: job.plan.map((step, index) => `${index + 1}. ${step}`).join('\n'),
      logs: job.plan,
      planTotalSteps: job.plan.length,
      planCompletedSteps: progress.completed,
      planRunningDetail: progress.runningDetail,
      timestamp: asDate(job.updated_at || job.created_at),
    });
  }

  const hasPlanCard = Boolean(job.plan?.length);
  if ((job.status === 'running' || job.steps?.some(step => step.status === 'running')) && !actions.some(a => a.status === 'running') && !hasPlanCard) {
    const step = job.steps?.find(s => s.status === 'running') || job.steps?.at(-1);
    actions.push({
      id: `running-${job.id}`,
      kind: 'workspace_inspect',
      description: step?.detail || 'LangGraph is running',
      status: 'running',
      risk: 'low',
      logs: (job.steps || []).map(s => [s.name, s.status, s.detail].filter(Boolean).join(' - ')),
      timestamp: asDate(job.updated_at || job.created_at),
    });
  }

  if ((job.status === 'done' || job.status === 'failed') && (job.result || job.error)) {
    const report = String(job.result || job.error || '');
    const parsed = parseVerificationFromText(report);
    actions.push({
      id: `verify-${job.id}`,
      kind: 'note',
      description: 'Verification gate summary',
      status: job.status === 'done' ? 'done' : 'failed',
      risk: 'low',
      result: report,
      logs: [
        parsed.hardGateOk === undefined ? '' : `Hard gate: ${parsed.hardGateOk ? 'OK' : 'FAILED'}`,
        parsed.missingRequirements.length ? `Missing requirements: ${parsed.missingRequirements.join(', ')}` : 'Missing requirements: None',
      ].filter(Boolean),
      hardGateOk: parsed.hardGateOk,
      missingRequirements: parsed.missingRequirements,
      timestamp: asDate(job.updated_at || job.created_at),
    });
  }

  return actions;
}

export function mapJobToSession(job: BackendJob): Session {
  return {
    id: job.id,
    title: job.title || job.request?.split('\n')[0]?.slice(0, 80) || 'Untitled',
    isPinned: false,
    lastActivity: asDate(job.updated_at || job.created_at),
    messages: mapJobToMessages(job),
    status: job.status || 'chat',
    permissionMode: job.permission_mode || 'auto_review',
  };
}

export function mapJobToMessages(job: BackendJob): Message[] {
  const rawMessages: Message[] = (job.messages || []).map((message, index) => ({
    id: String(
      message.id
      ?? `${job.id}-${message.role || 'assistant'}-${message.created_at || 'no-time'}-${index}`
    ),
    role: message.role === 'user' ? 'user' : 'assistant',
    content: message.content || '',
    timestamp: asDate(message.created_at),
    attachments: (message.attachments || []).map((file, fileIndex) => ({
      id: file.id || `${job.id}-${index}-${fileIndex}`,
      name: file.name || 'Attachment',
      size: file.size,
      type: file.type,
      dataBase64: undefined,
    })),
  }));
  const messages: Message[] = [];
  const pendingFocusFiles: string[] = [];
  for (const msg of rawMessages) {
    const text = String(msg.content || '').trim();
    if (msg.role === 'assistant' && text.toLowerCase().startsWith('đã thêm file focus:')) {
      const fileName = text.split(':').slice(1).join(':').trim().replace(/\.+$/, '');
      if (fileName) pendingFocusFiles.push(fileName);
      continue;
    }
    if (msg.role === 'user' && pendingFocusFiles.length) {
      const attachments = [...(msg.attachments || [])];
      for (const fileName of pendingFocusFiles.splice(0, pendingFocusFiles.length)) {
        if (!attachments.some(item => item.name === fileName)) {
          attachments.push({
            id: `focus-${job.id}-${msg.id}-${attachments.length}`,
            name: fileName,
          });
        }
      }
      msg.attachments = attachments;
    }
    const prev = messages[messages.length - 1];
    const sameAssistant =
      prev?.role === 'assistant'
      && msg.role === 'assistant'
      && String(prev.content || '').trim() === String(msg.content || '').trim();
    if (sameAssistant) continue;
    messages.push(msg);
  }

  const actions = mapJobToActions(job);
  const hasActiveActions = actions.some(action => action.status === 'running' || action.status === 'pending');

  // Attach active actions to the latest assistant message instead of creating
  // a separate "Action timeline" message.
  if (actions.length && hasActiveActions) {
    const lastIndex = messages.length - 1;
    if (lastIndex >= 0 && messages[lastIndex].role === 'assistant') {
      messages[lastIndex] = {
        ...messages[lastIndex],
        actions,
      };
    } else {
      messages.push({
        id: `actions-inline-${job.id}`,
        role: 'assistant',
        content: '',
        timestamp: asDate(job.updated_at || job.created_at),
        actions,
      });
    }
  }

  if ((job.status || '') !== 'chat' && (job.result || job.error)) {
    const reportText = String(job.result || job.error || '').trim();
    const hasSameAssistantMessage = messages.some(
      m => m.role === 'assistant' && String(m.content || '').trim() === reportText,
    );
    if (hasSameAssistantMessage) {
      return messages;
    }
    const resultId = `result-${job.id}`;
    const existingResultIndex = messages.findIndex(m => m.id === resultId);
    const resultMessage: Message = {
      id: resultId,
      role: 'assistant',
      content: reportText,
      timestamp: asDate(job.updated_at || job.created_at),
    };
    if (existingResultIndex >= 0) {
      messages[existingResultIndex] = resultMessage;
    } else {
      messages.push(resultMessage);
    }
  }

  return messages;
}

export async function listJobs(): Promise<BackendJob[]> {
  const data = await api<{ jobs: BackendJob[] }>('/api/jobs');
  return (data.jobs || []).filter(job => job.id !== 'main_chat');
}

export async function getJob(id: string, signal?: AbortSignal): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${id}`, { signal });
  return data.job;
}

export async function createSession(permissionMode: PermissionMode): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>('/api/chats', {
    method: 'POST',
    body: JSON.stringify({ permission_mode: permissionMode }),
  });
  return data.job;
}

export async function sendJobMessage(job: BackendJob | null, content: string, planMode: boolean, permissionMode: PermissionMode): Promise<BackendJob> {
  if (!job) {
    const data = await api<{ job: BackendJob }>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ request: content, permission_mode: permissionMode }),
    });
    return data.job;
  }

  if (planMode && ['chat', 'draft', 'planned', 'needs_input'].includes(job.status || '')) {
    const data = await api<{ job: BackendJob }>(`/api/jobs/${job.id}/plan-from-chat`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
    return data.job;
  }

  const data = await api<{ job: BackendJob }>(`/api/jobs/${job.id}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
  return data.job;
}

export async function approvePlan(jobId: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/approve`, { method: 'POST' });
  return data.job;
}

export async function reopenPlan(jobId: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/reopen-plan`, { method: 'POST' });
  return data.job;
}

export async function editPlan(jobId: string, plan: string[], comment: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/plan`, {
    method: 'PUT',
    body: JSON.stringify({ plan, comment }),
  });
  return data.job;
}

export async function reconfirmPlan(jobId: string, content: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/reconfirm-plan`, {
    method: 'POST',
    body: JSON.stringify({ content, executor: 'claude' }),
  });
  return data.job;
}

export async function runJob(jobId: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/run`, { method: 'POST' });
  return data.job;
}

export async function regeneratePlan(jobId: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/regenerate-plan`, { method: 'POST' });
  return data.job;
}

export async function approveAction(actionId: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/actions/${actionId}/approve`, { method: 'POST' });
  return data.job;
}

export async function rejectAction(actionId: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/actions/${actionId}/reject`, { method: 'POST' });
  return data.job;
}

export async function renameJob(jobId: string, title: string): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/title`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
  return data.job;
}

export async function deleteJob(jobId: string): Promise<void> {
  await api<{ ok: boolean }>(`/api/jobs/${jobId}`, { method: 'DELETE' });
}

export async function setPermissionMode(jobId: string, permissionMode: PermissionMode): Promise<BackendJob> {
  const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/permission-mode`, {
    method: 'PATCH',
    body: JSON.stringify({ permission_mode: permissionMode }),
  });
  return data.job;
}

export async function attachFiles(jobId: string, files: AttachedFile[]): Promise<BackendJob | null> {
  let latest: BackendJob | null = null;
  const needsExtract = (file: AttachedFile) => {
    const lower = file.name.toLowerCase();
    if (lower.endsWith('.pdf') || lower.endsWith('.docx') || lower.endsWith('.xlsx') || lower.endsWith('.pptx')) return true;
    const mime = (file.type || '').toLowerCase();
    if (mime.includes('pdf') || mime.includes('officedocument') || mime.includes('msword')) return true;
    return false;
  };
  for (const file of files) {
    let content = file.content || '';
    if ((needsExtract(file) || !content.trim()) && file.dataBase64) {
      try {
        const extracted = await api<{
          text?: string;
          warning?: string;
          truncated?: boolean;
        }>('/api/attachments/read', {
          method: 'POST',
          body: JSON.stringify({
            name: file.name,
            mime_type: file.type || '',
            content_base64: file.dataBase64,
            job_id: jobId,
          }),
        });
        content = extracted.text || '';
        if (!content.trim() && extracted.warning) {
          content = `[attachment-warning] ${extracted.warning}`;
        }
      } catch {
        // fallback to raw text if extraction endpoint fails
      }
    }
    const data = await api<{ job: BackendJob }>(`/api/jobs/${jobId}/focus-file`, {
      method: 'POST',
      body: JSON.stringify({ name: file.name, content }),
    });
    latest = data.job;
  }
  return latest;
}

export async function loadSettingsData() {
  const inferFolder = (tags: string, type: string, content: string) => {
    const parts = String(tags || '').split(/[,\n;]+/).map(s => s.trim()).filter(Boolean);
    const folderTag = parts.find(part => part.toLowerCase().startsWith('folder:'));
    if (folderTag) return folderTag.split(':').slice(1).join(':').trim() || 'notes/general';
    const blob = `${type}\n${content}\n${tags}`.toLowerCase();
    if (blob.includes('user:') || blob.includes('profile') || blob.includes('persona')) return 'user/profile';
    if (blob.includes('error') || blob.includes('failed') || blob.includes('lỗi') || blob.includes('exception')) return 'debug/findings';
    if (blob.includes('route') || blob.includes('win_rate') || blob.includes('success')) return 'debug/routes-success';
    if (blob.includes('skill') || blob.includes('trigger') || blob.includes('workflow')) return 'skills/patterns';
    if (blob.includes('repo') || blob.includes('workspace') || blob.includes('path') || blob.includes('.py') || blob.includes('.ts')) return 'project/context';
    return 'notes/general';
  };
  const isTechnicalMemory = (item: any) => {
    const content = String(item.content || '').toLowerCase();
    const title = String(item.title || '').toLowerCase();
    const source = String(item.source || '').toLowerCase();
    if (source === 'system' || source === 'agent_action' || source === 'runtime') return true;
    if (content.includes('job_id=') || content.includes('task_id=') || content.includes('attempt=') || content.includes('status=success')) return true;
    if (title.startsWith('route:') || title.startsWith('lesson:')) return true;
    return false;
  };
  const [memories, skills, commands, projects, recurringTasks] = await Promise.all([
    api<{ memories: any[] }>('/api/memories').then(data => data.memories || []).catch(() => []),
    api<{ skills: any[] }>('/api/skills').then(data => data.skills || []).catch(() => []),
    api<{ commands: any[] }>('/api/commands').then(data => data.commands || []).catch(() => []),
    api<{ projects: any[] }>('/api/projects').then(data => data.projects || []).catch(() => []),
    api<{ recurring_tasks: any[] }>('/api/recurring-tasks').then(data => data.recurring_tasks || []).catch(() => []),
  ]);

  return {
    memories: memories.map((item): MemoryItem => ({
      id: String(item.id),
      title: item.title || '',
      content: item.content || item.summary || '',
      type: item.type || 'global',
      timestamp: asDate(item.updated_at || item.created_at),
      tags: item.tags || '',
      source: item.source || '',
      folder: inferFolder(item.tags || '', item.type || 'global', item.content || ''),
      docPath: item.kind === 'user'
        ? 'memory_docs/user.md'
        : `memory_docs/${inferFolder(item.tags || '', item.type || 'global', item.content || '')}/MEMORY.md`,
      isTechnical: isTechnicalMemory(item),
    })),
    skills: skills.map((item): Skill => ({
      id: String(item.id),
      name: item.name || 'Skill',
      trigger: item.trigger || item.description || '',
      status: item.enabled === false ? 'inactive' : 'active',
      performance: item.performance ?? 0,
    })),
    commands: commands.map((item): Command => ({
      id: String(item.id),
      name: item.name || 'Command',
      command: item.command || '',
      mode: item.mode === 'full-access' ? 'full-access' : 'read-only',
    })),
    projects: projects.map((item): Project => ({
      id: String(item.id),
      name: item.name || 'Project',
      path: item.path || '',
      lastOpened: asDate(item.updated_at || item.created_at),
    })),
    recurringTasks: recurringTasks.map((item): RecurringTask => ({
      id: String(item.id),
      name: item.name || 'Task',
      schedule: item.schedule || item.cron || '',
      lastRun: item.last_run_at ? asDate(item.last_run_at) : undefined,
      status: item.enabled === false ? 'paused' : 'active',
    })),
  };
}

function titleFromContent(content: string): string {
  return content.trim().split('\n')[0].slice(0, 80) || 'Note';
}

export async function createMemory(payload: { content: string; type?: MemoryItem['type'] }) {
  const content = payload.content.trim();
  if (!content) throw new Error('Memory content is required');
  await api('/api/memories', {
    method: 'POST',
    body: JSON.stringify({
      kind: payload.type || 'global',
      title: titleFromContent(content),
      content,
      tags: payload.type || 'global',
      source: 'ui',
    }),
  });
}

export async function updateMemory(item: MemoryItem, content: string) {
  const next = content.trim();
  if (!next) throw new Error('Memory content is required');
  await api(`/api/memories/${item.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      kind: item.type || 'global',
      title: titleFromContent(next),
      content: next,
      tags: item.type || 'global',
      source: 'ui',
    }),
  });
}

export async function deleteMemory(id: string) {
  await api(`/api/memories/${id}`, { method: 'DELETE' });
}

export async function createSkill(payload: { name: string; trigger: string; body?: string }) {
  const name = payload.name.trim();
  if (!name) throw new Error('Skill name is required');
  await api('/api/skills', {
    method: 'POST',
    body: JSON.stringify({
      name,
      description: '',
      triggers: payload.trigger || '',
      source: 'ui',
      body: payload.body || `# ${name}\n\nGenerated from UI settings.`,
      status: 'active',
    }),
  });
}

export async function updateSkill(item: Skill, patch: { name?: string; trigger?: string; status?: Skill['status'] }) {
  const name = (patch.name ?? item.name).trim();
  if (!name) throw new Error('Skill name is required');
  await api(`/api/skills/${item.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      name,
      description: '',
      triggers: patch.trigger ?? item.trigger ?? '',
      source: 'ui',
      body: `# ${name}\n\nUpdated from UI settings.`,
      status: patch.status ?? item.status ?? 'active',
    }),
  });
}

export async function deleteSkill(id: string) {
  await api(`/api/skills/${id}`, { method: 'DELETE' });
}

export async function createCommand(payload: { name: string; command: string; mode?: Command['mode'] }) {
  const name = payload.name.trim();
  const command = payload.command.trim();
  if (!name || !command) throw new Error('Command name and command are required');
  await api('/api/commands', {
    method: 'POST',
    body: JSON.stringify({
      name,
      description: '',
      action_kind: 'workspace_command',
      payload: { command, mode: payload.mode || 'read-only' },
      status: 'active',
    }),
  });
}

export async function updateCommand(item: Command, patch: { name?: string; command?: string; mode?: Command['mode'] }) {
  const name = (patch.name ?? item.name).trim();
  const command = (patch.command ?? item.command).trim();
  if (!name || !command) throw new Error('Command name and command are required');
  await api(`/api/commands/${item.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      name,
      description: '',
      action_kind: 'workspace_command',
      payload: { command, mode: patch.mode ?? item.mode ?? 'read-only' },
      status: 'active',
    }),
  });
}

export async function deleteCommand(id: string) {
  await api(`/api/commands/${id}`, { method: 'DELETE' });
}

export async function createProject(payload: { name: string; path: string }) {
  const name = payload.name.trim();
  if (!name) throw new Error('Project name is required');
  await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({
      name,
      root_path: payload.path || '',
      summary: '',
      memory: '',
      status: 'active',
    }),
  });
}

export async function updateProject(item: Project, patch: { name?: string; path?: string }) {
  const name = (patch.name ?? item.name).trim();
  if (!name) throw new Error('Project name is required');
  await api(`/api/projects/${item.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      name,
      root_path: patch.path ?? item.path ?? '',
      summary: '',
      memory: '',
      status: 'active',
    }),
  });
}

export async function deleteProject(id: string) {
  await api(`/api/projects/${id}`, { method: 'DELETE' });
}

export async function createRecurringTask(payload: { name: string; schedule: string; prompt?: string }) {
  const name = payload.name.trim();
  if (!name) throw new Error('Recurring task name is required');
  await api('/api/recurring-tasks', {
    method: 'POST',
    body: JSON.stringify({
      name,
      prompt: payload.prompt || `Run recurring task: ${name}`,
      schedule: payload.schedule || 'manual',
      action_kind: 'note',
      payload: {},
      status: 'active',
      next_run_at: '',
    }),
  });
}

export async function updateRecurringTask(item: RecurringTask, patch: { name?: string; schedule?: string; status?: RecurringTask['status'] }) {
  const name = (patch.name ?? item.name).trim();
  if (!name) throw new Error('Recurring task name is required');
  await api(`/api/recurring-tasks/${item.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      name,
      prompt: `Run recurring task: ${name}`,
      schedule: patch.schedule ?? item.schedule ?? 'manual',
      action_kind: 'note',
      payload: {},
      status: patch.status === 'paused' ? 'paused' : 'active',
      next_run_at: '',
    }),
  });
}

export async function deleteRecurringTask(id: string) {
  await api(`/api/recurring-tasks/${id}`, { method: 'DELETE' });
}
