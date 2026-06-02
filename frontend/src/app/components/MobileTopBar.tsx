import { Button } from './ui/button';
import { Settings, Menu } from 'lucide-react';
import { Badge } from './ui/badge';

interface MobileTopBarProps {
  sessionTitle: string;
  permissionMode: string;
  onOpenSettings: () => void;
  onOpenSidebar: () => void;
}

export function MobileTopBar({
  sessionTitle,
  permissionMode,
  onOpenSettings,
  onOpenSidebar,
}: MobileTopBarProps) {
  return (
    <div className="h-12 bg-background border-b border-gray-200 flex items-center justify-between px-3">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={onOpenSidebar}>
          <Menu className="h-4 w-4 text-gray-600" />
        </Button>
        <h1 className="font-medium text-gray-900 text-sm truncate max-w-[180px]">
          {sessionTitle}
        </h1>
      </div>

      <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={onOpenSettings}>
        <Settings className="h-4 w-4 text-gray-600" />
      </Button>
    </div>
  );
}
