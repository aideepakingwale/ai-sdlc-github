import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { filterPacks, KIND_FILTERS, packButton, packState, type PackKind, type PackView, type Recommendations } from '../lib/profile';
import { PRIORITY_TONE, PRIORITY_WORD, STAGE_NAMES, type RulePriority } from '../lib/rules';
import { Pill } from './bits';

const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';
interface Preview { rules: Array<{ title: string; body: string; priority: RulePriority; stage: number | null; category: string; pack: string; packName: string; have: boolean }> }

/** Rule packs: what fits this project's profile first, then every pack and ready-made set to browse, read and add. */
export default function PackBrowser({ projectId, packs, canAuthor, onChanged }: { projectId: string; packs: PackView[]; canAuthor: boolean; onChanged: (msg: string) => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<PackKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const rec = useQuery({ queryKey: ['rule-advice', projectId, 'all'], queryFn: () => api.get<Recommendations>(`/api/projects/${projectId}/rules/recommendations`) });
  const preview = useQuery({ queryKey: ['pack-preview', projectId, open?.split(':')[1]], queryFn: () => api.get<Preview>(`/api/projects/${projectId}/rules/packs/${open?.split(':')[1]}`), enabled: Boolean(open) });
  const apply = useMutation({
    mutationFn: (id: string) => api.post<{ added: number; skipped: number }>(`/api/projects/${projectId}/rules/packs/${id}`),
    onSuccess: (r) => { onChanged(`Added ${r.added} rule${r.added === 1 ? '' : 's'}${r.skipped ? `, ${r.skipped} already there` : ''}.`); void qc.invalidateQueries({ queryKey: ['rule-advice', projectId] }); void qc.invalidateQueries({ queryKey: ['pack-preview', projectId] }); },
    onError: (e) => onChanged(e instanceof Error ? e.message : 'Could not add the pack'),
  });
  const byId = new Map(packs.map((p) => [p.id, p]));
  const recommended = rec.data ? [...(rec.data.primary ? [rec.data.primary] : []), ...rec.data.bundles.slice(1, 2), ...rec.data.packs.slice(0, 4), ...rec.data.baseline] : [];
  const shown = filterPacks(packs, kind, query);

  const card = (p: PackView, reasons?: string[]) => {
    const rid = reasons ? 'rec' : 'pack';
    const state = packState(p);
    return (
      <div key={p.id} className="rounded-xl border border-slate-300 bg-white p-3" data-testid={`v2-${rid}card-${p.id}`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-slate-900">{p.name}</span>
          <Pill tone={p.kind === 'bundle' ? 'brand' : 'slate'}>{p.kindLabel}</Pill>
          <Pill>{p.count} rules</Pill>
          {p.source !== 'builtin' && <Pill tone="amber" title="Set by your organisation">{p.source === 'org' ? 'Your organisation' : 'Customised'}</Pill>}
        </div>
        <p className="mt-1 text-xs text-slate-600">{p.description}</p>
        {reasons && reasons.length > 0 && <p className="mt-1 text-[11px] text-brand-700">Fits: {reasons.join(', ')}</p>}
        {p.kind === 'bundle' && p.includes.length > 0 && <p className="mt-1 text-[11px] text-slate-500">Includes {p.includes.map((i) => i.name).join(' · ')}</p>}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {canAuthor && <button type="button" className={state === 'applied' ? btn : primary} disabled={apply.isPending || state === 'applied'} onClick={() => apply.mutate(p.id)} data-testid={`v2-${reasons ? 'rec' : 'pack'}-${p.id}`}>{packButton(p)}</button>}
          <button type="button" className="text-xs font-semibold text-brand-700 hover:underline" aria-expanded={open === `${rid}:${p.id}`} onClick={() => setOpen(open === `${rid}:${p.id}` ? null : `${rid}:${p.id}`)}>{open === `${rid}:${p.id}` ? 'Hide the rules' : 'Read the rules'}</button>
          {state === 'partial' && <span className="text-[11px] text-slate-400">{p.alreadyHave} of {p.count} already there</span>}
        </div>
        {open === `${rid}:${p.id}` && (
          <div className="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-50 p-2" data-testid="v2-pack-rules">
            {!preview.data ? <div className="animate-pulse text-xs text-slate-400">Loading…</div> : (
              <ul className="space-y-1.5">
                {preview.data.rules.map((r, i) => (
                  <li key={i} className="text-xs">
                    <div className="flex flex-wrap items-center gap-1.5"><Pill tone={PRIORITY_TONE[r.priority]}>{PRIORITY_WORD[r.priority]}</Pill><span className="font-medium text-slate-800">{r.title}</span>
                      <span className="text-slate-400">{r.stage ? `Stage ${r.stage} · ${STAGE_NAMES[r.stage]}` : 'Every stage'}{p.kind === 'bundle' ? ` · ${r.packName}` : ''}{r.have ? ' · already added' : ''}</span></div>
                    <p className="text-slate-600">{r.body}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="mb-4" data-testid="v2-pack-browser">
      {rec.data && (
        <section className="mb-3 rounded-xl border border-brand-200 bg-brand-50/50 p-3" data-testid="v2-pack-recommended">
          <h3 className="text-sm font-semibold text-navy">Recommended for this project</h3>
          <p className="mb-2 text-xs text-slate-600">{rec.data.profile.empty ? 'No profile yet, so these are the essentials. Set the profile (Profile tab) for advice that fits your industry and regulations.' : `Based on: ${rec.data.profile.text}.`}{rec.data.profile.unconfirmed > 0 ? ` ${rec.data.profile.unconfirmed} value${rec.data.profile.unconfirmed === 1 ? ' was' : 's were'} found in your documents and not yet confirmed.` : ''}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {recommended.map((r) => { const p = byId.get(r.id); return p ? card(p, r.reasons.filter((x) => x !== 'Every project')) : null; })}
            {recommended.length === 0 && <div className="text-xs text-slate-500">Nothing more to suggest: the packs that fit this profile are in place.</div>}
          </div>
        </section>
      )}
      <div className="mb-2 flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Kind of pack">
        {KIND_FILTERS.map((f) => <button key={f.id} role="tab" aria-selected={kind === f.id} onClick={() => setKind(f.id)} data-testid={`v2-packkind-${f.id}`}
          className={`rounded-full px-3 py-1 text-xs font-medium ${kind === f.id ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{f.label}</button>)}
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search packs, industries, regulations…" aria-label="Search packs" className="ml-auto min-w-[14rem] rounded-lg border border-slate-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none" />
        <span className="text-xs text-slate-400">{shown.length} of {packs.length}</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2" data-testid="v2-pack-grid">{shown.map((p) => card(p))}</div>
    </div>
  );
}
