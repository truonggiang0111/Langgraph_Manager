import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ResponsiveWorkspace } from './components/ResponsiveWorkspace';
import { AttachedFile } from './types';
import { toast } from 'sonner';
import { Toaster } from './components/ui/sonner';
import {
  approveAction,
  approvePlan,
  attachFiles,
  BackendJob,
  createSession,
  deleteJob,
  editPlan,
  getJob,
  listJobs,
  loadSettingsData,
  mapJobToActions,
  mapJobToMessages,
  mapJobToSession,
  PermissionMode,
  reopenPlan,
  reconfirmPlan,
  regeneratePlan,
  rejectAction,
  renameJob,
  runJob,
  sendJobMessage,
  setPermissionMode,
} from './api';

const LAST_SESSION_KEY = 'lg_last_job_id';

const emptySettings = {
  memories: [],
  skills: [],
  commands: [],
  projects: [],
  recurringTasks: [],
};

export default function App() {
  const [jobs, setJobs] = useState<BackendJob[]>([]);
  const [activeJob, setActiveJob] = useState<BackendJob | null>(null);
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsData, setSettingsData] = useState(emptySettings);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [jobStreamConnected, setJobStreamConnected] = useState(false);
  const selectJobAbortRef = useRef<AbortController | null>(null);
  const sendInFlightRef = useRef(false);
  const lastSendRef = useRef<{ content: string; at: number }>({ content: '', at: 0 });
  const pollBurstUntilRef = useRef(0);

  const sessions = useMemo(() => jobs.map(mapJobToSession), [jobs]);
  const activeSessionId = activeJob?.id || sessions[0]?.id || '';
  const activeMessages = useMemo(() => activeJob ? mapJobToMessages(activeJob) : [], [activeJob]);
  const selectedAction = useMemo(
    () => mapJobToActions(activeJob).find(action => action.id === selectedActionId) || null,
    [activeJob, selectedActionId],
  );

  const upsertJobLocally = useCallback((job: BackendJob) => {
    setJobs(prev => {
      const next = prev.slice();
      const index = next.findIndex(item => item.id === job.id);
      if (index >= 0) next[index] = job;
      else next.unshift(job);
      return next;
    });
  }, []);

  const refreshJobs = useCallback(async () => {
    const nextJobs = await listJobs();
    setJobs(nextJobs);
    return nextJobs;
  }, []);

  const refreshSettings = useCallback(async () => {
    const data = await loadSettingsData();
    setSettingsData(data);
  }, []);

  const selectJob = useCallback(async (id: string) => {
    if (!id) return;
    selectJobAbortRef.current?.abort();
    const controller = new AbortController();
    selectJobAbortRef.current = controller;
    try {
      const job = await getJob(id, controller.signal);
      localStorage.setItem(LAST_SESSION_KEY, id);
      setActiveJob(job);
      setSelectedActionId(null);
      await refreshJobs();
    } catch (error) {
      if ((error as Error)?.name === 'AbortError') return;
      throw error;
    }
  }, [refreshJobs]);

  useEffect(() => () => selectJobAbortRef.current?.abort(), []);

  const bootstrap = useCallback(async () => {
    setLoading(true);
    try {
      let nextJobs = await refreshJobs();
      const remembered = localStorage.getItem(LAST_SESSION_KEY);
      const initialId = remembered && nextJobs.some(job => job.id === remembered)
        ? remembered
        : nextJobs[0]?.id;

      if (initialId) {
        await selectJob(initialId);
        return;
      }

      const created = await createSession('full_access');
      setActiveJob(created);
      localStorage.setItem(LAST_SESSION_KEY, created.id);
      nextJobs = await refreshJobs();
      setJobs(nextJobs);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to load sessions');
    } finally {
      setLoading(false);
    }
  }, [refreshJobs, selectJob]);

  useEffect(() => {
    document.documentElement.classList.add('dark');
    bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    if (!settingsOpen) return;
    refreshSettings()
      .catch(error => toast.error(error instanceof Error ? error.message : 'Unable to load settings'));
  }, [settingsOpen, refreshSettings]);

  useEffect(() => {
    if (!activeJob?.id || typeof window === 'undefined' || typeof EventSource === 'undefined') {
      setJobStreamConnected(false);
      return;
    }
    const source = new EventSource(`/api/jobs/${activeJob.id}/events`);

    const handleJob = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || '{}') as { job?: BackendJob };
        if (!payload.job) return;
        setJobStreamConnected(true);
        setActiveJob(payload.job);
        upsertJobLocally(payload.job);
      } catch {
        // ignore malformed SSE payloads
      }
    };

    const handleOpen = () => setJobStreamConnected(true);
    const handleError = () => setJobStreamConnected(false);

    source.addEventListener('job', handleJob as EventListener);
    source.addEventListener('open', handleOpen as EventListener);
    source.addEventListener('error', handleError as EventListener);
    source.onopen = handleOpen;
    source.onerror = handleError;

    return () => {
      setJobStreamConnected(false);
      source.close();
    };
  }, [activeJob?.id, upsertJobLocally]);

  useEffect(() => {
    if (!activeJob) return;
    if (jobStreamConnected) return;
    const shouldPoll = activeJob.status === 'running' || activeJob.pending_actions?.some(action => action.status === 'running');
    if (!shouldPoll) return;
    let cancelled = false;
    let timer: number | null = null;
    let inFlight: AbortController | null = null;
    const startedAt = Date.now();

    const nextDelay = () => {
      const elapsed = Date.now() - startedAt;
      const burstUntil = pollBurstUntilRef.current;
      if (burstUntil && Date.now() < burstUntil) return 250;
      if (elapsed < 10_000) return 700;
      if (elapsed < 30_000) return 1800;
      if (elapsed < 90_000) return 2500;
      return 3500;
    };

    const poll = async () => {
      try {
        inFlight?.abort();
        inFlight = new AbortController();
        const job = await getJob(activeJob.id, inFlight.signal);
        if (cancelled) return;

        setActiveJob(job);
        setJobs(prev => {
          const next = prev.slice();
          const index = next.findIndex(item => item.id === job.id);
          if (index >= 0) next[index] = job;
          else next.unshift(job);
          return next;
        });
        if (cancelled) return;

        const stillRunning = job.status === 'running' || job.pending_actions?.some(action => action.status === 'running');
        if (stillRunning) {
          timer = window.setTimeout(poll, nextDelay());
          return;
        }

        const previousMessageCount = activeJob.messages?.length || 0;
        const latestMessageCount = job.messages?.length || 0;
        if (latestMessageCount <= previousMessageCount) {
          timer = window.setTimeout(async () => {
            try {
              const confirmed = await getJob(activeJob.id);
              if (cancelled) return;
              setActiveJob(confirmed);
              setJobs(prev => {
                const next = prev.slice();
                const index = next.findIndex(item => item.id === confirmed.id);
                if (index >= 0) next[index] = confirmed;
                else next.unshift(confirmed);
                return next;
              });
            } catch {
              // ignore one-shot confirmation fetch failures
            }
          }, 450);
        }
      } catch {
        if (timer) window.clearTimeout(timer);
      }
    };

    timer = window.setTimeout(poll, nextDelay());
    return () => {
      cancelled = true;
      inFlight?.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [activeJob?.id, activeJob?.status, activeJob?.pending_actions, jobStreamConnected]);

  const updateActiveJob = async (job: BackendJob) => {
    setActiveJob(job);
    await refreshJobs();
  };

  const handleSendMessage = async (content: string, files: AttachedFile[], planMode: boolean) => {
    const normalized = content.trim();
    if (!normalized) return;
    const now = Date.now();
    if (
      sendInFlightRef.current ||
      (lastSendRef.current.content === normalized && now - lastSendRef.current.at < 2000)
    ) {
      return;
    }
    sendInFlightRef.current = true;
    lastSendRef.current = { content: normalized, at: now };
    const permissionMode = (activeJob?.permission_mode || 'full_access') as PermissionMode;
    setBusy(true);
    try {
      pollBurstUntilRef.current = Date.now() + 4000;
      let job = activeJob;
      const optimisticAt = new Date().toISOString();
      if (job) {
        const optimisticMessage = {
          id: `local-user-${now}`,
          role: 'user',
          content: normalized,
          created_at: optimisticAt,
          attachments: files.map((file, index) => ({
            id: `local-attach-${now}-${index}`,
            name: file.name,
            size: file.size,
            type: file.type,
            dataBase64: file.dataBase64,
          })),
        };
        const optimisticAssistant = planMode ? {
          id: `local-assistant-${now}`,
          role: 'langgraph',
          content: 'Đã nhận yêu cầu. Mình đang phân tích và dựng plan, bạn chờ chút nhé.',
          created_at: optimisticAt,
        } : null;
        const optimisticJob: BackendJob = {
          ...job,
          updated_at: optimisticAt,
          messages: [
            ...(job.messages || []),
            optimisticMessage,
            ...(optimisticAssistant ? [optimisticAssistant] : []),
          ],
        };
        setActiveJob(optimisticJob);
        upsertJobLocally(optimisticJob);
      }
      if (job && files.length) {
        job = await attachFiles(job.id, files) || job;
      }
      const updated = await sendJobMessage(job, normalized, planMode, permissionMode);
      await updateActiveJob(updated);
      toast.success(planMode ? 'Plan requested' : 'Message sent');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to send message');
    } finally {
      sendInFlightRef.current = false;
      setBusy(false);
    }
  };

  const handleNewSession = async () => {
    setBusy(true);
    try {
      const job = await createSession((activeJob?.permission_mode || 'full_access') as PermissionMode);
      await updateActiveJob(job);
      localStorage.setItem(LAST_SESSION_KEY, job.id);
      toast.success('New session created');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to create session');
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteSessions = async (ids: string[]) => {
    if (!ids.length) return;
    setBusy(true);
    const prevJobs = jobs;
    const prevActive = activeJob;
    try {
      const remaining = jobs.filter(job => !ids.includes(job.id));
      setJobs(remaining);
      if (activeJob && ids.includes(activeJob.id)) {
        setActiveJob(remaining[0] || null);
      }
      await Promise.all(ids.map(deleteJob));
      const nextJobs = await refreshJobs();
      if (activeJob && ids.includes(activeJob.id)) {
        const next = nextJobs.find(job => !ids.includes(job.id));
        if (next) await selectJob(next.id);
        else setActiveJob(await createSession('full_access'));
      }
      toast.success(`Deleted ${ids.length} session(s)`);
    } catch (error) {
      setJobs(prevJobs);
      setActiveJob(prevActive);
      toast.error(error instanceof Error ? error.message : 'Unable to delete session');
    } finally {
      setBusy(false);
    }
  };

  const handleRenameSession = async (id: string) => {
    const current = jobs.find(job => job.id === id);
    const title = window.prompt('Rename session:', current?.title || '');
    if (!title?.trim()) return;
    try {
      const job = await renameJob(id, title.trim());
      if (activeJob?.id === id) setActiveJob(job);
      await refreshJobs();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to rename session');
    }
  };

  const handlePlanAction = async (mode: 'reconfirm' | 'approve' | 'run' | 'regenerate') => {
    if (!activeJob) return;
    setBusy(true);
    try {
      if (mode === 'approve' || mode === 'run') {
        pollBurstUntilRef.current = Date.now() + 4000;
      }
      let job: BackendJob;
      if (mode === 'reconfirm') {
        const comment = window.prompt('What changed or what should be clarified before approve?', '') || '';
        job = await reconfirmPlan(activeJob.id, comment);
      } else if (mode === 'approve') {
        const optimisticRunning: BackendJob = {
          ...activeJob,
          status: 'running',
          updated_at: new Date().toISOString(),
        };
        setActiveJob(optimisticRunning);
        upsertJobLocally(optimisticRunning);
        await approvePlan(activeJob.id);
        job = await runJob(activeJob.id);
      } else if (mode === 'run') {
        job = await runJob(activeJob.id);
      } else {
        job = await regeneratePlan(activeJob.id);
      }
      await updateActiveJob(job);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to update plan');
    } finally {
      setBusy(false);
    }
  };

  const handlePermissionMode = async (mode: PermissionMode) => {
    if (!activeJob) return;
    try {
      const job = await setPermissionMode(activeJob.id, mode);
      await updateActiveJob(job);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to update permission mode');
    }
  };

  const handleActionDecision = async (id: string, decision: 'approve' | 'reject') => {
    if (id.startsWith('plan-')) {
      if (decision === 'approve') {
        pollBurstUntilRef.current = Date.now() + 4000;
        const jobId = id.replace(/^plan-/, '');
        if (activeJob?.id === jobId) {
          const optimisticRunning: BackendJob = {
            ...activeJob,
            status: 'running',
            updated_at: new Date().toISOString(),
          };
          setActiveJob(optimisticRunning);
          upsertJobLocally(optimisticRunning);
        }
        setBusy(true);
        try {
          await approvePlan(jobId);
          const job = await runJob(jobId);
          await updateActiveJob(job);
          toast.success('Plan approved and running');
        } catch (error) {
          toast.error(error instanceof Error ? error.message : 'Unable to approve and run plan');
        } finally {
          setBusy(false);
        }
      } else {
        const jobId = id.replace(/^plan-/, '');
        setBusy(true);
        try {
          const job = await reopenPlan(jobId);
          await updateActiveJob(job);
          toast.success('Plan reopened');
        } catch (error) {
          toast.error(error instanceof Error ? error.message : 'Unable to reopen plan');
        } finally {
          setBusy(false);
        }
      }
      return;
    }
    setBusy(true);
    try {
      if (decision === 'approve') {
        pollBurstUntilRef.current = Date.now() + 4000;
      }
      const job = decision === 'approve' ? await approveAction(id) : await rejectAction(id);
      await updateActiveJob(job);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to update action');
    } finally {
      setBusy(false);
    }
  };

  const handleInlinePlanReconfirm = async (actionId: string, steps: string[]) => {
    if (!actionId.startsWith('plan-')) return;
    if (!steps.length) {
      toast.error('Plan cannot be empty');
      return;
    }
    const jobId = actionId.replace(/^plan-/, '');
    setBusy(true);
    try {
      await editPlan(jobId, steps, 'Inline plan edit before reconfirm');
      const job = await reconfirmPlan(jobId, 'Inline reconfirm after direct step edits');
      await updateActiveJob(job);
      toast.success('Plan reconfirmed');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Unable to reconfirm plan');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <ResponsiveWorkspace
        sessions={sessions}
        activeSessionId={activeSessionId}
        activeMessages={activeMessages}
        selectedAction={selectedAction}
        activeJob={activeJob}
        settingsOpen={settingsOpen}
        busy={busy || loading}
        onSelectSession={selectJob}
        onNewSession={handleNewSession}
        onDeleteSessions={handleDeleteSessions}
        onRenameSession={handleRenameSession}
        onSendMessage={handleSendMessage}
        onSelectAction={setSelectedActionId}
        onOpenSettings={setSettingsOpen}
        onPlanAction={handlePlanAction}
        onPermissionModeChange={handlePermissionMode}
        onActionDecision={handleActionDecision}
        onReconfirmPlan={handleInlinePlanReconfirm}
        memories={settingsData.memories}
        skills={settingsData.skills}
        commands={settingsData.commands}
        projects={settingsData.projects}
        recurringTasks={settingsData.recurringTasks}
        onReloadSettings={refreshSettings}
      />
      <Toaster />
    </>
  );
}
