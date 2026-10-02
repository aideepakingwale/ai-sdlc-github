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
  return parts.map((p, j) => (j === i ? { ...p, ...patch } : p));
};

const EMPTY_RUN: RunViz = { nodes: [], activeNode: null, visitedNodes: [], plans: [], tools: {}, tier: null };

interface AppState {
  user: User | null;
  setUser: (u: User | null) => void;

  activeProjectId: string | null;
  setActiveProject: (id: string | null) => void;

  streaming: boolean;
  activity: ActivityItem[];
  liveResponse: string;
  // D-112 live streaming: the artifact document being written in real time, plus a
  // flag so the UI shows the live preview while it streams.
  liveParts: LivePart[];
  run: RunViz;
  beginStream: () => void;
  pushEvent: (e: StreamEvent) => void;
  endStream: () => void;
}

let activitySeq = 0;

export const useApp = create<AppState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),

  activeProjectId: null,
  setActiveProject: (activeProjectId) => set({ activeProjectId, activity: [], liveResponse: '' }),

  streaming: false,
  activity: [],
  liveResponse: '',
  liveParts: [],
  run: EMPTY_RUN,
  beginStream: () => set({ streaming: true, activity: [], liveResponse: '', liveParts: [], run: EMPTY_RUN }),
  pushEvent: (e) =>
    set((s) => {
      const add = (item: Omit<ActivityItem, 'id'>) => ({ activity: [...s.activity, { ...item, id: ++activitySeq }] });
      switch (e.type) {
        case 'session':
          return s.activeProjectId ? {} : { activeProjectId: e.projectId };
        case 'node':
          return {
            ...add({ kind: 'node', label: e.label }),
            run: {
              ...s.run,
              activeNode: e.node,
              visitedNodes: s.run.visitedNodes.includes(e.node) ? s.run.visitedNodes : [...s.run.visitedNodes, e.node],
            },
          };
        case 'plan':
          return {
            run: {
              ...s.run,
              nodes: e.nodes,
              plans: [...s.run.plans, e],
              tier: e.tier,
              tools: {
                ...s.run.tools,
                ...Object.fromEntries(e.expectedTools.filter((t) => !(t in s.run.tools)).map((t) => [t, 'expected' as const])),
              },
            },
          };
        case 'model': {
          const icon = e.tier === 'non_llm' ? '⚙️' : e.tier === 'local' ? '🖥️' : '☁️';
          return { ...add({ kind: 'node', label: `${icon} ${e.label}` }), run: { ...s.run, tier: e.tier } };
        }
        case 'tool_call': {
          const run = { ...s.run, tools: { ...s.run.tools, [e.tool]: e.status } };
          return e.status === 'start'
            ? { ...add({ kind: 'tool', label: `Calling ${e.tool}…`, status: 'start' }), run }
            : { ...add({ kind: 'tool', label: `${e.tool} ${e.status === 'success' ? '✓' : '✗'}${e.summary ? ` (${e.summary})` : ''}`, status: e.status }), run };
        }
        case 'artifact':
          return add({ kind: 'artifact', label: `${e.artifact.type}: ${e.artifact.title}` });
        case 'gate':
          return add({ kind: 'gate', label: `Gate ${e.status} — reviewer: ${e.reviewerRole}` });
        case 'token':
          return { liveResponse: s.liveResponse + e.content };
        case 'content_start': {
          const field = e.part ?? 'document';
          return {
            ...add({ kind: 'node', label: `✍ Writing ${e.title ?? 'the document'} live…` }),
            liveParts: upsertPart(s.liveParts, field, { title: e.title ?? field, text: '', status: 'running' }),
          };
        }
        case 'content_delta': {
          const field = e.part ?? 'document';
          const cur = s.liveParts.find((p) => p.field === field)?.text ?? '';
          return { liveParts: upsertPart(s.liveParts, field, { text: cur + e.text, status: 'running' }) };
        }
        case 'content_end':
          return {};
        case 'part':
          // A finished part carries its full text; a running one keeps what streamed so far.
          return {
            liveParts: upsertPart(s.liveParts, e.part, {
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
    }),
  endStream: () => set({ streaming: false }),
}));
