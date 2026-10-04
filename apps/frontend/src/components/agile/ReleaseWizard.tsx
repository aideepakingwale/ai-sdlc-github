import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { agileApi, type CarrySuggestions, type Question, type ReleaseAnswers, type ReleasePreview } from '../../api/agile';
import { ApiError } from '../../api/client';
import { carryBudgetUsed, KIND_LABEL, stepProblem, wizardSteps, WIZARD_LABEL, type WizardStepId } from '../../lib/agileView';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { Icon } from '../ui/Icon';
import { errorText, useAgileMutation } from './hooks';

const input = 'mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal';
const STAGE_HELP: Record<string, string> = {
  inherit: 'Refine, plan, build, review and retro — the same stages as the rest of the project.',
  lean: 'Skips the planning ceremony: the sprint is live as soon as it starts.',
  hotfix: 'Build, review, then release. For small, urgent changes.',
  'build-only': 'Build and review only; the release closes as soon as its sprint does.',
};

/** Starts a release. Fixed questions with fixed answers, a preview of exactly what will happen, then one confirmation. */
export default function ReleaseWizard({ projectId, focusRelease, onClose, onDone }: {
  projectId: string; focusRelease?: string | null; onClose: () => void; onDone: (releaseId: string) => void;
}) {
  const q = useQuery({ queryKey: ['agile-questions', projectId], queryFn: () => agileApi.questions(projectId) });
  const [a, setA] = useState<ReleaseAnswers | null>(null);
  const [step, setStep] = useState(0);
  const [preview, setPreview] = useState<ReleasePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [resume, setResume] = useState<string | null>(null);
  const dialog = useRef<HTMLDivElement>(null);

  // Defaults come from the server (project policy); the person's answers sit on top of them.
  useEffect(() => {
    if (q.data && !a) {
      const d = q.data.defaults as ReleaseAnswers;
      const forkable = q.data.releases.find((r) => r.id === focusRelease && r.canFork);
      setA({ startFrom: 'blank', unfinishedItems: 'none', stagePreset: 'inherit', intakeRule: 'pool', usePool: true, firstSprint: 'later',
        carry: [], items: [], epics: [], doneItems: 'context', ...d, ...(forkable ? { sourceRelease: forkable.id } : {}) });
    }
  }, [q.data, a, focusRelease]);
  useEffect(() => { dialog.current?.focus(); }, []);

  const start = useAgileMutation(projectId, (answers: ReleaseAnswers) => agileApi.startRelease(projectId, { ...answers, ...(resume ? { resumeReleaseId: resume } : {}) }));
  if (q.isError) return <Shell onClose={onClose} title="Start a release"><Callout tone="error" title="Could not load the questions">{errorText(q.error)}</Callout></Shell>;
  if (!q.data || !a) return <Shell onClose={onClose} title="Start a release"><div className="p-6 text-sm text-slate-400">Loading…</div></Shell>;

  const locked = new Set(q.data.locked);
  const canFork = (id: string): boolean => q.data.releases.find((r) => r.id === id)?.canFork ?? false;
  const steps = wizardSteps(a);
  const current: WizardStepId = steps[Math.min(step, steps.length - 1)]!;
  const problem = stepProblem(current, a, canFork);
  const set = (patch: Partial<ReleaseAnswers>): void => { setA({ ...a, ...patch }); setPreview(null); };
  const question = (id: string): Question | undefined => q.data.questions.find((x) => x.id === id);
  const goReview = async (): Promise<void> => {
    setStep(steps.length - 1); setPreviewing(true);
    try { setPreview(await agileApi.previewRelease(projectId, a)); } catch (e) { setPreview({ valid: false, errors: [errorText(e)], warnings: [], steps: [], summary: {} }); } finally { setPreviewing(false); }
  };
  const next = (): void => { if (steps[step + 1] === 'review') void goReview(); else setStep(step + 1); };
  const failed = start.error instanceof ApiError && start.error.details?.resumable ? start.error : null;

  return (
    <Shell onClose={onClose} title="Start a release" dialogRef={dialog}>
      <ol className="flex flex-wrap gap-1.5 border-b border-slate-200 px-5 py-3" aria-label="Steps">
        {steps.map((s, i) => (
          <li key={s} aria-current={i === step ? 'step' : undefined}
            className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${i === step ? 'bg-brand-600 text-white' : i < step ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-500'}`}>
            {i + 1}. {WIZARD_LABEL[s]}</li>))}
      </ol>
      <div className="min-h-[18rem] space-y-4 overflow-auto px-5 py-4">
        {current === 'basics' && (<>
          <label className="block text-xs font-semibold text-slate-600">Release name
            <input autoFocus value={a.name ?? ''} maxLength={120} onChange={(e) => set({ name: e.target.value })} className={input} placeholder="e.g. Mobile checkout" /></label>
          <label className="block text-xs font-semibold text-slate-600">{question('goal')?.title}
            <textarea value={a.goal ?? ''} maxLength={400} rows={3} onChange={(e) => set({ goal: e.target.value })} className={input} />
            <span className="mt-1 block text-[11px] font-normal text-slate-500">{question('goal')?.help}</span></label>
        </>)}

        {current === 'source' && (<>
          <Choice legend="Start from" value={a.startFrom ?? 'blank'} options={question('startFrom')?.options ?? []} disabled={locked.has('startFrom')}
            onChange={(v) => set({ startFrom: v as 'blank' | 'fork' })} />
          {a.startFrom === 'fork' && (
            <label className="block text-xs font-semibold text-slate-600">Fork from
              <select value={a.sourceRelease ?? ''} onChange={(e) => set({ sourceRelease: e.target.value, carry: [] })} className={input}>
                <option value="">Choose a release…</option>
                {q.data.releases.map((r) => <option key={r.id} value={r.id} disabled={!r.canFork}>{r.code} · {r.name}{r.canFork ? '' : ' (no closed sprint yet)'}</option>)}
              </select>
              <span className="mt-1 block text-[11px] font-normal text-slate-500">The new release starts empty. It is connected to this one for reference, but nothing is merged back.</span>
            </label>)}
        </>)}

        {current === 'carry' && a.sourceRelease && <CarryStep projectId={projectId} a={a} set={set} />}

        {current === 'items' && a.sourceRelease && <ItemsStep projectId={projectId} a={a} set={set} locked={locked} />}

        {current === 'stages' && (
          <Choice legend="Stages this release runs" value={a.stagePreset ?? 'inherit'} disabled={locked.has('stagePreset')}
            options={(question('stagePreset')?.options ?? []).filter((o) => o.id !== 'custom').map((o) => ({ ...o, hint: STAGE_HELP[o.id] }))}
            onChange={(v) => set({ stagePreset: v })} />)}

        {current === 'intake' && <IntakeStep projectId={projectId} a={a} set={set} locked={locked} question={question} />}

        {current === 'review' && (
          <div className="space-y-3" aria-live="polite">
            {previewing && <div className="text-sm text-slate-400">Checking…</div>}
            {preview && !preview.valid && <Callout tone="error" title="Fix this before starting">{preview.errors.join(' · ')}</Callout>}
            {preview?.valid && (<>
              <p className="text-sm font-semibold text-slate-700">Starting this release will:</p>
              <ul className="list-inside list-disc space-y-1 text-sm text-slate-700">{preview.steps.map((s) => <li key={s}>{s}</li>)}</ul>
              {preview.warnings.length > 0 && <Callout tone="warning" compact title="Worth a look">{preview.warnings.join(' · ')}</Callout>}
            </>)}
            {failed && (
              <Callout tone="error" title="Starting stopped part-way">{errorText(failed)} The release was created; you can resume where it stopped.
              </Callout>)}
            {start.isError && !failed && <Callout tone="error" compact title="That did not work">{errorText(start.error)}</Callout>}
          </div>)}
      </div>
      <div className="flex items-center gap-2 border-t border-slate-200 px-5 py-3">
        {problem && <span className="text-xs text-amber-800" role="status">{problem}</span>}
        <div className="ml-auto flex gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {step > 0 && <Button onClick={() => setStep(step - 1)}>Back</Button>}
          {current !== 'review'
            ? <Button variant="primary" disabled={Boolean(problem)} onClick={next} icon="arrow-right">Next</Button>
            : <Button variant="primary" icon="play" loading={start.isPending} disabled={!preview?.valid || previewing}
              onClick={() => start.mutate(a, {
                onSuccess: (r) => onDone(r.release.id),
                onError: (e) => { if (e instanceof ApiError && typeof e.details?.releaseId === 'string') setResume(e.details.releaseId); },
              })}>{resume ? 'Resume' : 'Start release'}</Button>}
        </div>
      </div>
    </Shell>
  );
}

function Shell({ title, children, onClose, dialogRef }: { title: string; children: React.ReactNode; onClose: () => void; dialogRef?: React.RefObject<HTMLDivElement> }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="release-wizard-title"
        onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl outline-none">
        <div className="flex items-center gap-2 border-b border-slate-200 px-5 py-3">
          <Icon name="gitbranch" size={16} className="text-brand-600" />
          <h2 id="release-wizard-title" className="text-base font-semibold text-slate-800">{title}</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="ml-auto rounded p-1 text-slate-400 hover:bg-slate-100"><Icon name="x" size={16} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Choice({ legend, value, options, onChange, disabled }: {
  legend: string; value: string; options: Array<{ id: string; label: string; hint?: string }>; onChange: (v: string) => void; disabled?: boolean;
}) {
  return (
    <fieldset className="space-y-1.5" disabled={disabled}>
      <legend className="text-xs font-semibold text-slate-600">{legend}{disabled && <span className="ml-2 font-normal text-slate-400">set by your project admin</span>}</legend>
      {options.map((o) => (
        <label key={o.id} className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${value === o.id ? 'border-brand-400 bg-brand-50' : 'border-slate-200 hover:border-slate-300'}`}>
          <input type="radio" name={legend} className="mt-1" checked={value === o.id} onChange={() => onChange(o.id)} />
          <span><span className="font-semibold text-slate-800">{o.label}</span>{o.hint && <span className="block text-xs text-slate-500">{o.hint}</span>}</span>
        </label>))}
    </fieldset>
  );
}

function CarryStep({ projectId, a, set }: { projectId: string; a: ReleaseAnswers; set: (p: Partial<ReleaseAnswers>) => void }) {
  const [s, setS] = useState<CarrySuggestions | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (): Promise<void> => {
    setBusy(true); setErr(null);
    try {
      const r = await agileApi.suggestCarry(projectId, a.sourceRelease!, { scopeText: `${a.name ?? ''}. ${a.goal ?? ''}`.trim() });
      setS(r);
      if (!(a.carry ?? []).length) set({ carry: r.suggestions.map((x) => x.id) });
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  useEffect(() => { void run(); /* once, when the step opens */ }, []);
  const chosen = new Set(a.carry ?? []);
  const toggle = (id: string): void => set({ carry: chosen.has(id) ? [...chosen].filter((x) => x !== id) : [...chosen, id] });
  const use = s ? carryBudgetUsed([...chosen], s.candidates, s.budget) : null;
  const why = new Map((s?.suggestions ?? []).map((x) => [x.id, x.reason]));
  const kinds = (['spec-section', 'requirement', 'decision', 'learning'] as const).filter((k) => s?.candidates.some((c) => c.kind === k));
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <p className="text-sm text-slate-600">The new release starts empty. Choose what it should build on.</p>
        <Button size="sm" className="ml-auto" icon="advice" loading={busy} onClick={() => void run()}>Suggest again</Button>
      </div>
      {s && <Badge tone={s.source === 'ai' ? 'brand' : 'neutral'} title="Suggestions are rankings only; you decide.">{s.source === 'ai' ? 'AI suggestions' : 'Suggested by keyword match'}</Badge>}
      {err && <Callout tone="warning" compact title="No suggestions">{err}</Callout>}
      {s && s.candidates.length === 0 && <Callout tone="info" compact title="Nothing to carry">The source release has no design sections, requirements or decisions yet. The new release starts blank.</Callout>}
      {kinds.map((k) => (
        <fieldset key={k} className="space-y-1">
          <legend className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{KIND_LABEL[k]}</legend>
          {s!.candidates.filter((c) => c.kind === k).map((c) => (
            <label key={c.id} className="flex items-start gap-2 rounded-lg px-2 py-1 text-sm hover:bg-slate-50">
              <input type="checkbox" className="mt-1" checked={chosen.has(c.id)} disabled={c.tooLarge} onChange={() => toggle(c.id)} />
              <span className="min-w-0"><span className="font-medium text-slate-800">{c.title}</span>
                {c.tooLarge && <span className="ml-2 text-xs text-amber-800">too large to carry</span>}
                {why.get(c.id) && <span className="block text-xs text-slate-500">{why.get(c.id)}</span>}</span>
            </label>))}
        </fieldset>))}
      {use && <div className={`text-xs ${use.over ? 'font-semibold text-red-700' : 'text-slate-500'}`} role="status">
        {use.over ?? `${use.items} of ${s!.budget.maxItems} entries · ${Math.round(use.bytes / 100) / 10} KB of ${Math.round(s!.budget.maxBytes / 1000)} KB`}</div>}
      <label className="flex items-center gap-2 text-xs text-slate-600">
        <input type="checkbox" checked={a.doneItems !== 'none'} onChange={(e) => set({ doneItems: e.target.checked ? 'context' : 'none' })} />
        Offer delivered stories as context (epics are always offered)</label>
    </div>
  );
}

function ItemsStep({ projectId, a, set, locked }: { projectId: string; a: ReleaseAnswers; set: (p: Partial<ReleaseAnswers>) => void; locked: Set<string> }) {
  const items = useQuery({
    queryKey: ['agile-backlog', projectId, 'wizard', a.sourceRelease], enabled: a.unfinishedItems === 'selected',
    queryFn: () => agileApi.backlog(projectId, { release: a.sourceRelease, scope: 'release' }),
  });
  const open = (items.data?.items ?? []).filter((i) => i.type !== 'epic' && ['new', 'refined', 'ready'].includes(i.status) && !i.iterationId);
  const chosen = new Set(a.items ?? []);
  return (
    <div className="space-y-3">
      <Choice legend="Unfinished backlog items of the source release" value={a.unfinishedItems ?? 'none'} disabled={locked.has('unfinishedItems')}
        options={[{ id: 'none', label: 'Leave them in the source release' }, { id: 'all', label: 'Move all of them', hint: 'Jira keys are kept; the source records where they went.' },
          { id: 'selected', label: 'Move the ones I pick' }]}
        onChange={(v) => set({ unfinishedItems: v as 'none' | 'all' | 'selected' })} />
      {a.unfinishedItems === 'selected' && (
        <div className="max-h-48 space-y-1 overflow-auto rounded-lg border border-slate-200 p-2">
          {items.isLoading && <div className="text-xs text-slate-400">Loading…</div>}
          {!items.isLoading && open.length === 0 && <div className="text-xs text-slate-500">The source release has no unfinished items outside a sprint.</div>}
          {open.map((i) => (
            <label key={i.key} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={chosen.has(i.key)} onChange={() => set({ items: chosen.has(i.key) ? [...chosen].filter((x) => x !== i.key) : [...chosen, i.key] })} />
              <span className="text-xs text-slate-400">{i.key}</span> {i.title}</label>))}
        </div>)}
      {a.unfinishedItems !== 'none' && (
        <label className="block text-xs font-semibold text-slate-600">Label for the moved items (optional)
          <input value={a.jiraLabel ?? ''} maxLength={40} onChange={(e) => set({ jiraLabel: e.target.value })} className={input} placeholder="e.g. mobile-v2" />
          <span className="mt-1 block text-[11px] font-normal text-slate-500">Added to the items and written through to Jira.</span></label>)}
    </div>
  );
}

function IntakeStep({ projectId, a, set, locked, question }: {
  projectId: string; a: ReleaseAnswers; set: (p: Partial<ReleaseAnswers>) => void; locked: Set<string>; question: (id: string) => Question | undefined;
}) {
  const epics = useQuery({ queryKey: ['agile-backlog', projectId, 'wizard-epics'], enabled: a.intakeRule === 'epic', queryFn: () => agileApi.backlog(projectId, { scope: 'pool' }) });
  const list = (epics.data?.items ?? []).filter((i) => i.type === 'epic' && i.status !== 'dropped');
  const chosen = new Set(a.epics ?? []);
  return (
    <div className="space-y-4">
      <Choice legend="Where do new Jira issues go?" value={a.intakeRule ?? 'pool'} disabled={locked.has('intakeRule')} options={question('intakeRule')?.options ?? []}
        onChange={(v) => set({ intakeRule: v as 'pool' | 'epic', ...(v === 'pool' ? { epics: [] } : {}) })} />
      {a.intakeRule === 'epic' && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-600">Epics that belong to this release</p>
          <div className="max-h-40 space-y-1 overflow-auto rounded-lg border border-slate-200 p-2">
            {epics.isLoading && <div className="text-xs text-slate-400">Loading…</div>}
            {!epics.isLoading && list.length === 0 && <div className="text-xs text-slate-500">No unassigned epics yet. You can map epics to the release later.</div>}
            {list.map((e) => (
              <label key={e.key} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={chosen.has(e.key)} onChange={() => set({ epics: chosen.has(e.key) ? [...chosen].filter((x) => x !== e.key) : [...chosen, e.key] })} />
                <span className="text-xs text-slate-400">{e.key}</span> {e.title}</label>))}
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={Boolean(a.adoptExistingEpicItems)} onChange={(e) => set({ adoptExistingEpicItems: e.target.checked })} />
            Also move those epics' items that are still in the shared pool</label>
        </div>)}
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={a.usePool !== false} disabled={locked.has('usePool')} onChange={(e) => set({ usePool: e.target.checked })} />
        Sprint planning may draw on the shared pool{locked.has('usePool') && <span className="text-xs text-slate-400"> · set by your project admin</span>}</label>
      <Choice legend="First sprint" value={a.firstSprint ?? 'later'} disabled={locked.has('firstSprint')} options={question('firstSprint')?.options ?? []}
        onChange={(v) => set({ firstSprint: v as 'later' | 'now' })} />
    </div>
  );
}
