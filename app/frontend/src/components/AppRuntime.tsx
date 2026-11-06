import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { forge, type Project } from '@/lib/forge';

interface Props {
  appRef: string | number;
  refreshKey?: number;
}

/** Renders the project's single-file HTML app inside a sandboxed iframe. */
export default function AppRuntime({ appRef, refreshKey = 0 }: Props) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    const id = typeof appRef === 'number' ? appRef : NaN;
    (id ? forge.project(id) : Promise.resolve(null))
      .then((p) => {
        if (!alive) return;
        setProject(p);
        setError('');
      })
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [appRef, refreshKey]);

  if (error) return <p className="p-6 text-destructive">{error}</p>;
  if (!project) return <p className="flex items-center gap-2 p-6 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />加载应用…</p>;
  if (!project.html) return <p className="p-6 text-muted-foreground">应用还没有生成内容</p>;

  return (
    <iframe
      title={project.name}
      srcDoc={project.html}
      sandbox="allow-scripts allow-same-origin allow-modals"
      className="h-full min-h-[520px] w-full border-0 bg-white"
    />
  );
}
