import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../../api/client';
import type { Artefact } from '../../api/types';
import CodeExplorer from '../../components/CodeExplorer';
import ContextPanel from '../../components/ContextPanel';
import GatePanel from '../../components/GatePanel';
import { PartTabs } from '../../components/PartTabs';
import { PromptEditor } from '../../components/PromptEditor';
import { StageDocument } from '../../components/StageDocument';
import { useStageController, TRAIT_META, type StageControllerProps } from '../../components/StageWorkspace';
import { Icon } from '../../components/ui/Icon';
import { COLOR_CLASSES } from '../../api/flow';
import { Pill, StageDot, stageTone } from '../bits';
import QualityCards, { useFeedback } from './QualityCards';
import { stageMode, type StageMode } from './stageMode';

const fmtTime = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

/* ------------------------------------------------------------------ small pieces */
function Fold({ icon, label, detail, open, onToggle, children }: { icon: string; label: string; detail?: string; open: boolean; onToggle: () => void; children?: React.ReactNode }) {
  return (
    <div>
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm text-slate-600 hover:bg-slate-100">
        <span className="w-4 text-center text-slate-400" aria-hidden="true">{icon}</span>
        <span>{label}{detail && <code className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600">{detail}</code>}</span>
        <span className="ml-auto text-slate-400" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="mb-2 ml-7 mt-1">{children}</div>}
    </div>
  );
}

function Said({ who, children }: { who: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-0.5 text-xs text-slate-500">{who}</div>
      <div className="max-w-[46rem] text-[15px] leading-relaxed text-slate-800">{children}</div>
    </div>
  );
}

const Card = ({ title, sub, aside, children, id }: { title: string; sub?: string; aside?: React.ReactNode; children: React.ReactNode; id?: string }) => (
  <section id={id} data-testid={id} className="rounded-xl border border-slate-200 bg-white p-4">
    <div className="flex items-start justify-between gap-3">
      <h2 className="text-[15px] font-semibold text-navy">{title}</h2>{aside}
    </div>
    {sub && <p className="mb-3 mt-0.5 text-[13px] text-slate-500">{sub}</p>}
    {children}
  </section>
);

