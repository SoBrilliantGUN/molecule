import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Loader2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { forge, type Project } from '@/lib/forge';

const EXAMPLES = ['做一个读书笔记管理，记录书名、作者、阅读状态和评分', '做一个健身打卡应用，记录日期、动作、组数、重量', '做一个招聘跟进表，包含候选人、岗位、面试阶段'];

export default function Home() {
  const nav = useNavigate();
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);

  const reload = () => {
    forge.projects().then(setProjects).catch(() => undefined);
  };
  useEffect(reload, []);

  const create = async () => {
    setBusy(true);
    try {
      const p = await forge.createProject({ prompt });
      nav(`/project/${p.id}`);
    } catch (e) {
      toast.error('创建失败', { description: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    try {
      await forge.deleteProject(id);
      reload();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-[1200px] px-5 py-10 md:px-10">
      <div className="grid gap-12 lg:grid-cols-[7fr_3fr]">
        <section>
          <p className="font-mono-ui text-xs uppercase tracking-widest text-ember">Atoms Forge</p>
          <h1 className="font-display mt-3 text-[40px] font-bold leading-[1.05] md:text-[56px]">一句话，锻造一个<br />能用的全栈应用。</h1>
          <p className="mt-4 max-w-xl text-muted-foreground">描述你要的应用，Agent 提取实体与字段，实时预览；继续聊天即可增量修改，满意后一键发布或导出代码。</p>
          <div className="mt-8 border border-border bg-card p-3">
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="例如：做一个客户管理应用，记录客户姓名、公司、跟进阶段和下次联系日期"
              className="resize-none border-0 text-base shadow-none focus-visible:ring-0"
              aria-label="应用描述"
            />
            <div className="flex items-center justify-between border-t border-border pt-3">
              <div className="flex flex-wrap gap-2">
                {EXAMPLES.map((ex) => (
                  <button key={ex} onClick={() => setPrompt(ex)} className="max-w-[180px] truncate bg-sunk px-2 py-1 text-xs text-muted-foreground hover:text-foreground">
                    {ex}
                  </button>
                ))}
              </div>
              <Button disabled={!prompt.trim() || busy} onClick={() => void create()}>
                {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                {busy ? '生成中…' : '开始构建'}
                {!busy && <ArrowRight className="ml-1 h-4 w-4" />}
              </Button>
            </div>
          </div>
        </section>

        <aside>
          <h2 className="font-mono-ui text-xs uppercase tracking-widest text-muted-foreground">怎么开始</h2>
          <p className="mt-3 text-sm text-muted-foreground">在左侧输入一句话描述你想要的业务应用（如任务、客户、库存），Agent 会自动提取数据模型并生成可运行的应用。</p>
        </aside>
      </div>

      <section className="mt-16">
        <h2 className="font-display text-2xl font-semibold">我的项目</h2>
        {projects.length === 0 ? (
          <p className="mt-3 text-muted-foreground">还没有项目。描述一个想法，开始构建。</p>
        ) : (
          <ul className="mt-3 divide-y divide-border border-y border-border">
            {projects.map((p) => (
              <li key={p.id} className="flex min-h-[56px] items-center justify-between gap-3">
                <button className="flex-1 py-3 text-left" onClick={() => nav(`/project/${p.id}`)}>
                  <span className="font-display font-semibold">{p.name}</span>
                  <span className="ml-3 font-mono-ui text-xs text-muted-foreground">{p.published ? '已发布' : '草稿'}</span>
                </button>
                <Button size="icon" variant="ghost" aria-label={`删除 ${p.name}`} onClick={() => void remove(p.id)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
