import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, Loader2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { forge, type Project, type Race, type SpecResult } from '@/lib/forge';

const EXAMPLES = [
  { name: '读书笔记', prompt: '做一个读书笔记管理，记录书名、作者、阅读状态和评分' },
  { name: '健身打卡', prompt: '做一个健身打卡应用，记录日期、动作、组数、重量' },
  { name: '招聘跟进', prompt: '做一个招聘跟进表，包含候选人、岗位、面试阶段' },
  { name: '项目看板', prompt: '做一个带优先级、截止日、拖拽排序的项目看板' },
];
const VIEW_OPTIONS = ['', '列表', '表格', '看板', '卡片'];
const THEME_OPTIONS = ['', '简洁克制', '活泼明快', '商务正式', '极客冷峻'];
// 澄清阻塞时长：面板出现后 60 秒内未操作，视为「未选择」，按默认理解自动生成。
const CLARIFY_SECONDS = 60;

// 幂等键：每次点击「生成应用」生成一次，随 URL 带到生成页；刷新/重进时复用，避免重复生成。
const genKey = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

export default function Home() {
  const nav = useNavigate();
  const [prompt, setPrompt] = useState('');
  const [race, setRace] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [races, setRaces] = useState<Race[]>([]);

  // 生成前理解 + 澄清（阻塞式：60 秒内未操作，自动按默认理解生成）
  const [specBusy, setSpecBusy] = useState(false);
  const [spec, setSpec] = useState<SpecResult | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // 底部「其他补充」全局输入框（可选，留空则不追加）。
  const [note, setNote] = useState('');
  // 澄清倒计时（秒）与「是否已因超时自动生成」防重入标记。
  const [countdown, setCountdown] = useState(CLARIFY_SECONDS);
  const timedOutRef = useRef(false);

  // 可选结构化规格（方案 B：折叠的「更多选项」）
  const [advOpen, setAdvOpen] = useState(false);
  const [adv, setAdv] = useState({ name: '', view: '', theme: '' });

  const reload = () => {
    forge.projects().then(setProjects).catch(() => undefined);
    forge.races().then(setRaces).catch(() => undefined);
  };
  useEffect(reload, []);

  const buildFinalPrompt = (withAnswers: boolean) => {
    const extra: string[] = [];
    if (adv.name.trim()) extra.push(`应用名「${adv.name.trim()}」`);
    if (adv.view) extra.push(`主视图：${adv.view}`);
    if (adv.theme) extra.push(`风格：${adv.theme}`);
    if (withAnswers && spec) {
      for (const q of spec.questions) {
        // 下拉选「不指定」或输入留空 → 该追问跳过，不写进 prompt。
        const v = (answers[q.key] ?? '').trim();
        if (v) extra.push(`${q.question.replace(/[?？]\s*$/, '')}：${v}`);
      }
      if (note.trim()) extra.push(`补充：${note.trim()}`);
    }
    return extra.length ? `${prompt.trim()}\n【补充要求】\n${extra.map((e) => `- ${e}`).join('\n')}` : prompt.trim();
  };

  const goGenerate = (text: string) => {
    nav(`/create?prompt=${encodeURIComponent(text)}&race=${race ? '1' : '0'}&key=${genKey()}`);
  };

  const finishClarify = (withAnswers: boolean) => {
    setSpec(null);
    goGenerate(buildFinalPrompt(withAnswers));
  };

  // 澄清面板出现即进入阻塞：重置 60 秒倒计时。
  useEffect(() => {
    if (!spec?.needs_clarify) return;
    setCountdown(CLARIFY_SECONDS);
    timedOutRef.current = false;
  }, [spec?.needs_clarify]);

  // 每秒递减；归零视为「用户未选择」，按默认理解自动生成（等价于「跳过」）。
  useEffect(() => {
    if (!spec?.needs_clarify || countdown <= 0) return;
    const id = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(id);
  }, [spec?.needs_clarify, countdown]);

  useEffect(() => {
    if (spec?.needs_clarify && countdown === 0 && !timedOutRef.current) {
      timedOutRef.current = true;
      finishClarify(false);
    }
  }, [spec?.needs_clarify, countdown]);

  const create = async () => {
    const text = prompt.trim();
    if (!text || specBusy) return;
    setSpecBusy(true);
    setSpec(null);
    try {
      const res = await forge.spec(text);
      if (res.needs_clarify && res.questions.length) {
        const init: Record<string, string> = {};
        res.questions.forEach((q) => {
          init[q.key] = q.default;
        });
        setAnswers(init);
        setNote('');
        setSpec(res);
        return;
      }
      goGenerate(buildFinalPrompt(false));
    } catch {
      goGenerate(buildFinalPrompt(false)); // spec 失败也 fail-open，不阻塞生成
    } finally {
      setSpecBusy(false);
    }
  };

  const remove = async (e: MouseEvent<HTMLButtonElement>, id: number) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await forge.deleteProject(id);
      reload();
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const removeRace = async (e: MouseEvent<HTMLButtonElement>, id: number) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await forge.deleteRace(id);
      reload();
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const openExample = (ex: { name: string; prompt: string }) => {
    // 示例点击只把提示词填进输入框，由用户点「生成应用」再生成（不直接打开应用）。
    setPrompt(ex.prompt);
  };

  return (
    <div className="mx-auto max-w-[1080px] px-5 py-10 md:px-8">
      <section>
        <h1 className="font-display text-4xl font-black leading-tight md:text-[42px]">一句话，生成能用的应用。</h1>
        <p className="mt-4 max-w-[600px] text-muted-foreground">描述你要的应用，自动生成数据模型和可运行的网页应用。实时预览，继续对话即可修改，满意后导出使用。</p>

        <div className="mt-7 max-w-[720px] rounded-xl border border-border p-1.5 shadow-sm">
          <Textarea
            value={prompt}
            onChange={(e) => {
              setPrompt(e.target.value);
              if (spec) setSpec(null);
            }}
            rows={4}
            placeholder="例如：做一个客户管理应用，记录客户姓名、公司、跟进阶段和下次联系日期"
            className="resize-none border-0 text-base shadow-none focus-visible:ring-0"
            aria-label="应用描述"
          />
          <div className="flex items-center justify-between gap-3 border-t border-border px-2.5 py-2.5">
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((ex) => (
                <button key={ex.name} onClick={() => openExample(ex)} className="max-w-[180px] truncate rounded-full border border-border bg-sunk px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-primary">
                  {ex.name}
                </button>
              ))}
            </div>
            <button
              disabled={!prompt.trim() || specBusy}
              onClick={create}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {specBusy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> 理解需求…
                </>
              ) : race ? (
                '并行生成'
              ) : (
                '生成应用'
              )}
            </button>
          </div>
        </div>

        {spec?.needs_clarify && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
            <div className="w-full max-w-[560px] overflow-hidden rounded-2xl border border-border bg-white shadow-xl">
              {/* 顶部倒计时进度条：剩余时间越少越短 */}
              <div className="h-1 w-full bg-sunk">
                <div
                  className="h-full bg-primary transition-all duration-1000 ease-linear"
                  style={{ width: `${(countdown / CLARIFY_SECONDS) * 100}%` }}
                />
              </div>

              <div className="p-6">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="font-display text-lg font-bold">生成前确认一下</h2>
                    <p className="mt-1 text-sm text-muted-foreground">我理解你要做的是「{spec.title || '一个应用'}」——细节已按最可能的答案预填，直接确认即可。</p>
                  </div>
                  <span className="shrink-0 rounded-full border border-border bg-sunk px-2.5 py-0.5 text-xs text-muted-foreground">已理解 {Math.round((spec.confidence || 0) * 100)}%</span>
                </div>

                {(spec.fields.length > 0 || spec.view) && (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {spec.fields.map((f) => (
                      <span key={f.name} className="rounded-md border border-border bg-sunk px-2 py-0.5 text-xs text-foreground">
                        {f.name}
                      </span>
                    ))}
                    {spec.view && <span className="rounded-md bg-sunk px-2 py-0.5 text-xs text-muted-foreground">{spec.view}视图</span>}
                  </div>
                )}

                <div className="mt-4 grid gap-3">
                  {spec.questions.map((q) => (
                    <div key={q.key}>
                      <label className="mb-1.5 block text-xs font-semibold text-muted-foreground">{q.question}</label>
                      {q.type === 'select' && q.options.length ? (
                        <select
                          value={answers[q.key] ?? q.default}
                          onChange={(e) => setAnswers((prev) => ({ ...prev, [q.key]: e.target.value }))}
                          className="h-9 w-full rounded-lg border border-border bg-white px-2.5 text-sm outline-none focus:border-primary"
                        >
                          <option value="">不指定</option>
                          {q.options.map((o) => (
                            <option key={o} value={o}>
                              {o}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          value={answers[q.key] ?? q.default}
                          onChange={(e) => setAnswers((prev) => ({ ...prev, [q.key]: e.target.value }))}
                          className="h-9 w-full rounded-lg border border-border bg-white px-2.5 text-sm outline-none focus:border-primary"
                        />
                      )}
                    </div>
                  ))}
                </div>

                <div className="mt-3">
                  <label className="mb-1.5 block text-xs font-semibold text-muted-foreground">其他补充（可选）</label>
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    placeholder="还有什么要补充的，直接写在这里"
                    className="w-full resize-none rounded-lg border border-border bg-white px-2.5 py-2 text-sm outline-none focus:border-primary"
                  />
                </div>

                <div className="mt-6 flex items-center justify-between gap-3">
                  <span className="flex items-baseline gap-1 text-sm text-muted-foreground">
                    <span className="font-mono-ui text-base font-semibold tabular-nums text-foreground">{countdown}</span>
                    秒后自动生成
                  </span>
                  <div className="flex items-center gap-3">
                    <button onClick={() => finishClarify(false)} className="text-sm text-muted-foreground hover:text-primary">
                      跳过
                    </button>
                    <button onClick={() => finishClarify(true)} className="inline-flex h-9 items-center justify-center rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
                      确认生成
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="mt-4 max-w-[720px]">
          <button onClick={() => setAdvOpen((v) => !v)} className="inline-flex items-center gap-1.5 text-sm font-semibold text-muted-foreground hover:text-primary">
            <ChevronDown className={`h-4 w-4 transition-transform ${advOpen ? 'rotate-180' : ''}`} />
            更多选项
            <span className="font-normal text-muted-foreground/70">名称 · 视图 · 风格（可选）</span>
          </button>
          {advOpen && (
            <div className="mt-2.5 grid gap-3 rounded-xl border border-border bg-sunk/40 p-4 sm:grid-cols-3">
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-muted-foreground">应用名称</label>
                <input value={adv.name} onChange={(e) => setAdv((v) => ({ ...v, name: e.target.value }))} placeholder="留空自动起名" className="h-9 w-full rounded-lg border border-border bg-white px-2.5 text-sm outline-none focus:border-primary" />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-muted-foreground">主视图</label>
                <select value={adv.view} onChange={(e) => setAdv((v) => ({ ...v, view: e.target.value }))} className="h-9 w-full rounded-lg border border-border bg-white px-2.5 text-sm outline-none focus:border-primary">
                  {VIEW_OPTIONS.map((o) => (
                    <option key={o} value={o}>
                      {o || '自动判断'}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-muted-foreground">视觉风格</label>
                <select value={adv.theme} onChange={(e) => setAdv((v) => ({ ...v, theme: e.target.value }))} className="h-9 w-full rounded-lg border border-border bg-white px-2.5 text-sm outline-none focus:border-primary">
                  {THEME_OPTIONS.map((o) => (
                    <option key={o} value={o}>
                      {o || '贴合主题'}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
        </div>

        <div className="mt-4 flex max-w-[720px] items-center gap-3">
          <Switch id="race" checked={race} onCheckedChange={setRace} />
          <label htmlFor="race" className="text-sm font-semibold">
            赛马模式
            <span className="block text-xs font-normal text-muted-foreground">多个模型并行生成，你挑一个最喜欢的</span>
          </label>
        </div>
      </section>

      <section className="mt-14">
        <div className="flex items-baseline justify-between">
          <h2 className="font-display text-2xl font-bold">我的项目</h2>
          <span className="text-sm text-muted-foreground">{projects.length} 个</span>
        </div>
        {projects.length === 0 ? (
          <p className="mt-4 text-muted-foreground">还没有项目。描述一个想法，开始构建。</p>
        ) : (
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <a
                key={p.id}
                href={`/project/${p.id}`}
                onClick={(e) => {
                  e.preventDefault();
                  nav(`/project/${p.id}`);
                }}
                className="group rounded-xl border border-border p-4 transition-colors hover:border-primary"
              >
                <div className="mb-4 flex h-24 items-center justify-center rounded-lg border border-border bg-sunk px-3 text-center text-xs text-muted-foreground">{p.generating ? '正在生成…' : p.name}</div>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="line-clamp-2 font-semibold" title={p.name}>{p.name}</div>
                    <div className="mt-1 text-xs text-muted-foreground">更新于 {p.updated_at?.slice(0, 16) ?? '—'}</div>
                  </div>
                  <button
                    onClick={(e) => void remove(e, p.id)}
                    aria-label={`删除 ${p.name}`}
                    className="rounded p-1.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
                <div className="mt-3">
                  <span className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs ${p.generating ? 'border-amber-200 bg-amber-50 text-amber-600' : 'border-border text-muted-foreground'}`}>
                    {p.generating ? '生成中…' : '草稿'}
                  </span>
                </div>
              </a>
            ))}
          </div>
        )}
      </section>

      {races.length > 0 && (
        <section className="mt-10">
          <div className="flex items-baseline justify-between">
            <h2 className="font-display text-2xl font-bold">赛马</h2>
            <span className="text-sm text-muted-foreground">{races.length} 次</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {races.map((r) => {
              const done = (r.results?.items ?? []).filter((it) => it.ok).length;
              return (
                <a
                  key={r.id}
                  href={`/create?race=1&raceId=${r.id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    nav(`/create?race=1&raceId=${r.id}`);
                  }}
                  className="group rounded-xl border border-border p-4 transition-colors hover:border-primary"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="line-clamp-2 font-semibold" title={r.prompt}>{r.prompt}</div>
                    <button
                      onClick={(e) => void removeRace(e, r.id)}
                      aria-label={`删除赛马 ${r.prompt}`}
                      className="rounded p-1.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">
                      {r.status === 'running' ? '生成中…' : `${done} 个方案可选`}
                    </span>
                    <span className="text-sm text-primary group-hover:underline">
                      {r.status === 'running' ? '查看进度' : '选择方案'} →
                    </span>
                  </div>
                </a>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
