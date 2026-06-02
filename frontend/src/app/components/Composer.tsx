import { Button } from './ui/button';
import { Textarea } from './ui/textarea';
import { useRef, useState } from 'react';
import { Send, Paperclip, X, FileIcon, Mic, Sparkles } from 'lucide-react';
import { AttachedFile } from '../types';
import { Switch } from './ui/switch';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { PermissionMode } from '../api';

interface ComposerProps {
  status?: string;
  busy?: boolean;
  permissionMode: PermissionMode;
  onSendMessage: (content: string, files: AttachedFile[], planMode: boolean) => void;
  onPlanAction: (mode: 'reconfirm' | 'approve' | 'run' | 'regenerate') => void;
  onPermissionModeChange: (mode: PermissionMode) => void;
}

export function Composer({
  status,
  busy,
  permissionMode,
  onSendMessage,
  onPlanAction,
  onPermissionModeChange: _onPermissionModeChange,
}: ComposerProps) {
  const [message, setMessage] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [planMode, setPlanMode] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const sendLockRef = useRef(false);
  const toBase64 = (buffer: ArrayBuffer) => {
    const bytes = new Uint8Array(buffer);
    const chunk = 0x8000;
    let binary = '';
    for (let i = 0; i < bytes.length; i += chunk) {
      const slice = bytes.subarray(i, i + chunk);
      binary += String.fromCharCode(...slice);
    }
    return btoa(binary);
  };

  const handleSend = () => {
    if (busy || sendLockRef.current) return;
    const payload = message.trim();
    if (payload) {
      sendLockRef.current = true;
      onSendMessage(payload, attachedFiles, planMode);
      setMessage('');
      setAttachedFiles([]);
      setPlanMode(false);
      window.setTimeout(() => {
        sendLockRef.current = false;
      }, 600);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const removeFile = (id: string) => {
    setAttachedFiles(files => files.filter(f => f.id !== id));
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const items = await Promise.all(Array.from(files).map(async file => ({
      id: `${file.name}-${file.lastModified}-${file.size}`,
      name: file.name,
      size: file.size,
      type: file.type || 'text/plain',
      content: await file.text().catch(() => ''),
      dataBase64: toBase64(await file.arrayBuffer()),
    })));
    setAttachedFiles(prev => [...prev, ...items]);
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };


  const startOnboardingFlow = () => {
    if (busy || sendLockRef.current) return;
    const content = message.trim() || 'Phan tich doan chat hien tai va lap plan cu the theo ngu canh dang trao doi, khong dung mau mac dinh.';
    sendLockRef.current = true;
    onSendMessage(content, [], true);
    window.setTimeout(() => {
      sendLockRef.current = false;
    }, 600);
  };

  return (
    <div className="shrink-0 rounded-b-[22px] bg-transparent p-4">
      <div className="mx-auto max-w-4xl rounded-[22px] border border-border bg-card px-4 pb-3 pt-3 shadow-sm">
        {attachedFiles.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachedFiles.map(file => (
              <Badge
                key={file.id}
                variant="secondary"
                className="pl-2 pr-1 py-1 gap-2"
              >
                <FileIcon className="h-3 w-3" />
                <span className="text-xs">
                  {file.name} ({formatFileSize(file.size)})
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-4 w-4 p-0 hover:bg-gray-300"
                  onClick={() => removeFile(file.id)}
                >
                  <X className="h-3 w-3" />
                </Button>
              </Badge>
            ))}
          </div>
        )}

        <div className="flex gap-2">
          <div className="relative flex-1">
            <Textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type your message..."
              className="min-h-[48px] max-h-[200px] resize-none border-0 bg-transparent px-0 py-0 pr-2 shadow-none outline-none focus-visible:ring-0"
            />
          </div>
        </div>

        <div className="mt-3 flex items-center gap-3 text-xs">
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              handleFiles(event.target.files);
              event.currentTarget.value = '';
            }}
          />
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 shrink-0 rounded-full p-0 hover:bg-gray-100"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            <Paperclip className="h-4 w-4 text-gray-500" />
          </Button>
          <div className="flex items-center gap-2">
            <Switch
              id="plan-mode"
              checked={planMode}
              onCheckedChange={setPlanMode}
              className="data-[state=checked]:bg-orange-500"
            />
            <Label htmlFor="plan-mode" className="cursor-pointer text-gray-600">Plan Mode</Label>
          </div>
          <Button size="sm" variant="outline" className="h-7 text-xs" disabled={busy} onClick={startOnboardingFlow}>
            <Sparkles className="mr-1 h-3 w-3" />
            Onboard Agent
          </Button>
          <Badge variant="outline" className="h-7 px-2 text-xs">
            {permissionMode === 'full_access' ? 'Full access' : 'Full access'}
          </Badge>
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 shrink-0 rounded-full p-0 hover:bg-gray-100"
            disabled
            aria-label="Voice input"
          >
            <Mic className="h-4 w-4 text-gray-500" />
          </Button>
          <Button
            onClick={handleSend}
            disabled={busy || !message.trim()}
            size="sm"
            className="h-9 w-9 shrink-0 rounded-full !bg-white p-0 !text-gray-900 hover:!bg-gray-200 disabled:!bg-gray-500 disabled:!text-gray-300"
            aria-label="Send message"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