/* ------------------------------------------------------------------ the stage */
export default function StageChat(props: StageControllerProps & { onOpenPipeline: () => void; onOpenContext?: () => void }) {
  const c = useStageController(props);
  const qc = useQueryClient();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [changes, setChanges] = useState('');
  const [gateError, setGateError] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  const artefactRows = useQuery({
    queryKey: ['artefacts', props.projectId],
    queryFn: () => api.get<{ artefacts: Artefact[] }>(`/api/projects/${props.projectId}/artefacts`),
  });
  const fb = useFeedback(props.projectId, props.selectedSeq);
  const decide = useMutation({
    mutationFn: (v: { decision: 'APPROVE' | 'AMEND'; comments?: string }) =>
      api.post(`/api/gates/${props.projectId}/phase/${props.selectedSeq}/review`, v),
    onSuccess: () => {
      setGateError(''); setChanges('');
      for (const k of [['project', props.projectId], ['artefacts', props.projectId], ['flow', props.projectId], ['signoffs', props.projectId, props.selectedSeq]]) void qc.invalidateQueries({ queryKey: k });
    },
    onError: (e) => setGateError(e instanceof Error ? e.message : 'The decision could not be saved'),
  });
  const sig = c ? `${c.stage?.status}|${c.stageMessages.length}|${c.stageArtefacts.length}|${c.streamingHere}|${c.plan ? 1 : 0}|${c.clarification?.length ?? 0}` : '';
  useEffect(() => { const el = scroller.current; if (el) el.scrollTo({ top: el.scrollHeight }); }, [sig]);
  if (!c) return null;
  const {
    stage, stages, idx, plan, planBusy, planFresh, locked, streamingHere, activity, liveResponse, liveParts, parts, clarification, clarifyAns, setClarifyAns,
    clarifyStep, setClarifyStep, submitClarification, uploading, fileInputRef, attachments, removeAttachment, onAttach, selectedRefChips, selectedTemplateChips,
    setRefIds, setFormworkIds, stageMessages, stageArtefacts, guide, amendUndecided, amendBusy, chooseAmend, renderArtifacts, overrideTrait, retrigger,
    prompt, setPrompt, allMentions, pickMention, unpickMention, textareaRef, promptError, promptMissing, canReviewPlan, onReviewSubmit, triggerPlan,
    refineText, setRefineText, sendRefinement, docOpen, setDocOpen, regenerable, retriggerPart, blockedReason, onSelectStage, onOpenArtefact, setViewArtefactId,
    pendingGate, user, projectId, selectedSeq, uploadNotes, streaming,
  } = c;
  const mode: StageMode = stageMode({ status: stage.status, streaming: streamingHere, hasPlan: Boolean(plan), hasQuestions: Boolean(clarification?.length) });
  const versions = new Map((artefactRows.data?.artefacts ?? []).map((a) => [a.id, a.version]));
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const openArt = (id: string) => (onOpenArtefact ? onOpenArtefact(id) : setViewArtefactId(id));
  const canDecide = user.role === 'SUPER_ADMIN' || stage.canReview;
  const color = COLOR_CLASSES[stage.color];
  const qualityScore = (() => { const f = fb.validation.find((x) => x.category === 'quality-score'); const m = f?.comment.match(/(\d+)\s*\/\s*100/); return f?.rating ?? (m ? Number(m[1]) : null); })();
  const afterRun = mode === 'review' || mode === 'approved' || mode === 'escalated';

  /* ---- header: one slim line ---- */
  const header = (
    <header className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2.5" data-testid="v2-stage-header">
      <div className="flex gap-1">
        <button type="button" aria-label="Previous stage" disabled={idx <= 0} onClick={() => stages[idx - 1] && onSelectStage(stages[idx - 1]!.phase)} className="rounded-lg border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30"><Icon name="chevron-left" size={14} /></button>
        <button type="button" aria-label="Next stage" disabled={idx >= stages.length - 1} onClick={() => stages[idx + 1] && onSelectStage(stages[idx + 1]!.phase)} className="rounded-lg border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30"><Icon name="chevron-right" size={14} /></button>
      </div>
      <h1 className="font-display text-xl font-bold text-navy">{stage.name}</h1>
      <Pill tone={stageTone(stage.color)}>{color.label}</Pill>
      <Pill>{stage.persona}</Pill>
      <Pill title="The role that signs off this stage">Reviewer: {stage.reviewerRole}</Pill>
      {stage.stale && <Pill tone="amber" title={stage.staleReason ?? 'An upstream input changed'}>Outdated</Pill>}
      {fb.security.length > 0 && <Pill tone={fb.openBlocking ? 'red' : 'brand'} title="The security review runs when the output reaches review">{fb.openBlocking ? 'Security review: HIGH' : 'Security review at gate'}</Pill>}
      {locked && <Pill tone="brand">Editing locked</Pill>}
      <button type="button" onClick={props.onOpenPipeline} aria-label="Open the pipeline view" data-testid="v2-minimap"
        className="ml-auto flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 hover:border-brand-300">
        {stages.map((s, i) => (
          <span key={s.phase} className="flex items-center gap-1.5">
            <StageDot n="" color={s.color} size={s.phase === stage.phase ? 14 : 10} />
            {i < stages.length - 1 && <span className="h-px w-2.5 bg-slate-300" />}
          </span>
        ))}
        <span className="ml-1">Pipeline</span>
      </button>
    </header>
  );

  const nextBanner = (
    <div className="flex items-center gap-2 rounded-lg bg-brand-100 px-3.5 py-2 text-sm text-navy" data-testid="v2-next">
      <span aria-hidden="true">→</span>
      <span><b>Next: {guide.next.title}</b><span className="text-slate-600"> · {guide.next.body.split(/(?<=[.!?])\s/)[0]}</span></span>
    </div>
  );

  /* ---- conversation: what was said ---- */
  const turns = stageMessages.map((m, i) => m.role === 'user' && /^\s*▶\s*Plan triggered/.test(m.content) ? (
    <Fold key={m.id} icon="▶" label="Plan triggered" detail={fmtTime(m.createdAt)} open={!!open[m.id]} onToggle={() => toggle(m.id)}>
      <div className="prose-chat rounded-lg bg-slate-100 p-3 text-sm text-slate-700"><ReactMarkdown>{m.content.replace(/^\s*▶\s*Plan triggered[^\n]*\n*/, '')}</ReactMarkdown></div>
    </Fold>
  ) : m.role === 'user' ? (
    <div key={m.id} className="rounded-xl bg-slate-100 px-3.5 py-3" data-testid="v2-turn-user">
      <div className="mb-1 text-xs text-slate-500">You{i === 0 ? ' · brief' : ''} · {fmtTime(m.createdAt)}</div>
      <div className="prose-chat text-sm text-slate-800"><ReactMarkdown>{m.content}</ReactMarkdown></div>
    </div>
  ) : (
    <Said key={m.id} who={`DevMind · ${m.role === 'system' ? 'system' : stage.persona} · ${fmtTime(m.createdAt)}`}>
      <div className="prose-chat"><ReactMarkdown>{m.content}</ReactMarkdown></div>
    </Said>
  ));

  /* ---- one question at a time ---- */
  const questionCard = (() => {
    if (!clarification?.length || streamingHere) return null;
    const total = clarification.length, step = Math.min(clarifyStep, total - 1), q = clarification[step];
    if (!q) return null;
    const ans = clarifyAns[q.id] || { selected: [], other: '' };
    const answered = (id: string) => { const a = clarifyAns[id]; return !!a && (a.selected.length > 0 || a.other.trim().length > 0); };
    const pick = (label: string) => setClarifyAns((prev) => {
      const cur = prev[q.id] || { selected: [], other: '' };
      const selected = q.multiSelect ? (cur.selected.includes(label) ? cur.selected.filter((l) => l !== label) : [...cur.selected, label]) : (cur.selected.includes(label) ? [] : [label]);
      return { ...prev, [q.id]: { ...cur, selected } };
    });
    const isLast = step === total - 1;
    return (
      <Card title="A few questions first" sub={`Question ${step + 1} of ${total}. Anything you skip is left to the agent.`} id="v2-questions"
        aside={<span className="flex items-center gap-1.5 pt-1.5" aria-label={`Question ${step + 1} of ${total}`}>{clarification.map((x, i) => (
          <button key={x.id} type="button" onClick={() => setClarifyStep(i)} title={x.question} className={`h-1.5 rounded-full ${i === step ? 'w-6 bg-brand-600' : answered(x.id) ? 'w-3 bg-emerald-500' : 'w-3 bg-slate-200'}`} />
        ))}</span>}>
        <p className="text-[15px] font-semibold text-slate-900">{q.question}</p>
        {q.rationale && <p className="mt-0.5 text-xs text-slate-500">{q.rationale}</p>}
        <div className="mt-2.5 flex flex-wrap gap-2">
          {(q.options || []).map((o) => {
            const on = ans.selected.includes(o.label);
            return <button key={o.label} type="button" title={o.description} onClick={() => pick(o.label)} aria-pressed={on} disabled={streaming}
              className={`rounded-full border px-4 py-1.5 text-sm ${on ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-300 bg-white text-slate-700 hover:border-brand-400'}`}>{o.label}</button>;
          })}
        </div>
        <input type="text" placeholder="Other…" value={ans.other} disabled={streaming} aria-label="Other answer"
          onChange={(e) => setClarifyAns((prev) => ({ ...prev, [q.id]: { ...(prev[q.id] || { selected: [], other: '' }), other: e.target.value } }))}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); if (isLast) submitClarification(); else setClarifyStep(step + 1); } }}
          className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none" />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => setClarifyStep(Math.max(0, step - 1))} disabled={step === 0 || streaming} className="rounded-lg border border-slate-300 px-3 py-1 text-sm disabled:opacity-40">Back</button>
          {isLast
            ? <button type="button" onClick={submitClarification} disabled={streaming} className="rounded-lg bg-brand-600 px-3.5 py-1 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50">Submit answers</button>
            : <button type="button" onClick={() => setClarifyStep(step + 1)} disabled={streaming} className="rounded-lg border border-slate-300 px-3 py-1 text-sm">Next</button>}
          <button type="button" data-testid="clarify-upload" disabled={streaming || uploading || locked} onClick={() => fileInputRef.current?.click()}
            className={`ml-auto rounded-lg border px-3 py-1 text-sm ${q.needsDocument ? 'border-brand-500 bg-brand-100 text-brand-700' : 'border-slate-300 text-slate-700'}`}>
            {uploading ? 'Uploading…' : q.needsDocument ? 'Upload the missing document' : 'Forgot a document? Upload it'}
          </button>
        </div>
      </Card>
    );
  })();

  /* ---- the plan ---- */
  const planCard = plan && (
    <Card title="Plan" id="plan-card"
      sub={`${plan.intel?.willProduce?.filter((w) => w.include).length ?? stage.outputs.length} of ${plan.intel?.willProduce?.length ?? stage.outputs.length} artefacts`}
      aside={plan.intel ? <Pill tone="brand" title="Tailored to your input, tech stack, earlier outputs and configuration">AI-planned{plan.intel.cached ? ' · cached' : ''}</Pill> : undefined}>
      {plan.intel?.understood && <p className="mb-3 text-[15px] leading-relaxed text-slate-800">{plan.intel.understood}</p>}
      {renderArtifacts()}
      {(plan.intel?.recommendation) && <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-slate-700"><b>Advice.</b> {plan.intel.recommendation}</p>}
      {(plan.intel?.outOfScope?.length ?? 0) > 0 && <p className="mt-2 text-xs text-slate-500"><b>Not covered here:</b> {plan.intel!.outOfScope.join('; ')}</p>}
      <div className="mt-4"><ContextPanel projectId={projectId} seq={selectedSeq} refreshKey={`v2|${locked}|${stage.status}|${attachments.length}|${plan.planState?.fresh ?? ''}|${(plan.overlay.promptOverlay ?? '').length}`} /></div>
      <details className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2" data-testid="v2-plan-models">
        <summary className="cursor-pointer text-sm"><b>Pipeline &amp; models</b> <span className="text-slate-500">{plan.agent.nodes.length} steps</span></summary>
        <div className="mt-2 space-y-1.5 text-sm">
          <div className="flex justify-between gap-3"><span>Agent</span><span className="text-slate-600">{plan.agent.persona} · {plan.agent.tier} model</span></div>
          {plan.agent.nodes.map((n) => <div key={n} className="flex justify-between gap-3"><span className="capitalize">{n.replace(/[_-]/g, ' ')}</span><span className="text-slate-500">step</span></div>)}
          {fb.security.length > 0 && <div className="flex justify-between gap-3"><span>Security review</span><Pill tone="green">on at the gate</Pill></div>}
          <p className="pt-1 text-xs text-slate-500">Which models run each kind of work is set under Model routes (admins) and per stage in the Workflow designer.</p>
        </div>
      </details>
      {(plan.traits?.filter((t) => !t.trait.startsWith('_')).length ?? 0) > 0 && (
        <details className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <summary className="cursor-pointer text-sm"><b>Project fit</b> <span className="text-slate-500">what the AI judged about your project</span></summary>
          <div className="mt-2 space-y-1.5">
            {plan.traits!.filter((t) => !t.trait.startsWith('_')).map((t) => {
              const m = TRAIT_META[t.trait] ?? { label: t.trait, hint: '' };
              return (
                <div key={t.trait} className="flex items-center justify-between gap-3 text-sm" title={`${m.hint}. Evidence: ${t.evidence}`}>
                  <span><b>{m.label}</b> <span className="text-slate-500">{t.evidence}</span></span>
                  <span className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs font-semibold" role="group" aria-label={`${m.label}: auto, yes or no`}>
                    {([['', 'Auto'], ['present', 'Yes'], ['absent', 'No']] as const).map(([v, label]) => {
                      const cur = t.source === 'override' ? (t.value ? 'present' : 'absent') : '';
                      return <button key={label} type="button" aria-pressed={cur === v} disabled={planBusy || locked} onClick={() => overrideTrait(t.trait, (v || null) as 'present' | 'absent' | null)}
                        className={`px-2.5 py-1 ${cur === v ? 'bg-brand-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-100'}`}>{label}</button>;
                    })}
                  </span>
                </div>
              );
            })}
          </div>
        </details>
      )}
    </Card>
  );

  /* ---- amend: choose how to re-plan ---- */
  const amendCard = stage.status === 'AMEND_REQUESTED' && amendUndecided ? (
    <Card title="Changes were requested. How should I re-plan?" sub="Your earlier instructions, answers and the previous version are all still on record." id="v2-amend">
      <div className="grid gap-3 sm:grid-cols-2">
        <button type="button" disabled={amendBusy || plan === null} onClick={() => void chooseAmend('amend')} data-testid="amend-keep" className="rounded-xl border border-slate-300 p-3 text-left hover:border-brand-500 disabled:opacity-50">
          <div className="text-sm font-semibold">Amend the existing work <Pill tone="brand">recommended</Pill></div>
          <div className="mt-1 text-[13px] text-slate-500">Keep everything decided so far. Your changes extend it; the plan, project fit and the agent all see the history and the previous version.</div>
        </button>
        <button type="button" disabled={amendBusy || plan === null} onClick={() => void chooseAmend('fresh')} data-testid="amend-fresh" className="rounded-xl border border-slate-300 p-3 text-left hover:border-brand-500 disabled:opacity-50">
          <div className="text-sm font-semibold">Start from a blank slate</div>
          <div className="mt-1 text-[13px] text-slate-500">Set aside the earlier answers, analysis and previous version. Only your new instructions apply; attached files stay.</div>
        </button>
      </div>
    </Card>
  ) : null;

  /* ---- generation ---- */
  const done = parts.filter((p) => p.status === 'done').length;
  const runRows = streamingHere ? (
    <div className="space-y-0.5" data-testid="v2-runrows">
      {(liveParts.length ? liveParts.map((p) => ({ id: p.field, label: p.title ?? p.field, state: p.status })) : activity.map((a) => ({ id: String(a.id), label: a.label, state: a.status === 'error' ? 'failed' : a.status === 'start' ? 'running' : 'done' }))).map((r) => (
        <div key={r.id} className="flex items-center gap-2.5 px-2 py-1 text-sm text-slate-600">
          <span className="w-4 text-center">{r.state === 'done' ? <span className="text-emerald-600">✓</span> : r.state === 'running' ? <Icon name="loader" size={13} spin className="text-brand-600" /> : r.state === 'failed' ? <span className="text-bared-600">✗</span> : '·'}</span>
          <span>{r.state === 'running' ? 'Writing' : r.state === 'done' ? 'Wrote' : r.state === 'failed' ? 'Failed' : 'Queued'} <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{r.label}</code></span>
        </div>
      ))}
      {liveResponse && <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-100 p-3 font-mono text-xs text-slate-700">{liveResponse}</pre>}
      <p className="mt-2 text-center text-xs text-slate-500">Editing is locked while this stage generates. It keeps running if you leave.</p>
      {liveParts.length > 0 && <div className="mt-2"><PartTabs parts={liveParts} /></div>}
    </div>
  ) : null;

  /* ---- outputs ---- */
  const outputs = stageArtefacts.length > 0 && (
    <Card title="Artefacts" sub="Each opens in the preview with its own tools and exports."
      aside={<span className="flex items-center gap-2">{qualityScore != null && <Pill tone={qualityScore >= 70 ? 'green' : 'amber'} title="The validation agent's score for this stage">Quality {qualityScore}</Pill>}<button type="button" onClick={() => setDocOpen(true)} className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-semibold hover:border-brand-400">Open as one document</button></span>}>
      <div className="space-y-1.5">
        {stageArtefacts.map((a) => (
          <button key={a.id} type="button" onClick={() => openArt(a.id)} data-testid={`v2-art-${a.id}`} className="flex w-full items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-left hover:border-brand-400">
            <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-slate-900">{a.title}</span><span className="block text-xs text-slate-500">{a.type} · v{versions.get(a.id) ?? 1}</span></span>
            <span className="text-sm text-slate-500">Open ↗</span>
          </button>
        ))}
      </div>
      {docOpen && <StageDocument projectId={projectId} artefacts={stageArtefacts} title={stage.name} subtitle={`${stage.persona} · ${stage.name}`} onClose={() => setDocOpen(false)} />}
    </Card>
  );

  const failed = parts.filter((p) => p.status === 'failed');

  /* ---- dock: the decision that needs you ---- */
  let dock: React.ReactNode = null;
  if (mode === 'review' && stage.template !== 6) {
    dock = (
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-lg" data-testid="v2-approval">
        <div className="min-w-0 flex-1"><div className="text-sm font-semibold text-navy">Approve {stage.name}?</div>
          <div className="text-xs text-slate-500">{gateError || (fb.openBlocking ? `${fb.openBlocking} critical security finding${fb.openBlocking > 1 ? 's' : ''} block${fb.openBlocking > 1 ? '' : 's'} approval. Resolve ${fb.openBlocking > 1 ? 'them' : 'it'}, or request changes.` : canDecide ? 'No blocking findings. Quality checks are above.' : `Only ${stage.reviewerRole} can sign this gate.`)}</div></div>
        {fb.openBlocking > 0 && <button type="button" onClick={() => document.getElementById('v2-checks')?.scrollIntoView({ behavior: 'smooth', block: 'center' })} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold hover:border-brand-400">Show finding</button>}
        <button type="button" onClick={() => decide.mutate({ decision: 'APPROVE' })} disabled={!canDecide || decide.isPending || fb.openBlocking > 0} data-testid="v2-approve"
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50">{decide.isPending ? 'Approving…' : 'Approve stage'}</button>
      </div>
    );
  } else if (mode === 'escalated') {
    dock = (
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-bared-500/40 bg-white px-4 py-3 shadow-lg">
        <div className="min-w-0 flex-1"><div className="text-sm font-semibold text-navy">Escalated: a person needs to decide</div><div className="text-xs text-slate-500">The automatic rework limit was reached.</div></div>
        {stage.canRetrigger && <button type="button" onClick={() => retrigger.mutate(stage.phase)} disabled={retrigger.isPending} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold hover:border-brand-400">Retry</button>}
      </div>
    );
  }

  /* ---- composer: always at the bottom ---- */
  const writable = plan?.canEdit ?? stage.canRetrigger;
  const blocked = blockedReason.length > 0;               // an earlier stage is not approved yet: nothing here can be planned or run
  const asking = Boolean(clarification?.length) && !streamingHere;
  const label = asking ? (clarifyStep >= (clarification?.length ?? 1) - 1 ? 'Submit answers' : 'Next') : { new: 'Review plan', plan: 'Generate', running: 'Generating', review: 'Request changes', amend: 'Update plan', approved: 'Re-run stage', escalated: 'Request changes' }[mode];
  const placeholder = blocked ? `Waiting for ${blockedReason.join(', ')} to be approved. You can write here once it is.` : asking ? 'Answer the questions above first. Anything you skip is left to the agent.' : {
    new: stage.phase === 1 ? 'Describe what you need. Type @ to reference outputs, templates or files.' : `Add guidance for the ${stage.persona} (optional). Type @ to pull in earlier outputs, templates or files.`,
    plan: 'Refine the brief and I will update the plan…', running: 'Generating… editing is locked', review: 'Describe the changes you want… (or approve above)',
    amend: amendUndecided ? 'Choose how to re-plan above first' : 'Adjust the instructions, then update the plan', approved: 'This stage is approved. Re-run it to change it.', escalated: 'Describe what to change…',
  }[mode];
  const editorDisabled = blocked || asking || locked || mode === 'running' || mode === 'approved' || (mode === 'amend' && amendUndecided);
  const useBrief = mode === 'new' || mode === 'amend';
  const primary = (() => {
    if (asking) {
      const last = clarifyStep >= (clarification?.length ?? 1) - 1;
      return { run: () => (last ? void submitClarification() : setClarifyStep(clarifyStep + 1)), disabled: streaming, loading: false };
    }
    switch (mode) {
      case 'new': case 'amend': return { run: () => void onReviewSubmit(), disabled: !canReviewPlan || amendUndecided, loading: planBusy };
      case 'plan': return { run: () => (refineText.trim() ? void sendRefinement() : void triggerPlan()), disabled: planBusy || locked || !planFresh && !refineText.trim(), loading: planBusy };
      case 'review': case 'escalated': return { run: () => decide.mutate({ decision: 'AMEND', comments: changes.trim() || 'Changes requested' }), disabled: decide.isPending || !canDecide || !changes.trim(), loading: decide.isPending };
      case 'approved': return { run: () => retrigger.mutate(stage.phase), disabled: !stage.canRetrigger || retrigger.isPending, loading: retrigger.isPending };
      default: return { run: () => undefined, disabled: true, loading: false };
    }
  })();
  const chips = (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {selectedRefChips.map((a) => <span key={`r-${a.id}`} className="inline-flex items-center gap-1 rounded-full bg-brand-100 px-2.5 py-0.5 text-xs text-brand-700">@{a.title}<button type="button" aria-label={`Remove ${a.title}`} onClick={() => setRefIds((p) => p.filter((id) => id !== a.id))}>✕</button></span>)}
      {selectedTemplateChips.map((f) => <span key={`t-${f.id}`} className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2.5 py-0.5 text-xs text-violet-700">{f.name}<button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFormworkIds((p) => p.filter((id) => id !== f.id))}>✕</button></span>)}
      {attachments.map((a) => <span key={`a-${a.id}`} className="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-white px-2.5 py-0.5 text-xs text-slate-700">📎 {a.filename}<button type="button" aria-label={`Remove ${a.filename}`} disabled={locked} onClick={() => removeAttachment(a.id)}>✕</button></span>)}
    </div>
  );
  const composerBox = (
    <div className="rounded-xl border border-slate-300 bg-white p-3 shadow-sm focus-within:border-brand-500" data-testid="v2-composer">
      <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => onAttach(e.target.files)} />
      {(attachments.length + selectedRefChips.length + selectedTemplateChips.length > 0) && chips}
      {uploadNotes.map((n) => <p key={n.name} className={`mb-1 text-xs ${n.tone === 'error' ? 'text-bared-600' : 'text-amber-700'}`}><b>{n.name}</b> — {n.tone === 'error' ? `could not be attached: ${n.text}` : `attached, only partly read: ${n.text}`}</p>)}
      {mode === 'review' || mode === 'escalated' ? (
        <textarea value={changes} onChange={(e) => setChanges(e.target.value)} placeholder={placeholder} aria-label="Changes you want" rows={2} className="w-full resize-none bg-transparent text-sm outline-none" />
      ) : (
        <PromptEditor ref={textareaRef} value={mode === 'plan' ? refineText : prompt} onChange={mode === 'plan' ? setRefineText : setPrompt} mentions={allMentions} onPick={pickMention} onUnpick={unpickMention} disabled={editorDisabled} placeholder={placeholder} />
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" disabled={blocked || locked || uploading} onClick={() => fileInputRef.current?.click()} className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:border-brand-400 disabled:opacity-40">{uploading ? 'Uploading…' : '+ Document'}</button>
        <span className="text-xs text-slate-500">type @ to reference outputs</span>
        <button type="button" onClick={primary.run} disabled={blocked || primary.disabled || (mode === 'plan' && !writable)} data-testid="v2-primary"
          className="ml-auto rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50">{primary.loading ? `${label}…` : label}</button>
      </div>
      {useBrief && promptMissing && !asking && <p className="mt-1.5 text-xs text-slate-500">{promptError}</p>}
    </div>
  );

  /* ---- assemble ---- */
  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-50" data-testid="v2-stagechat" data-mode={mode} data-blocked={blocked ? 'true' : 'false'}>
      {header}
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[760px] flex-col gap-4 px-4 py-5">
          {blockedReason.length > 0 ? (
            <div className="rounded-lg bg-amber-50 px-3.5 py-3 text-sm text-amber-900"><b>This stage is waiting for an earlier one.</b> It runs once {blockedReason.join(', ')} {blockedReason.length > 1 ? 'are' : 'is'} approved.</div>
          ) : nextBanner}
          {stageMessages.length === 0 && mode === 'new' && (
            <Said who={`DevMind · ${stage.persona}`}>Tell me what <b>{stage.name}</b> should produce. I will ask a few questions if I need to, then show you a plan before anything is generated.</Said>
          )}
          {turns}
          {questionCard}
          {amendCard}
          {mode !== 'new' && mode !== 'plan' && plan && (mode === 'amend' ? null : (
            <Fold icon="◎" label="Plan" detail={`${plan.intel?.willProduce?.filter((w) => w.include).length ?? stage.outputs.length} artefacts`} open={!!open.plan} onToggle={() => toggle('plan')}>{planCard}</Fold>
          ))}
          {(mode === 'plan' || (mode === 'amend' && !amendUndecided)) && planCard}
          {runRows}
          {!streamingHere && parts.length > 0 && afterRun && (
            <Fold icon={failed.length ? '✗' : '✓'} label={failed.length ? `${failed.length} file(s) failed` : `Generated ${done} files`} detail={`${done} of ${parts.length}`} open={!!open.run || failed.length > 0} onToggle={() => toggle('run')}>
              <PartTabs parts={parts.map((p) => (p.status === 'running' ? { ...p, status: 'failed' as const, error: 'Interrupted — partial text kept' } : p))} retrigger={retriggerPart} />
              {regenerable.length > 0 && !streaming && <p className="mt-2 text-xs text-slate-500">Retry a file to regenerate just that one; the others are kept.</p>}
            </Fold>
          )}
          {afterRun && (
            <Said who={`DevMind · ${mode === 'approved' ? 'approved' : 'ready for review'}`}>{stage.template === 6 ? 'Open the files to review them.' : 'Open an artefact to read it, then approve or ask for changes.'}</Said>
          )}
          {stage.template === 6 && afterRun && <CodeExplorer projectId={projectId} phase={selectedSeq} />}
          {afterRun && outputs}
          {afterRun && (stageArtefacts.length > 0 || ['PENDING_REVIEW', 'APPROVED'].includes(stage.status)) && (
            <QualityCards projectId={projectId} phase={selectedSeq} onUploadMissing={() => fileInputRef.current?.click()} uploading={uploading}
              canResolve={user.role === 'SUPER_ADMIN' || user.role === 'PROJECT_MANAGER' || Boolean(pendingGate && pendingGate.phase === selectedSeq && pendingGate.canReview)} />
          )}
          {afterRun && pendingGate && pendingGate.phase === selectedSeq && (
            <Fold icon="👥" label="Review matrix" detail="who signs what" open={!!open.matrix} onToggle={() => toggle('matrix')}>
              <GatePanel projectId={projectId} pending={pendingGate} artefacts={c.artefacts as never} user={user} />
            </Fold>
          )}
          {stage.stale && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg bg-amber-50 px-3.5 py-3 text-sm text-amber-900">
              <span><b>This stage may be outdated.</b> {stage.staleReason ?? 'An upstream input was re-generated after this stage ran.'}</span>
              {stage.canRetrigger && <button type="button" onClick={() => retrigger.mutate(stage.phase)} disabled={retrigger.isPending} className="ml-auto rounded-lg border border-amber-400 px-3 py-1 text-xs font-semibold">{retrigger.isPending ? 'Re-running…' : 'Re-run this stage'}</button>}
            </div>
          )}
          {mode === 'approved' && <p className="text-center text-xs text-slate-500">Approved{stage.reviewedBy ? ` by ${stage.reviewedBy}` : ''}.</p>}
        </div>
      </div>
      <div className="shrink-0 border-t border-slate-200 bg-slate-50 px-4 pb-4 pt-3">
        <div className="mx-auto flex max-w-[760px] flex-col gap-3">{dock}{composerBox}</div>
      </div>
    </div>
  );
}
