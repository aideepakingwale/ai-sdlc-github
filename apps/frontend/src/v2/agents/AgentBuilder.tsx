import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api/client';
import {
  ARTEFACT_TYPES, FORMATS, MODELS, MODEL_ROLES, PHASE_ROLES, SOURCE_OPTIONS, STAGE_NAMES, STEPS, VALUE_TYPES, appendLine, auditSubmittable, costHint, decodePalette, delegationMeters, fromDeveloperJson, insertAt,
  sampleInputs, segments, statusChip, stepIndex, toDeveloperJson, uniqueName, parseCap, tokensLabel, TOKEN_CAP, VERDICT_TONE, type AgentBody, type Card, type CompareResult, type DefDetail, type DefUsage, type InputDef, type ModelRole, type OutputDef, type PaletteKind, type RunResultView,
} from '../../lib/agents';
import { Pill } from '../bits';
import AuditPanel from './AuditPanel';
import Palette, { type PaletteAgent } from './Palette';

const field = 'w-full rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm focus:border-brand-500 focus:outline-none disabled:bg-slate-100 disabled:text-slate-500';
const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';
const TABS_AGENT = [['identity', 'Identity'], ['engine', 'Engine'], ['io', 'Inputs and outputs'], ['deleg', 'Delegation'], ['tools', 'Tools'], ['audit', 'Audit']] as const;
const TABS_SKILL = [['identity', 'Identity'], ['engine', 'Engine'], ['access', 'Access'], ['audit', 'Audit']] as const;
const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

/**
 * The builder for one custom agent or skill. A draft saves itself; the audit, the submit and the approval are separate, explicit steps.
 * `projectId` is set for a project's own agents (delegates and test context come from that project); without it this is the organisation library.
 */
