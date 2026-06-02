import { ActionStatus } from '../types';
import { Badge } from './ui/badge';
import { CheckCircle2, Circle, XCircle, AlertCircle, Loader2 } from 'lucide-react';

interface StatusBadgeProps {
  status: ActionStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const config = {
    pending: {
      label: 'Pending',
      variant: 'secondary' as const,
      icon: Circle,
      className: 'bg-gray-100 text-gray-600 hover:bg-gray-100'
    },
    running: {
      label: 'Running',
      variant: 'default' as const,
      icon: Loader2,
      className: 'bg-orange-500 text-white hover:bg-orange-500'
    },
    done: {
      label: 'Done',
      variant: 'default' as const,
      icon: CheckCircle2,
      className: 'bg-green-500 text-white hover:bg-green-500'
    },
    failed: {
      label: 'Failed',
      variant: 'destructive' as const,
      icon: XCircle,
      className: 'bg-red-500 text-white hover:bg-red-500'
    },
    rejected: {
      label: 'Rejected',
      variant: 'secondary' as const,
      icon: AlertCircle,
      className: 'bg-gray-700 text-white hover:bg-gray-700'
    }
  };

  const { label, icon: Icon, className } = config[status];

  return (
    <Badge className={className}>
      <Icon className={`mr-1 h-3 w-3 ${status === 'running' ? 'animate-spin' : ''}`} />
      {label}
    </Badge>
  );
}
