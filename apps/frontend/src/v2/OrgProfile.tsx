import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { KIND_ORDER, type ProfileKind, type VocabEntry } from '../lib/profile';

interface OrgProfileResponse { items: Array<{ id: string; kind: ProfileKind; value: string }>; canEdit: boolean; vocab: Record<ProfileKind, VocabEntry[]>; kinds: Record<ProfileKind, { label: string; single: boolean }> }

/** Governance -> Organisation -> Profile: the default every project starts from. A project can change it; it only shapes which rule packs are advised. */
export default function OrgProfile() {
  const qc = useQueryClient();
  const [error, setError] = useState('');
  const q = useQuery({ queryKey: ['org-profile'], queryFn: () => api.get<OrgProfileResponse>('/api/org/profile') });
  const save = useMutation({
    mutationFn: (items: Array<{ kind: ProfileKind; value: string }>) => api.put('/api/org/profile', { items }),
    onSuccess: () => { setError(''); void qc.invalidateQueries({ queryKey: ['org-profile'] }); void qc.invalidateQueries({ queryKey: ['profile'] }); void qc.invalidateQueries({ queryKey: ['rule-advice'] }); },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not save'),
  });
  if (!q.data) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const { items, canEdit, vocab, kinds } = q.data;
  const current = items.map((i) => ({ kind: i.kind, value: i.value }));
  const label = (k: ProfileKind, v: string) => vocab[k].find((x) => x.id === v)?.label ?? v;
  return (
    <div data-testid="v2-org-profile-panel">
      <p className="mb-3 text-sm text-slate-600">What most of your projects have in common, for example your industry and the regulations you answer to. Every project starts from this; the project can add to it, leave a value out, or change it. The platform also reads each project&apos;s documents and proposes values for the team to confirm.</p>
      {error && <div className="mb-2 text-xs text-red-700" role="alert">{error}</div>}
      <ul className="divide-y divide-slate-200 rounded-xl border border-slate-300 bg-white">
        {KIND_ORDER.map((kind) => {
          const mine = items.filter((i) => i.kind === kind);
          const taken = new Set(mine.map((i) => i.value));
          const choices = vocab[kind].filter((v) => !taken.has(v.id));
          const single = kinds[kind].single;
          return (
            <li key={kind} className="flex flex-wrap items-center gap-2 px-3 py-2.5">
              <span className="w-40 shrink-0 text-sm font-semibold text-slate-800">{kinds[kind].label}</span>
              {mine.length === 0 && <span className="text-xs text-slate-400">Not set</span>}
              {mine.map((i) => (
                <span key={i.id} className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white py-0.5 pl-2.5 pr-1.5 text-sm" data-testid={`v2-org-profile-${kind}-${i.value}`}>
                  <span className="font-medium text-slate-900">{label(kind, i.value)}</span>
                  {canEdit && <button type="button" className="text-xs text-slate-400 hover:text-red-600" aria-label={`Remove ${label(kind, i.value)}`} onClick={() => save.mutate(current.filter((c) => !(c.kind === kind && c.value === i.value)))}>×</button>}
                </span>
              ))}
              {canEdit && choices.length > 0 && (
                <select aria-label={`${single ? 'Set' : 'Add'} ${kinds[kind].label.toLowerCase()}`} value="" data-testid={`v2-org-profile-add-${kind}`}
                  onChange={(e) => { if (e.target.value) save.mutate([...current, { kind, value: e.target.value }]); }}
                  className="ml-auto rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600">
                  <option value="">{single && mine.length ? 'Change…' : '+ Add…'}</option>
                  {choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
              )}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[11px] text-slate-400">The organisation profile never adds rules on its own. It decides which packs are suggested when a project reaches solution architecture and technical design.</p>
    </div>
  );
}