export default function AgentBuilder({ defId, projectId, onBack }: { defId: string; projectId?: string | null; onBack: () => void }) {
  const qc = useQueryClient();
  const key = ['agent-def', defId];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<DefDetail>(`/api/agent-defs/${defId}`) });
  const [name, setName] = useState('');
  const [body, setBody] = useState<AgentBody | null>(null);
  const [mode, setMode] = useState<'guided' | 'dev'>('guided');
  const [tab, setTab] = useState('identity');
  const [testOpen, setTestOpen] = useState(true);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const [devText, setDevText] = useState('');
  const [devError, setDevError] = useState('');
  const dirty = useRef(false);
  const lastSaved = useRef('');
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const [spawn, setSpawn] = useState<string | null>(null);
  const [over, setOver] = useState(false);

  const detail = q.data;
  const cur = detail?.current ?? null;
  const kind = detail?.def.kind ?? 'agent';
  const rights = detail?.rights;
  const editable = Boolean(rights?.edit && cur && cur.status !== 'pending' && !detail?.def.retired);

  // adopt what the server holds, unless the person is mid-edit
  useEffect(() => {
    if (!detail || !cur?.body) return;
    const serverJson = JSON.stringify({ n: detail.def.name, b: cur.body });
    if (body === null || (!dirty.current && serverJson !== lastSaved.current)) {
      setName(detail.def.name); setBody(cur.body); lastSaved.current = serverJson; setDevText(toDeveloperJson(detail.def.name, cur.body));
    }
  }, [detail, cur, body]);

  const put = (d: DefDetail) => qc.setQueryData(key, d);
  const refreshLists = () => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); void qc.invalidateQueries({ queryKey: ['agent-approvals'] }); };

  // ---- autosave
  const save = useMutation({
    mutationFn: (v: { name: string; body: AgentBody }) => api.put<DefDetail>(`/api/agent-defs/${defId}/draft`, v),
    onMutate: () => setSaveState('saving'),
    onSuccess: (d, v) => { lastSaved.current = JSON.stringify({ n: v.name, b: v.body }); dirty.current = false; put(d); setSaveState('saved'); setMessage(''); refreshLists(); },
    onError: (e) => { setSaveState('error'); setMessage(`Not saved: ${errText(e, 'could not save')}`); },
  });
  const saveRef = useRef(save.mutate);
  saveRef.current = save.mutate;
  useEffect(() => {
    if (!dirty.current || !body || !editable) return;
    const t = setTimeout(() => saveRef.current({ name, body }), 900);
    return () => clearTimeout(t);
  }, [name, body, editable]);
  const edit = useCallback((fn: (b: AgentBody) => AgentBody) => { dirty.current = true; setSaveState('idle'); setBody((b) => (b ? fn(b) : b)); }, []);
  const rename = (n: string) => { dirty.current = true; setSaveState('idle'); setName(n); };
  const flush = async (): Promise<void> => { if (dirty.current && body && editable) await save.mutateAsync({ name, body }); };

  // ---- audit, fix, acknowledge, submit, withdraw, retire
  const audit = useMutation({
    mutationFn: async () => { await flush(); return api.post<DefDetail & { audit: unknown }>(`/api/agent-defs/${defId}/audit`); },
    onSuccess: (d) => { put(d); setMessage(''); setTab('audit'); refreshLists(); }, onError: (e) => setMessage(errText(e, 'The audit could not run')),
  });
  const fix = useMutation({
    mutationFn: async (id: string) => { await flush(); return api.post<DefDetail>(`/api/agent-defs/${defId}/audit/fix/${id}`); },
    onSuccess: (d) => { dirty.current = false; put(d); if (d.current?.body) { setBody(d.current.body); lastSaved.current = JSON.stringify({ n: d.def.name, b: d.current.body }); setDevText(toDeveloperJson(d.def.name, d.current.body)); } setMessage(''); },
    onError: (e) => setMessage(errText(e, 'Could not apply that fix')),
  });
  const act = (path: string, ok = '') => ({
    mutationFn: async () => { await flush(); return api.post<DefDetail>(`/api/agent-defs/${defId}/${path}`); },
    onSuccess: (d: DefDetail) => { put(d); setMessage(ok); refreshLists(); }, onError: (e: unknown) => setMessage(errText(e, 'That did not work')),
  });
  const ack = useMutation({ mutationFn: (v: boolean) => api.post<DefDetail>(`/api/agent-defs/${defId}/audit/ack`, { ack: v }), onSuccess: put, onError: (e) => setMessage(errText(e, 'Could not record that')) });
  const submit = useMutation(act('submit', 'Submitted. A project admin will review it.'));
  const withdraw = useMutation(act('withdraw'));
  const retire = useMutation({ mutationFn: () => api.post(`/api/agent-defs/${defId}/retire`), onSuccess: () => { refreshLists(); onBack(); } });
  const remove = useMutation({ mutationFn: () => api.del(`/api/agent-defs/${defId}`), onSuccess: () => { refreshLists(); onBack(); }, onError: (e) => setMessage(errText(e, 'Could not delete')) });
  const toggleOpen = useMutation({ mutationFn: (open: boolean) => api.put<DefDetail>(`/api/agent-defs/${defId}/open`, { open }), onSuccess: (d) => { put(d); refreshLists(); }, onError: (e) => setMessage(errText(e, 'Could not change that')) });
  const [draftOpen, setDraftOpen] = useState(false);
  const [draftText, setDraftText] = useState('');
  const draft = useMutation({
    mutationFn: async (text: string) => { await flush(); return api.post<DefDetail>(`/api/agent-defs/${defId}/draft-from-text`, { text }); },
    onSuccess: (d) => {
      dirty.current = false; put(d);
      if (d.current?.body) { setName(d.def.name); setBody(d.current.body); lastSaved.current = JSON.stringify({ n: d.def.name, b: d.current.body }); setDevText(toDeveloperJson(d.def.name, d.current.body)); }
      setDraftOpen(false); setDraftText(''); setMessage('Drafted from your document. Read it through, edit it, then run the audit.'); refreshLists();
    },
    onError: (e) => setMessage(errText(e, 'Could not draft from that document')),
  });
  const decide = useMutation({
    mutationFn: (v: { decision: string; comment: string }) => api.post<DefDetail>(`/api/agent-defs/${defId}/decision`, v),
    onSuccess: (d) => { put(d); refreshLists(); }, onError: (e) => setMessage(errText(e, 'Could not record the decision')),
  });

  // ---- delegates and the palette
  const candidates = useQuery({
    queryKey: ['agent-candidates', projectId ?? 'org', defId], enabled: kind === 'agent',
    queryFn: async (): Promise<PaletteAgent[]> => {
      if (projectId) {
        const r = await api.get<{ mine: Card[]; open: Card[] }>(`/api/projects/${projectId}/agents?kind=agent`);
        return [...r.mine.filter((c) => c.publishedVersion), ...r.open].filter((c) => c.id !== defId).map((c) => ({ id: c.id, name: c.name }));
      }
      const r = await api.get<{ org: Card[] }>('/api/agent-library?kind=agent');
      return r.org.filter((c) => c.publishedVersion && !c.retired && c.id !== defId).map((c) => ({ id: c.id, name: c.name }));
    },
  });
  const nameOf = (id: string) => candidates.data?.find((c) => c.id === id)?.name ?? `${id.slice(0, 8)}…`;
  const free = (candidates.data ?? []).filter((c) => !(body?.children ?? []).some((x) => x.agent_id === c.id));

  const use = (k: PaletteKind, v: string) => {
    if (!editable || !body) return;
    if (k === 'input') { const n = uniqueName('input', body.inputs.map((i) => i.name)); edit((b) => ({ ...b, inputs: [...b.inputs, { name: n, type: v as InputDef['type'], source: 'brief', required: true }] })); setSpawn(n); setTab('io'); setMessage('Input added. A port appeared on the agent; rename it in the table.'); }
    else if (k === 'output') { const n = uniqueName('output', body.outputs.map((o) => o.name)); edit((b) => ({ ...b, outputs: [...b.outputs, { name: n, type: v as OutputDef['type'], artefact_type: 'REPORT', format: v === 'object' || v === 'list' ? 'JSON' : 'Markdown' }] })); setSpawn(n); setTab('io'); setMessage('Output added. Choose the artefact it becomes.'); }
    else if (k === 'agent') { if (!(body.children ?? []).some((c) => c.agent_id === v)) { edit((b) => ({ ...b, children: [...(b.children ?? []), { agent_id: v, when: 'always' }] })); setTab('deleg'); setMessage('Delegate added.'); } }
    else if (k === 'var') {
      const ta = promptRef.current; const pos = ta && mode === 'guided' ? ta.selectionStart : body.prompt.length;
      const r = insertAt(body.prompt, pos, `{${v}}`); edit((b) => ({ ...b, prompt: r.text })); setMode('guided');
      requestAnimationFrame(() => { promptRef.current?.focus(); promptRef.current?.setSelectionRange(r.cursor, r.cursor); });
    } else if (k === 'snippet') { edit((b) => ({ ...b, prompt: appendLine(b.prompt, v) })); setMode('guided'); }
  };
  useEffect(() => { if (spawn) { const t = setTimeout(() => setSpawn(null), 700); return () => clearTimeout(t); } return undefined; }, [spawn]);
  const onDrop = (e: React.DragEvent) => { e.preventDefault(); setOver(false); const p = decodePalette(e.dataTransfer.getData('text/plain')); if (p) use(p.kind, p.value); };
  const dropProps = editable ? { onDragOver: (e: React.DragEvent) => { e.preventDefault(); setOver(true); }, onDragLeave: () => setOver(false), onDrop, 'data-drop': 'build' } : {};

  const showInPrompt = (snippet: string) => {
    setMode('guided');
    requestAnimationFrame(() => { const ta = promptRef.current; if (!ta) return; const i = ta.value.indexOf(snippet); ta.focus(); if (i >= 0) { ta.setSelectionRange(i, i + snippet.length); } });
  };

  const status = cur?.status ?? 'draft';
  const auditFresh = Boolean(cur?.audit && !cur.audit.stale);
  const chip = statusChip(status, cur?.version);
  const parentNote = detail?.newerSource;
  const isSubmitter = cur?.submittedById != null && false; // the server decides who may withdraw; the button shows for editors
  void isSubmitter;
  const names = useMemo(() => (body?.inputs ?? []).map((i) => i.name), [body]);
  if (q.isLoading || !detail || !body) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  if (q.isError) return <div className="text-sm text-bared-700">Could not open this {kind}.</div>;
  const tabs = kind === 'agent' ? TABS_AGENT : TABS_SKILL;
  const hint = costHint(body.role, body.children?.length ?? 0);
  const readOnlyOpen = detail.def.scope === 'org' && !rights?.edit;

  return (
    <div data-testid="v2-builder" data-status={status}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <button type="button" onClick={onBack} className="text-xs font-semibold text-brand-700 hover:underline" data-testid="v2-builder-back">← Back</button>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <input aria-label="Name" data-testid="v2-builder-name" value={name} disabled={!editable} onChange={(e) => rename(e.target.value)} className="min-w-[14rem] rounded-lg border border-transparent bg-transparent px-1 font-display text-2xl font-bold text-navy hover:border-slate-300 focus:border-brand-500 focus:bg-white focus:outline-none disabled:hover:border-transparent" />
            <Pill tone={chip.tone}>{chip.label}</Pill><Pill>{MODEL_ROLES.find((r) => r.id === body.role)?.label}</Pill>
            {detail.def.scope === 'org' && <Pill tone="amber">{detail.def.open ? 'Open to projects' : 'Organisation'}</Pill>}
          </div>
          <div className="text-xs text-slate-500">{kind === 'agent' ? 'Agent' : 'Skill'} · v{cur?.version}{detail.def.source ? ` · copied from ${detail.def.source.name}${detail.def.source.version ? ` v${detail.def.source.version}` : ''}` : ''} · by {cur?.author || detail.def.createdBy}</div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Stepper status={status} fresh={auditFresh} />
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-slate-400" role="status" data-testid="v2-save-state">{saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : saveState === 'error' ? 'Not saved' : ''}</span>
            <button type="button" role="switch" aria-checked={mode === 'dev'} data-testid="v2-builder-mode" onClick={() => { if (mode === 'guided') setDevText(toDeveloperJson(name, body)); setMode(mode === 'guided' ? 'dev' : 'guided'); }} className={btn}>{mode === 'dev' ? 'Developer mode' : 'Guided mode'}</button>
            <button type="button" className={btn} onClick={() => setTestOpen((v) => !v)} data-testid="v2-builder-test-toggle">{testOpen ? 'Hide' : 'Show'} test</button>
            {rights?.edit && status === 'pending' && <button type="button" className={btn} data-testid="v2-builder-withdraw" onClick={() => withdraw.mutate()}>Withdraw to edit</button>}
            {rights?.edit && <button type="button" className={primary} data-testid="v2-builder-submit" disabled={!detail.canSubmit && !(auditFresh && auditSubmittable(cur?.audit))} onClick={() => submit.mutate()} title="Run the audit and clear every blocking finding first">Submit for approval</button>}
          </div>
        </div>
      </div>

      {message && <div className="mb-2 rounded-lg bg-slate-100 px-3 py-1.5 text-xs text-slate-700" role="status" data-testid="v2-builder-message">{message}</div>}
      {readOnlyOpen && <Note>An open library {kind}. It is read-only here. Copy it to this project to change it, from the Open library list.</Note>}
      {status === 'pending' && <Note tone="amber">Waiting for approval. It cannot be edited while it waits.</Note>}
      {status === 'rejected' && <Note tone="red">Changes requested: “{cur?.comment || 'See the audit report.'}” Edit and submit it again.</Note>}
      {status === 'published' && rights?.edit && <Note>This version is approved and cannot change. Editing it starts version {(cur?.version ?? 0) + 1}.</Note>}
      {parentNote && <Note>Copied from {detail.def.source?.name} v{detail.def.source?.version}. A newer v{parentNote.version} exists. Your copy stays as it is.</Note>}
      {cur?.audit?.stale && <Note tone="amber">Edited since the last audit. Run it again before submitting. <button type="button" className="ml-1 font-semibold underline" onClick={() => audit.mutate()}>Run audit</button></Note>}

      <div className="grid items-start gap-3 lg:grid-cols-[13rem_minmax(0,1fr)_26rem]">
        <aside className="rounded-xl border border-slate-300 bg-white p-3" aria-label="Outline">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Outline</div>
          <ul className="space-y-0.5 text-sm">
            <li className="flex items-center justify-between rounded-md bg-brand-50 px-2 py-1 font-semibold"><span className="truncate">{name}</span><span className={`h-2 w-2 rounded-full ${cur?.audit && !cur.audit.stale ? (cur.audit.summary.block ? 'bg-bared-500' : 'bg-emerald-500') : 'animate-pulse bg-amber-500'}`} title="Audit status" /></li>
            {(body.children ?? []).map((c) => <li key={c.agent_id} className="truncate pl-4 text-slate-600">↳ {nameOf(c.agent_id)}</li>)}
            {kind === 'agent' && <><li className="flex justify-between px-2 py-0.5 text-slate-600"><span>Inputs</span><span className="text-slate-400">{body.inputs.length}</span></li><li className="flex justify-between px-2 py-0.5 text-slate-600"><span>Outputs</span><span className="text-slate-400">{body.outputs.length}</span></li></>}
          </ul>
          {editable && <Palette kind={kind} variables={names} agents={kind === 'agent' ? free : []} onUse={use} />}
        </aside>

        <section className={`rounded-xl border bg-white p-4 ${over ? 'border-dashed border-brand-500 bg-brand-50' : 'border-slate-300'}`} data-testid="v2-builder-centre" {...dropProps}>
          {mode === 'dev' ? (
            <div>
              <div className="flex items-center justify-between"><h3 className="font-display text-base font-semibold text-navy">Definition</h3><Pill>Developer mode</Pill></div>
              <p className="mb-2 mt-1 text-xs text-slate-500">The same definition as the form, as JSON. Edits apply when you click outside the box.</p>
              <textarea aria-label="Definition JSON" data-testid="v2-builder-json" rows={22} spellCheck={false} disabled={!editable} value={devText} onChange={(e) => setDevText(e.target.value)}
                onBlur={() => { const r = fromDeveloperJson(devText, body); if (!r.ok) { setDevError(r.error); return; } setDevError(''); dirty.current = true; setName(r.name || name); setBody(r.body); setSaveState('idle'); }}
                className={`${field} font-mono text-xs leading-relaxed`} />
              {devError ? <div className="mt-1 text-xs text-bared-700" role="alert">{devError}</div> : <div className="mt-1 text-xs text-emerald-700">Valid JSON.</div>}
            </div>
          ) : (
            <div className="space-y-3">
              {editable && (
                <div className="rounded-lg border border-dashed border-slate-300 p-2" data-testid="v2-draft-box">
                  {!draftOpen ? (
                    <button type="button" className={btn} data-testid="v2-draft-open" onClick={() => setDraftOpen(true)}>✦ Draft from a document</button>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-xs text-slate-500">Paste a runbook, procedure or policy, or choose a text file. The model writes a first draft of the {kind}. It replaces the description, prompt, inputs and outputs below; you review and audit it like anything else.</p>
                      <textarea aria-label="Document" data-testid="v2-draft-text" rows={7} value={draftText} onChange={(e) => setDraftText(e.target.value)} className={`${field} font-mono text-xs`} placeholder="Runbook or procedure…" />
                      <div className="flex flex-wrap items-center gap-2">
                        <input type="file" accept=".txt,.md,.markdown,.yaml,.yml,.json,.sh,.csv,text/*" aria-label="Choose a file" data-testid="v2-draft-file" className="text-xs"
                          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void f.text().then(setDraftText); }} />
                        <button type="button" className={primary} disabled={draft.isPending || draftText.trim().length < 40} data-testid="v2-draft-run" onClick={() => draft.mutate(draftText)}>{draft.isPending ? 'Drafting…' : 'Draft it'}</button>
                        <button type="button" className={btn} onClick={() => setDraftOpen(false)}>Cancel</button>
                        <span className="text-[11px] text-slate-400">{draftText.length.toLocaleString()} of 20,000 characters</span>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <div><h3 className="font-display text-base font-semibold text-navy">Instructions</h3><p className="text-xs text-slate-500">Say what the {kind} does and what a good answer looks like. Use {'{name}'} to bring in an input.</p></div>
              <label className="block text-xs font-semibold text-slate-600">What it does <span className="font-normal text-slate-400">(the audit checks the {kind} against this)</span>
                <input data-testid="v2-builder-desc" className={`${field} mt-1 font-normal`} value={body.description} disabled={!editable} onChange={(e) => edit((b) => ({ ...b, description: e.target.value }))} /></label>
              <label className="block text-xs font-semibold text-slate-600">Prompt
                <textarea ref={promptRef} data-testid="v2-builder-prompt" rows={10} spellCheck={false} disabled={!editable} value={body.prompt} onChange={(e) => edit((b) => ({ ...b, prompt: e.target.value }))} className={`${field} mt-1 font-mono text-[12.5px] font-normal leading-relaxed`} /></label>
              <div>
                <div className="mb-1 text-xs text-slate-500">How the variables read</div>
                <div className="whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm leading-7" data-testid="v2-builder-pills">
                  {segments(body.prompt, names).map((s, i) => s.variable
                    ? <span key={i} className={`mx-0.5 inline-block rounded border px-1.5 font-mono text-xs ${s.declared ? 'border-brand-200 bg-brand-100 text-brand-700' : 'border-bared-500 bg-bared-200 text-bared-700'}`} title={s.declared ? 'An input' : 'No input has this name'}>{s.declared ? '' : '⚠ '}{s.variable}</span>
                    : <span key={i}>{s.text}</span>)}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500"><Pill>🔒 Always applied</Pill><Pill>Responsible-AI policy</Pill><Pill>Project rules</Pill><Pill>Stack decision</Pill><span>These cannot be removed.</span></div>
            </div>
          )}
        </section>

        <aside className={`rounded-xl border bg-white p-3 ${over ? 'border-dashed border-brand-500' : 'border-slate-300'}`} data-testid="v2-builder-inspector" {...dropProps}>
          <div role="tablist" aria-label="Inspector" className="mb-3 flex flex-wrap gap-0.5 border-b border-slate-200">
            {tabs.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} data-testid={`v2-btab-${id}`} onClick={() => setTab(id)} className={`-mb-px border-b-2 px-2 py-1.5 text-xs ${tab === id ? 'border-brand-600 font-semibold text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>{label}</button>)}
          </div>
          {tab === 'identity' && (
            <div className="space-y-2.5 text-xs">
              {kind === 'agent' && <label className="block font-semibold text-slate-600">Kind
                <select className={`${field} mt-1 font-normal`} disabled={!editable} value={body.stage_kind} onChange={(e) => edit((b) => ({ ...b, stage_kind: e.target.value as 'specialist' | 'stage' }))}>
                  <option value="specialist">Specialist inside a stage</option><option value="stage">Whole custom stage</option></select></label>}
              <label className="block font-semibold text-slate-600">Icon<select className={`${field} mt-1 font-normal`} disabled={!editable} value={body.icon} onChange={(e) => edit((b) => ({ ...b, icon: e.target.value }))}>{['shield', 'wand', 'document', 'plane', 'chart'].map((i) => <option key={i}>{i}</option>)}</select></label>
              <div className="rounded-lg bg-brand-50 p-2 text-slate-600">Type {'{refund_request}'} in the prompt and it appears as a pill. A name with no matching input shows a red pill.</div>
              <div className="flex flex-wrap gap-2 pt-2">
                {rights?.edit && detail.versions.every((v) => v.status !== 'published' && v.status !== 'superseded') && <button type="button" className={`${btn} text-bared-700`} onClick={() => remove.mutate()} data-testid="v2-builder-delete">Delete draft</button>}
                {rights?.edit && detail.versions.some((v) => v.status === 'published') && <button type="button" className={`${btn} text-bared-700`} onClick={() => retire.mutate()} data-testid="v2-builder-retire">Retire</button>}
                {detail.def.scope === 'org' && rights?.edit && detail.versions.some((v) => v.status === 'published') && (
                  <button type="button" className={btn} data-testid="v2-builder-open" onClick={() => toggleOpen.mutate(!detail.def.open)}>{detail.def.open ? 'Stop sharing with projects' : 'Open to projects'}</button>)}
              </div>
              {detail.def.scope === 'org' && rights?.approve && status === 'pending' && <OrgDecision onDecide={(v) => decide.mutate(v)} busy={decide.isPending} />}
            </div>
          )}
          {tab === 'engine' && (
            <div className="space-y-2.5 text-xs">
              <label className="block font-semibold text-slate-600">Model role
                <select data-testid="v2-engine-role" className={`${field} mt-1 font-normal`} disabled={!editable} value={body.role} onChange={(e) => edit((b) => ({ ...b, role: e.target.value as ModelRole }))}>{MODEL_ROLES.map((r) => <option key={r.id} value={r.id}>{r.label} · {r.hint}</option>)}</select></label>
              <label className="block font-semibold text-slate-600">Model <span className="font-normal text-slate-400">(approved by your administrator)</span>
                <select className={`${field} mt-1 font-normal`} disabled={!editable} value={body.model ?? ''} onChange={(e) => edit((b) => ({ ...b, model: e.target.value || null }))}><option value="">Automatic (administrator default)</option>{MODELS.map((m) => <option key={m}>{m}</option>)}</select></label>
              <label className="block font-semibold text-slate-600">If it fails, use
                <select className={`${field} mt-1 font-normal`} disabled={!editable} value={body.fallback ?? ''} onChange={(e) => edit((b) => ({ ...b, fallback: e.target.value || null }))}><option value="">None</option>{MODELS.map((m) => <option key={m}>{m}</option>)}</select></label>
              <label className="block font-semibold text-slate-600">Temperature <b className="text-slate-900">{body.temperature.toFixed(1)}</b>
                <input type="range" min={0} max={1} step={0.1} disabled={!editable} value={body.temperature} onChange={(e) => edit((b) => ({ ...b, temperature: Number(e.target.value) }))} className="mt-1 w-full" /></label>
              <CapField value={body.budget_tokens ?? null} editable={editable} onChange={(v) => edit((b) => ({ ...b, budget_tokens: v }))} />
              <div className="rounded-lg bg-brand-50 p-2 text-slate-600">About ${hint.dollars} per run, about {hint.seconds} s. The same prompt and schema go to the fallback if the first model fails.</div>
              {rights?.edit && <UsageCard defId={defId} />}
            </div>
          )}
          {tab === 'io' && kind === 'agent' && <IoTab body={body} editable={editable} spawn={spawn} edit={edit} name={name} />}
          {tab === 'deleg' && kind === 'agent' && (
            <div className="space-y-2.5 text-xs">
              <p className="text-slate-500">This agent can hand parts of its work to other agents. Each one sees only what it needs.</p>
              <div className="font-semibold text-slate-800">{name}</div>
              <ul className="space-y-2 border-l-2 border-slate-200 pl-3" data-testid="v2-deleg-list">
                {(body.children ?? []).map((c, k) => (
                  <li key={c.agent_id} className="space-y-1" data-testid="v2-delegate">
                    <div className="flex items-center justify-between"><b>{nameOf(c.agent_id)}</b><button type="button" aria-label="Remove delegate" disabled={!editable} className="text-slate-400 hover:text-bared-600" onClick={() => edit((b) => ({ ...b, children: (b.children ?? []).filter((_, i) => i !== k) }))}>×</button></div>
                    <label className="block text-slate-500">Run when<input className={`${field} mt-0.5`} disabled={!editable} value={c.when} onChange={(e) => edit((b) => ({ ...b, children: (b.children ?? []).map((x, i) => (i === k ? { ...x, when: e.target.value } : x)) }))} placeholder="always, or for example len(history) > 3" /></label>
                  </li>
                ))}
                {!(body.children ?? []).length && <li className="text-slate-400">No delegates yet.</li>}
              </ul>
              {editable && free.length > 0 && <div className="flex gap-2"><select id="add-delegate" aria-label="Add a delegate" className={`${field} flex-1`}>{free.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
                <button type="button" className={btn} data-testid="v2-add-delegate" onClick={() => { const v = (document.getElementById('add-delegate') as HTMLSelectElement | null)?.value; if (v) use('agent', v); }}>+ Delegate</button></div>}
              <div className="space-y-1.5">{delegationMeters(body.children?.length ?? 0).map((m) => (
                <div key={m.label}><div className="flex justify-between"><span>{m.label}</span><b>{m.value} of {m.max}</b></div><div className="h-2 overflow-hidden rounded-full bg-slate-200"><div className={`h-full ${m.value >= m.max ? 'bg-bared-500' : 'bg-brand-500'}`} style={{ width: `${Math.min(100, (m.value / m.max) * 100)}%` }} /></div></div>))}</div>
              <p className="text-slate-400">A condition can use the inputs, for example <code>len(history) &gt; 3</code>. Only reading and comparing is allowed; no code runs.</p>
            </div>
          )}
          {tab === 'tools' && (
            <div className="space-y-2 text-xs"><p className="text-slate-500">Tools let an agent act on other systems. They arrive in a later release and need extra approval.</p>
              {[['Jira · create issue', 'write', 'Held until the stage is approved'], ['Git · read file', 'read', 'Limited to this project repository'], ['Booking API webhook', 'external', 'Needs super-admin approval and an allowed address']].map(([n, k, d]) => (
                <div key={n} className="rounded-lg border border-slate-200 p-2"><div className="flex justify-between"><b>{n}</b><Pill tone={k === 'read' ? 'green' : 'amber'}>{k}</Pill></div><div className="text-slate-500">{d}</div><label className="mt-1 flex items-center gap-1.5 text-slate-500"><input type="checkbox" disabled /> Later release</label></div>))}
            </div>
          )}
          {tab === 'access' && kind === 'skill' && (
            <div className="space-y-3 text-xs">
              <p className="text-slate-500">Who can run this skill, and from which stages. The project can narrow this per stage.</p>
              <div><b>Roles</b><div className="mt-1 flex flex-wrap gap-1.5">{PHASE_ROLES.map((r) => <Chip key={r} on={(body.roles ?? []).includes(r)} disabled={!editable} onClick={() => edit((b) => ({ ...b, roles: toggle(b.roles ?? [], r) }))}>{r}</Chip>)}</div></div>
              <div><b>Stages</b><div className="mt-1 flex flex-wrap gap-1.5">{[1, 2, 3, 4, 5, 6, 7].map((s) => <Chip key={s} on={(body.stages ?? []).includes(s)} disabled={!editable} onClick={() => edit((b) => ({ ...b, stages: toggle(b.stages ?? [], s) }))}>{STAGE_NAMES[s]}</Chip>)}</div></div>
            </div>
          )}
          {tab === 'audit' && <AuditPanel version={cur} running={audit.isPending} canEdit={Boolean(rights?.edit && (status === 'draft' || status === 'rejected' || status === 'published'))} fixing={fix.isPending}
            onRun={() => audit.mutate()} onFix={(id) => fix.mutate(id)} onAck={(v) => ack.mutate(v)} onShow={showInPrompt} />}
        </aside>
      </div>
      {detail.def.scope === 'project' && rights?.approve && status === 'pending' && (
        <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900" data-testid="v2-builder-approve-hint">Waiting for approval. Review it under Approvals; it needs someone other than <b>{cur?.submittedBy}</b>.</div>
      )}
      {testOpen && <TestDrawer defId={defId} detail={detail} body={body} editable={Boolean(rights?.edit)} />}
    </div>
  );
}

const toggle = <T,>(list: T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

function Chip({ on, disabled, onClick, children }: { on: boolean; disabled?: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" disabled={disabled} aria-pressed={on} onClick={onClick} className={`rounded-full px-2.5 py-0.5 font-medium ${on ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'} disabled:opacity-60`}>{children}</button>;
}

function Note({ children, tone = 'brand' }: { children: React.ReactNode; tone?: 'brand' | 'amber' | 'red' }) {
  const cls = { brand: 'bg-brand-50 text-slate-800', amber: 'bg-amber-50 text-amber-900', red: 'bg-bared-200 text-bared-700' }[tone];
  return <div className={`mb-2 rounded-lg px-3 py-2 text-xs ${cls}`}>{children}</div>;
}

function Stepper({ status, fresh }: { status: Parameters<typeof stepIndex>[0]; fresh: boolean }) {
  const at = stepIndex(status, fresh);
  return <ol className="flex flex-wrap gap-1.5" aria-label="Where it is">{STEPS.map((s, i) => <li key={s} aria-current={i === at ? 'step' : undefined} className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${i < at ? 'bg-emerald-100 text-emerald-700' : i === at ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500'}`}>{s}</li>)}</ol>;
}

function OrgDecision({ onDecide, busy }: { onDecide: (v: { decision: string; comment: string }) => void; busy: boolean }) {
  const [c, setC] = useState('');
  return (
    <div className="space-y-1.5 border-t border-slate-200 pt-2">
      <b>Decision</b><input aria-label="Comment" className={field} value={c} onChange={(e) => setC(e.target.value)} placeholder="Comment (needed to ask for changes)" />
      <div className="flex gap-2"><button type="button" disabled={busy} className={primary} data-testid="v2-org-approve" onClick={() => onDecide({ decision: 'approve', comment: c })}>Approve and publish</button>
        <button type="button" disabled={busy} className={btn} onClick={() => onDecide({ decision: 'changes', comment: c })}>Request changes</button></div>
    </div>
  );
}

function IoTab({ body, editable, spawn, edit, name }: { body: AgentBody; editable: boolean; spawn: string | null; edit: (fn: (b: AgentBody) => AgentBody) => void; name: string }) {
  const setIn = (k: number, patch: Partial<InputDef>) => edit((b) => ({ ...b, inputs: b.inputs.map((x, i) => (i === k ? { ...x, ...patch } : x)) }));
  const setOut = (k: number, patch: Partial<OutputDef>) => edit((b) => ({ ...b, outputs: b.outputs.map((x, i) => (i === k ? { ...x, ...patch } : x)) }));
  const cell = 'w-full rounded border border-slate-300 bg-white px-1 py-0.5 text-xs disabled:bg-slate-100';
  return (
    <div className="space-y-3 text-xs">
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2" data-testid="v2-ports" aria-label="Ports">
        <div className="flex flex-col items-start gap-1">{body.inputs.map((i) => <span key={i.name} className={`rounded-full border border-slate-200 bg-brand-100 px-2 py-0.5 font-mono text-brand-700 ${spawn === i.name ? 'animate-bounce' : ''}`}>● {i.name}</span>)}{!body.inputs.length && <span className="text-slate-400">No inputs</span>}</div>
        <div className="rounded-xl border-2 border-brand-500 px-2 py-1 text-center font-semibold"><span className="block max-w-[6rem] truncate">{name}</span><span className="rounded-full border border-slate-200 bg-slate-100 px-1.5 text-[10px] font-normal">✦ {MODEL_ROLES.find((r) => r.id === body.role)?.label}</span></div>
        <div className="flex flex-col items-end gap-1">{body.outputs.map((o) => <span key={o.name} className={`rounded-full border border-slate-200 bg-emerald-100 px-2 py-0.5 font-mono text-emerald-700 ${spawn === o.name ? 'animate-bounce' : ''}`}>{o.name} ●</span>)}{!body.outputs.length && <span className="text-slate-400">No outputs</span>}</div>
      </div>
      <div><div className="mb-1 flex items-center justify-between"><b>Inputs</b><button type="button" className={btn} disabled={!editable} data-testid="v2-add-input" onClick={() => edit((b) => ({ ...b, inputs: [...b.inputs, { name: uniqueName('input', b.inputs.map((i) => i.name)), type: 'string', source: 'brief', required: true }] }))}>+ Add input</button></div>
        <div className="overflow-x-auto"><table className="w-full"><thead><tr className="text-left text-[11px] text-slate-500"><th className="pr-1">Name</th><th className="pr-1">Type</th><th className="pr-1">Comes from</th><th /></tr></thead><tbody>
          {body.inputs.map((i, k) => <tr key={k} data-testid="v2-input-row"><td className="pr-1"><input aria-label="Input name" className={cell} disabled={!editable} value={i.name} onChange={(e) => setIn(k, { name: e.target.value })} /></td>
            <td className="pr-1"><select aria-label="Type" className={cell} disabled={!editable} value={i.type} onChange={(e) => setIn(k, { type: e.target.value as InputDef['type'] })}>{VALUE_TYPES.map((t) => <option key={t}>{t}</option>)}</select></td>
            <td className="pr-1"><select aria-label="Comes from" className={cell} disabled={!editable} value={i.source} onChange={(e) => setIn(k, { source: e.target.value })}>{SOURCE_OPTIONS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</select></td>
            <td><button type="button" aria-label="Remove input" disabled={!editable} className="text-slate-400 hover:text-bared-600" onClick={() => edit((b) => ({ ...b, inputs: b.inputs.filter((_, j) => j !== k) }))}>×</button></td></tr>)}
        </tbody></table></div></div>
      <div><div className="mb-1 flex items-center justify-between"><b>Outputs</b><button type="button" className={btn} disabled={!editable} data-testid="v2-add-output" onClick={() => edit((b) => ({ ...b, outputs: [...b.outputs, { name: uniqueName('output', b.outputs.map((o) => o.name)), type: 'string', artefact_type: 'REPORT', format: 'Markdown' }] }))}>+ Add output</button></div>
        <div className="overflow-x-auto"><table className="w-full"><thead><tr className="text-left text-[11px] text-slate-500"><th className="pr-1">Name</th><th className="pr-1">Type</th><th className="pr-1">Becomes</th><th className="pr-1">Format</th><th /></tr></thead><tbody>
          {body.outputs.map((o, k) => <tr key={k} data-testid="v2-output-row"><td className="pr-1"><input aria-label="Output name" className={cell} disabled={!editable} value={o.name} onChange={(e) => setOut(k, { name: e.target.value })} /></td>
            <td className="pr-1"><select aria-label="Type" className={cell} disabled={!editable} value={o.type} onChange={(e) => setOut(k, { type: e.target.value as OutputDef['type'] })}>{VALUE_TYPES.map((t) => <option key={t}>{t}</option>)}</select></td>
            <td className="pr-1"><input aria-label="Artefact type" list="artefact-types" className={cell} disabled={!editable} value={o.artefact_type} onChange={(e) => setOut(k, { artefact_type: e.target.value.toUpperCase() })} /></td>
            <td className="pr-1"><select aria-label="Format" className={cell} disabled={!editable} value={o.format} onChange={(e) => setOut(k, { format: e.target.value as OutputDef['format'] })}>{FORMATS.map((f) => <option key={f}>{f}</option>)}</select></td>
            <td><button type="button" aria-label="Remove output" disabled={!editable} className="text-slate-400 hover:text-bared-600" onClick={() => edit((b) => ({ ...b, outputs: b.outputs.filter((_, j) => j !== k) }))}>×</button></td></tr>)}
        </tbody></table><datalist id="artefact-types">{ARTEFACT_TYPES.map((t) => <option key={t} value={t} />)}</datalist></div>
        <p className="mt-1 text-slate-400">Every output becomes an artefact with a type and format, so later stages, export and quality scoring can use it.</p></div>
    </div>
  );
}

function TestDrawer({ defId, detail, body, editable }: { defId: string; detail: DefDetail; body: AgentBody; editable: boolean }) {
  const qc = useQueryClient();
  const sample = JSON.stringify(sampleInputs(body.inputs), null, 2);
  const [text, setTextRaw] = useState(sample);
  const touched = useRef(false);
  const setText = (v: string) => { touched.current = true; setTextRaw(v); };
  // until the person types their own, the pinned input follows the declared inputs
  useEffect(() => { if (!touched.current) setTextRaw(sample); }, [sample]);
  const [error, setError] = useState('');
  const [res, setRes] = useState<RunResultView | null>(null);
  const [caseName, setCaseName] = useState('');
  const run = useMutation({
    mutationFn: async () => { let inputs: Record<string, unknown>; try { inputs = JSON.parse(text) as Record<string, unknown>; } catch { throw new Error('The pinned input is not valid JSON'); } return api.post<RunResultView>(`/api/agent-defs/${defId}/test`, { inputs }); },
    onSuccess: (r) => { setRes(r); setError(''); }, onError: (e) => { setRes(null); setError(errText(e, 'The run failed')); },
  });
  const saveCase = useMutation({
    mutationFn: () => api.post(`/api/agent-defs/${defId}/cases`, { name: caseName || `Case ${detail.cases.length + 1}`, inputs: JSON.parse(text) as Record<string, unknown> }),
    onSuccess: () => { setCaseName(''); void qc.invalidateQueries({ queryKey: ['agent-def', defId] }); }, onError: (e) => setError(errText(e, 'Could not save the case')),
  });
  const [cmp, setCmp] = useState<CompareResult | null>(null);
  const compare = useMutation({
    mutationFn: () => api.post<CompareResult>(`/api/agent-defs/${defId}/compare`, {}),
    onSuccess: (r) => { setCmp(r); setError(''); }, onError: (e) => { setCmp(null); setError(errText(e, 'The comparison failed')); },
  });
  const delCase = useMutation({ mutationFn: (id: string) => api.del(`/api/agent-defs/${defId}/cases/${id}`), onSuccess: () => void qc.invalidateQueries({ queryKey: ['agent-def', defId] }) });
  const pill = (v: unknown) => JSON.stringify(v, null, 2);
  return (
    <section className="mt-3 rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-test-drawer" aria-label="Test">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-display text-base font-semibold text-navy">Test</h3>
        <div className="flex flex-wrap items-center gap-2">
          {detail.cases.length > 0 && <select aria-label="Saved cases" className="rounded-lg border border-slate-300 px-2 py-1 text-xs" value="" onChange={(e) => { const c = detail.cases.find((x) => x.id === e.target.value); if (c) setText(JSON.stringify(c.inputs, null, 2)); }}><option value="">Saved cases ({detail.cases.length})</option>{detail.cases.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}
          <button type="button" className={btn} onClick={() => { touched.current = false; setTextRaw(sample); }} data-testid="v2-test-sample">Generate sample</button>
          <button type="button" className={btn} disabled={!editable || compare.isPending || detail.versions.length < 2} data-testid="v2-compare" title={detail.versions.length < 2 ? 'Edit an approved version to start the next one, then compare' : 'Run the earlier version and this one on your saved cases and have a judge score both'} onClick={() => compare.mutate()}>{compare.isPending ? 'Comparing…' : `Compare with v${Math.max(1, (detail.current?.version ?? 2) - 1)}`}</button>
          <button type="button" className={primary} disabled={!editable || run.isPending} onClick={() => run.mutate()} data-testid="v2-test-run">{run.isPending ? 'Running…' : `▶ Run ${detail.def.kind}`}</button>
        </div>
      </div>
      <label className="block text-xs font-semibold text-slate-600">Pinned sample input
        <textarea aria-label="Pinned sample input" data-testid="v2-test-input" rows={4} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} className={`${field} mt-1 font-mono text-xs font-normal`} /></label>
      {error && <div className="mt-2 rounded-lg bg-bared-200 px-3 py-1.5 text-xs text-bared-700" role="alert" data-testid="v2-test-error">{error}</div>}
      {!res && !error && <p className="mt-2 text-xs text-slate-500">Pin sample inputs, then run this {detail.def.kind} on its own. Nothing else in the stage runs and nothing is saved.</p>}
      {res && (
        <div className="mt-3 space-y-2" data-testid="v2-test-result">
          <div className="grid gap-3 md:grid-cols-2">
            <div><div className="mb-1 text-xs text-slate-500">Input JSON</div><pre className="max-h-56 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-xs">{text}</pre></div>
            <div><div className="mb-1 text-xs text-slate-500">Output JSON</div><pre className="max-h-56 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-xs" data-testid="v2-test-output">{pill(res.outputs)}</pre></div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <Pill tone="green">Matches the declared outputs</Pill>{res.mock && <Pill tone="amber">Offline model</Pill>}<span>{res.totalTokens.toLocaleString()} tokens · {res.provider}/{res.model}</span>
            {res.tree.children.length > 0 && <span>delegated to {res.tree.children.map((c) => c.name).join(', ')}</span>}
            <input aria-label="Case name" placeholder="Name this case" value={caseName} onChange={(e) => setCaseName(e.target.value)} className="ml-auto w-36 rounded-lg border border-slate-300 px-2 py-0.5" />
            <button type="button" className={btn} disabled={saveCase.isPending} onClick={() => saveCase.mutate()} data-testid="v2-test-save">Save as test case</button>
          </div>
          {res.warnings.map((w) => <div key={w} className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">{w}</div>)}
          {res.tree.skipped.length > 0 && <div className="text-xs text-slate-500">Not run: {res.tree.skipped.map((s) => `${s.agent.slice(0, 8)} (${s.why})`).join('; ')}</div>}
          <div className="grid gap-3 md:grid-cols-2">
            <details><summary className="cursor-pointer text-xs font-semibold text-slate-600">Prompt sent</summary><pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900 p-2 text-[11px] text-slate-100">{`SYSTEM\n${res.systemPrompt ?? ''}\n\nUSER\n${res.userPrompt ?? ''}`}</pre></details>
            <details><summary className="cursor-pointer text-xs font-semibold text-slate-600">Context used</summary><ul className="mt-1 space-y-0.5 text-xs text-slate-600">{res.context.map((c, i) => <li key={i}>{c.label} · {c.chars.toLocaleString()} characters</li>)}</ul></details>
          </div>
        </div>
      )}
      {cmp && <CompareView r={cmp} />}
      {detail.cases.length > 0 && <ul className="mt-3 flex flex-wrap gap-1.5 text-xs">{detail.cases.map((c) => <li key={c.id} className="flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5">{c.name}<button type="button" aria-label={`Delete ${c.name}`} className="text-slate-400 hover:text-bared-600" onClick={() => delCase.mutate(c.id)}>×</button></li>)}</ul>}
    </section>
  );
}

function CapField({ value, editable, onChange }: { value: number | null; editable: boolean; onChange: (v: number | null) => void }) {
  const [text, setText] = useState(value ? String(value) : '');
  const [err, setErr] = useState('');
  useEffect(() => { setText(value ? String(value) : ''); }, [value]);
  return (
    <label className="block font-semibold text-slate-600">Token cap per run <span className="font-normal text-slate-400">(optional)</span>
      <input data-testid="v2-engine-cap" inputMode="numeric" className={`${field} mt-1 font-normal`} disabled={!editable} value={text} placeholder={`Platform limit, ${TOKEN_CAP.max.toLocaleString()}`}
        onChange={(e) => { setText(e.target.value); const r = parseCap(e.target.value); if (r.ok) { setErr(''); onChange(r.value); } else setErr(r.error); }} />
      {err ? <span className="mt-0.5 block font-normal text-bared-700" role="alert" data-testid="v2-engine-cap-error">{err}</span>
        : <span className="mt-0.5 block font-normal text-slate-400">The most this agent, with the agents it hands work to, may spend in one run. It is checked between agents, so one long answer can finish.</span>}
    </label>
  );
}

function UsageCard({ defId }: { defId: string }) {
  const q = useQuery({ queryKey: ['agent-usage', defId], queryFn: () => api.get<DefUsage>(`/api/agent-defs/${defId}/usage`), retry: false });
  const u = q.data;
  if (!u) return null;
  const max = Math.max(1, ...u.daily.map((d) => d.tokens));
  return (
    <div className="rounded-lg border border-slate-200 p-2" data-testid="v2-def-usage">
      <div className="flex items-center justify-between"><b>Last {u.days} days</b><span className="text-slate-500">{u.runs} run{u.runs === 1 ? '' : 's'} · {tokensLabel(u.tokens)} tokens</span></div>
      {u.daily.length > 0 ? <div className="mt-1 flex h-10 items-end gap-0.5" aria-label="Tokens per day">{u.daily.map((d) => <span key={d.day} title={`${d.day}: ${d.tokens.toLocaleString()} tokens`} className="w-2 rounded-sm bg-brand-400" style={{ height: `${Math.max(8, (d.tokens / max) * 100)}%` }} />)}</div>
        : <div className="mt-1 text-slate-400">Not run yet.</div>}
      {Object.keys(u.bySource).length > 0 && <div className="mt-1 text-slate-500">{Object.entries(u.bySource).map(([k, v]) => `${k} ${tokensLabel(v)}`).join(' · ')}</div>}
    </div>
  );
}

function CompareView({ r }: { r: CompareResult }) {
  const sc = (n: number | null) => (n === null ? '–' : n.toFixed(1));
  return (
    <div className="mt-3 space-y-2 rounded-lg border border-slate-200 p-3" data-testid="v2-compare-result" data-verdict={r.summary.verdict}>
      <div className="flex flex-wrap items-center gap-2"><Pill tone={VERDICT_TONE[r.summary.verdict]}>{r.summary.verdict === 'better' ? 'Better' : r.summary.verdict === 'worse' ? 'Worse' : r.summary.verdict === 'same' ? 'About the same' : 'Not scored'}</Pill>
        <span className="text-sm text-slate-700" data-testid="v2-compare-message">{r.summary.message}</span></div>
      <div className="text-xs text-slate-500">v{r.b} wins {r.summary.winsB}, v{r.a} wins {r.summary.winsA}, ties {r.summary.ties}{r.usedSample ? ' · compared on a made-up sample because no test cases are saved' : ''} · {(r.tokens.promptTokens + r.tokens.completionTokens).toLocaleString()} tokens</div>
      <ul className="space-y-1.5">{r.cases.map((c, i) => (
        <li key={i} className="rounded border border-slate-200 p-2 text-xs" data-testid="v2-compare-case">
          <div className="flex flex-wrap items-center justify-between gap-2"><b>{c.name}</b><span>v{r.a} {sc(c.scoreA)} · v{r.b} {sc(c.scoreB)}{c.winner && c.winner !== 'tie' ? ` · v${c.winner === 'a' ? r.a : r.b} is better` : c.winner === 'tie' ? ' · tie' : ''}</span></div>
          {c.reason && <div className="text-slate-500">{c.reason}</div>}
          {(c.errorA || c.errorB) && <div className="text-bared-700">{c.errorA && `v${r.a}: ${c.errorA} `}{c.errorB && `v${r.b}: ${c.errorB}`}</div>}
          <details className="mt-1"><summary className="cursor-pointer text-slate-500">Both answers</summary>
            <div className="mt-1 grid gap-2 md:grid-cols-2"><pre className="max-h-40 overflow-auto rounded bg-slate-50 p-1.5">{JSON.stringify(c.a, null, 2)}</pre><pre className="max-h-40 overflow-auto rounded bg-slate-50 p-1.5">{JSON.stringify(c.b, null, 2)}</pre></div></details>
        </li>))}</ul>
    </div>
  );
}
