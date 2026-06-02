import { Message } from '../types';
import { ActionCard } from './ActionCard';
import { formatDistanceToNowStrict } from 'date-fns';
import { User, Bot, FileIcon, FileImage, FileText } from 'lucide-react';
import { memo, type ReactNode } from 'react';

interface MessageBubbleProps {
  message: Message;
  onSelectAction?: (actionId: string) => void;
  onActionDecision?: (actionId: string, decision: 'approve' | 'reject') => void;
  onReconfirmPlan?: (actionId: string, steps: string[]) => void;
}

function MessageBubbleInner({ message, onSelectAction, onActionDecision, onReconfirmPlan }: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const hasContent = message.content.trim().length > 0;
  const hasAttachments = (message.attachments?.length || 0) > 0;
  const relativeTime = formatDistanceToNowStrict(message.timestamp, { addSuffix: true });
  const attachmentIcon = (name: string, type?: string) => {
    const lower = name.toLowerCase();
    const mime = (type || '').toLowerCase();
    if (lower.endsWith('.png') || lower.endsWith('.jpg') || lower.endsWith('.jpeg') || lower.endsWith('.webp') || mime.startsWith('image/')) {
      return FileImage;
    }
    if (lower.endsWith('.pdf') || mime.includes('pdf')) {
      return FileText;
    }
    return FileIcon;
  };
  const openPreview = (file: { name: string; type?: string; dataBase64?: string }) => {
    if (!file.dataBase64) return;
    const mime = file.type || (file.name.toLowerCase().endsWith('.pdf') ? 'application/pdf' : 'application/octet-stream');
    const url = `data:${mime};base64,${file.dataBase64}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  };
  const renderContent = (text: string) => {
    const pattern = /(https?:\/\/[^\s`]+|\/workspace\/[^\s`]+|[A-Za-z]:\\[^\s`]+)/g;
    const lines = text.split('\n');
    return lines.map((line, lineIndex) => {
      const parts: ReactNode[] = [];
      let lastIndex = 0;
      line.replace(pattern, (match, _g, offset: number) => {
        if (offset > lastIndex) {
          parts.push(line.slice(lastIndex, offset));
        }
        const isHttp = /^https?:\/\//i.test(match);
        const href = isHttp
          ? match
          : `/api/files/open?path=${encodeURIComponent(match)}`;
        parts.push(
          <a
            key={`${lineIndex}-${offset}-${match}`}
            href={href}
            target="_blank"
            rel="noreferrer"
            className={`underline underline-offset-2 ${isUser ? 'text-orange-200' : 'text-orange-600'}`}
          >
            {match}
          </a>,
        );
        lastIndex = offset + match.length;
        return match;
      });
      if (lastIndex < line.length) {
        parts.push(line.slice(lastIndex));
      }
      return (
        <span key={`line-${lineIndex}`}>
          {parts.length ? parts : line}
          {lineIndex < lines.length - 1 ? <br /> : null}
        </span>
      );
    });
  };

  return (
    <div className={`flex gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div className={`
        flex-shrink-0 h-7 w-7 rounded-full flex items-center justify-center
        ${isUser ? 'bg-gray-700' : 'bg-orange-500'}
      `}>
        {isUser ? (
          <User className="h-4 w-4 text-white" />
        ) : (
          <Bot className="h-4 w-4 text-white" />
        )}
      </div>

      <div className={`flex-1 max-w-[80%] ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
        {(hasContent || hasAttachments) && (
          <div className={`
            rounded-lg px-3 py-2
            ${isUser ? 'bg-gray-700 text-white' : 'bg-white border border-gray-200'}
          `}>
            {hasContent && <p className="text-sm whitespace-pre-wrap break-words">{renderContent(message.content)}</p>}
            {hasAttachments && (
              <div className={`${hasContent ? 'mt-2' : ''} flex flex-wrap gap-2`}>
                {message.attachments!.map((file) => (
                  <button
                    key={file.id}
                    type="button"
                    onClick={() => openPreview(file)}
                    className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs ${
                      isUser ? 'bg-gray-600 text-gray-100' : 'bg-gray-100 text-gray-700'
                    }`}
                  >
                    {(() => {
                      const Icon = attachmentIcon(file.name, file.type);
                      return <Icon className="h-3 w-3" />;
                    })()}
                    {file.name}
                  </button>
                ))}
              </div>
            )}
            <p className={`mt-1 text-xs ${isUser ? 'text-gray-300' : 'text-gray-400'}`}>
              {relativeTime}
            </p>
          </div>
        )}

        {message.actions && message.actions.length > 0 && (
          <div className="mt-2 space-y-2 w-full">
            {message.actions.map((action) => (
              <ActionCard
                key={action.id}
                action={action}
                onApprove={() => onActionDecision?.(action.id, 'approve')}
                onReject={() => onActionDecision?.(action.id, 'reject')}
                onViewLogs={() => onSelectAction?.(action.id)}
                onViewResult={() => onSelectAction?.(action.id)}
                onReconfirmPlan={(steps) => onReconfirmPlan?.(action.id, steps)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function areEqual(prev: MessageBubbleProps, next: MessageBubbleProps) {
  if (prev.message.id !== next.message.id) return false;
  if (prev.message.role !== next.message.role) return false;
  if (prev.message.content !== next.message.content) return false;
  if (prev.message.timestamp.getTime() !== next.message.timestamp.getTime()) return false;
  const prevAttachments = prev.message.attachments || [];
  const nextAttachments = next.message.attachments || [];
  if (prevAttachments.length !== nextAttachments.length) return false;
  for (let i = 0; i < prevAttachments.length; i += 1) {
    if (prevAttachments[i].id !== nextAttachments[i].id || prevAttachments[i].name !== nextAttachments[i].name) {
      return false;
    }
  }

  const prevActions = prev.message.actions || [];
  const nextActions = next.message.actions || [];
  if (prevActions.length !== nextActions.length) return false;

  for (let i = 0; i < prevActions.length; i += 1) {
    const a = prevActions[i];
    const b = nextActions[i];
    if (
      a.id !== b.id
      || a.status !== b.status
      || a.description !== b.description
      || a.risk !== b.risk
      || a.result !== b.result
      || a.timestamp.getTime() !== b.timestamp.getTime()
      || (a.logs?.length || 0) !== (b.logs?.length || 0)
    ) {
      return false;
    }
  }

  return true;
}

export const MessageBubble = memo(MessageBubbleInner, areEqual);
