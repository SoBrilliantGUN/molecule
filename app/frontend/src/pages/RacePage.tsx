import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { forge, type Race } from '@/lib/forge';

const LABELS: Record<string, string> = { quality: '质量', security: '安全', ux: '体验' };

export default function RacePage() {
  const nav = useNavigate();
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [races, setRaces] = useState<Race[]>([]);

  const load = () => forge.races().then(setRaces).catch(() => undefined);
  useEffect(() => void load(), []);

  const start = async () => {
    setBusy(true);
    try {
      await forge.startRace(prompt);
      await load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const select = async (raceId: number, index: number) => {
    try {
      const p = await forge.selectRace(raceId, index);
      nav(`/project/${p.id}`);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-[1200px] px-5 py-10 md:px-10">
      <h1 className="font-display text-4xl font-bold">多方案并行对比</h1>
      <p className="mt-2 max-w-2xl text-muted-foreground">同一需求交给 3 个模型并行生成应用，按质量 / 安全 / 体验评分，选定一个直接生成项目。</p>
      <div className="mt-6 max-w-2xl space-y-3">
        <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} placeholder="描述要构建的应用…" aria-label="对比需求" />
        <Button disabled={!prompt.trim() || busy} onClick={() => void start()}>
          {busy && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}{busy ? '三个方案生成中…' : '开始对比'}
        </Button>
      </div>

      <div className="mt-12 space-y-12">
        {races.map((race) => (
          <section key={race.id}>
            <h2 className="font-display text-lg font-semibold">「{race.prompt.slice(0, 60)}」</h2>
            <div className="mt-3 grid gap-px border border-border bg-border md:grid-cols-3">
              {race.results.items.map((it, i) => (
                <article key={i} className="flex flex-col bg-card p-4">
                  <div className="flex items-center justify-between">
                    <span className="font-mono-ui text-xs">{it.model}</span>
                    {race.results.recommended === i && it.ok && <span className="bg-ember px-1.5 py-0.5 font-mono-ui text-[10px] text-white">推荐</span>}
                  </div>
                  {it.ok && it.score ? (
                    <>
                      <p className="font-display mt-2 text-3xl font-bold">{it.total}</p>
                      <dl className="mt-1 grid grid-cols-3 text-xs">
                        {Object.entries(it.score).map(([k, v]) => (
                          <div key={k}><dt className="text-muted-foreground">{LABELS[k]}</dt><dd className="font-mono-ui">{v}</dd></div>
                        ))}
                      </dl>
                      {it.issues && it.issues.length > 0 && <p className="mt-2 text-xs text-muted-foreground">意见：{it.issues.join('；')}</p>}
                      <p className="mt-2 font-mono-ui text-[11px] text-muted-foreground">{it.tokens} tokens · ¥{it.cost_cny}</p>
                      <iframe title={`${it.model} 预览`} srcDoc={it.html} sandbox="allow-scripts allow-same-origin" className="mt-3 h-48 w-full border border-border bg-white" />
                      <Button size="sm" className="mt-3" disabled={race.selected_index !== null} onClick={() => void select(race.id, i)}>
                        {race.selected_index === i ? <><Check className="mr-1 h-3.5 w-3.5" />已选</> : '选用此方案'}
                      </Button>
                    </>
                  ) : (
                    <p className="mt-3 text-sm text-destructive">生成失败：{it.error}</p>
                  )}
                </article>
              ))}
            </div>
          </section>
        ))}
        {races.length === 0 && <p className="text-muted-foreground">还没有对比记录。</p>}
      </div>
    </div>
  );
}
