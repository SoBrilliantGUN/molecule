import { useState } from 'react';
import { Download, ExternalLink, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { forge, type Project } from '@/lib/forge';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-border py-5">
      <h3 className="font-mono-ui text-xs uppercase tracking-widest text-muted-foreground">{title}</h3>
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

export default function ShipTab({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const [busy, setBusy] = useState('');
  const publicUrl = `${window.location.origin}/app/${project.slug}`;

  const run = async (tag: string, fn: () => Promise<void>) => {
    setBusy(tag);
    try {
      await fn();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy('');
    }
  };

  const download = () => {
    if (!project.html) return toast.error('应用还没有生成内容');
    const blob = new Blob([project.html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: `${project.slug}.html` });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="px-5 pb-8">
      <Section title="发布">
        <p className="text-sm text-muted-foreground">发布后任何人都可通过公开链接打开这个单文件应用（数据存在浏览器 localStorage）。</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={busy === 'pub'} onClick={() => void run('pub', async () => { await forge.publish(project.id, !project.published); onChanged(); })}>
            {busy === 'pub' && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            {project.published ? '取消发布' : '一键发布'}
          </Button>
          {project.published && (
            <a href={publicUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono-ui text-xs text-ember underline">
              {publicUrl}<ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </Section>

      <Section title="导出">
        <p className="text-sm text-muted-foreground">把生成的应用存成单个 .html 文件，双击浏览器即可打开使用。</p>
        <Button variant="outline" className="!bg-transparent" disabled={!project.html} onClick={download}>
          <Download className="mr-1 h-4 w-4" />下载 .html
        </Button>
      </Section>
    </div>
  );
}
