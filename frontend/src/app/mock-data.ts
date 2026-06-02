import { Session, Message, Action, MemoryItem, Skill, Command, Project, RecurringTask } from './types';

export const mockActions: Action[] = [
  {
    id: '1',
    kind: 'coding_agent_executor',
    description: 'Generate AI chat workspace UI',
    status: 'done',
    risk: 'medium',
    result: 'Successfully created workspace layout with sidebar and chat area',
    logs: [
      '[2026-05-31 10:30:15] Starting coding agent executor',
      '[2026-05-31 10:30:16] Analyzing requirements',
      '[2026-05-31 10:30:20] Creating components',
      '[2026-05-31 10:30:25] Task completed successfully'
    ],
    timestamp: new Date('2026-05-31T10:30:00'),
    expanded: false
  },
  {
    id: '2',
    kind: 'workspace_command',
    description: 'Install dependencies with pnpm',
    status: 'running',
    risk: 'low',
    logs: [
      '[2026-05-31 10:35:10] Running: pnpm install',
      '[2026-05-31 10:35:15] Downloading packages...'
    ],
    timestamp: new Date('2026-05-31T10:35:00'),
    expanded: false
  }
];

export const mockMessages: Message[] = [
  {
    id: '1',
    role: 'user',
    content: 'Create a professional AI chat workspace interface',
    timestamp: new Date('2026-05-31T10:29:00')
  },
  {
    id: '2',
    role: 'assistant',
    content: 'I\'ll create a modern, professional AI chat workspace with a sidebar for sessions, main chat area, and inspector panel. The design will use orange, gray, and white colors as specified.',
    timestamp: new Date('2026-05-31T10:29:30'),
    actions: [mockActions[0]]
  },
  {
    id: '3',
    role: 'user',
    content: 'Add the ability to manage sessions and view action results',
    timestamp: new Date('2026-05-31T10:35:00')
  },
  {
    id: '4',
    role: 'assistant',
    content: 'I\'ll add session management features and an inspector panel for viewing results and logs.',
    timestamp: new Date('2026-05-31T10:35:05'),
    actions: [mockActions[1]]
  }
];

export const mockSessions: Session[] = [
  {
    id: '1',
    title: 'AI Chat Workspace',
    isPinned: true,
    lastActivity: new Date('2026-05-31T10:35:00'),
    messages: mockMessages
  },
  {
    id: '2',
    title: 'Build Dashboard UI',
    isPinned: false,
    lastActivity: new Date('2026-05-30T15:20:00'),
    messages: []
  },
  {
    id: '3',
    title: 'Refactor API endpoints',
    isPinned: false,
    lastActivity: new Date('2026-05-30T09:15:00'),
    messages: []
  },
  {
    id: '4',
    title: 'Fix authentication bug',
    isPinned: false,
    lastActivity: new Date('2026-05-29T14:30:00'),
    messages: []
  },
  {
    id: '5',
    title: 'Add user settings',
    isPinned: false,
    lastActivity: new Date('2026-05-25T11:45:00'),
    messages: []
  }
];

export const mockMemories: MemoryItem[] = [
  {
    id: '1',
    content: 'User prefers TypeScript for all React components',
    type: 'session',
    timestamp: new Date('2026-05-31T10:00:00')
  },
  {
    id: '2',
    content: 'Always use shadcn/ui components when available',
    type: 'global',
    timestamp: new Date('2026-05-30T14:00:00')
  }
];

export const mockSkills: Skill[] = [
  {
    id: '1',
    name: 'React Component Generator',
    trigger: '/generate-component',
    status: 'active',
    performance: 95
  },
  {
    id: '2',
    name: 'Code Review Assistant',
    trigger: '/review',
    status: 'active',
    performance: 88
  }
];

export const mockCommands: Command[] = [
  {
    id: '1',
    name: 'Install Package',
    command: 'pnpm install {package}',
    mode: 'full-access'
  },
  {
    id: '2',
    name: 'List Files',
    command: 'ls -la',
    mode: 'read-only'
  }
];

export const mockProjects: Project[] = [
  {
    id: '1',
    name: 'AI Chat Workspace',
    path: '/workspaces/default/code',
    lastOpened: new Date('2026-05-31T10:00:00')
  }
];

export const mockRecurringTasks: RecurringTask[] = [
  {
    id: '1',
    name: 'Daily Code Review',
    schedule: '0 9 * * *',
    lastRun: new Date('2026-05-31T09:00:00'),
    status: 'active'
  }
];
