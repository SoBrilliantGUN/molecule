export interface Project {
  id: number;
  name: string;
  description: string | null;
  slug: string;
  html: string | null;
  updated_at?: string;
  generating?: boolean;
  run_key?: string | null;
  status?: string | null;
  error?: string | null;
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
export interface SpecField {
  name: string;
  type: string;
}
export interface SpecQuestion {
  key: string;
  question: string;
  type: 'select' | 'text';
  options: string[];
  default: string;
}
export interface SpecResult {
  needs_clarify: boolean;
  title: string;
  fields: SpecField[];
  view: string;
  confidence: number;
  questions: SpecQuestion[];
}
export interface RaceItem {
  model: string;
  ok: boolean;
  title?: string;
  html?: string;
  error?: string;
}
export interface Race {
  id: number;
  prompt: string;
  status?: string;
  selected_index: number | null;
  project_id: number | null;
  results: { items: RaceItem[] };
}
export interface RaceStart {
  id: number;
  run_key: string;
  prompt: string;
  status: string;
  models: string[];
}
export interface RaceModelEvent {
  model: string;
  status: 'running' | 'done' | 'error';
  text?: string;
  title?: string;
  html?: string;
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
  spec: (prompt: string) => call<SpecResult>('POST', '/spec', { prompt }, 30_000),
  project: (id: number) => call<Project>('GET', `/projects/${id}`),
  createProject: (body: { prompt?: string }) => call<Project>('POST', '/projects', body, LONG),
  deleteProject: (id: number) => call<{ ok: boolean }>('DELETE', `/projects/${id}`),
  messages: (id: number) => call<ChatMessage[]>('GET', `/projects/${id}/messages`),
  chat: (id: number, message: string) => call<ChatResult>('POST', `/projects/${id}/chat`, { message }, LONG),

  startRace: (prompt: string, client_key?: string) => call<RaceStart>('POST', '/races', { prompt, client_key }, LONG),
  races: () => call<Race[]>('GET', '/races'),
  race: (id: number) => call<Race>('GET', `/races/${id}`),
  selectRace: (id: number, model: string) => call<Project>('POST', `/races/${id}/select`, { model }),
  streamRace: (raceId: number, onModel: (e: RaceModelEvent) => void, onDone: () => void) => {
    const es = new EventSource(`/api/v1/forge/races/${raceId}/events`);
    es.addEventListener('race_model', (ev: MessageEvent) => {
      try {
        onModel(JSON.parse(ev.data as string) as RaceModelEvent);
      } catch {
        /* ignore malformed frame */
      }
    });
    es.addEventListener('done', () => {
      onDone();
      es.close();
    });
    es.onerror = () => es.close();
    return es;
  },

  // async run + SSE
  startRun: (body: { message: string; project_id?: number; client_key?: string }) =>
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
