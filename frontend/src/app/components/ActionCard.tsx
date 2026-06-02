import { Action } from '../types';
import { StatusBadge } from './StatusBadge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader } from './ui/card';
import { ChevronDown, ChevronRight, CheckCircle, XCircle, FileText, Terminal, Circle, Loader2 } from 'lucide-react';
import { memo, useEffect, useRef, useState } from 'react';
import { Badge } from './ui/badge';
import { formatDistanceToNowStrict } from 'date-fns';

interface ActionCardProps {
  action: Action;
  onApprove?: () => void;
  onReject?: () => void;
  onViewLogs?: () => void;
  onViewResult?: () => void;
  onReconfirmPlan?: (steps: string[]) => void;
}

const actionIcons = {
  coding_agent_executor: Terminal,
  workspace_command: Terminal,
  workspace_inspect: FileText,
  workspace_read_file: FileText,
  workspace_diff: FileText,
  create_memory: FileText,
  create_skill: FileText,
  note: FileText,
  git_checkpoint: FileText,
  git_restore_checkpoint: FileText,
};

function ActionCardInner({ action, onApprove, onReject, onViewLogs, onViewResult, onReconfirmPlan }: ActionCardProps) {
  const [expanded, setExpanded] = useState(action.status === 'pending' || action.expanded || false);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const [planSteps, setPlanSteps] = useState<string[]>([]);
  const stepRefs = useRef<Record<number, HTMLTextAreaElement | null>>({});
  const Icon = actionIcons[action.kind];
  const isPlanAction = action.id.startsWith('plan-');
  const canInlineEditPlan = isPlanAction && action.status === 'pending';
  const completedSteps = Math.max(0, Math.min(planSteps.length, action.planCompletedSteps || 0));
  const runningStepIndex = action.status === 'running' && completedSteps < planSteps.length ? completedSteps : -1;

  const autoResizeStep = (idx: number) => {
    const el = stepRefs.current[idx];
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  };

  useEffect(() => {
    if (!isPlanAction) return;
    const lines = (action.logs || [])
      .map(item => String(item || '').trim())
      .filter(Boolean);
    setPlanSteps(lines);
  }, [isPlanAction, action.id, action.logs]);

  useEffect(() => {
    if (action.status === 'pending') {
      setExpanded(true);
    }
  }, [action.id, action.status]);

  useEffect(() => {
    if (!isPlanAction || !expanded) return;
    planSteps.forEach((_, idx) => autoResizeStep(idx));
  }, [isPlanAction, expanded, planSteps]);

  useEffect(() => {
    if (action.status !== 'running') return;
    const timer = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [action.status]);

  const actionTimeAgo = formatDistanceToNowStrict(action.timestamp, { addSuffix: true });
  void nowTick;

  const riskColors = {
    low: 'bg-green-100 text-green-700',
    medium: 'bg-orange-100 text-orange-700',
    high: 'bg-red-100 text-red-700'
  };

  return (
    <Card className="border-l-4 border-l-orange-500 bg-white">
      <CardHeader className="p-3">
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-md bg-gray-100">
            <Icon className="h-4 w-4 text-gray-600" />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-gray-500">{action.kind}</span>
              <StatusBadge status={action.status} />
              <Badge variant="outline" className={riskColors[action.risk]}>
                {action.risk} risk
              </Badge>
            </div>
            <p className="text-sm font-medium text-gray-900">{action.description}</p>
            <p className="mt-1 inline-flex w-fit rounded-md bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
              {actionTimeAgo}
            </p>
          </div>

          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </Button>
        </div>
      </CardHeader>

      {expanded && (
        <CardContent className="p-3 pt-0 space-y-2">
          {isPlanAction ? (
            <div className="space-y-2">
              {planSteps.map((step, idx) => (
                <div key={`${action.id}-step-${idx}`} className="rounded-md border border-gray-200 bg-gray-50 p-2">
                  <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-gray-500">
                    {idx < completedSteps ? (
                      <CheckCircle className="h-3.5 w-3.5 text-green-600" />
                    ) : idx === runningStepIndex ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-orange-500" />
                    ) : (
                      <Circle className="h-3.5 w-3.5 text-gray-400" />
                    )}
                    <span>{idx + 1}.</span>
                  </div>
                  <textarea
                    value={step}
                    onChange={(e) => {
                      const next = planSteps.slice();
                      next[idx] = e.target.value;
                      setPlanSteps(next);
                      window.requestAnimationFrame(() => autoResizeStep(idx));
                    }}
                    ref={(el) => {
                      stepRefs.current[idx] = el;
                      if (el) window.requestAnimationFrame(() => autoResizeStep(idx));
                    }}
                    disabled={!canInlineEditPlan}
                    className={`w-full overflow-hidden resize-none rounded-md border border-gray-200 bg-white p-2 text-sm text-gray-800 disabled:opacity-90 ${idx < completedSteps ? 'line-through text-gray-500' : ''}`}
                    rows={1}
                  />
                </div>
              ))}
              {action.status === 'running' && action.planRunningDetail && (
                <p className="text-xs text-orange-600">Đang chạy: {action.planRunningDetail}</p>
              )}
            </div>
          ) : action.result && (
            <div className="bg-gray-50 rounded-md p-3">
              <p className="text-xs font-medium text-gray-500 mb-1">Result</p>
              <p className="text-sm text-gray-700">{action.result}</p>
            </div>
          )}

          {!isPlanAction && action.logs && action.logs.length > 0 && (
            <div className="bg-gray-900 rounded-md p-3 font-mono text-xs text-gray-100">
              {action.logs.slice(0, 3).map((log, i) => (
                <div key={i} className="mb-1">{log}</div>
              ))}
              {action.logs.length > 3 && (
                <div className="text-gray-400">... {action.logs.length - 3} more lines</div>
              )}
            </div>
          )}

          <div className="flex gap-2 flex-wrap">
            {action.status === 'pending' && (
              <>
                <Button size="sm" onClick={onApprove} className="bg-orange-500 hover:bg-orange-600">
                  <CheckCircle className="mr-1 h-3 w-3" />
                  Approve
                </Button>
                <Button size="sm" variant="outline" onClick={onReject}>
                  <XCircle className="mr-1 h-3 w-3" />
                  Reject
                </Button>
                {isPlanAction && canInlineEditPlan && onReconfirmPlan && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onReconfirmPlan(planSteps.map(s => s.trim()).filter(Boolean))}
                  >
                    Reconfirm
                  </Button>
                )}
              </>
            )}
            {!isPlanAction && onViewResult && (
              <Button size="sm" variant="outline" onClick={onViewResult}>
                <FileText className="mr-1 h-3 w-3" />
                View Result
              </Button>
            )}
            {!isPlanAction && onViewLogs && (
              <Button size="sm" variant="outline" onClick={onViewLogs}>
                <Terminal className="mr-1 h-3 w-3" />
                View Logs
              </Button>
            )}
          </div>
        </CardContent>
      )}
    </Card>
  );
}

function areEqual(prev: ActionCardProps, next: ActionCardProps) {
  return (
    prev.action.id === next.action.id
    && prev.action.status === next.action.status
    && prev.action.description === next.action.description
    && prev.action.risk === next.action.risk
    && prev.action.result === next.action.result
    && prev.action.timestamp.getTime() === next.action.timestamp.getTime()
    && (prev.action.logs?.length || 0) === (next.action.logs?.length || 0)
  );
}

export const ActionCard = memo(ActionCardInner, areEqual);
