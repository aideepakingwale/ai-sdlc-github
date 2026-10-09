import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import {
  CATEGORIES, checkWord, filterRules, hasHints, NO_FILTER, originLabel, PRIORITIES, PRIORITY_TONE, PRIORITY_WORD, STAGE_NAMES,
  type Compliance, type Pack, type Rule, type RuleCategory, type RuleDraft, type RuleFilter, type RuleHints, type RulePriority,
} from '../lib/rules';
import { Pill } from './bits';

interface Overview { entries: Rule[]; orgEntries: Rule[]; canAuthor: boolean; packs: Pack[]; compliance: Compliance }
interface Draft { title: string; body: string; category: RuleCategory; priority: RulePriority; stage: number | null }
const EMPTY: Draft = { title: '', body: '', category: 'rule', priority: 'must', stage: null };
const input = 'rounded-lg border border-slate-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none';
const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';

/**
 * Project Context -> Rules. The binding rules every agent run is told: the project's own, the organisation's (read-only, with a reasoned
 * opt-out), starter packs, drafting from a document, and what the last check found.
 */
export default function RulesTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [panel, setPanel] = useState<null | 'new' | 'packs' | 'draft'>(null);
  const [filter, setFilter] = useState<RuleFilter>(NO_FILTER);
  const [editing, setEditing] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const q = useQuery({ queryKey: ['rules', projectId], queryFn: () => api.get<Overview>(`/api/projects/${projectId}/rules/overview`) });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['rules', projectId] }); void qc.invalidateQueries({ queryKey: ['canon', projectId] }); void qc.invalidateQueries({ queryKey: ['context-preview', projectId] }); };
  const fail = (e: unknown) => setNote(e instanceof Error ? e.message : 'That did not work');
  const apply = useMutation({
    mutationFn: (id: string) => api.post<{ added: number; skipped: number }>(`/api/projects/${projectId}/rules/packs/${id}`),
    onSuccess: (r) => { setNote(`Added ${r.added} rule${r.added === 1 ? '' : 's'}${r.skipped ? `, ${r.skipped} already there` : ''}.`); refresh(); }, onError: fail,
  });
  const patch = useMutation({ mutationFn: (v: { id: string; body: Record<string, unknown> }) => api.patch(`/api/projects/${projectId}/canon/${v.id}`, v.body), onSuccess: () => { setEditing(null); refresh(); }, onError: fail });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/api/projects/${projectId}/canon/${id}`), onSuccess: refresh, onError: fail });
  const optOut = useMutation({ mutationFn: (v: { id: string; reason: string }) => api.put(`/api/projects/${projectId}/rules/org/${v.id}/opt-out`, { reason: v.reason }), onSuccess: refresh, onError: fail });
  const optIn = useMutation({ mutationFn: (id: string) => api.del(`/api/projects/${projectId}/rules/org/${id}/opt-out`), onSuccess: refresh, onError: fail });
  if (!q.data) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const { entries, orgEntries, canAuthor, packs, compliance } = q.data;
  const shown = filterRules(entries, filter);

  return (
    <div data-testid="v2-rules-tab">
      <p className="mb-3 text-sm text-slate-600">
        Rules are binding. Every agent run is told them, <b>must</b> first. Add your own, start from a pack, or draft them from a standards document.
        {!canAuthor && ' You can read them; the project manager and architects change them.'}
      </p>
      {canAuthor && (
        <div className="mb-3 flex flex-wrap gap-2">
          <button type="button" className={panel === 'new' ? primary : btn} onClick={() => setPanel(panel === 'new' ? null : 'new')} data-testid="v2-rules-new">New rule</button>
          <button type="button" className={panel === 'packs' ? primary : btn} onClick={() => setPanel(panel === 'packs' ? null : 'packs')} data-testid="v2-rules-packs">Starter packs</button>
          <button type="button" className={panel === 'draft' ? primary : btn} onClick={() => setPanel(panel === 'draft' ? null : 'draft')} data-testid="v2-rules-draft">Draft from a document</button>
        </div>
      )}
      {note && <div className="mb-2 text-xs text-slate-600" role="status" data-testid="v2-rules-note">{note}</div>}
      {panel === 'new' && <NewRule projectId={projectId} onDone={() => { setPanel(null); refresh(); }} />}
      {panel === 'packs' && (
        <div className="mb-3 grid gap-2 sm:grid-cols-2" data-testid="v2-rule-packs">
          {packs.map((p) => (
            <div key={p.id} className="rounded-xl border border-slate-300 bg-white p-3">
              <div className="flex items-center gap-2"><span className="text-sm font-semibold text-slate-800">{p.name}</span><Pill>{p.count} rules</Pill></div>
              <p className="mt-1 text-xs text-slate-500">{p.description}</p>
              <div className="mt-2 flex items-center gap-2">
                <button type="button" className={primary} disabled={apply.isPending || p.alreadyHave >= p.count} onClick={() => apply.mutate(p.id)} data-testid={`v2-pack-${p.id}`}>{p.alreadyHave >= p.count ? 'Added' : p.alreadyHave ? `Add the other ${p.count - p.alreadyHave}` : 'Add to this project'}</button>
                {p.alreadyHave > 0 && p.alreadyHave < p.count && <span className="text-[11px] text-slate-400">{p.alreadyHave} already there</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      {panel === 'draft' && <DraftFromDocument projectId={projectId} onDone={(n) => { setPanel(null); setNote(`Added ${n} drafted rule${n === 1 ? '' : 's'}.`); refresh(); }} />}

      {orgEntries.length > 0 && (
        <section className="mb-4" data-testid="v2-org-rules">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Organisation rules <span className="font-normal normal-case text-slate-400">· set by your administrators, they apply to every project</span></h3>
          <ul className="space-y-1.5">
            {orgEntries.map((r) => (
              <li key={r.id} className={`rounded-lg border px-3 py-2 ${r.optedOut ? 'border-dashed border-slate-300 bg-slate-50' : 'border-slate-200 bg-white'}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <Pill tone={PRIORITY_TONE[r.priority]}>{PRIORITY_WORD[r.priority]}</Pill><Pill tone="brand">Organisation</Pill>
                  <span className={`text-sm font-medium ${r.optedOut ? 'text-slate-400 line-through' : 'text-slate-900'}`}>{r.title}</span>
                  {canAuthor && <span className="ml-auto text-xs">
                    {r.optedOut ? <button type="button" className="font-semibold text-brand-700 hover:underline" onClick={() => optIn.mutate(r.id)}>Follow again</button>
                      : <button type="button" className="text-slate-500 hover:text-slate-800" onClick={() => { const why = window.prompt('Why does this project not follow this rule? (recorded in the audit log)'); if (why) optOut.mutate({ id: r.id, reason: why }); }} data-testid={`v2-optout-${r.id}`}>Opt out…</button>}
                  </span>}
                </div>
                <p className="mt-0.5 text-xs text-slate-600">{r.body}</p>
                {r.optedOut && <p className="mt-0.5 text-[11px] text-amber-700">Not followed here: {r.optOutReason}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-2" data-testid="v2-rules-filters">
        <input value={filter.query} onChange={(e) => setFilter({ ...filter, query: e.target.value })} placeholder="Search rules…" aria-label="Search rules" className={`${input} min-w-[12rem] flex-1`} />
        <select aria-label="Stage" value={String(filter.stage)} onChange={(e) => setFilter({ ...filter, stage: e.target.value === 'all' || e.target.value === 'every' ? e.target.value : Number(e.target.value) })} className={input}>
          <option value="all">All stages</option><option value="every">Every stage only</option>
          {Object.entries(STAGE_NAMES).map(([n, name]) => <option key={n} value={n}>{n} · {name}</option>)}
        </select>
        <select aria-label="Priority" value={filter.priority} onChange={(e) => setFilter({ ...filter, priority: e.target.value as RuleFilter['priority'] })} className={input}>
          <option value="all">Any priority</option>{PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_WORD[p]}</option>)}
        </select>
        <select aria-label="Kind" value={filter.category} onChange={(e) => setFilter({ ...filter, category: e.target.value as RuleFilter['category'] })} className={input}>
          <option value="all">Any kind</option>{CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <span className="text-xs text-slate-400">{shown.length} of {entries.length}</span>
      </div>
      {entries.length === 0 && <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">No rules yet. Start from a pack, draft them from a document, or add one.</div>}
      <ul className="space-y-1.5" data-testid="v2-rules-list">
        {shown.map((r) => (
          <li key={r.id} className={`rounded-lg border bg-white px-3 py-2 ${r.active ? 'border-slate-200' : 'border-dashed border-slate-300 opacity-60'}`} data-testid="v2-rule">
            {editing === r.id ? (
              <EditRule rule={r} busy={patch.isPending} onCancel={() => setEditing(null)} onSave={(body) => patch.mutate({ id: r.id, body })} />
            ) : (
              <RuleRow rule={r} check={compliance.rules[r.id]} canAuthor={canAuthor}
                onEdit={() => setEditing(r.id)} onToggle={() => patch.mutate({ id: r.id, body: { active: !r.active } })} onDelete={() => { if (window.confirm(`Delete “${r.title}”?`)) remove.mutate(r.id); }} />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RuleRow({ rule: r, check, canAuthor, onEdit, onToggle, onDelete }: { rule: Rule; check?: Compliance['rules'][string]; canAuthor: boolean; onEdit: () => void; onToggle: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const word = checkWord(check);
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={PRIORITY_TONE[r.priority]}>{PRIORITY_WORD[r.priority]}</Pill>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">{r.category}</span>
        <span className="text-[11px] text-slate-500">{r.stage ? `Stage ${r.stage} · ${STAGE_NAMES[r.stage]}` : 'Every stage'}</span>
        <span className="text-sm font-medium text-slate-900">{r.title}</span>
        {originLabel(r.origin) && <span className="text-[11px] text-slate-400">{originLabel(r.origin)}</span>}
        {word && <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}><Pill tone={word.tone} title="What the last check of the stage's documents found">{word.text}</Pill></button>}
        {canAuthor && <span className="ml-auto flex gap-2 text-xs">
          <button type="button" className="font-semibold text-brand-700 hover:underline" onClick={onEdit}>Edit</button>
          <button type="button" className="text-slate-500 hover:text-slate-800" onClick={onToggle}>{r.active ? 'Turn off' : 'Turn on'}</button>
          <button type="button" className="text-slate-400 hover:text-red-600" onClick={onDelete}>Delete</button>
        </span>}
      </div>
      <p className="mt-0.5 text-xs text-slate-600">{r.body}</p>
      {open && check?.violations && check.violations.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-[11px] text-red-700">{check.violations.map((v, i) => <li key={i}>Stage {v.phase}{v.artefact ? `, ${v.artefact}` : ''}: {v.evidence || 'no detail given'}</li>)}</ul>
      )}
    </>
  );
}

function EditRule({ rule, busy, onCancel, onSave }: { rule: Rule; busy: boolean; onCancel: () => void; onSave: (b: Record<string, unknown>) => void }) {
  const [d, setD] = useState<Draft>({ title: rule.title, body: rule.body, category: rule.category, priority: rule.priority, stage: rule.stage });
  return (
    <div className="space-y-2" data-testid="v2-rule-edit">
      <Fields d={d} onChange={setD} />
      <div className="flex justify-end gap-2"><button type="button" className={btn} onClick={onCancel}>Cancel</button><button type="button" className={primary} disabled={busy || d.title.trim().length < 3 || !d.body.trim()} onClick={() => onSave({ ...d })}>Save</button></div>
    </div>
  );
}

function Fields({ d, onChange }: { d: Draft; onChange: (d: Draft) => void }) {
  return (
    <>
      <input value={d.title} onChange={(e) => onChange({ ...d, title: e.target.value })} placeholder="Title, for example “Validate all input”" aria-label="Rule title" className={`${input} w-full`} data-testid="v2-rule-title" />
      <textarea value={d.body} onChange={(e) => onChange({ ...d, body: e.target.value })} rows={3} placeholder="The rule in one or two sentences, and why" aria-label="Rule text" className={`${input} w-full`} data-testid="v2-rule-body" />
      <div className="flex flex-wrap gap-2 text-xs">
        <select aria-label="Priority" value={d.priority} onChange={(e) => onChange({ ...d, priority: e.target.value as RulePriority })} className={input}>{PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_WORD[p]}</option>)}</select>
        <select aria-label="Kind" value={d.category} onChange={(e) => onChange({ ...d, category: e.target.value as RuleCategory })} className={input}>{CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}</select>
        <select aria-label="Applies to" value={d.stage ?? ''} onChange={(e) => onChange({ ...d, stage: e.target.value === '' ? null : Number(e.target.value) })} className={input}>
          <option value="">Every stage</option>{Object.entries(STAGE_NAMES).map(([n, name]) => <option key={n} value={n}>Stage {n} · {name}</option>)}
        </select>
      </div>
    </>
  );
}

/** A new rule. Before it is saved it is checked for duplicates, contradictions and clashes with the pinned stack; the person decides. */
function NewRule({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const [d, setD] = useState<Draft>(EMPTY);
  const [hints, setHints] = useState<RuleHints | null>(null);
  const [error, setError] = useState('');
  const save = useMutation({ mutationFn: () => api.post(`/api/projects/${projectId}/canon`, d), onSuccess: onDone, onError: (e) => setError(e instanceof Error ? e.message : 'Could not save') });
  const check = useMutation({
    mutationFn: () => api.post<RuleHints>(`/api/projects/${projectId}/rules/check`, d),
    onSuccess: (h) => { setHints(h); if (!hasHints(h)) save.mutate(); },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not check'),
  });
  const ready = d.title.trim().length >= 3 && d.body.trim().length > 0;
  return (
    <div className="mb-3 space-y-2 rounded-xl border border-brand-200 bg-brand-50/40 p-3" data-testid="v2-rule-new-form">
      <Fields d={d} onChange={(x) => { setD(x); setHints(null); }} />
      {hasHints(hints) && hints && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="alert" data-testid="v2-rule-hints">
          <div className="font-semibold">Worth a look before you save</div>
          <ul className="mt-1 list-disc pl-4">
            {hints.duplicates.map((x) => <li key={x.id}>Very close to the existing rule “{x.title}”{x.scope === 'org' ? ' (organisation)' : ''}.</li>)}
            {hints.conflicts.map((x) => <li key={x.id}>May contradict “{x.title}”: {x.why}</li>)}
            {hints.stack.map((x, i) => <li key={i}>Names {x.rule} for {x.layer}, but your stack pins {x.pinned}.</li>)}
          </ul>
        </div>
      )}
      {error && <div className="text-xs text-red-700">{error}</div>}
      <div className="flex justify-end gap-2">
        {hasHints(hints) && <button type="button" className={btn} onClick={() => save.mutate()} disabled={save.isPending} data-testid="v2-rule-save-anyway">Save anyway</button>}
        {!hasHints(hints) && <button type="button" className={primary} disabled={!ready || check.isPending || save.isPending} onClick={() => check.mutate()} data-testid="v2-rule-save">{check.isPending ? 'Checking…' : 'Add rule'}</button>}
      </div>
    </div>
  );
}

function DraftFromDocument({ projectId, onDone }: { projectId: string; onDone: (n: number) => void }) {
  const [text, setText] = useState('');
  const [drafts, setDrafts] = useState<Array<RuleDraft & { keep: boolean }>>([]);
  const [via, setVia] = useState('');
  const [error, setError] = useState('');
  const draft = useMutation({
    mutationFn: () => api.post<{ rules: RuleDraft[]; via: string }>(`/api/projects/${projectId}/rules/draft`, { text }),
    onSuccess: (r) => { setDrafts(r.rules.map((x) => ({ ...x, keep: true }))); setVia(r.via); setError(r.rules.length ? '' : 'No rules found in that text.'); },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not draft'),
  });
  const add = useMutation({
    mutationFn: async () => { const chosen = drafts.filter((d) => d.keep); for (const d of chosen) await api.post(`/api/projects/${projectId}/canon`, { ...d, origin: 'draft' }); return chosen.length; },
    onSuccess: onDone, onError: (e) => setError(e instanceof Error ? e.message : 'Could not add'),
  });
  const set = (i: number, patch: Partial<RuleDraft & { keep: boolean }>) => setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  return (
    <div className="mb-3 rounded-xl border border-brand-200 bg-brand-50/40 p-3" data-testid="v2-rule-draft-panel">
      {drafts.length === 0 ? (
        <>
          <p className="mb-2 text-xs text-slate-600">Paste a standards or policy document (or open a text or markdown file). The assistant proposes rules from it; you choose which to keep.</p>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="Paste the document here…" aria-label="Document to draft rules from" className={`${input} w-full`} data-testid="v2-rule-draft-text" />
          <div className="mt-2 flex items-center gap-2">
            <label className={`${btn} cursor-pointer`}>Open a file<input type="file" accept=".md,.txt,.markdown,text/plain" className="hidden" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()); e.target.value = ''; }} /></label>
            <button type="button" className={`${primary} ml-auto`} disabled={text.trim().length < 40 || draft.isPending} onClick={() => draft.mutate()} data-testid="v2-rule-draft-go">{draft.isPending ? 'Reading…' : 'Draft rules'}</button>
          </div>
        </>
      ) : (
        <>
          <div className="mb-2 text-xs text-slate-600">{drafts.length} proposed{via === 'keywords' ? ' (from the wording of the text; the model was not available)' : ''}. Untick any you do not want and edit the rest.</div>
          <ul className="space-y-2" data-testid="v2-rule-drafts">
            {drafts.map((d, i) => (
              <li key={i} className="rounded-lg border border-slate-200 bg-white p-2">
                <div className="flex items-center gap-2">
                  <input type="checkbox" checked={d.keep} onChange={(e) => set(i, { keep: e.target.checked })} aria-label={`Keep ${d.title}`} />
                  <input value={d.title} onChange={(e) => set(i, { title: e.target.value })} className={`${input} flex-1`} aria-label="Title" />
                  <select value={d.priority} onChange={(e) => set(i, { priority: e.target.value as RulePriority })} className={input} aria-label="Priority">{PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_WORD[p]}</option>)}</select>
                </div>
                <textarea value={d.body} onChange={(e) => set(i, { body: e.target.value })} rows={2} className={`${input} mt-1 w-full`} aria-label="Rule text" />
              </li>
            ))}
          </ul>
          <div className="mt-2 flex gap-2"><button type="button" className={btn} onClick={() => setDrafts([])}>Back</button><button type="button" className={`${primary} ml-auto`} disabled={add.isPending || !drafts.some((d) => d.keep)} onClick={() => add.mutate()} data-testid="v2-rule-draft-add">Add {drafts.filter((d) => d.keep).length} rule{drafts.filter((d) => d.keep).length === 1 ? '' : 's'}</button></div>
        </>
      )}
      {error && <div className="mt-2 text-xs text-red-700">{error}</div>}
    </div>
  );
}
