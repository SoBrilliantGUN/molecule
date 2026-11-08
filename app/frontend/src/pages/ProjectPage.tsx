import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronLeft, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import AppRuntime from '@/components/AppRuntime';
import ChatPanel from '@/components/ChatPanel';
import { forge, type Project } from '@/lib/forge';

/** 生成中的进度视图：流式展示后端 push 的阶段/核验事件，避免用户以为卡住。 */
function GeneratingProgress({ events }: { events: string[] }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-8">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在生成应用…
      </div>
      {events.length > 0 && (
        <ul className="max-h-[320px] w-full max-w-xl space-y-1 overflow-y-auto rounded-xl border border-border bg-sunk/40 p-4">
          {events.map((t, i) => (
            <li key={i} className="flex gap-2 font-mono-ui text-xs text-muted-foreground">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
              <span className="break-all">{t}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground/70">生成完成会自动展示，也可以关掉页面稍后再回来。</p>
    </div>
  );
}

export default function ProjectPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [pane, setPane] = useState<'chat' | 'stage'>('chat');
  const [tick, setTick] = useState(0);
  const [events, setEvents] = useState<string[]>([]);

  const reload = useCallback(() => {
    forge
      .project(projectId)
      .then(setProject)
      .catch((e) => toast.error((e as Error).message));
    setTick((t) => t + 1);
  }, [projectId]);
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  // 生成中（尚无内容的新应用）：流式展示进度，完成后刷新出应用。迭代修改走 ChatPanel 自己的流。
  useEffect(() => {
    if (!project?.generating || !project.run_key || project.html) return;
    setEvents([]);
    const es = forge.streamRun(
      project.run_key,
      (e) => setEvents((prev) => [...prev, e.data.text ?? '']),
      () => reload(),
    );
    return () => es.close();
  }, [project?.run_key, project?.generating, project?.html, reload]);

  if (!project) return <p className="p-8 text-muted-foreground">加载项目…</p>;

  const showProgress = project.generating && !project.html;
  const showError = !project.html && !project.generating && project.status === 'error';

  const retry = () => {
    const message = project.description ?? project.name ?? '';
    if (!message) return;
    forge
      .startRun({ message, project_id: projectId })
      .then(() => reload())
      .catch((e) => toast.error((e as Error).message));
  };

  return (
    <div className="flex h-[calc(100vh-56px)] flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-2">
          <Link to="/" aria-label="返回首页" className="flex h-10 w-10 items-center justify-center hover:bg-sunk"><ChevronLeft className="h-5 w-5" /></Link>
          <h1 className="font-display text-lg font-semibold">{project.name}</h1>
          {project.generating && <span className="flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-600"><Loader2 className="h-3 w-3 animate-spin" />生成中</span>}
        </div>
        <div className="flex border border-border md:hidden">
          {(['chat', 'stage'] as const).map((p) => (
            <button key={p} onClick={() => setPane(p)} className={`min-h-[40px] px-3 text-sm ${pane === p ? 'bg-primary text-primary-foreground' : ''}`}>{p === 'chat' ? '对话' : '应用'}</button>
          ))}
        </div>
      </header>
      <div className="grid min-h-0 flex-1 md:grid-cols-[380px_1fr]">
        <section className={`min-h-0 border-r border-border ${pane === 'chat' ? 'block' : 'hidden'} md:block`}>
          <ChatPanel projectId={projectId} onChanged={reload} />
        </section>
        <section className={`flex min-h-0 flex-col ${pane === 'stage' ? 'flex' : 'hidden'} md:flex`}>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {showProgress ? (
              <GeneratingProgress events={events} />
            ) : showError ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
                <p className="text-sm font-semibold text-destructive">生成失败</p>
                <p className="max-w-md text-xs text-muted-foreground">{project.error ?? '生成过程中出现问题，请重试。'}</p>
                <button onClick={retry} className="inline-flex h-9 items-center justify-center rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
                  重新生成
                </button>
              </div>
            ) : (
              <AppRuntime appRef={projectId} refreshKey={tick} />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
