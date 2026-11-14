import { useEffect, useRef, useState } from 'react';
import { Loader2, Send } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { forge, type ChatMessage, type TraceEvent } from '@/lib/forge';

const SKIP = new Set(['todo']);

function Trace({ events }: { events?: TraceEvent[] }) {
  const shown = (events ?? []).filter((e) => !SKIP.has(e.kind));
  if (!shown.length) return null;
  return (
    <ul className="mt-2 space-y-0.5">
      {shown.map((e, i) => (
        <li key={i} className="flex gap-2 font-mono-ui text-xs text-muted-foreground">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
          <span className="break-all">{e.text}</span>
        </li>
      ))}
    </ul>
  );
}

interface Props {
  projectId: number;
  onChanged: () => void;
}

export default function ChatPanel({ projectId, onChanged }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    forge.messages(projectId).then(setMessages).catch((e) => toast.error((e as Error).message));
  }, [projectId]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' });
  }, [messages, busy]);

  const [liveEvents, setLiveEvents] = useState<TraceEvent[]>([]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput('');
    setBusy(true);
    setLiveEvents([]);
    setMessages((m) => [...m, { id: Date.now(), role: 'user', content: text }]);
    try {
      const { run_key } = await forge.startRun({ message: text, project_id: projectId });
      forge.streamRun(
        run_key,
        (e) => setLiveEvents((ev) => [...ev, { kind: e.type, text: e.data.text ?? '' }]),
        () => {
          setMessages((m) => [...m, { id: Date.now() + 1, role: 'assistant', content: '已完成这一步。' }]);
          onChanged();
          setBusy(false);
        }
      );
    } catch (e) {
      toast.error('发送失败', { description: (e as Error).message });
      setMessages((m) => [...m, { id: Date.now() + 2, role: 'assistant', content: `⚠️ ${(e as Error).message}` }]);
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.map((m) => (
          <div key={m.id} className={m.role === 'user' ? 'flex justify-end' : ''}>
            <div className={m.role === 'user' ? 'max-w-[88%] bg-sunk px-3 py-2 text-sm' : 'border-l-2 border-border pl-3 text-sm'}>
              <p className="whitespace-pre-wrap">{m.content}</p>
              {m.role === 'assistant' && <Trace events={m.meta?.events} />}
              {m.meta?.verify_failed && (
                <p className="mt-1 text-xs text-amber-700">
                  ⚠️ 核验未通过（{Math.round((m.meta.verify?.score ?? 0) * 100)}%），可继续修改。
                </p>
              )}
              {m.meta?.usage && (
                <p className="mt-1 font-mono-ui text-[11px] text-muted-foreground">
                  {m.meta.usage.tokens} tokens · ¥{m.meta.usage.cost_cny} · {m.meta.usage.turns} 轮
                </p>
              )}
            </div>
          </div>
        ))}
        {liveEvents.length > 0 && (
          <div className="space-y-1 border-l-2 border-primary pl-3">
            {liveEvents.map((e, i) => (
              <p key={i} className="font-mono-ui text-xs text-muted-foreground">
                {e.text}
              </p>
            ))}
          </div>
        )}
        {busy && (
          <p className="flex items-center gap-2 border-l-2 border-primary pl-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Agent 正在生成或修改应用…
          </p>
        )}
        <div ref={bottom} />
      </div>
      <div className="border-t border-border p-3">
        <div className="flex gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            placeholder="例如：给任务加一个负责人字段；把状态改成四种…"
            className="resize-none"
            aria-label="聊天输入"
          />
          <Button size="icon" className="h-auto w-12" disabled={busy || !input.trim()} onClick={() => void send()} aria-label="发送">
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
