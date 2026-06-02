import { Session } from '../types';
import { formatDistanceToNow } from 'date-fns';
import { MessageSquare, Pin, MoreVertical, Pencil, Trash2 } from 'lucide-react';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onToggleSelect: () => void;
  onRename: () => void;
  onDelete: () => void;
}

export function SessionItem({
  session,
  isActive,
  isSelected,
  onSelect,
  onToggleSelect,
  onRename,
  onDelete,
}: SessionItemProps) {
  return (
    <div
      className={`
        group flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer transition-colors
        ${isActive ? 'bg-orange-50 text-orange-900' : 'hover:bg-gray-200'}
      `}
      onClick={onSelect}
    >
      <Checkbox
        checked={isSelected}
        onCheckedChange={onToggleSelect}
        onClick={(e) => e.stopPropagation()}
        className="data-[state=checked]:bg-orange-500 data-[state=checked]:border-orange-500"
      />

      <MessageSquare className="h-4 w-4 flex-shrink-0 text-gray-500" />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1">
          <span className="text-sm font-medium truncate">{session.title}</span>
          {session.isPinned && <Pin className="h-3 w-3 text-orange-500 flex-shrink-0" />}
        </div>
        <p className="text-xs text-gray-500">
          {formatDistanceToNow(session.lastActivity, { addSuffix: true })}
        </p>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center justify-center rounded-md hover:bg-gray-200"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreVertical className="h-3 w-3" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onRename(); }}>
            <Pencil className="mr-2 h-4 w-4" />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="text-red-600"
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
