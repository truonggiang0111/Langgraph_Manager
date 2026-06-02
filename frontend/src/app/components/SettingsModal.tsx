import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from './ui/alert-dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { Badge } from './ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';
import { Plus, Pencil, Trash2, Search } from 'lucide-react';
import { toast } from 'sonner';
import {
  MemoryItem,
  Skill,
  Command,
  Project,
  RecurringTask,
} from '../types';
import { useState } from 'react';
import {
  createCommand,
  createMemory,
  createProject,
  createRecurringTask,
  createSkill,
  deleteCommand,
  deleteMemory,
  deleteProject,
  deleteRecurringTask,
  deleteSkill,
  updateCommand,
  updateMemory,
  updateProject,
  updateRecurringTask,
  updateSkill,
} from '../api';

interface SettingsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  memories: MemoryItem[];
  skills: Skill[];
  commands: Command[];
  projects: Project[];
  recurringTasks: RecurringTask[];
  onReloadSettings: () => Promise<void>;
}

export function SettingsModal({
  open,
  onOpenChange,
  memories,
  skills,
  commands,
  projects,
  recurringTasks,
  onReloadSettings,
}: SettingsModalProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [showTechnicalMemory, setShowTechnicalMemory] = useState(false);
  const [memoryFolderFilter, setMemoryFolderFilter] = useState('all');
  const [memoryDraft, setMemoryDraft] = useState({ id: 0, content: '' });
  const [skillDraft, setSkillDraft] = useState({ id: 0, name: '', trigger: '' });
  const [commandDraft, setCommandDraft] = useState({ id: 0, name: '', command: '' });
  const [projectDraft, setProjectDraft] = useState({ id: 0, name: '', path: '' });
  const [recurringDraft, setRecurringDraft] = useState({ id: 0, name: '', schedule: 'manual' });
  const [deleteTarget, setDeleteTarget] = useState<{ type: 'memory' | 'skill' | 'command' | 'project' | 'recurring'; id: number; label: string } | null>(null);

  const runAndReload = async (work: () => Promise<void>, successMessage: string) => {
    try {
      await work();
      await onReloadSettings();
      toast.success(successMessage);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Action failed');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    const { type, id } = deleteTarget;
    if (type === 'memory') await runAndReload(() => deleteMemory(id), 'Memory deleted');
    if (type === 'skill') await runAndReload(() => deleteSkill(id), 'Skill deleted');
    if (type === 'command') await runAndReload(() => deleteCommand(id), 'Command deleted');
    if (type === 'project') await runAndReload(() => deleteProject(id), 'Project deleted');
    if (type === 'recurring') await runAndReload(() => deleteRecurringTask(id), 'Recurring task deleted');
    setDeleteTarget(null);
  };

  const memoryFolders = ['all', ...Array.from(new Set(memories.map(m => m.folder || 'notes/general'))).sort()];
  const visibleMemories = memories.filter((memory) => {
    if (!showTechnicalMemory && memory.isTechnical) return false;
    if (memoryFolderFilter !== 'all' && (memory.folder || 'notes/general') !== memoryFolderFilter) return false;
    const q = searchQuery.trim().toLowerCase();
    if (!q) return true;
    const hay = `${memory.title || ''}\n${memory.content}\n${memory.tags || ''}\n${memory.source || ''}\n${memory.folder || ''}`.toLowerCase();
    return hay.includes(q);
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="!fixed !inset-0 !h-auto !w-auto !max-w-none !translate-x-0 !translate-y-0 flex flex-col gap-0 overflow-hidden rounded-none p-0 sm:!inset-4 sm:rounded-lg">
        <DialogHeader className="shrink-0 p-4 pb-0 sm:p-6 sm:pb-0">
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription className="sr-only">
            Manage your workspace settings including quality metrics, sessions, memory, skills, commands, projects, roles, and recurring tasks.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="quality" className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="shrink-0 px-4 pt-4 sm:px-6">
            <TabsList className="grid h-auto w-full grid-cols-2 gap-1 p-1 sm:grid-cols-4 xl:grid-cols-8">
              <TabsTrigger value="quality">Quality</TabsTrigger>
              <TabsTrigger value="session">Session</TabsTrigger>
              <TabsTrigger value="memory">Memory</TabsTrigger>
              <TabsTrigger value="skills">Skills</TabsTrigger>
              <TabsTrigger value="commands">Commands</TabsTrigger>
              <TabsTrigger value="projects">Projects</TabsTrigger>
              <TabsTrigger value="roles">Roles</TabsTrigger>
              <TabsTrigger value="recurring">Recurring</TabsTrigger>
            </TabsList>
          </div>

          <div className="min-h-0 flex-1 overflow-auto px-4 py-4 sm:px-6">
            <TabsContent value="quality" className="m-0 space-y-4">
              <div>
                <h3 className="text-sm font-medium mb-3">Quality Metrics</h3>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="bg-gray-50 rounded-lg p-4">
                    <p className="text-sm text-gray-500 mb-1">1-hop Executor Rate</p>
                    <p className="text-2xl font-semibold text-gray-900">87%</p>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-4">
                    <p className="text-sm text-gray-500 mb-1">Non-casual Routes</p>
                    <p className="text-2xl font-semibold text-gray-900">342</p>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-4">
                    <p className="text-sm text-gray-500 mb-1">Route Events</p>
                    <p className="text-2xl font-semibold text-gray-900">1,245</p>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-4">
                    <p className="text-sm text-gray-500 mb-1">Avg Performance</p>
                    <p className="text-2xl font-semibold text-gray-900">91%</p>
                  </div>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="session" className="m-0 space-y-4">
              <div>
                <h3 className="text-sm font-medium mb-3">Session Settings</h3>
                <div className="space-y-3">
                  <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                    <span className="text-sm">Auto-save sessions</span>
                    <Badge>Enabled</Badge>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                    <span className="text-sm">Restore last session</span>
                    <Badge>Enabled</Badge>
                  </div>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="memory" className="m-0 space-y-4">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                  <Input
                    placeholder="Search memories..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-9"
                  />
                </div>
                <Button
                  className="shrink-0 bg-orange-500 hover:bg-orange-600"
                  onClick={() => setMemoryDraft({ id: 0, content: '' })}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Add Memory
                </Button>
                <Button
                  variant={showTechnicalMemory ? 'default' : 'outline'}
                  className={showTechnicalMemory ? 'bg-orange-500 hover:bg-orange-600' : ''}
                  onClick={() => setShowTechnicalMemory((v) => !v)}
                >
                  {showTechnicalMemory ? 'Hide Technical' : 'Show Technical'}
                </Button>
              </div>
              <div className="flex flex-wrap gap-2">
                {memoryFolders.map(folder => (
                  <Button
                    key={folder}
                    variant={memoryFolderFilter === folder ? 'default' : 'outline'}
                    size="sm"
                    className={memoryFolderFilter === folder ? 'bg-orange-500 hover:bg-orange-600' : ''}
                    onClick={() => setMemoryFolderFilter(folder)}
                  >
                    {folder}
                  </Button>
                ))}
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <Textarea
                  placeholder="Memory content..."
                  value={memoryDraft.content}
                  onChange={(e) => setMemoryDraft((prev) => ({ ...prev, content: e.target.value }))}
                  className="min-h-24"
                />
                <div className="mt-2 flex items-center justify-end gap-2">
                  {memoryDraft.id > 0 && (
                    <Button variant="ghost" onClick={() => setMemoryDraft({ id: 0, content: '' })}>Cancel</Button>
                  )}
                  <Button
                    className="bg-orange-500 hover:bg-orange-600"
                    onClick={() => {
                      const content = memoryDraft.content.trim();
                      if (!content) return;
                      if (memoryDraft.id > 0) {
                        const current = memories.find((m) => m.id === memoryDraft.id);
                        if (!current) return;
                        runAndReload(() => updateMemory(current, content), 'Memory updated');
                      } else {
                        runAndReload(() => createMemory({ content, type: 'global' }), 'Memory created');
                      }
                      setMemoryDraft({ id: 0, content: '' });
                    }}
                  >
                    {memoryDraft.id > 0 ? 'Update Memory' : 'Save Memory'}
                  </Button>
                </div>
              </div>

              <div className="hidden md:block">
                <Table className="w-full table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[38%]">Content</TableHead>
                      <TableHead className="w-[12%]">Type</TableHead>
                      <TableHead className="w-[16%]">Folder</TableHead>
                      <TableHead className="w-[12%]">Source</TableHead>
                      <TableHead className="w-[12%]">Date</TableHead>
                      <TableHead className="w-[10%] text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {visibleMemories.map((memory) => (
                      <TableRow key={memory.id}>
                        <TableCell className="truncate">
                          <div className="truncate">{memory.content}</div>
                          {memory.docPath && (
                            <a
                              href={`/api/files/open?path=${encodeURIComponent(memory.docPath)}`}
                              target="_blank"
                              rel="noreferrer"
                              className="mt-1 block truncate text-xs text-orange-600 underline"
                            >
                              {memory.docPath}
                            </a>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{memory.type}</Badge>
                        </TableCell>
                        <TableCell className="truncate text-xs text-gray-500">{memory.folder || 'notes/general'}</TableCell>
                        <TableCell className="truncate text-xs text-gray-500">{memory.source || '-'}</TableCell>
                        <TableCell className="text-sm text-gray-500">
                          {memory.timestamp.toLocaleDateString()}
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0"
                              onClick={() => setMemoryDraft({ id: memory.id, content: memory.content })}
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0 text-red-600"
                              onClick={() => setDeleteTarget({ type: 'memory', id: memory.id, label: memory.content.slice(0, 60) })}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <div className="space-y-2 md:hidden">
                {visibleMemories.map((memory) => (
                  <div key={memory.id} className="rounded-lg border border-border bg-card p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="line-clamp-3 text-sm">{memory.content}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <Badge variant="outline">{memory.type}</Badge>
                          <Badge variant="outline">{memory.folder || 'notes/general'}</Badge>
                          <span>{memory.timestamp.toLocaleDateString()}</span>
                        </div>
                        {memory.docPath && (
                          <a
                            href={`/api/files/open?path=${encodeURIComponent(memory.docPath)}`}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-2 block truncate text-xs text-orange-600 underline"
                          >
                            {memory.docPath}
                          </a>
                        )}
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0"
                          onClick={() => setMemoryDraft({ id: memory.id, content: memory.content })}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-red-600"
                          onClick={() => setDeleteTarget({ type: 'memory', id: memory.id, label: memory.content.slice(0, 60) })}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="skills" className="m-0 space-y-4">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                  <Input placeholder="Search skills..." className="pl-9" />
                </div>
                <Button
                  className="shrink-0 bg-orange-500 hover:bg-orange-600"
                  onClick={() => setSkillDraft({ id: 0, name: '', trigger: '' })}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Add Skill
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-card p-3 sm:grid-cols-[1fr,1fr,auto]">
                <Input placeholder="Skill name" value={skillDraft.name} onChange={(e) => setSkillDraft((p) => ({ ...p, name: e.target.value }))} />
                <Input placeholder="Trigger" value={skillDraft.trigger} onChange={(e) => setSkillDraft((p) => ({ ...p, trigger: e.target.value }))} />
                <div className="flex gap-2">
                  {skillDraft.id > 0 && <Button variant="ghost" onClick={() => setSkillDraft({ id: 0, name: '', trigger: '' })}>Cancel</Button>}
                  <Button className="bg-orange-500 hover:bg-orange-600" onClick={() => {
                    const name = skillDraft.name.trim();
                    const trigger = skillDraft.trigger.trim();
                    if (!name) return;
                    if (skillDraft.id > 0) {
                      const current = skills.find((s) => s.id === skillDraft.id);
                      if (!current) return;
                      runAndReload(() => updateSkill(current, { name, trigger }), 'Skill updated');
                    } else {
                      runAndReload(() => createSkill({ name, trigger }), 'Skill created');
                    }
                    setSkillDraft({ id: 0, name: '', trigger: '' });
                  }}>{skillDraft.id > 0 ? 'Update Skill' : 'Save Skill'}</Button>
                </div>
              </div>

              <div className="hidden md:block">
                <Table className="w-full table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[24%]">Name</TableHead>
                      <TableHead className="w-[38%]">Trigger</TableHead>
                      <TableHead className="w-[14%]">Status</TableHead>
                      <TableHead className="w-[14%]">Performance</TableHead>
                      <TableHead className="w-[10%] text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {skills.map((skill) => (
                      <TableRow key={skill.id}>
                        <TableCell className="truncate">{skill.name}</TableCell>
                        <TableCell className="truncate font-mono text-xs">{skill.trigger}</TableCell>
                        <TableCell>
                          <Badge variant={skill.status === 'active' ? 'default' : 'secondary'} className={skill.status === 'active' ? 'bg-green-500' : ''}>
                            {skill.status}
                          </Badge>
                        </TableCell>
                        <TableCell>{skill.performance}%</TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0"
                              onClick={() => setSkillDraft({ id: skill.id, name: skill.name, trigger: skill.trigger })}
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0 text-red-600"
                              onClick={() => setDeleteTarget({ type: 'skill', id: skill.id, label: skill.name })}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <div className="space-y-2 md:hidden">
                {skills.map((skill) => (
                  <div key={skill.id} className="rounded-lg border border-border bg-card p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{skill.name}</p>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{skill.trigger}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <Badge variant={skill.status === 'active' ? 'default' : 'secondary'} className={skill.status === 'active' ? 'bg-green-500' : ''}>
                            {skill.status}
                          </Badge>
                          <span>{skill.performance}%</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0"
                          onClick={() => setSkillDraft({ id: skill.id, name: skill.name, trigger: skill.trigger })}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-red-600"
                          onClick={() => setDeleteTarget({ type: 'skill', id: skill.id, label: skill.name })}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="commands" className="m-0 space-y-4">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                  <Input placeholder="Search commands..." className="pl-9" />
                </div>
                <Button
                  className="shrink-0 bg-orange-500 hover:bg-orange-600"
                  onClick={() => setCommandDraft({ id: 0, name: '', command: '' })}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Add Command
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-card p-3 sm:grid-cols-[1fr,2fr,auto]">
                <Input placeholder="Command name" value={commandDraft.name} onChange={(e) => setCommandDraft((p) => ({ ...p, name: e.target.value }))} />
                <Input placeholder="Command" value={commandDraft.command} onChange={(e) => setCommandDraft((p) => ({ ...p, command: e.target.value }))} />
                <div className="flex gap-2">
                  {commandDraft.id > 0 && <Button variant="ghost" onClick={() => setCommandDraft({ id: 0, name: '', command: '' })}>Cancel</Button>}
                  <Button className="bg-orange-500 hover:bg-orange-600" onClick={() => {
                    const name = commandDraft.name.trim();
                    const commandText = commandDraft.command.trim();
                    if (!name || !commandText) return;
                    if (commandDraft.id > 0) {
                      const current = commands.find((c) => c.id === commandDraft.id);
                      if (!current) return;
                      runAndReload(() => updateCommand(current, { name, command: commandText }), 'Command updated');
                    } else {
                      runAndReload(() => createCommand({ name, command: commandText }), 'Command created');
                    }
                    setCommandDraft({ id: 0, name: '', command: '' });
                  }}>{commandDraft.id > 0 ? 'Update Command' : 'Save Command'}</Button>
                </div>
              </div>

              <div className="hidden md:block">
                <Table className="w-full table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[24%]">Name</TableHead>
                      <TableHead className="w-[50%]">Command</TableHead>
                      <TableHead className="w-[14%]">Mode</TableHead>
                      <TableHead className="w-[12%] text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {commands.map((command) => (
                      <TableRow key={command.id}>
                        <TableCell className="truncate">{command.name}</TableCell>
                        <TableCell className="truncate font-mono text-xs">{command.command}</TableCell>
                        <TableCell>
                          <Badge variant="outline">{command.mode}</Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0"
                              onClick={() => setCommandDraft({ id: command.id, name: command.name, command: command.command })}
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0 text-red-600"
                              onClick={() => setDeleteTarget({ type: 'command', id: command.id, label: command.name })}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <div className="space-y-2 md:hidden">
                {commands.map((command) => (
                  <div key={command.id} className="rounded-lg border border-border bg-card p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{command.name}</p>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{command.command}</p>
                        <div className="mt-2">
                          <Badge variant="outline">{command.mode}</Badge>
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0"
                          onClick={() => setCommandDraft({ id: command.id, name: command.name, command: command.command })}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-red-600"
                          onClick={() => setDeleteTarget({ type: 'command', id: command.id, label: command.name })}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="projects" className="m-0 space-y-4">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                  <Input placeholder="Search projects..." className="pl-9" />
                </div>
                <Button
                  className="shrink-0 bg-orange-500 hover:bg-orange-600"
                  onClick={() => setProjectDraft({ id: 0, name: '', path: '' })}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Add Project
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-card p-3 sm:grid-cols-[1fr,2fr,auto]">
                <Input placeholder="Project name" value={projectDraft.name} onChange={(e) => setProjectDraft((p) => ({ ...p, name: e.target.value }))} />
                <Input placeholder="Project path" value={projectDraft.path} onChange={(e) => setProjectDraft((p) => ({ ...p, path: e.target.value }))} />
                <div className="flex gap-2">
                  {projectDraft.id > 0 && <Button variant="ghost" onClick={() => setProjectDraft({ id: 0, name: '', path: '' })}>Cancel</Button>}
                  <Button className="bg-orange-500 hover:bg-orange-600" onClick={() => {
                    const name = projectDraft.name.trim();
                    const path = projectDraft.path.trim();
                    if (!name) return;
                    if (projectDraft.id > 0) {
                      const current = projects.find((p) => p.id === projectDraft.id);
                      if (!current) return;
                      runAndReload(() => updateProject(current, { name, path }), 'Project updated');
                    } else {
                      runAndReload(() => createProject({ name, path }), 'Project created');
                    }
                    setProjectDraft({ id: 0, name: '', path: '' });
                  }}>{projectDraft.id > 0 ? 'Update Project' : 'Save Project'}</Button>
                </div>
              </div>

              <div className="hidden md:block">
                <Table className="w-full table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[24%]">Name</TableHead>
                      <TableHead className="w-[46%]">Path</TableHead>
                      <TableHead className="w-[18%]">Last Opened</TableHead>
                      <TableHead className="w-[12%] text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {projects.map((project) => (
                      <TableRow key={project.id}>
                        <TableCell className="truncate">{project.name}</TableCell>
                        <TableCell className="truncate font-mono text-xs">{project.path}</TableCell>
                        <TableCell className="text-sm text-gray-500">
                          {project.lastOpened.toLocaleDateString()}
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0"
                              onClick={() => setProjectDraft({ id: project.id, name: project.name, path: project.path })}
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0 text-red-600"
                              onClick={() => setDeleteTarget({ type: 'project', id: project.id, label: project.name })}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <div className="space-y-2 md:hidden">
                {projects.map((project) => (
                  <div key={project.id} className="rounded-lg border border-border bg-card p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{project.name}</p>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{project.path}</p>
                        <p className="mt-2 text-xs text-muted-foreground">{project.lastOpened.toLocaleDateString()}</p>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0"
                          onClick={() => setProjectDraft({ id: project.id, name: project.name, path: project.path })}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-red-600"
                          onClick={() => setDeleteTarget({ type: 'project', id: project.id, label: project.name })}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="roles" className="m-0 space-y-4">
              <div>
                <h3 className="text-sm font-medium mb-3">Role Plugins</h3>
                <p className="text-sm text-gray-500">No role plugins configured yet.</p>
              </div>
            </TabsContent>

            <TabsContent value="recurring" className="m-0 space-y-4">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                  <Input placeholder="Search recurring tasks..." className="pl-9" />
                </div>
                <Button
                  className="shrink-0 bg-orange-500 hover:bg-orange-600"
                  onClick={() => setRecurringDraft({ id: 0, name: '', schedule: 'manual' })}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Add Task
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-2 rounded-lg border border-border bg-card p-3 sm:grid-cols-[1fr,1fr,auto]">
                <Input placeholder="Task name" value={recurringDraft.name} onChange={(e) => setRecurringDraft((p) => ({ ...p, name: e.target.value }))} />
                <Input placeholder="Schedule" value={recurringDraft.schedule} onChange={(e) => setRecurringDraft((p) => ({ ...p, schedule: e.target.value }))} />
                <div className="flex gap-2">
                  {recurringDraft.id > 0 && <Button variant="ghost" onClick={() => setRecurringDraft({ id: 0, name: '', schedule: 'manual' })}>Cancel</Button>}
                  <Button className="bg-orange-500 hover:bg-orange-600" onClick={() => {
                    const name = recurringDraft.name.trim();
                    const schedule = recurringDraft.schedule.trim() || 'manual';
                    if (!name) return;
                    if (recurringDraft.id > 0) {
                      const current = recurringTasks.find((t) => t.id === recurringDraft.id);
                      if (!current) return;
                      runAndReload(() => updateRecurringTask(current, { name, schedule }), 'Recurring task updated');
                    } else {
                      runAndReload(() => createRecurringTask({ name, schedule }), 'Recurring task created');
                    }
                    setRecurringDraft({ id: 0, name: '', schedule: 'manual' });
                  }}>{recurringDraft.id > 0 ? 'Update Task' : 'Save Task'}</Button>
                </div>
              </div>

              <div className="hidden md:block">
                <Table className="w-full table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[24%]">Name</TableHead>
                      <TableHead className="w-[30%]">Schedule</TableHead>
                      <TableHead className="w-[16%]">Last Run</TableHead>
                      <TableHead className="w-[16%]">Status</TableHead>
                      <TableHead className="w-[14%] text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {recurringTasks.map((task) => (
                      <TableRow key={task.id}>
                        <TableCell className="truncate">{task.name}</TableCell>
                        <TableCell className="truncate font-mono text-xs">{task.schedule}</TableCell>
                        <TableCell className="text-sm text-gray-500">
                          {task.lastRun?.toLocaleDateString() || 'Never'}
                        </TableCell>
                        <TableCell>
                          <Badge variant={task.status === 'active' ? 'default' : 'secondary'} className={task.status === 'active' ? 'bg-green-500' : ''}>
                            {task.status}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0"
                              onClick={() => setRecurringDraft({ id: task.id, name: task.name, schedule: task.schedule })}
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0 text-red-600"
                              onClick={() => setDeleteTarget({ type: 'recurring', id: task.id, label: task.name })}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <div className="space-y-2 md:hidden">
                {recurringTasks.map((task) => (
                  <div key={task.id} className="rounded-lg border border-border bg-card p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{task.name}</p>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{task.schedule}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <Badge variant={task.status === 'active' ? 'default' : 'secondary'} className={task.status === 'active' ? 'bg-green-500' : ''}>
                            {task.status}
                          </Badge>
                          <span>{task.lastRun?.toLocaleDateString() || 'Never'}</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0"
                          onClick={() => setRecurringDraft({ id: task.id, name: task.name, schedule: task.schedule })}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-red-600"
                          onClick={() => setDeleteTarget({ type: 'recurring', id: task.id, label: task.name })}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete item?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete "{deleteTarget?.label || 'selected item'}".
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-red-600 hover:bg-red-700">
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  );
}
