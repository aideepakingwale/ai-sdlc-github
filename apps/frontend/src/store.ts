import { create } from 'zustand';
import type { PlanEvent, StreamEvent, User } from './api/types';

/** Live activity feed entry rendered inside the in-flight assistant bubble. */
export interface ActivityItem {
  id: number;
  kind: 'node' | 'tool' | 'artifact' | 'gate';
  label: string;
  status?: 'start' | 'success' | 'error';
}

/** Run visualizer state: live view of plan → nodes → tools for a run. */
export interface RunViz {
  nodes: string[];
  activeNode: string | null;
  visitedNodes: string[];
  plans: PlanEvent[];
  /** tool name → expected | start | success | error */
  tools: Record<string, 'expected' | 'start' | 'success' | 'error'>;
  tier: string | null;
}

const EMPTY_RUN: RunViz = { nodes: [], activeNode: null, visitedNodes: [], plans: [], tools: {}, tier: null };

interface AppState {
  user: User | null;
  setUser: (u: User | null) => void;

  activeProjectId: string | null;
  setActiveProject: (id: string | null) => void;

  streaming: boolean;
  activity: ActivityItem[];
  liveResponse: string;
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
  run: EMPTY_RUN,
  beginStream: () => set({ streaming: true, activity: [], liveResponse: '', run: EMPTY_RUN }),
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
