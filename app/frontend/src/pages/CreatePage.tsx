import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { forge, type Project, type RaceModelEvent } from '@/lib/forge';

interface Pane {
  model: string;
  status: 'waiting' | 'running' | 'done' | 'error';
  text?: string;
  title?: string;
  html?: string;
}

const STATUS_LABEL: Record<Pane['status'], string> = { waiting: '排队中', running: '生成中', done: '完成', error: '失败' };

export default function CreatePage() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const prompt = params.get('prompt') ?? '';
  const raceMode = params.get('race') === '1';
  const key = params.get('key') ?? undefined;
  const raceIdParam = params.get('raceId');
  const existingRaceId = raceIdParam ? Number(raceIdParam) : null;

  // 赛马的标题用 race 自己的 prompt（续看时 URL 里可能没有 prompt）
  const [racePrompt, setRacePrompt] = useState('');

  // 单模型
  const [events, setEvents] = useState<string[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [modify, setModify] = useState('');

  // 赛马
  const [raceId, setRaceId] = useState<number | null>(null);
  const [panes, setPanes] = useState<Pane[]>([]);
  const [raceDone, setRaceDone] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);

  const runSingle = useCallback((message: string, projectId?: number, clientKey?: string) => {
    setBusy(true);
    setEvents([]);
    forge
      .startRun({ message, project_id: projectId, client_key: clientKey })
      .then(({ run_key }) => {
        forge.streamRun(
          run_key,
          (e) => setEvents((prev) => [...prev, e.data.text ?? '']),
          (data) => {
            setBusy(false);
            if (data.project_id != null) {
              forge.project(data.project_id).then(setProject).catch(() => undefined);
            }
          },
        );
      })
      .catch((e) => {
        toast.error('创建失败', { description: (e as Error).message });
        setBusy(false);
      });
  }, []);

  const applyRaceEvent = useCallback((ev: RaceModelEvent) => {
    setPanes((prev) => {
      const upd = { model: ev.model, status: ev.status, text: ev.text, title: ev.title, html: ev.html } as Pane;
      return prev.some((p) => p.model === ev.model) ? prev.map((p) => (p.model === ev.model ? upd : p)) : [...prev, upd];
    });
  }, []);

  const runRace = useCallback(() => {
    setRacePrompt(prompt);
    forge
      .startRace(prompt, key)
      .then(({ id, models }) => {
        setRaceId(id);
        setPanes(models.map((m) => ({ model: m, status: 'waiting' as const })));
        forge.streamRace(
          id,
          applyRaceEvent,
          () => setRaceDone(true),
        );
      })
      .catch((e) => toast.error((e as Error).message));
  }, [prompt, key, applyRaceEvent]);

  const resumeRace = useCallback((id: number) => {
    setRaceId(id);
    forge
      .race(id)
      .then((r) => {
        setRacePrompt(r.prompt ?? '');
        const items = r.results?.items ?? [];
        setPanes(items.map((it) => ({ model: it.model, status: (it.ok ? 'done' : 'error') as Pane['status'], title: it.title, html: it.html, text: it.error })));
        setRaceDone(r.status === 'done');
        setSelectedIndex(r.selected_index);
        setSelectedProjectId(r.project_id);
        if (r.status !== 'done') {
          forge.streamRace(
            id,
            applyRaceEvent,
            () => setRaceDone(true),
          );
        }
      })
      .catch((e) => toast.error((e as Error).message));
  }, [applyRaceEvent]);

  useEffect(() => {
    if (raceMode) {
      if (existingRaceId != null) resumeRace(existingRaceId);
      else if (!prompt) nav('/');
      else runRace();
      return;
    }
    if (!prompt) {
      nav('/');
      return;
    }
    runSingle(prompt, undefined, key);
  }, [prompt, raceMode, existingRaceId, runSingle, runRace, resumeRace, nav, key]);

  const submitModify = () => {
    const t = modify.trim();
    if (!t || !project || busy) return;
    setModify('');
    runSingle(t, project.id);
  };

  const selectRace = async (index: number) => {
    if (raceId == null || selectedIndex != null) return;
    try {
      const p = await forge.selectRace(raceId, index);
      setSelectedIndex(index);
      setSelectedProjectId(p.id);
      toast.success('已选中该方案，应用已加入「我的项目」');
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-[1280px] px-5 py-8 md:px-8">
      <Link to="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary">
        ← 返回首页
      </Link>
      <div className="mt-4 flex items-baseline justify-between">
        <h1 className="font-display text-2xl font-bold">{raceMode ? '多方案并行生成' : '正在生成'}</h1>
        <span className="text-sm text-muted-foreground">{raceMode ? '赛马模式' : '单模型'}</span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">「{raceMode ? (racePrompt || prompt) : prompt}」</p>

      {raceMode ? (
        <div className="mt-5">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-3.5 py-1.5 text-xs text-primary">
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
            {selectedIndex != null ? '已选中一个方案' : raceDone ? '全部完成 · 选择一个方案' : `${panes.filter((p) => p.status === 'done').length}/${panes.length} 已完成 · 并行生成中…`}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {panes.map((p, i) => (
              <div key={p.model} className="flex min-h-[420px] flex-col rounded-xl border border-border p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-muted-foreground">{p.model}</span>
                  <span className="text-xs text-muted-foreground">{STATUS_LABEL[p.status]}</span>
                </div>
                <div className="mt-3 flex-1">
                  {p.status === 'running' && (
                    <div className="space-y-2">
                      <p className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        {p.text}
                      </p>
                      <div className="h-1.5 overflow-hidden rounded-full bg-sunk">
                        <div className="h-full w-1/2 animate-pulse rounded-full bg-primary" />
                      </div>
                    </div>
                  )}
                  {p.status === 'done' && p.html && (
                    <iframe title={p.title ?? p.model} srcDoc={p.html} sandbox="allow-scripts allow-same-origin allow-modals allow-forms" className="h-[420px] w-full rounded-lg border border-border bg-white" />
                  )}
                  {p.status === 'error' && <p className="text-xs text-destructive">{p.text}</p>}
                </div>
                {p.status === 'done' && raceDone && (
                  selectedIndex === i ? (
                    <Link to={`/project/${selectedProjectId}`} className="mt-3 inline-flex h-9 w-full items-center justify-center gap-1 rounded-lg bg-green-600 text-sm font-semibold text-white hover:bg-green-700">
                      ✓ 已选中 · 打开应用
                    </Link>
                  ) : (
                    <button
                      onClick={() => void selectRace(i)}
                      disabled={selectedIndex != null}
                      className="mt-3 inline-flex h-9 w-full items-center justify-center rounded-lg bg-primary text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:bg-gray-300"
                    >
                      选中此方案
                    </button>
                  )
                )}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="mt-5 grid gap-4 lg:grid-cols-[340px_1fr]">
          <div className="rounded-xl border border-border">
            <div className="border-b border-border px-4 py-2.5 text-xs font-semibold text-muted-foreground">生成进度</div>
            <div className="max-h-[420px] space-y-1.5 overflow-y-auto p-4">
              {events.length === 0 && busy && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> 正在接收…
                </p>
              )}
              {events.map((t, i) => (
                <p key={i} className="flex gap-2 font-mono-ui text-xs text-muted-foreground">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span className="break-all">{t}</span>
                </p>
              ))}
              {!busy && project && (
                <p className="flex items-center gap-2 text-sm text-ok">
                  <span className="h-1.5 w-1.5 rounded-full bg-ok" /> 完成
                </p>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-3">
            {project?.html ? (
              <iframe title={project.name} srcDoc={project.html} sandbox="allow-scripts allow-same-origin allow-modals allow-forms" className="h-[420px] w-full rounded-xl border border-border bg-white" />
            ) : (
              <div className="flex h-[420px] items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted-foreground">
                {busy ? '生成中…' : '等待生成'}
              </div>
            )}
            <div className="flex items-center gap-2 rounded-xl border border-border p-1.5">
              <input
                value={modify}
                onChange={(e) => setModify(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submitModify()}
                placeholder="一句话修改，例如：加一个「负责人」字段"
                className="min-w-0 flex-1 border-0 px-3 py-2 text-sm outline-none"
                disabled={!project || busy}
                aria-label="一句话修改"
              />
              <button
                onClick={submitModify}
                disabled={!project || busy || !modify.trim()}
                className="inline-flex h-9 shrink-0 items-center justify-center rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:bg-gray-300"
              >
                修改
              </button>
            </div>
            {project && (
              <Link to={`/project/${project.id}`} className="text-sm text-primary hover:underline">
                打开项目页 →
              </Link>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
