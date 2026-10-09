import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { itemsOf, KIND_ORDER, STATE_TONE, stateText, unconfirmed, type ProfileResponse } from '../lib/profile';
import { Pill } from './bits';

/**
 * Project Context -> Profile. What kind of project this is: industry, regulations, what the system does, data sensitivity and regions.
 * The organisation sets a default; the project can change it; the platform proposes values it finds in your documents for you to confirm.
 * The profile decides which rule packs are recommended.
 */
export default function ProfileTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const q = useQuery({ queryKey: ['profile', projectId], queryFn: () => api.get<ProfileResponse>(`/api/projects/${projectId}/profile`) });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['profile', projectId] }); void qc.invalidateQueries({ queryKey: ['rule-advice', projectId] }); void qc.invalidateQueries({ queryKey: ['rules', projectId] }); };
  const edit = useMutation({
    mutationFn: (b: { upsert?: unknown[]; remove?: string[]; confirm?: string[] }) => api.put(`/api/projects/${projectId}/profile`, { upsert: [], remove: [], confirm: [], ...b }),
    onSuccess: () => { setNote(''); refresh(); }, onError: (e) => setNote(e instanceof Error ? e.message : 'Could not save'),
  });
  const recheck = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/config/stack/advise`),
    onSuccess: () => { setNote('Read the documents again.'); refresh(); void qc.invalidateQueries({ queryKey: ['project-config', projectId] }); }, onError: (e) => setNote(e instanceof Error ? e.message : 'Could not read the documents'),
  });
  if (!q.data) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const { effective, vocab, kinds, canEdit } = q.data;
  const found = unconfirmed(effective.items);
  const excluded = q.data.items.filter((i) => i.status === 'excluded');

  return (
    <div data-testid="v2-profile-tab">
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-300 bg-slate-100 p-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-navy">Project profile</div>
          <div className="text-xs text-slate-500" data-testid="v2-profile-summary">{q.data.text || 'Nothing set yet. Your organisation can set a default, you can set it here, or it is filled in from your documents.'}</div>
        </div>
        {canEdit && <button type="button" onClick={() => recheck.mutate()} disabled={recheck.isPending} data-testid="v2-profile-recheck" className="ml-auto rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50">{recheck.isPending ? 'Reading the documents…' : 'Re-check from documents'}</button>}
      </div>
      {found.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-900" data-testid="v2-profile-found">
          <span>{found.length} value{found.length === 1 ? ' was' : 's were'} found in your documents. They already shape the advice; confirm them or change them.</span>
          {canEdit && <button type="button" onClick={() => edit.mutate({ confirm: found })} data-testid="v2-profile-confirm-all" className="ml-auto rounded-lg bg-brand-600 px-3 py-1 font-semibold text-white hover:bg-brand-700">Confirm all</button>}
        </div>
      )}
      {note && <div className="mb-2 text-xs text-red-700" role="status">{note}</div>}
      <ul className="divide-y divide-slate-200 rounded-xl border border-slate-300 bg-white">
        {KIND_ORDER.map((kind) => {
          const mine = itemsOf(effective.items, kind);
          const taken = new Set(mine.map((i) => i.value));
          const choices = vocab[kind].filter((v) => !taken.has(v.id));
          const single = kinds[kind].single;
          return (
            <li key={kind} className="px-3 py-2.5" data-kind={kind}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-40 shrink-0 text-sm font-semibold text-slate-800">{kinds[kind].label}</span>
                {mine.length === 0 && <span className="text-xs text-slate-400">Not set</span>}
                {mine.map((i) => (
                  <span key={i.id} className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white py-0.5 pl-2.5 pr-1.5 text-sm" data-testid={`v2-profile-${i.id}`}>
                    <span className="font-medium text-slate-900">{i.label}</span>
                    <Pill tone={STATE_TONE[i.state]} title={i.evidence ? `“${i.evidence}”` : stateText(i)}>{stateText(i)}</Pill>
                    {canEdit && i.state === 'identified' && <button type="button" className="text-xs font-semibold text-emerald-700 hover:underline" onClick={() => edit.mutate({ confirm: [i.id] })}>Confirm</button>}
                    {canEdit && i.state === 'inherited' && !single && <button type="button" className="text-xs text-slate-400 hover:text-red-600" title="Leave this out for this project" onClick={() => edit.mutate({ upsert: [{ kind, value: i.value, status: 'excluded' }] })}>Not here</button>}
                    {canEdit && i.state !== 'inherited' && <button type="button" className="text-xs text-slate-400 hover:text-red-600" aria-label={`Remove ${i.label}`} onClick={() => edit.mutate({ remove: [i.id] })}>×</button>}
                  </span>
                ))}
                {canEdit && choices.length > 0 && (
                  <select aria-label={`${single ? 'Set' : 'Add'} ${kinds[kind].label.toLowerCase()}`} value="" data-testid={`v2-profile-add-${kind}`}
                    onChange={(e) => { if (e.target.value) edit.mutate({ upsert: [{ kind, value: e.target.value, status: 'pinned' }] }); }}
                    className="ml-auto rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600">
                    <option value="">{single && mine.length ? 'Change…' : '+ Add…'}</option>
                    {choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                  </select>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {excluded.length > 0 && (
        <p className="mt-2 text-xs text-slate-500">Left out for this project: {excluded.map((i) => (
          <span key={i.id} className="mr-2">{vocab[i.kind].find((v) => v.id === i.value)?.label ?? i.value} {canEdit && <button type="button" className="underline" onClick={() => edit.mutate({ remove: [i.id] })}>undo</button>}</span>
        ))}</p>
      )}
      <p className="mt-3 text-[11px] text-slate-400">The profile only shapes advice: which rule packs we suggest when you reach solution architecture and technical design. Rules themselves are always your choice.</p>
    </div>
  );
}
