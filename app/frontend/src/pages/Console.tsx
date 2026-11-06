import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { forge, type BreakerRow, type RunRow, type UsageSummary } from '@/lib/forge';

const STATE_COLOR: Record<string, string> = { closed: 'text-ok', open: 'text-destructive', half_open: 'text-ember' };

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="border-l border-border pl-4">
      <p className="font-mono-ui text-xs uppercase text-muted-foreground">{label}</p>
      <p className="font-display text-3xl font-bold">{value}</p>
    </div>
  );
}

export default function Console() {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [breakers, setBreakers] = useState<BreakerRow[]>([]);
  const [runs, setRuns] = useState<RunRow[]>([]);

  const load = useCallback(() => {
    forge.usage().then(setUsage).catch(() => undefined);
    forge.breakers().then(setBreakers).catch(() => undefined);
    forge.runs().then(setRuns).catch(() => undefined);
  }, []);
  useEffect(() => load(), [load]);

  const resume = async (key: string) => {
    try {
      await forge.resume(key);
      toast.success('已从检查点恢复');
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-[1200px] px-5 py-10 md:px-10">
      <h1 className="font-display text-4xl font-bold">Agent 控制台</h1>
      <p className="mt-2 text-muted-foreground">所有模型调用都经由网关：预检 → 快照复用 → 重试 → 计价 → 落库。</p>
      <div className="mt-8 grid grid-cols-2 gap-6 md:grid-cols-4">
        <Stat label="调用次数" value={usage?.calls ?? '–'} />
        <Stat label="Tokens" value={usage?.tokens ?? '–'} />
        <Stat label="成本 ¥" value={usage?.cost_cny ?? '–'} />
        <Stat label="失败" value={usage?.errors ?? '–'} />
      </div>

      <div className="mt-12 grid gap-10 lg:grid-cols-2">
        <section>
          <h2 className="font-mono-ui text-xs uppercase tracking-widest text-muted-foreground">按模型</h2>
          <ul className="mt-2 divide-y divide-border border-y border-border text-sm">
            {usage?.by_model.map((m) => <li key={m.model} className="flex h-11 items-center justify-between"><span className="font-mono-ui">{m.model}</span><span>{m.calls} 次 · ¥{m.cost_cny}</span></li>)}
            {!usage?.by_model.length && <li className="py-3 text-muted-foreground">暂无调用</li>}
          </ul>
        </section>
        <section>
          <div className="flex items-center justify-between">
            <h2 className="font-mono-ui text-xs uppercase tracking-widest text-muted-foreground">熔断器（持久化）</h2>
            <Button size="sm" variant="outline" className="!bg-transparent" onClick={() => void forge.resetBreakers().then(load)}>全部复位</Button>
          </div>
          <ul className="mt-2 divide-y divide-border border-y border-border text-sm">
            {breakers.map((b) => <li key={b.key} className="flex h-11 items-center justify-between"><span className="font-mono-ui">{b.key}</span><span className={STATE_COLOR[b.state]}>{b.state} · 失败 {b.failures}</span></li>)}
            {!breakers.length && <li className="py-3 text-muted-foreground">所有工具与资源正常（尚无失败记录）</li>}
          </ul>
        </section>
      </div>

      <section className="mt-12">
        <h2 className="font-mono-ui text-xs uppercase tracking-widest text-muted-foreground">最近 Agent 运行</h2>
        <ul className="mt-2 divide-y divide-border border-y border-border text-sm">
          {runs.map((r) => (
            <li key={r.run_key} className="flex min-h-[44px] items-center justify-between gap-3">
              <span className="min-w-0 flex-1 truncate">{r.input || '（无输入）'} <span className="font-mono-ui text-xs text-muted-foreground">· {r.intent ?? '-'}</span></span>
              <span className="font-mono-ui text-xs">{r.status} · {r.usage.tokens ?? 0} tokens</span>
              {(r.status === 'running' || r.status === 'failed') && <Button size="sm" variant="outline" className="!bg-transparent" onClick={() => void resume(r.run_key)}>从检查点恢复</Button>}
            </li>
          ))}
          {!runs.length && <li className="py-3 text-muted-foreground">暂无运行记录</li>}
        </ul>
      </section>

      <section className="mt-12">
        <h2 className="font-mono-ui text-xs uppercase tracking-widest text-muted-foreground">最近模型调用</h2>
        <table className="mt-2 w-full text-sm">
          <tbody>
            {usage?.recent.map((c, i) => (
              <tr key={i} className="h-10 border-b border-border font-mono-ui text-xs">
                <td>{c.purpose}</td><td>{c.model}</td><td>{c.tokens}</td><td>¥{c.cost_cny}</td><td>{c.latency_ms}ms</td><td className={c.status === 'ok' ? 'text-ok' : 'text-destructive'}>{c.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
