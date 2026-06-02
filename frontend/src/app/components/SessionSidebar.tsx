import { Session } from '../types';
import { SessionItem } from './SessionItem';
import { Button } from './ui/button';
import { ScrollArea } from './ui/scroll-area';
import { Plus, Trash2, ChevronLeft } from 'lucide-react';
import { useState } from 'react';
import { Checkbox } from './ui/checkbox';
import { isToday, isYesterday, isAfter, subDays } from 'date-fns';

interface SessionSidebarProps {
  sessions: Session[];
  activeSessionId: string;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSessions: (ids: string[]) => void;
  onRenameSession: (id: string) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSessions,
  onRenameSession,
  collapsed,
  onToggleCollapse,
}: SessionSidebarProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const groupedSessions = {
    today: sessions.filter(s => isToday(s.lastActivity)),
    yesterday: sessions.filter(s => isYesterday(s.lastActivity)),
    previous7Days: sessions.filter(s => {
      const sevenDaysAgo = subDays(new Date(), 7);
      return !isToday(s.lastActivity) && !isYesterday(s.lastActivity) && isAfter(s.lastActivity, sevenDaysAgo);
    }),
  };

  const toggleSelection = (id: string) => {
    const newSet = new Set(selectedIds);
    if (newSet.has(id)) {
      newSet.delete(id);
    } else {
      newSet.add(id);
    }
    setSelectedIds(newSet);
  };

  const selectAll = () => {
    setSelectedIds(new Set(sessions.map(s => s.id)));
  };

  const deselectAll = () => {
    setSelectedIds(new Set());
  };

  if (collapsed) {
    return (
      <div className="w-12 bg-sidebar border-r border-sidebar-border flex flex-col items-center py-4">
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0 hover:bg-gray-300"
          onClick={onToggleCollapse}
        >
          <ChevronLeft className="h-4 w-4 rotate-180 text-gray-600" />
        </Button>
      </div>
    );
  }

  return (
    <div className="w-64 bg-sidebar border-r border-sidebar-border flex flex-col">
      <div className="p-3 border-b border-sidebar-border">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-900">Sessions</h2>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0 hover:bg-gray-300"
            onClick={onToggleCollapse}
          >
            <ChevronLeft className="h-3.5 w-3.5 text-gray-600" />
          </Button>
        </div>
        <Button
          onClick={onNewSession}
          className="w-full bg-orange-500 hover:bg-orange-600 h-9"
          size="sm"
        >
          <Plus className="mr-2 h-4 w-4" />
          New Session
        </Button>
      </div>

      {selectedIds.size > 0 && (
        <div className="px-3 py-2 bg-orange-50 border-b border-orange-200 flex items-center justify-between">
          <span className="text-xs text-gray-700">{selectedIds.size} selected</span>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={deselectAll}
              className="h-6 text-xs px-2"
            >
              Deselect
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => {
                onDeleteSessions(Array.from(selectedIds));
                setSelectedIds(new Set());
              }}
              className="h-6 text-xs px-2"
            >
              <Trash2 className="mr-1 h-3 w-3" />
              Delete
            </Button>
          </div>
        </div>
      )}

      <ScrollArea className="flex-1">
        <div className="p-3 space-y-4">
          {groupedSessions.today.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-medium text-gray-500 uppercase">Today</h3>
                <Checkbox
                  checked={groupedSessions.today.every(s => selectedIds.has(s.id))}
                  onCheckedChange={(checked) => {
                    if (checked) {
                      groupedSessions.today.forEach(s => selectedIds.add(s.id));
                      setSelectedIds(new Set(selectedIds));
                    } else {
                      groupedSessions.today.forEach(s => selectedIds.delete(s.id));
                      setSelectedIds(new Set(selectedIds));
                    }
                  }}
                  className="data-[state=checked]:bg-orange-500 data-[state=checked]:border-orange-500"
                />
              </div>
              <div className="space-y-1">
                {groupedSessions.today.map(session => (
                  <SessionItem
                    key={session.id}
                    session={session}
                    isActive={session.id === activeSessionId}
                    isSelected={selectedIds.has(session.id)}
                    onSelect={() => onSelectSession(session.id)}
                    onToggleSelect={() => toggleSelection(session.id)}
                    onRename={() => onRenameSession(session.id)}
                    onDelete={() => onDeleteSessions([session.id])}
                  />
                ))}
              </div>
            </div>
          )}

          {groupedSessions.yesterday.length > 0 && (
            <div>
              <h3 className="text-xs font-medium text-gray-500 uppercase mb-2">Yesterday</h3>
              <div className="space-y-1">
                {groupedSessions.yesterday.map(session => (
                  <SessionItem
                    key={session.id}
                    session={session}
                    isActive={session.id === activeSessionId}
                    isSelected={selectedIds.has(session.id)}
                    onSelect={() => onSelectSession(session.id)}
                    onToggleSelect={() => toggleSelection(session.id)}
                    onRename={() => onRenameSession(session.id)}
                    onDelete={() => onDeleteSessions([session.id])}
                  />
                ))}
              </div>
            </div>
          )}

          {groupedSessions.previous7Days.length > 0 && (
            <div>
              <h3 className="text-xs font-medium text-gray-500 uppercase mb-2">Previous 7 Days</h3>
              <div className="space-y-1">
                {groupedSessions.previous7Days.map(session => (
                  <SessionItem
                    key={session.id}
                    session={session}
                    isActive={session.id === activeSessionId}
                    isSelected={selectedIds.has(session.id)}
                    onSelect={() => onSelectSession(session.id)}
                    onToggleSelect={() => toggleSelection(session.id)}
                    onRename={() => onRenameSession(session.id)}
                    onDelete={() => onDeleteSessions([session.id])}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
