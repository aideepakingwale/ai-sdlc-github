import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';

export interface AgentDef {
  id: string; name: string; version: number; category: string; runtime: 'specialist' | 'native' | 'proposed'; status: string;
  description: string; role: string; uses?: string[]; owns?: Array<{ id: string; variables: string[] }>; tools?: Array<{ name: string; run: string; access: string }>; entrypoint?: string; stage?: number; kind?: string; fields?: string[];
  artifacts?: string[]; upstream?: string[]; after?: string[]; path: string; body: string; notes?: string;
}
export interface AgentInventory { agents: AgentDef[]; counts: Record<string, number> }

const RUNTIME_LABEL: Record<string, string> = { specialist: 'Specialist', native: 'Pipeline', proposed: 'Proposed' };
const RUNTIME_HELP: Record<string, string> = {
  specialist: 'Written from its own file: the file body is exactly what the model is told.',
  native: 'A service makes the call; the file documents it and sets its model role.',
  proposed: 'Planned. Listed so the design is visible, never run.',
};

/** Governance → Agents: every agent of the platform, the file it lives in, its model role and what it reads. */
export default function AgentCatalog() {
  const q = useQuery({ queryKey: ['governance', 'agents'], queryFn: () => api.get<AgentInventory>('/api/agents') });
  const [filter, setFilter] = useState<'all' | 'specialist' | 'native' | 'proposed'>('all');
  const [open, setOpen] = useState<string | null>(null);
  if (q.isLoading) return <div className="animate-pulse text-sm text-slate-400">Loading agents…</div>;
  const list = (q.data?.agents ?? []).filter((a) => filter === 'all' || a.runtime === filter);
  return (
    <div className="space-y-2" data-testid="v2-agent-catalog">
      <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
        Every agent is a <b>markdown file</b> under <code>agents/</code>. Frontmatter declares its model role and what it reads; for a specialist the body is its instruction,
        so tuning an agent is an edit to its file. {Object.entries(RUNTIME_HELP).map(([k, v]) => <span key={k} className="mr-2"><b>{RUNTIME_LABEL[k]}</b>: {v}</span>)}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {(['all', 'specialist', 'native', 'proposed'] as const).map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${filter === f ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>
            {f === 'all' ? `All (${q.data?.agents.length ?? 0})` : `${RUNTIME_LABEL[f]} (${q.data?.counts[f] ?? 0})`}
          </button>
        ))}
      </div>
      {list.map((a) => (
        <div key={a.id} className="rounded-lg border border-slate-200" data-testid="v2-agent-row">
          <button type="button" onClick={() => setOpen(open === a.id ? null : a.id)} aria-expanded={open === a.id} className="flex w-full items-center gap-2 px-3 py-2 text-left">
            <span className="font-mono text-[11px] font-semibold text-brand-700">{a.id}</span>
            <span className="rounded bg-slate-100 px-1 text-[9px] text-slate-500">{RUNTIME_LABEL[a.runtime]}{a.stage ? ` · stage ${a.stage}` : ''} · {a.role}</span>
            <span className="min-w-0 flex-1 truncate text-[11px] text-slate-500">{a.description}</span>
            <span className="text-slate-300">{open === a.id ? '▾' : '▸'}</span>
          </button>
          {open === a.id && (
            <div className="space-y-2 rounded-b-lg bg-slate-50 p-3 text-xs text-slate-600">
              <div className="font-mono text-[11px] text-slate-500">agents/{a.path} · v{a.version}</div>
              {a.runtime === 'specialist' && (
                <div>
                  Writes <b>{a.fields?.join(', ')}</b>{a.artifacts?.length ? <> → {a.artifacts.join(', ')}</> : null}. Reads{' '}
                  {[...(a.upstream ?? []), ...(a.after ?? []).map((f) => `this stage: ${f}`)].join(', ') || 'the brief only'}.
                </div>
              )}
              {a.entrypoint && <div>Called from <code>{a.entrypoint}</code></div>}
              {!!a.owns?.length && <div>Its prompts: {a.owns.map((p) => <code key={p.id} className="mr-1" title={p.variables.length ? `fills ${p.variables.join(', ')}` : ''}>{p.id}</code>)}{a.uses?.length ? <> · shared: {a.uses.map((p) => <code key={p} className="mr-1">{p}</code>)}</> : null}</div>}
              <div data-testid="v2-agent-tools">Tools: {a.tools?.length ? a.tools.map((t) => <span key={t.name} className="mr-1.5 inline-block rounded bg-white px-1.5 py-0.5 font-mono text-[10px] text-slate-700 ring-1 ring-slate-200" title={t.access === 'write' ? 'Changes an external system' : 'Read-only'}>{t.name}<span className={t.access === 'write' ? ' text-amber-700' : ' text-slate-400'}> · {t.access}</span></span>) : <span className="text-slate-400">none. It only writes text; no tool is run with its output.</span>}{!!a.tools?.length && <span className="ml-1 text-slate-400">run by the stage runner on this agent's output; writes to Jira, Confluence and the design and pipeline commits are held until the stage is approved</span>}</div>
              <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">{a.body}</pre>
              {a.notes && <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-white p-3 text-[11px] text-slate-600">{a.notes}</pre>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
