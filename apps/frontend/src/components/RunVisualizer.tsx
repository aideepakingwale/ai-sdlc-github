import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { PlanPreview } from '../api/types';
import { useApp } from '../store';

/**
 * Run Visualizer: shows WHAT the agent workflow will do and what it is
 * doing — the LangGraph node path, the planner's plan steps, the model tier
 * and the MCP tools/skills involved. Before a run it renders the server's
 * plan-preview; during a run it lights up live from SSE events.
 */

const TIER_BADGE: Record<string, string> = { non_llm: '⚙️ non-LLM', local: '🖥️ local', frontier: '☁️ frontier' };

const NODE_LABELS: Record<string, string> = {
  guardrail: 'Guardrail',
  planner: 'Planner',
  executor: 'Executor',
  agent: 'Executor',
  compressor: 'Executor',
  validator: 'Validator',
  synthesizer: 'Synthesizer',
  formatter: 'Formatter',
  fact_check: 'Fact-check',
};

function NodePath({ nodes, active, visited }: { nodes: string[]; active: string | null; visited: string[] }) {
  const activeCanon = active ? (NODE_LABELS[active] ?? active) : null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {nodes.map((n, i) => {
        const canon = NODE_LABELS[n] ?? n;
        const isActive = activeCanon === canon;
        const isVisited = visited.some((v) => (NODE_LABELS[v] ?? v) === canon);
        return (
          <span key={n} className="flex items-center gap-1">
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                isActive
                  ? 'animate-pulse bg-blue-600 text-white'
                  : isVisited
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-slate-100 text-slate-400'
              }`}
            >
              {canon}
            </span>
            {i < nodes.length - 1 && <span className="text-[10px] text-slate-300">→</span>}
          </span>
        );
      })}
    </div>
  );
}

function ToolLight({ name, state }: { name: string; state: 'expected' | 'start' | 'success' | 'error' }) {
  const dot =
    state === 'success' ? 'bg-emerald-500' : state === 'error' ? 'bg-red-500' : state === 'start' ? 'bg-blue-500 animate-pulse' : 'bg-slate-300';
  return (
    <span className="flex items-center gap-1 rounded bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-600" title={state}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {name}
    </span>
  );
}

export default function RunVisualizer({ projectId }: { projectId: string }) {
  const { streaming, run } = useApp();
  const [open, setOpen] = useState(false);
  const expanded = open || streaming;

  // Pre-run blueprint: what the NEXT chat turn would execute.
  const preview = useQuery({
    queryKey: ['plan-preview', projectId],
    queryFn: () => api.get<PlanPreview>(`/api/projects/${projectId}/plan-preview`),
    enabled: expanded && !streaming,
    refetchInterval: 10_000,
  });

  const live = streaming || run.plans.length > 0;

  return (
    <div className="bg-white px-4 py-2">
      {/* Title lives in the Panel chrome; this row keeps the live/tier
          badges and the detail toggle. */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 text-xs text-slate-500"
        title="Plan, execution path, tools and skills for the agent workflow run"
      >
        <span className="font-semibold uppercase tracking-wide">Plan &amp; execution</span>
        {streaming && <span className="animate-pulse rounded bg-blue-100 px-1.5 text-[10px] font-semibold text-blue-700">live</span>}
        {run.tier && <span className="rounded bg-slate-100 px-1.5 text-[10px]">{TIER_BADGE[run.tier] ?? run.tier}</span>}
        <span className="ml-auto">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-2">
          {/* LangGraph node path */}
          {(run.nodes.length > 0 || preview.data) && (
            <NodePath
              nodes={run.nodes.length > 0 ? run.nodes : (preview.data?.nodes ?? [])}
              active={streaming ? run.activeNode : null}
              visited={run.visitedNodes}
            />
          )}

          {/* Live plans (from the plan SSE event), else the pre-run preview */}
          {live ? (
            <div className="flex gap-2 overflow-x-auto">
              {run.plans.map((p) => (
                <div key={p.stage} className="w-64 shrink-0 rounded-lg border border-blue-200 bg-blue-50/50 p-2">
                  <div className="flex items-center justify-between text-[11px] font-bold text-slate-700">
                    <span>
                      {p.stage}. {p.stageName}
                    </span>
                    <span className="rounded bg-white px-1 text-[9px] font-semibold text-slate-500">{TIER_BADGE[p.tier]}</span>
                  </div>
                  <div className="text-[10px] text-slate-500">{p.persona}</div>
                  <ol className="mt-1 space-y-0.5">
                    {p.steps.map((st) => (
                      <li key={st.id} className="truncate text-[10px] text-slate-600" title={st.description}>
                        • {st.description}
                      </li>
                    ))}
                  </ol>
                  {p.expectedTools.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {p.expectedTools.map((t) => (
                        <ToolLight key={t} name={t} state={run.tools[t] ?? 'expected'} />
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {run.plans.length === 0 && streaming && (
                <div className="text-[11px] text-slate-400">Planning…</div>
              )}
            </div>
          ) : preview.data ? (
            preview.data.stages.length > 0 ? (
              <div className="flex gap-2 overflow-x-auto">
                {preview.data.parallel && (
                  <div className="flex items-center text-[10px] font-semibold text-indigo-500">∥</div>
                )}
                {preview.data.stages.map((st) => (
                  <div key={st.seq} className="w-64 shrink-0 rounded-lg border border-slate-200 p-2">
                    <div className="flex items-center justify-between text-[11px] font-bold text-slate-700">
                      <span>
                        {st.seq}. {st.name}
                      </span>
                      <span className="rounded bg-slate-100 px-1 text-[9px] font-semibold text-slate-500">{TIER_BADGE[st.tier]}</span>
                    </div>
                    <div className="text-[10px] text-slate-500">
                      {st.persona} · gate: {st.reviewerRole}
                    </div>
                    <ol className="mt-1 space-y-0.5">
                      {st.steps.map((s) => (
                        <li key={s.id} className="truncate text-[10px] text-slate-600" title={s.description}>
                          • {s.description}
                        </li>
                      ))}
                    </ol>
                    {st.expectedTools.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {st.expectedTools.map((t) => (
                          <ToolLight key={t} name={t} state="expected" />
                        ))}
                      </div>
                    )}
                    {st.skills.length > 0 && (
                      <div className="mt-1 truncate text-[9px] text-slate-400" title={st.skills.map((s) => s.name).join(', ')}>
                        skills: {st.skills.map((s) => s.name).join(', ')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-amber-600">
                ⛔ Nothing to run — awaiting gate review:{' '}
                {preview.data.pendingGates.map((g) => `${g.name} (${g.reviewerRole})`).join(', ') || 'none'}
              </div>
            )
          ) : (
            <div className="text-[11px] text-slate-400">Loading plan preview…</div>
          )}
        </div>
      )}
    </div>
  );
}
