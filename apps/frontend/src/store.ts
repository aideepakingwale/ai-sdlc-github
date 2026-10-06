import { create } from 'zustand';
import type { PlanEvent, StreamEvent, User } from './api/types';

/** Live activity feed entry rendered inside the in-flight assistant bubble. */
export interface ActivityItem {
  id: number;
  kind: 'node' | 'tool' | 'artifact' | 'gate';
  label: string;
  status?: 'start' | 'success' | 'error';
}

/** Run visualizer state (D-31): live view of plan → nodes → tools for a run. */
export interface RunViz {
  nodes: string[];
  activeNode: string | null;
  visitedNodes: string[];
  plans: PlanEvent[];
  /** tool name → expected | start | success | error */
  tools: Record<string, 'expected' | 'start' | 'success' | 'error'>;
  tier: string | null;
}

/** One generated file shown as a tab while a stage generates (and after reconnecting). */
export interface LivePart {
  field: string;
  title: string;
  text: string;
  status: 'running' | 'done' | 'failed';
  error?: string | null;
}

const upsertPart = (parts: LivePart[], field: string, patch: Partial<LivePart>): LivePart[] => {
  const i = parts.findIndex((p) => p.field === field);
  if (i < 0) return [...parts, { field, title: field, text: '', status: 'running', ...patch }];
  const next = { ...parts[i]!, ...patch };
  const rest = parts.filter((_, j) => j !== i);
  // A part that just finished goes to the END, so the finished group reads in completion order
  // (newest last) and the tab bar can keep what is still being written up front.
  return patch.status === 'done' && parts[i]!.status !== 'done' ? [...rest, next] : parts.map((p, j) => (j === i ? next : p));
};

const EMPTY_RUN: RunViz = { nodes: [], activeNode: null, visitedNodes: [], plans: [], tools: {}, tier: null };

/** The state of ONE generation run (a stage generating, or a chat turn). Runs are independent: starting one in a
 *  project never shows up in another project or another stage. */
export interface StreamState {
  active: boolean;
  /** Order in which runs began (newest = highest); lets a panel show "the latest run of this project". */
  seq: number;
  activity: ActivityItem[];
  liveResponse: string;
  /** The files being written, one tab each. */
  liveParts: LivePart[];
  run: RunViz;
}

/** Key of a run: the project and the stage (or 'chat' for a pipeline chat turn). */
export const streamKey = (projectId: string | null | undefined, phase: number | 'chat' | null = null): string =>
  `${projectId ?? 'new'}:${phase ?? '*'}`;

export const IDLE_STREAM: StreamState = { active: false, seq: 0, activity: [], liveResponse: '', liveParts: [], run: EMPTY_RUN };

interface AppState {
  user: User | null;
  setUser: (u: User | null) => void;

  activeProjectId: string | null;
  setActiveProject: (id: string | null) => void;

  streams: Record<string, StreamState>;
  beginStream: (key: string) => void;
  pushEvent: (key: string, e: StreamEvent) => void;
  endStream: (key: string) => void;
}

let activitySeq = 0;
let runSeq = 0;
const KEEP_FINISHED = 8;

