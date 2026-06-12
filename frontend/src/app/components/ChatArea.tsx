import { Message } from '../types';
import { MessageBubble } from './MessageBubble';
import { ScrollArea } from './ui/scroll-area';
import { useEffect, useRef } from 'react';

interface ChatAreaProps {
  messages: Message[];
  onSelectAction: (actionId: string) => void;
  onActionDecision?: (actionId: string, decision: 'approve' | 'reject') => void;
  onReconfirmPlan?: (actionId: string, steps: string[]) => void;
}

export function ChatArea({ messages, onSelectAction, onActionDecision, onReconfirmPlan }: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center rounded-t-2xl bg-background">
        <div className="text-center">
          <div className="mb-3 h-12 w-12 rounded-full bg-gray-200 flex items-center justify-center mx-auto">
            <span className="text-2xl">💬</span>
          </div>
          <h3 className="text-base font-medium text-gray-900 mb-1">No messages yet</h3>
          <p className="text-sm text-gray-500">Start a conversation with the AI assistant</p>
        </div>
      </div>
    );
  }

  return (
    <ScrollArea
      className="min-h-0 flex-1 overflow-hidden rounded-t-2xl bg-background"
      viewportRef={scrollAreaRef}
    >
      <div className="p-4 space-y-4 max-w-4xl mx-auto">
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            onSelectAction={onSelectAction}
            onActionDecision={onActionDecision}
            onReconfirmPlan={onReconfirmPlan}
          />
        ))}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
