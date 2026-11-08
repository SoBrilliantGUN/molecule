export interface Project {
  id: number;
  name: string;
  description: string | null;
  slug: string;
  published: boolean;
  html: string | null;
  updated_at?: string;
}
export interface TraceEvent {
  kind: string;
  text: string;
}
export interface Usage {
  turns: number;
  tokens: number;
  cost_cny: number;
  tool_calls: number;
  seconds: number;
  ratio: number;
}
export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  meta?: { events?: TraceEvent[]; usage?: Usage; intent?: string; run_key?: string };
}
export interface ChatResult {
  run_key: string;
  status: string;
  reply: string;
  intent: string | null;
  events: TraceEvent[];
  usage: Usage;
}
export interface RaceItem {
  model: string;
  ok: boolean;
  title?: string;
  html?: string;
  score?: { quality: number; security: number; ux: number };
  total: number;
  issues?: string[];
  cost_cny: number;
  tokens: number;
  error?: string;
}
export interface Race {
  id: number;
  prompt: string;
  selected_index: number | null;
  project_id: number | null;
  results: { items: RaceItem[]; recommended: number };
}
export interface RunStart {
  run_key: string;
  project_id: number | null;
}
// SSE stage/dag/verify events pushed by /runs/{key}/events
export interface ForgeEvent {
  type: string; // stage | dag | verify | done | error
  data: {
    index?: number;
    total?: number;
    text?: string;
    stage?: string;
    node?: string;
    action?: string;
    score?: number;
    passed?: boolean;
    missing?: string[];
    project_id?: number;
    name?: string;
    message?: string;
  };
}

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
const LONG = 600_000;

export function errorMessage(e: unknown): string {
  const err = e as { message?: string };
  return err?.message || '请求失败';
}

async function call<T>(method: Method, url: string, data?: unknown, timeout = 60_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(`/api/v1/forge${url}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: method === 'GET' || method === 'DELETE' ? undefined : JSON.stringify(data ?? {}),
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = '请求失败';
      try {
        const j = (await res.json()) as { detail?: unknown };
        if (typeof j.detail === 'string') detail = j.detail;
      } catch {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export const forge = {
  projects: () => call<Project[]>('GET', '/projects'),
  project: (id: number) => call<Project>('GET', `/projects/${id}`),
  createProject: (body: { prompt?: string }) => call<Project>('POST', '/projects', body, LONG),
  deleteProject: (id: number) => call<{ ok: boolean }>('DELETE', `/projects/${id}`),
  messages: (id: number) => call<ChatMessage[]>('GET', `/projects/${id}/messages`),
  chat: (id: number, message: string) => call<ChatResult>('POST', `/projects/${id}/chat`, { message }, LONG),
  publish: (id: number, published: boolean) => call<Project>('POST', `/projects/${id}/publish?published=${published}`),

  startRace: (prompt: string) => call<Race>('POST', '/races', { prompt }, LONG),
  races: () => call<Race[]>('GET', '/races'),
  selectRace: (id: number, index: number) => call<Project>('POST', `/races/${id}/select/${index}`),

  // async run + SSE
  startRun: (body: { message: string; project_id?: number }) =>
    call<RunStart>('POST', '/runs', body, 30_000),
  streamRun: (runKey: string, onEvent: (e: ForgeEvent) => void, onDone: (data: ForgeEvent['data']) => void) => {
    const es = new EventSource(`/api/v1/forge/runs/${runKey}/events`);
    const types = ['stage', 'dag', 'verify', 'error'] as const;
    types.forEach((t) => {
      es.addEventListener(t, (ev: MessageEvent) => {
        try {
          const data = JSON.parse(ev.data as string) as ForgeEvent['data'];
          onEvent({ type: t, data });
        } catch {
          /* ignore malformed frame */
        }
      });
    });
    es.addEventListener('done', (ev: MessageEvent) => {
      try {
        onDone(JSON.parse(ev.data as string) as ForgeEvent['data']);
      } catch {
        onDone({});
      }
      es.close();
    });
    es.onerror = () => {
      // EventSource closes itself on stream end; don't spam on transient net blips
      es.close();
    };
    return es;
  },
};
