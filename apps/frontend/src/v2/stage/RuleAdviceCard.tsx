import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { adviceIds, adviceItems, type Recommendations } from '../../lib/profile';
import { STAGE_NAMES } from '../../lib/rules';
import { Pill } from '../bits';

/**
 * Shown when the project reaches solution architecture or technical design and rules that fit its profile are not in place yet.
 * It names what fits (from the project profile), adds it in one click, opens Project Context to review, or is dismissed for this stage.
 * It never appears for what the project already has.
 */
export default function RuleAdviceCard({ projectId, stage, onOpenContext }: { projectId: string; stage: number; onOpenContext?: () => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const key = ['rule-advice', projectId, stage];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<Recommendations>(`/api/projects/${projectId}/rules/recommendations?stage=${stage}`) });
  const done = () => { void qc.invalidateQueries({ queryKey: ['rule-advice', projectId] }); void qc.invalidateQueries({ queryKey: ['rules', projectId] }); void qc.invalidateQueries({ queryKey: ['canon', projectId] }); void qc.invalidateQueries({ queryKey: ['context-preview', projectId] }); };
  const add = useMutation({
    mutationFn: (ids: string[]) => api.post<{ added: number; skipped: number }>(`/api/projects/${projectId}/rules/packs-apply`, { ids }),
    onSuccess: (r) => { setNote(`Added ${r.added} rule${r.added === 1 ? '' : 's'} for the agents to follow.`); done(); },
    onError: (e) => setNote(e instanceof Error ? e.message : 'Could not add the rules'),
  });
  const dismiss = useMutation({ mutationFn: () => api.post(`/api/projects/${projectId}/rules/recommendations/dismiss`, { stage }), onSuccess: done });
  const r = q.data;
  if (note && !(r?.due)) return <div className="rounded-lg bg-green-50 px-3.5 py-2 text-sm text-green-900" role="status" data-testid="v2-advice-done">{note}</div>;
  if (!r || !r.due) return null;
  const items = adviceItems(r);
  const ids = adviceIds(r);
  return (
    <section className="rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3" data-testid="v2-advice" aria-label="Rules for this project">
      <h3 className="text-sm font-semibold text-navy">Before {STAGE_NAMES[stage]}: rules that fit this project</h3>
      <p className="mt-0.5 text-xs text-slate-600">
        {r.profile.empty ? 'No profile is set, so these are the essentials every project needs.' : `This project is ${r.profile.text}.`} These are not in your rules yet, and every agent run would follow them.
        {r.profile.unconfirmed > 0 && ` ${r.profile.unconfirmed} value${r.profile.unconfirmed === 1 ? ' was' : 's were'} found in your documents and ${r.profile.unconfirmed === 1 ? 'is' : 'are'} not confirmed yet.`}
      </p>
      <ul className="mt-2 space-y-1.5">
        {items.map((i) => (
          <li key={i.id} className="flex flex-wrap items-center gap-2 text-sm" data-testid={`v2-advice-${i.id}`}>
            <span className="font-medium text-slate-800">{i.name}</span>
            <Pill tone={i.kind === 'bundle' ? 'brand' : 'slate'}>{i.missing} rule{i.missing === 1 ? '' : 's'} to add</Pill>
            {i.reasons.filter((x) => x !== 'Every project').length > 0 && <span className="text-xs text-slate-500">{i.reasons.filter((x) => x !== 'Every project').join(', ')}</span>}
          </li>
        ))}
      </ul>
      {note && <p className="mt-1 text-xs text-slate-600" role="status">{note}</p>}
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {r.canAuthor && <button type="button" onClick={() => add.mutate(ids)} disabled={add.isPending} data-testid="v2-advice-add" className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">{add.isPending ? 'Adding…' : 'Add these rules'}</button>}
        {onOpenContext && <button type="button" onClick={onOpenContext} data-testid="v2-advice-review" className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400">Review in Project Context</button>}
        {r.canAuthor && <button type="button" onClick={() => dismiss.mutate()} disabled={dismiss.isPending} data-testid="v2-advice-dismiss" className="ml-auto text-xs text-slate-500 hover:text-slate-800">Not for this stage</button>}
      </div>
    </section>
  );
}
