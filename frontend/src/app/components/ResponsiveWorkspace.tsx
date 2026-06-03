import { useEffect, useState } from 'react';
import { SessionSidebar } from './SessionSidebar';
import { ChatArea } from './ChatArea';
import { Composer } from './Composer';
import { InspectorPanel } from './InspectorPanel';
import { SettingsModal } from './SettingsModal';
import { Action, AttachedFile, Message, Session } from '../types';
import { Sheet, SheetContent } from './ui/sheet';
import { Drawer, DrawerContent } from './ui/drawer';
import { Button } from './ui/button';
import { FileText, Menu, Settings, Terminal } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { ScrollArea } from './ui/scroll-area';
import { BackendJob, PermissionMode } from '../api';
import { FacebookResearchResult } from './FacebookResearchResult';

const INSPECTOR_COLLAPSED_KEY = 'lg_inspector_collapsed';

interface ResponsiveWorkspaceProps {
  sessions: Session[];
  activeSessionId: string;
  activeMessages: Message[];
  selectedAction: Action | null;
  activeJob: BackendJob | null;
  settingsOpen: boolean;
  busy?: boolean;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSessions: (ids: string[]) => void;
  onRenameSession: (id: string) => void;
  onSendMessage: (content: string, files: AttachedFile[], planMode: boolean) => void;
  onSelectAction: (id: string) => void;
  onOpenSettings: (open: boolean) => void;
  onPlanAction: (mode: 'reconfirm' | 'approve' | 'run' | 'regenerate') => void;
  onPermissionModeChange: (mode: PermissionMode) => void;
  onActionDecision: (id: string, decision: 'approve' | 'reject') => void;
  onReconfirmPlan: (actionId: string, steps: string[]) => void;
  memories: any[];
  skills: any[];
  commands: any[];
  projects: any[];
  recurringTasks: any[];
  onReloadSettings: () => Promise<void>;
}

