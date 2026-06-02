import { Button } from './ui/button';
import { Settings, Pin } from 'lucide-react';
import { Badge } from './ui/badge';

interface TopBarProps {
  sessionTitle: string;
  isPinned: boolean;
  permissionMode: string;
  onOpenSettings: () => void;
}

export function TopBar({ sessionTitle, isPinned, permissionMode, onOpenSettings }: TopBarProps) {
  return (
    <div className="h-14 bg-white border-b border-gray-200 flex items-center justify-between px-6">
      <div className="flex items-center gap-3">
        <h1 className="font-semibold text-gray-900">{sessionTitle}</h1>
        {isPinned && <Pin className="h-4 w-4 text-orange-500" />}
      </div>

      <div className="flex items-center gap-3">
        <Badge variant="outline" className="bg-gray-50">
          {permissionMode}
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={onOpenSettings}
        >
          <Settings className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
