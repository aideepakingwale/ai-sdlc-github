import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { Pill } from './bits';

interface Result { complied: number; violated: number; unclear: number; items: Array<{ rule: string; status: string; evidence: string | null }> }

/** How the latest check found this document against the project's must-rules. Hidden when no check has named it. */
export default function ArtefactRules({ projectId, artefactId }: { projectId: string; artefactId: string }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ['artefact-rules', projectId, artefactId], queryFn: () => api.get<Result>(`/api/projects/${projectId}/artefacts/${artefactId}/rules`) });
  const r = q.data;
  if (!r || r.items.length === 0) return null;
  return (
    <div className="border-t border-slate-200 px-3 py-2 text-xs" data-testid="v2-artefact-rules">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-2 text-left">
        <span className="font-semibold text-slate-700">Rules</span>
        {r.violated > 0 ? <Pill tone="red">{r.violated} not followed</Pill> : <Pill tone="green">All followed</Pill>}
        {r.complied > 0 && <span className="text-slate-400">{r.complied} followed</span>}
        <span className="ml-auto text-slate-400">{open ? '▾' : '▸'}</span>
      </button>
      {open && <ul className="mt-1 space-y-0.5">{r.items.map((i, k) => <li key={k} className={i.status === 'violated' ? 'text-red-700' : 'text-slate-600'}>{i.status === 'violated' ? '✕' : i.status === 'complied' ? '✓' : '?'} {i.rule}{i.evidence ? ` — ${i.evidence}` : ''}</li>)}</ul>}
    </div>
  );
}