export function ResponsiveWorkspace({
  sessions,
  activeSessionId,
  activeMessages,
  selectedAction,
  activeJob,
  settingsOpen,
  busy,
  onSelectSession,
  onNewSession,
  onDeleteSessions,
  onRenameSession,
  onSendMessage,
  onSelectAction,
  onOpenSettings,
  onPlanAction,
  onPermissionModeChange,
  onActionDecision,
  onReconfirmPlan,
  memories,
  skills,
  commands,
  projects,
  recurringTasks,
  onReloadSettings,
}: ResponsiveWorkspaceProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => {
    return localStorage.getItem(INSPECTOR_COLLAPSED_KEY) !== 'false';
  });
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);

  const activeSession = sessions.find(s => s.id === activeSessionId);
  const permissionMode = (activeJob?.permission_mode || activeSession?.permissionMode || 'full_access') as PermissionMode;

  useEffect(() => {
    localStorage.setItem(INSPECTOR_COLLAPSED_KEY, String(inspectorCollapsed));
  }, [inspectorCollapsed]);

  return (
    <>
      {/* Desktop Layout */}
      <div className="relative hidden md:flex size-full bg-background">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onOpenSettings(true)}
          className={`absolute top-3 z-20 h-8 w-8 p-0 bg-background/80 backdrop-blur hover:bg-gray-100 ${
            inspectorCollapsed ? 'right-14' : 'right-[21rem]'
          }`}
          aria-label="Open settings"
        >
          <Settings className="h-4 w-4 text-gray-600" />
        </Button>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <SessionSidebar
            sessions={sessions}
            activeSessionId={activeSessionId}
            onSelectSession={onSelectSession}
            onNewSession={onNewSession}
            onDeleteSessions={onDeleteSessions}
            onRenameSession={onRenameSession}
            collapsed={sidebarCollapsed}
            onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
          />

          <div className="flex min-w-0 flex-1 p-2">
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-border bg-background shadow-sm">
            <ChatArea messages={activeMessages} onSelectAction={onSelectAction} onActionDecision={onActionDecision} onReconfirmPlan={onReconfirmPlan} />
            <Composer
              status={activeJob?.status}
              busy={busy}
              permissionMode={permissionMode}
              onSendMessage={onSendMessage}
              onPlanAction={onPlanAction}
              onPermissionModeChange={onPermissionModeChange}
            />
            </div>
          </div>

          <InspectorPanel
            selectedAction={selectedAction}
            collapsed={inspectorCollapsed}
            onToggleCollapse={() => setInspectorCollapsed(!inspectorCollapsed)}
          />
        </div>
      </div>

      {/* Mobile Layout */}
      <div className="relative flex md:hidden size-full flex-col bg-background">
        <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between px-3 pt-2">
          <Button
            variant="ghost"
            size="sm"
            className="pointer-events-auto h-8 w-8 p-0 bg-background/85 shadow-sm backdrop-blur hover:bg-gray-100"
            onClick={() => setMobileSidebarOpen(true)}
            aria-label="Open sessions"
          >
            <Menu className="h-4 w-4 text-gray-600" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="pointer-events-auto h-8 w-8 p-0 bg-background/85 shadow-sm backdrop-blur hover:bg-gray-100"
            onClick={() => onOpenSettings(true)}
            aria-label="Open settings"
          >
            <Settings className="h-4 w-4 text-gray-600" />
          </Button>
        </div>

        <div className="flex min-h-0 flex-1 p-2 pt-10">
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-border bg-background shadow-sm">
          <ChatArea messages={activeMessages} onSelectAction={(id) => {
            onSelectAction(id);
            setMobileInspectorOpen(true);
          }} onActionDecision={onActionDecision} onReconfirmPlan={onReconfirmPlan} />
          <Composer
            status={activeJob?.status}
            busy={busy}
            permissionMode={permissionMode}
            onSendMessage={onSendMessage}
            onPlanAction={onPlanAction}
            onPermissionModeChange={onPermissionModeChange}
          />
          </div>
        </div>

        {selectedAction && (
          <div className="p-2 bg-gray-100 border-t border-gray-200">
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => setMobileInspectorOpen(true)}
            >
              <FileText className="mr-2 h-4 w-4" />
              View Action Details
            </Button>
          </div>
        )}
      </div>

      {/* Mobile Sidebar Sheet */}
      <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
        <SheetContent side="left" className="w-[280px] p-0">
          <SessionSidebar
            sessions={sessions}
            activeSessionId={activeSessionId}
            onSelectSession={(id) => {
              onSelectSession(id);
              setMobileSidebarOpen(false);
            }}
            onNewSession={() => {
              onNewSession();
              setMobileSidebarOpen(false);
            }}
            onDeleteSessions={onDeleteSessions}
            onRenameSession={onRenameSession}
            collapsed={false}
            onToggleCollapse={() => {}}
          />
        </SheetContent>
      </Sheet>

      {/* Mobile Inspector Drawer */}
      <Drawer open={mobileInspectorOpen} onOpenChange={setMobileInspectorOpen}>
        <DrawerContent className="max-h-[85vh]">
          {selectedAction ? (
            <div className="p-4">
              <h2 className="font-semibold text-lg mb-4">Action Details</h2>
              <Tabs defaultValue="result">
                <TabsList className="grid w-full grid-cols-2">
                  <TabsTrigger value="result">Result</TabsTrigger>
                  <TabsTrigger value="logs">Logs</TabsTrigger>
                </TabsList>

                <TabsContent value="result" className="mt-4">
                  <ScrollArea className="h-[300px]">
                    <div className="space-y-4">
                      <div>
                        <h3 className="text-sm font-medium text-gray-700 mb-2">Action</h3>
                        <p className="text-sm text-gray-600">{selectedAction.kind}</p>
                      </div>

                      <div>
                        <h3 className="text-sm font-medium text-gray-700 mb-2">Description</h3>
                        <p className="text-sm text-gray-600">{selectedAction.description}</p>
                      </div>

                      {selectedAction.facebookResearch ? (
                        <div>
                          <h3 className="text-sm font-medium text-gray-700 mb-2">Facebook Research</h3>
                          <FacebookResearchResult data={selectedAction.facebookResearch} />
                        </div>
                      ) : selectedAction.result && (
                        <div>
                          <h3 className="text-sm font-medium text-gray-700 mb-2">Result</h3>
                          <div className="bg-gray-50 rounded-md p-3 text-sm text-gray-700">
                            {selectedAction.result}
                          </div>
                        </div>
                      )}

                      <div>
                        <h3 className="text-sm font-medium text-gray-700 mb-2">Status</h3>
                        <p className="text-sm text-gray-600 capitalize">{selectedAction.status}</p>
                      </div>

                      <div>
                        <h3 className="text-sm font-medium text-gray-700 mb-2">Risk Level</h3>
                        <p className="text-sm text-gray-600 capitalize">{selectedAction.risk}</p>
                      </div>
                    </div>
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="logs" className="mt-4">
                  <ScrollArea className="h-[300px]">
                    {selectedAction.logs && selectedAction.logs.length > 0 ? (
                      <div className="bg-gray-900 rounded-md p-3 font-mono text-xs text-gray-100 space-y-1">
                        {selectedAction.logs.map((log, i) => (
                          <div key={i}>{log}</div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-center py-8">
                        <p className="text-sm text-gray-500">No logs available</p>
                      </div>
                    )}
                  </ScrollArea>
                </TabsContent>
              </Tabs>
            </div>
          ) : (
            <div className="p-8 text-center">
              <p className="text-sm text-gray-500">No action selected</p>
            </div>
          )}
        </DrawerContent>
      </Drawer>

      {/* Settings Modal */}
      <SettingsModal
        open={settingsOpen}
        onOpenChange={onOpenSettings}
        memories={memories}
        skills={skills}
        commands={commands}
        projects={projects}
        recurringTasks={recurringTasks}
        onReloadSettings={onReloadSettings}
      />
    </>
  );
}