/** Fold one stream event into a run's state. */
function reduce(cur: StreamState, e: StreamEvent): Partial<StreamState> {
  const add = (item: Omit<ActivityItem, 'id'>) => ({ activity: [...cur.activity, { ...item, id: ++activitySeq }] });
      switch (e.type) {
        case 'session':
          return {};
        case 'node':
          return {
            ...add({ kind: 'node', label: e.label }),
            run: {
              ...cur.run,
              activeNode: e.node,
              visitedNodes: cur.run.visitedNodes.includes(e.node) ? cur.run.visitedNodes : [...cur.run.visitedNodes, e.node],
            },
          };
        case 'plan':
          return {
            run: {
              ...cur.run,
              nodes: e.nodes,
              plans: [...cur.run.plans, e],
              tier: e.tier,
              tools: {
                ...cur.run.tools,
                ...Object.fromEntries(e.expectedTools.filter((t) => !(t in cur.run.tools)).map((t) => [t, 'expected' as const])),
              },
            },
          };
        case 'model': {
          const icon = e.tier === 'non_llm' ? '⚙️' : e.tier === 'local' ? '🖥️' : '☁️';
          return { ...add({ kind: 'node', label: `${icon} ${e.label}` }), run: { ...cur.run, tier: e.tier } };
        }
        case 'tool_call': {
          const run = { ...cur.run, tools: { ...cur.run.tools, [e.tool]: e.status } };
          return e.status === 'start'
            ? { ...add({ kind: 'tool', label: `Calling ${e.tool}…`, status: 'start' }), run }
            : { ...add({ kind: 'tool', label: `${e.tool} ${e.status === 'success' ? '✓' : '✗'}${e.summary ? ` (${e.summary})` : ''}`, status: e.status }), run };
        }
        case 'artifact':
          return add({ kind: 'artifact', label: `${e.artifact.type}: ${e.artifact.title}` });
        case 'gate':
          return add({ kind: 'gate', label: `Gate ${e.status} — reviewer: ${e.reviewerRole}` });
        case 'token':
          return { liveResponse: cur.liveResponse + e.content };
        case 'content_start': {
          const field = e.part ?? 'document';
          return {
            ...add({ kind: 'node', label: `✍ Writing ${e.title ?? 'the document'} live…` }),
            liveParts: upsertPart(cur.liveParts, field, { title: e.title ?? field, text: '', status: 'running' }),
          };
        }
        case 'content_delta': {
          const field = e.part ?? 'document';
          const soFar = cur.liveParts.find((p) => p.field === field)?.text ?? '';
          return { liveParts: upsertPart(cur.liveParts, field, { text: soFar + e.text, status: 'running' }) };
        }
        case 'content_end':
          return {};
        case 'part':
          // A finished part carries its full text; a running one keeps what streamed so far.
          return {
            liveParts: upsertPart(cur.liveParts, e.part, {
              status: e.status, error: e.error,
              ...(e.status === 'done' && e.text ? { text: e.text } : {}),
            }),
          };
        case 'done':
          return { liveResponse: e.finalResponse };
        case 'error':
          return add({ kind: 'node', label: `Error: ${e.message}` });
        default:
          return {};
      }
}

export const useApp = create<AppState>((set) => ({
  user: null,
  // Signing out drops every live run too: the next person in this tab must not see the previous one's output.
  setUser: (user) => set(user ? { user } : { user, streams: {}, activeProjectId: null }),

  activeProjectId: null,
  setActiveProject: (activeProjectId) => set({ activeProjectId }),

  streams: {},
  beginStream: (key) =>
    set((s) => {
      // Finished runs are only kept for a short while: this is a viewer cache, the server owns the run.
      const finished = Object.entries(s.streams).filter(([k, v]) => !v.active && k !== key).sort((a, b) => b[1].seq - a[1].seq);
      const dropped = new Set(finished.slice(KEEP_FINISHED).map(([k]) => k));
      const kept = Object.fromEntries(Object.entries(s.streams).filter(([k]) => !dropped.has(k)));
      return { streams: { ...kept, [key]: { ...IDLE_STREAM, active: true, seq: ++runSeq } } };
    }),
  pushEvent: (key, e) =>
    set((s) => {
      const cur = s.streams[key] ?? { ...IDLE_STREAM, seq: ++runSeq };
      const next = { streams: { ...s.streams, [key]: { ...cur, ...reduce(cur, e) } } };
      // The first chat turn of a new project learns its id from the stream.
      return e.type === 'session' && !s.activeProjectId ? { ...next, activeProjectId: e.projectId } : next;
    }),
  endStream: (key) =>
    set((s) => (s.streams[key] ? { streams: { ...s.streams, [key]: { ...s.streams[key]!, active: false } } } : {})),
}));

/** One run's state (idle when there is none). */
export const useStream = (key: string): StreamState => useApp((s) => s.streams[key] ?? IDLE_STREAM);

/** The most recent run of a project (a running one wins), for panels that follow "what is happening here". */
export const useProjectStream = (projectId: string): StreamState =>
  useApp((s) => {
    const mine = Object.entries(s.streams).filter(([k]) => k.startsWith(`${projectId}:`)).map(([, v]) => v);
    return mine.find((v) => v.active) ?? mine.sort((a, b) => b.seq - a.seq)[0] ?? IDLE_STREAM;
  });
