import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronLeft } from 'lucide-react';
import { toast } from 'sonner';
import AppRuntime from '@/components/AppRuntime';
import ChatPanel from '@/components/ChatPanel';
import ShipTab from '@/components/ShipTab';
import { forge, type Project } from '@/lib/forge';

const TABS = [
  ['preview', '预览'],
  ['ship', '发布与导出'],
] as const;

export default function ProjectPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number][0]>('preview');
  const [pane, setPane] = useState<'chat' | 'stage'>('chat');
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => {
    forge.project(projectId).then(setProject).catch((e) => toast.error((e as Error).message));
    setTick((t) => t + 1);
  }, [projectId]);
  useEffect(reload, [reload]);

  if (!project) return <p className="p-8 text-muted-foreground">加载项目…</p>;

  return (
    <div className="flex h-[calc(100vh-56px)] flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-2">
          <Link to="/" aria-label="返回首页" className="flex h-10 w-10 items-center justify-center hover:bg-sunk"><ChevronLeft className="h-5 w-5" /></Link>
          <h1 className="font-display text-lg font-semibold">{project.name}</h1>
          {project.published && <span className="flex items-center gap-1 font-mono-ui text-xs text-ok"><span className="h-1.5 w-1.5 rounded-full bg-ok" />已发布</span>}
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
          <div className="flex h-11 shrink-0 gap-1 border-b border-border px-3">
            {TABS.map(([key, label]) => (
              <button key={key} onClick={() => setTab(key)} className={`px-3 text-sm ${tab === key ? 'border-b-2 border-primary font-medium' : 'text-muted-foreground hover:text-foreground'}`}>{label}</button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {tab === 'preview' && <AppRuntime appRef={projectId} refreshKey={tick} />}
            {tab === 'ship' && <ShipTab project={project} onChanged={reload} />}
          </div>
        </section>
      </div>
    </div>
  );
}
