import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import type { StageItem } from '../../lib/agents';
import { Pill } from '../bits';
import AgentRunPanel from '../agents/AgentRunPanel';

/** In a stage: the custom agents attached to it, and a way to run one now. What it writes is saved into this stage. */
export default function StageAgentsStrip({ projectId, stageKey, canWrite }: { projectId: string; stageKey: string; canWrite: boolean }) {
  const q = useQuery({ queryKey: ['stage-agents', projectId, stageKey], queryFn: () => api.get<{ items: StageItem[] }>(`/api/projects/${projectId}/stages/${encodeURIComponent(stageKey)}/agents`), retry: false });
  const [running, setRunning] = useState<string | null>(null);
  const agents = (q.data?.items ?? []).filter((i) => i.kind === 'agent');
  if (!agents.length) return null;
  return (
    <section className="rounded-xl border border-slate-300 bg-white p-3" data-testid="v2-stage-agents-strip" aria-label="Custom agents">
      <div className="mb-2 flex items-center justify-between"><h3 className="font-display text-sm font-bold text-navy">Custom agents in this stage</h3><span className="text-xs text-slate-500">They run after the stage's own agents</span></div>
      <ul className="space-y-1.5">
        {agents.map((a) => (
          <li key={a.defId} className="flex flex-wrap items-center justify-between gap-2 text-sm" data-testid="v2-strip-agent">
            <span><b>{a.name}</b> <span className="text-xs text-slate-400">v{a.pinnedVersion}</span> <Pill tone={a.runs === 'on_request' ? 'amber' : 'green'}>{a.runs === 'always' ? 'every time' : a.runs === 'when' ? 'when…' : 'on request'}</Pill>
              {a.wiring?.blocked && <Pill tone="red" title="Nothing earlier produces an input it needs">needs input</Pill>}</span>
            <button type="button" disabled={!canWrite} onClick={() => setRunning(running === a.defId ? null : a.defId)} data-testid="v2-strip-run"
              className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50">{running === a.defId ? 'Close' : 'Run now'}</button>
          </li>))}
      </ul>
      {running && <div className="mt-3 border-t border-slate-200 pt-3"><AgentRunPanel projectId={projectId} defId={running} stageKey={stageKey} /></div>}
    </section>
  );
}
