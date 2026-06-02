import { Button } from './ui/button';
import { Settings } from 'lucide-react';

interface CompactTopBarProps {
  onOpenSettings: () => void;
}

export function CompactTopBar({ onOpenSettings }: CompactTopBarProps) {
  return (
    <div className="h-12 bg-background border-b border-gray-200 flex items-center justify-end px-4">
      <Button
        variant="ghost"
        size="sm"
        onClick={onOpenSettings}
        className="h-8 w-8 p-0"
      >
        <Settings className="h-4 w-4 text-gray-600" />
      </Button>
    </div>
  );
}
