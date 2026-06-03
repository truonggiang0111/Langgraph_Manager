export type ActionStatus = 'pending' | 'running' | 'done' | 'failed' | 'rejected';

export type ActionKind =
  | 'coding_agent_executor'
  | 'workspace_command'
  | 'workspace_inspect'
  | 'workspace_read_file'
  | 'workspace_diff'
  | 'create_memory'
  | 'create_skill'
  | 'host_browser_list'
  | 'host_browser_open'
  | 'host_browser_open_current'
  | 'host_browser_launch'
  | 'host_browser_facebook_research'
  | 'host_browser_screenshot'
  | 'note'
  | 'git_checkpoint'
  | 'git_restore_checkpoint';

export interface Action {
  id: string;
  kind: ActionKind;
  description: string;
  status: ActionStatus;
  risk: 'low' | 'medium' | 'high';
  result?: string;
  logs?: string[];
  timestamp: Date;
  expanded?: boolean;
  hardGateOk?: boolean;
  missingRequirements?: string[];
  planTotalSteps?: number;
  planCompletedSteps?: number;
  planRunningDetail?: string;
  facebookResearch?: {
    summaryText: string;
    counts: {
      recentConfirmed: number;
      timeUnknown: number;
      staleConfirmed: number;
    };
    items: Array<{
      rank: number;
      author: string;
      summary: string;
      keepReason: string;
      timeStatus: string;
      url: string;
      rawUrl?: string;
    }>;
  };
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  actions?: Action[];
  attachments?: Array<{
    id: string;
    name: string;
    size?: number;
    type?: string;
    dataBase64?: string;
  }>;
}

export interface Session {
  id: string;
  title: string;
  isPinned: boolean;
  lastActivity: Date;
  messages: Message[];
  status?: string;
  permissionMode?: string;
}

export interface AttachedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  content?: string;
  dataBase64?: string;
}

export interface MemoryItem {
  id: string;
  title?: string;
  content: string;
  type: 'session' | 'global' | 'skill' | 'route';
  timestamp: Date;
  tags?: string;
  source?: string;
  folder?: string;
  docPath?: string;
  isTechnical?: boolean;
}

export interface Skill {
  id: string;
  name: string;
  trigger: string;
  status: 'active' | 'inactive';
  performance?: number;
}

export interface Command {
  id: string;
  name: string;
  command: string;
  mode: 'read-only' | 'full-access';
}

export interface Project {
  id: string;
  name: string;
  path: string;
  lastOpened: Date;
}

export interface RecurringTask {
  id: string;
  name: string;
  schedule: string;
  lastRun?: Date;
  status: 'active' | 'paused';
}
