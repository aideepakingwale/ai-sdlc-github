import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { STAGE_NAMES, tokens } from '../lib/rules';
import { Pill } from './bits';

interface Preview { stage: number; name: string; stack: string; rules: string; templates: string; tokens: { stack: number; rules: number; templates: number; total: number } }

/** What the agents of one stage are told about the project, exactly as sent: the stack, the rules and the templates. */
export default function AgentsSeeTab({ projectId }: { projectId: string }) {
  const [stage, setStage] = useState(3);
  const q = useQuery({ queryKey: ['context-preview', projectId, stage], queryFn: () => api.get<Preview>(`/api/projects/${projectId}/context-preview?stage=${stage}`) });
  const blocks: Array<[string, string, string, number]> = q.data ? [['Stack', q.data.stack, 'v2-see-stack', q.data.tokens.stack], ['Rules', q.data.rules, 'v2-see-rules', q.data.tokens.rules], ['Templates', q.data.templates, 'v2-see-templates', q.data.tokens.templates]] : [];
  return (
    <div data-testid="v2-see-tab">
      <p className="mb-3 text-sm text-slate-600">The exact text added to every run of a stage, so nothing shaping the output is hidden. Pick a stage; token counts are rough estimates.</p>
      <div className="mb-3 flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Stage">
        {Object.entries(STAGE_NAMES).map(([n, name]) => (
          <button key={n} role="tab" aria-selected={stage === Number(n)} onClick={() => setStage(Number(n))} data-testid={`v2-see-stage-${n}`}
            className={`rounded-full px-3 py-1 text-xs font-medium ${stage === Number(n) ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{n} · {name}</button>
        ))}
        {q.data && <span className="ml-auto"><Pill tone="brand">≈ {tokens(q.data.tokens.total)} tokens</Pill></span>}
      </div>
      {!q.data && <div className="animate-pulse text-sm text-slate-400">Loading…</div>}
      <div className="space-y-3">
        {blocks.map(([label, text, id, tok]) => (
          <section key={label} className="rounded-xl border border-slate-300 bg-white" data-testid={id}>
            <header className="flex items-center gap-2 border-b border-slate-200 px-3 py-1.5"><span className="text-sm font-semibold text-slate-800">{label}</span><span className="text-xs text-slate-400">≈ {tokens(tok)} tokens</span></header>
            {text ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap p-3 font-mono text-[11px] leading-5 text-slate-700">{text}</pre>
              : <div className="p-3 text-xs text-slate-400">Nothing for this stage.{label === 'Rules' ? ' Add rules on the Rules tab.' : label === 'Templates' ? ' Documents follow the standard layout.' : ''}</div>}
          </section>
        ))}
      </div>
    </div>
  );
}
