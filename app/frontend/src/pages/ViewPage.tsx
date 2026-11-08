import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { downloadHtml, forge, type Project } from '@/lib/forge';

/** 公开分享页：按 slug 全屏渲染生成的应用，无需进入主应用。 */
export default function ViewPage() {
  const { slug } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    if (!slug) return;
    forge
      .pubProject(slug)
      .then(setProject)
      .catch((e) => setError((e as Error).message));
  }, [slug]);
  useEffect(load, [load]);

  const exportHtml = () => {
    if (!project?.html) return;
    downloadHtml(project.html, `${(project.name || '应用').replace(/[\\/:*?"<>|]/g, '')}.html`);
    toast.success('已导出 HTML 文件');
  };

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm font-semibold text-destructive">应用不存在或已被删除</p>
        <p className="text-xs text-muted-foreground">{error}</p>
        <Link to="/" className="text-sm text-primary hover:underline">← 返回 Molecules</Link>
      </div>
    );
  }
  if (!project) {
    return (
      <p className="flex min-h-screen items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />加载应用…
      </p>
    );
  }

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-2 text-sm">
          <Link to="/" className="flex items-center gap-2 text-muted-foreground hover:text-primary">
            <span className="flex h-5 w-5 items-center justify-center rounded-md bg-primary text-[10px] font-bold text-primary-foreground">M</span>
            Molecules
          </Link>
          <span className="text-muted-foreground">/</span>
          <span className="font-display font-semibold">{project.name}</span>
        </div>
        {project.html && (
          <button
            onClick={exportHtml}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-sm font-semibold text-muted-foreground hover:border-primary hover:text-primary"
          >
            <Download className="h-4 w-4" /> 导出 HTML
          </button>
        )}
      </header>
      <div className="min-h-0 flex-1">
        {project.html ? (
          <iframe
            title={project.name}
            srcDoc={project.html}
            sandbox="allow-scripts allow-same-origin allow-modals allow-forms"
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <p className="p-8 text-muted-foreground">应用还没有生成内容</p>
        )}
      </div>
    </div>
  );
}
