import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import { api, streamStageTrigger } from '../api/client';
import type { ProjectFlow } from '../api/flow';
import type { ChatMessage, PhaseStateView, User } from '../api/types';
import { useApp, type ActivityItem } from '../store';
import ArtifactViewer from './ArtifactViewer';
import FeedbackPanel from './FeedbackPanel';
import GatePanel from './GatePanel';

const STATUS_META: Record<string, { label: string; cls: string; icon: string }> = {
  NOT_STARTED: { label: 'Not started', cls: 'bg-slate-100 text-slate-600', icon: '○' },
  IN_PROGRESS: { label: 'Generating', cls: 'bg-blue-100 text-blue-700', icon: '◐' },
  PENDING_REVIEW: { label: 'Awaiting review', cls: 'bg-amber-100 text-amber-700', icon: '⏸' },
  APPROVED: { label: 'Approved', cls: 'bg-emerald-100 text-emerald-700', icon: '✓' },
  AMEND_REQUESTED: { label: 'Changes requested', cls: 'bg-orange-100 text-orange-700', icon: '↺' },
  ESCALATED: { label: 'Escalated', cls: 'bg-red-100 text-red-700', icon: '⚠' },
};

const ACTIVITY_ICON: Record<ActivityItem['kind'], string> = { node: '⚙️', tool: '🔌', artifact: '📄', gate: '⛔' };

// Plan Review & Edit gate
interface StagePlan {
  phase: number;
  status: string;
  canEdit: boolean;
  blockedOn: string[];
  stage: { name: string; persona: string; reviewerRole: string; writeRoles: string[]; outputs: string[]; inputs: string[] };
  agent: { persona: string; tier: string; nodes: string[] };
  skills: Array<{ id: string; name: string; tier: string }>;
  expectedTools: string[];
  context: {
    priorArtifacts: Array<{ id: string; phase: number; type: string; title: string }>;
    canonApplied: boolean;
    formworks: Array<{ id: string; name: string; artefactType: string; scope: string }>;
    attachments: Array<{ id: string; filename: string; isText: boolean }>;
    ragSnippets: number;
    curatedInjectedChars: number;
  };
  overlay: { promptOverlay: string; referencedArtifactIds: string[]; attachmentIds: string[]; formworkIds: string[]; origin: string };
  prompt: { system: string; user: string };
}

/** A stage is runnable now when it hasn't run yet (or a rework is expected) and
 *  every dependency stage is approved (its upstream inputs exist). A stage that
 *  is generating, awaiting review, or already approved shows its status instead
 *  of a run box. */
function isRunnable(stage: ProjectFlow['stages'][number], byKey: Map<string, ProjectFlow['stages'][number]>): boolean {
  if (!['NOT_STARTED', 'AMEND_REQUESTED'].includes(stage.status)) return false;
  // Optional dependencies don't block (they may be skipped when starting mid-pipeline).
  return stage.dependsOn.every((dep) => {
    const d = byKey.get(dep);
    return !d || d.optional || d.status === 'APPROVED';
  });
}

/**
 * Stage Workspace: the stage-centric working surface — the primary
 * interaction area. For the selected stage it shows status, a compose/run area
 * (when the stage is runnable), the gate review decision bar (when pending),
 * the produced output files, and the stage's own conversation thread. Upstream
 * navigation is the Pipeline Rail; prev/next moves along the pipeline here.
 */
export default function StageWorkspace({
  projectId,
  flow,
  selectedSeq,
  onSelectStage,
  user,
  messages,
  pendingGate,
  artefacts,
}: {
  projectId: string;
  flow: ProjectFlow;
  selectedSeq: number;
  onSelectStage: (seq: number) => void;
  user: User;
  messages: ChatMessage[];
  pendingGate: PhaseStateView | null;
  artefacts: Array<{ id: string; phase: number; type: string; title: string; url: string | null }>;
}) {
  const qc = useQueryClient();
  const { streaming, activity, liveResponse, beginStream, pushEvent, endStream } = useApp();
  const [prompt, setPrompt] = useState('');
  const [viewArtefactId, setViewArtefactId] = useState<string | null>(null);
  const [refIds, setRefIds] = useState<string[]>([]);
  const [formworkIds, setFormworkIds] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [plan, setPlan] = useState<StagePlan | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [showSystemPrompt, setShowSystemPrompt] = useState(false);
  // Inline "@" mention autosuggest.
  const [mention, setMention] = useState<{ open: boolean; query: string; at: number }>({
    open: false, query: '', at: 0,
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  // files the user attached to THIS stage, and the prior-stage outputs
  // they can hand-pick as @references (in addition to auto-included upstream).
  const attachmentsQ = useQuery({
    queryKey: ['attachments', projectId, selectedSeq],
    queryFn: () =>
      api.get<{ attachments: Array<{ id: string; filename: string; sizeBytes: number; isText: boolean }> }>(
        `/api/projects/${projectId}/phase/${selectedSeq}/attachments`,
      ),
    enabled: Boolean(projectId),
  });
  const attachments = attachmentsQ.data?.attachments ?? [];

  // templates (formworks) available to @-reference — project + platform.
  const formworksQ = useQuery({
    queryKey: ['formworks', projectId],
    queryFn: () =>
      api.get<{ formworks: Array<{ id: string; name: string; artefactType: string; scope: string }> }>(
        `/api/projects/${projectId}/formworks`,
      ),
    enabled: Boolean(projectId),
  });
  const formworks = formworksQ.data?.formworks ?? [];

  // Reset the compose box and curated selection when the user switches stages,
  // so one stage's draft never leaks into another.
  useEffect(() => {
    setPrompt('');
    setRefIds([]);
    setFormworkIds([]);
    setMention({ open: false, query: '', at: 0 });
    setPlan(null);
    setShowSystemPrompt(false);
  }, [selectedSeq]);

  /** Clear the compose box, pinned references/templates and the rendered plan —
   *  called after a stage runs so the fields don't retain the last submission. */
  function resetComposer() {
    setPrompt('');
    setRefIds([]);
    setFormworkIds([]);
    setMention({ open: false, query: '', at: 0 });
    setShowSystemPrompt(false);
    setPlan(null);
  }

  // Re-run a stale (or completed) stage to refresh it against its changed upstream.
  const retrigger = useMutation({
    mutationFn: (seq: number) => api.post(`/api/projects/${projectId}/phase/${seq}/retrigger`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['flow', projectId] });
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });

  const stages = flow.stages;
  const byKey = useMemo(() => new Map(stages.map((s) => [s.key, s])), [stages]);
  const stage = stages.find((s) => s.phase === selectedSeq) ?? stages[0];
  const idx = stages.findIndex((s) => s.phase === stage?.phase);
  const runnable = stage ? isRunnable(stage, byKey) : false;
  const meta = STATUS_META[stage?.status ?? 'NOT_STARTED'] ?? STATUS_META.NOT_STARTED!;

  const stageMessages = useMemo(
    () => messages.filter((m) => m.phase === selectedSeq),
    [messages, selectedSeq],
  );
  const streamingHere = streaming && stage?.phase === flow.currentPhase;

  useEffect(() => {
    threadRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [stageMessages.length, activity.length, liveResponse]);

  if (!stage) return null;

  const blockedReason = !runnable && stage.status !== 'APPROVED' && stage.status !== 'PENDING_REVIEW' && stage.status !== 'IN_PROGRESS'
    ? stage.dependsOn
        .filter((dep) => { const d = byKey.get(dep); return d && !d.optional && d.status !== 'APPROVED'; })
        .map((dep) => byKey.get(dep)?.name ?? dep)
    : [];

  const overlayBody = () => ({
    promptOverlay: prompt,
    referencedArtifactIds: refIds,
    attachmentIds: attachments.map((a) => a.id),
    formworkIds,
  });

  // save the overlay and (re-)render the full plan — "Review / Update plan".
  async function reviewPlan(e?: FormEvent) {
    e?.preventDefault();
    if (planBusy || streaming || promptError) return;
    setPlanBusy(true);
    setMention({ open: false, query: '', at: 0 });
    try {
      const p = await api.put<StagePlan>(`/api/projects/${projectId}/phase/${selectedSeq}/plan`, overlayBody());
      setPlan(p);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Could not build the plan');
    } finally {
      setPlanBusy(false);
    }
  }

  // trigger generation from the reviewed plan (nothing runs until here).
  async function triggerPlan() {
    if (streaming || !plan?.canEdit) return;
    // persist the latest overlay first, then run the reviewed plan
    try { await api.put(`/api/projects/${projectId}/phase/${selectedSeq}/plan`, overlayBody()); } catch { /* proceed */ }
    beginStream();
    try {
      await streamStageTrigger(projectId, selectedSeq, pushEvent);
    } finally {
      endStream();
      // Clear the composer so the just-submitted prompt/references don't linger.
      resetComposer();
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
      void qc.invalidateQueries({ queryKey: ['flow', projectId] });
      void qc.invalidateQueries({ queryKey: ['phase', projectId] });
    }
  }

  async function onAttach(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await api.upload(`/api/projects/${projectId}/phase/${selectedSeq}/attachments`, file);
      }
      await qc.invalidateQueries({ queryKey: ['attachments', projectId, selectedSeq] });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function removeAttachment(id: string) {
    await api.del(`/api/projects/${projectId}/phase/${selectedSeq}/attachments/${id}`);
    void qc.invalidateQueries({ queryKey: ['attachments', projectId, selectedSeq] });
  }

  const stageArtefacts = artefacts.filter((a) => a.phase === selectedSeq);
  // Prior-stage outputs the user can pin as @references (upstream of this stage).
  const priorArtefacts = artefacts.filter((a) => a.phase < selectedSeq);

  // --- inline "@" mention autosuggest ---
  // Every referenceable thing, in one list: prior generated content, templates,
  // and already-uploaded files. Selecting one pins it into the next run.
  type Mention =
    | { kind: 'artifact'; id: string; label: string; sub: string }
    | { kind: 'template'; id: string; label: string; sub: string }
    | { kind: 'file'; id: string; label: string; sub: string };
  const allMentions: Mention[] = [
    ...priorArtefacts.map((a) => ({ kind: 'artifact' as const, id: a.id, label: a.title, sub: `P${a.phase} · ${a.type}` })),
    ...formworks.map((f) => ({ kind: 'template' as const, id: f.id, label: f.name, sub: `Template · ${f.artefactType}` })),
    ...attachments.map((a) => ({ kind: 'file' as const, id: a.id, label: a.filename, sub: 'Uploaded file' })),
  ];
  const mentionMatches = mention.open
    ? allMentions.filter((m) => m.label.toLowerCase().includes(mention.query.toLowerCase())).slice(0, 8)
    : [];

  function onPromptChange(e: ChangeEvent<HTMLTextAreaElement>) {
    const value = e.target.value;
    setPrompt(value);
    const caret = e.target.selectionStart ?? value.length;
    // find an "@token" ending at the caret with no whitespace inside the token
    const before = value.slice(0, caret);
    const m = before.match(/@([\w.-]*)$/);
    if (m) setMention({ open: true, query: m[1] ?? '', at: caret - (m[1]?.length ?? 0) - 1 });
    else if (mention.open) setMention({ open: false, query: '', at: 0 });
  }

  function pickMention(m: Mention) {
    if (m.kind === 'artifact') setRefIds((prev) => (prev.includes(m.id) ? prev : [...prev, m.id]));
    if (m.kind === 'template') setFormworkIds((prev) => (prev.includes(m.id) ? prev : [...prev, m.id]));
    // files are already auto-included; selecting is a no-op beyond the mention text.
    // Replace the "@query" fragment with a readable mention token.
    const caret = textareaRef.current?.selectionStart ?? prompt.length;
    const token = `@${m.label.replace(/\s+/g, '_')} `;
    setPrompt((p) => p.slice(0, mention.at) + token + p.slice(caret));
    setMention({ open: false, query: '', at: 0 });
    setTimeout(() => textareaRef.current?.focus(), 0);
  }

  const selectedRefChips = refIds
    .map((id) => priorArtefacts.find((a) => a.id === id))
    .filter(Boolean) as Array<{ id: string; phase: number; type: string; title: string }>;
  const selectedTemplateChips = formworkIds
    .map((id) => formworks.find((f) => f.id === id))
    .filter(Boolean) as Array<{ id: string; name: string; artefactType: string }>;

  // ---- input validation for the compose box ----
  // Stage 1 must be told what to build; any stage that DOES get a prompt needs a
  // usable one (not a stray character). Later stages may run with no prompt at all.
  const trimmedPrompt = prompt.trim();
  const promptRequired = stage.phase === 1;
  const promptMissing = promptRequired && trimmedPrompt.length === 0;
  const promptTooShort = trimmedPrompt.length > 0 && trimmedPrompt.length < 12;
  const promptError = promptMissing
    ? 'Describe what to build before reviewing the plan.'
    : promptTooShort
      ? 'Add a bit more detail — at least 12 characters — so the agent has something to work with.'
      : '';
  const canReviewPlan = !planBusy && !streaming && !promptError;

  return (
    <div className="flex h-full flex-col bg-slate-50">
      {/* ---- stage header + prev/next ---- */}
      <div className="flex items-center gap-3 border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex items-center gap-1">
          <button
            onClick={() => stages[idx - 1] && onSelectStage(stages[idx - 1]!.phase)}
            disabled={idx <= 0}
            className="rounded px-1.5 py-1 text-slate-400 hover:bg-slate-100 disabled:opacity-30"
            title="Previous stage"
          >
            ‹
          </button>
          <button
            onClick={() => stages[idx + 1] && onSelectStage(stages[idx + 1]!.phase)}
            disabled={idx >= stages.length - 1}
            className="rounded px-1.5 py-1 text-slate-400 hover:bg-slate-100 disabled:opacity-30"
            title="Next stage"
          >
            ›
          </button>
        </div>
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-sm font-bold text-white">
          {stage.phase}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-bold text-slate-800">{stage.name}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${meta.cls}`}>
              {meta.icon} {meta.label}
            </span>
            {stage.stale && (
              <span className="rounded-full bg-bared-200 px-2 py-0.5 text-[10px] font-semibold text-bared-700" title={stage.staleReason ?? 'An upstream input changed'}>
                ⚠ Outdated
              </span>
            )}
          </div>
          <div className="truncate text-[11px] text-slate-500">
            {stage.persona} · gate: {stage.reviewerRole}
            {stage.assignee ? ` · ${stage.assignee.displayName}` : ' · unassigned'}
            {' · '}Level {stage.level + 1} of {(flow.levels?.length ?? 1)}
          </div>
        </div>
        <div className="hidden shrink-0 items-center gap-1 text-[10px] text-slate-400 md:flex">
          <span className="rounded bg-slate-100 px-1.5 py-0.5">in: {stage.inputs.join(', ') || '—'}</span>
          <span>→</span>
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">out: {stage.outputs.join(', ') || '—'}</span>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        {/* ---- stale / impact-propagation banner ---- */}
        {stage.stale && (
          <section className="rounded-xl border border-bared-500/40 bg-bared-200/50 p-4">
            <div className="flex items-start gap-3">
              <span className="text-lg" aria-hidden>⚠</span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-bared-700">This stage may be outdated</div>
                <p className="mt-0.5 text-xs text-bared-700/90">
                  {stage.staleReason ?? 'An upstream input was re-generated after this stage ran'}
                  {stage.staleSource ? ` (stage ${stage.staleSource})` : ''}. Its outputs were produced from the
                  previous version, so review or re-run this stage to bring it back in sync.
                </p>
                <div className="mt-2 flex items-center gap-2">
                  {stage.canRetrigger && (
                    <button
                      type="button"
                      onClick={() => retrigger.mutate(stage.phase)}
                      disabled={retrigger.isPending}
                      className="rounded-lg bg-bared-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-bared-700 disabled:opacity-50"
                    >
                      {retrigger.isPending ? 'Re-running…' : '↺ Re-run this stage'}
                    </button>
                  )}
                  {stage.staleSource != null && (
                    <button
                      type="button"
                      onClick={() => onSelectStage(stage.staleSource!)}
                      className="rounded-lg border border-bared-500/40 px-3 py-1.5 text-xs font-semibold text-bared-700 hover:bg-bared-200"
                    >
                      View upstream change
                    </button>
                  )}
                  <span className="text-[11px] text-bared-700/70">Approving this stage as-is also clears the flag.</span>
                </div>
                {retrigger.isError && (
                  <div className="mt-1.5 text-[11px] text-bared-700">
                    {retrigger.error instanceof Error ? retrigger.error.message : 'Re-run failed'}
                  </div>
                )}
              </div>
            </div>
          </section>
        )}

        {/* ---- compose & run ---- */}
        {runnable ? (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-800">
                {stage.status === 'AMEND_REQUESTED' ? '↺ Re-run with changes' : '▶ Review the plan, then run this stage'}
              </span>
              <span className="text-[11px] text-slate-400">
                the {stage.persona} agent will generate {stage.outputs.join(', ')}
              </span>
            </div>
            {stage.status === 'AMEND_REQUESTED' && (
              <div className="mb-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2 text-[11px] text-orange-800">
                ↺ Changes were requested at gate review. The reviewer's feedback is pre-filled into the plan below — review it and trigger a fresh generation.
              </div>
            )}
            <form onSubmit={reviewPlan}>
              <div className="relative">
                <textarea
                  ref={textareaRef}
                  className="w-full rounded-lg border border-slate-300 p-3 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-200"
                  rows={3}
                  placeholder={
                    stage.phase === 1
                      ? 'Describe what to build. Type @ to reference generated content, templates or uploaded files.'
                      : `Add guidance for the ${stage.persona} (optional). Type @ to pull in prior outputs, templates or files.`
                  }
                  value={prompt}
                  onChange={onPromptChange}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape' && mention.open) setMention({ open: false, query: '', at: 0 });
                  }}
                  disabled={streaming}
                />
                {/* inline @ autosuggest */}
                {mention.open && mentionMatches.length > 0 && (
                  <div className="absolute left-2 top-full z-30 mt-1 max-h-64 w-80 overflow-auto rounded-lg border border-slate-200 bg-white shadow-xl">
                    <div className="border-b border-slate-100 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                      Reference — generated content, templates &amp; files
                    </div>
                    {mentionMatches.map((m) => (
                      <button
                        type="button"
                        key={`${m.kind}-${m.id}`}
                        onClick={() => pickMention(m)}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-50"
                      >
                        <span className="text-sm">{m.kind === 'artifact' ? '📄' : m.kind === 'template' ? '📐' : '📎'}</span>
                        <span className="min-w-0 flex-1 truncate text-slate-700">{m.label}</span>
                        <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{m.sub}</span>
                      </button>
                    ))}
                  </div>
                )}
                {mention.open && mentionMatches.length === 0 && (
                  <div className="absolute left-2 top-full z-30 mt-1 w-80 rounded-lg border border-slate-200 bg-white px-3 py-2 text-[11px] text-slate-400 shadow-xl">
                    No references match “{mention.query}”. Attach a file or generate upstream stages first.
                  </div>
                )}
              </div>

              {/* ---- attach + selected-context chips ---- */}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => onAttach(e.target.files)} />
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading || streaming}
                  className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-400 hover:text-brand-700 disabled:opacity-40"
                >
                  {uploading ? 'Uploading…' : '📎 Attach files'}
                </button>
                <span className="text-[11px] text-slate-400">or type <span className="font-mono text-slate-500">@</span> to reference existing context</span>
              </div>

              {(attachments.length > 0 || selectedRefChips.length > 0 || selectedTemplateChips.length > 0) && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selectedRefChips.map((a) => (
                    <span key={`r-${a.id}`} className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 text-[11px] text-brand-700" title="Pinned prior output">
                      📄 {a.title}
                      <button type="button" onClick={() => setRefIds((p) => p.filter((id) => id !== a.id))} className="ml-0.5 text-brand-400 hover:text-red-600">✕</button>
                    </span>
                  ))}
                  {selectedTemplateChips.map((f) => (
                    <span key={`t-${f.id}`} className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[11px] text-violet-700" title="Template applied">
                      📐 {f.name}
                      <button type="button" onClick={() => setFormworkIds((p) => p.filter((id) => id !== f.id))} className="ml-0.5 text-violet-400 hover:text-red-600">✕</button>
                    </span>
                  ))}
                  {attachments.map((a) => (
                    <span key={`a-${a.id}`} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600" title={a.isText ? 'Inlined into the prompt' : 'Binary — kept but not inlined'}>
                      📎 {a.filename}{!a.isText && <span className="text-amber-600">(binary)</span>}
                      <button type="button" onClick={() => removeAttachment(a.id)} className="ml-0.5 text-slate-400 hover:text-red-600">✕</button>
                    </span>
                  ))}
                </div>
              )}

              {promptError && (
                <div className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-bared-600">
                  <span aria-hidden>⚠</span> {promptError}
                </div>
              )}
              <div className="mt-2 flex items-center gap-2">
                <button
                  disabled={!canReviewPlan}
                  aria-disabled={!canReviewPlan}
                  className="rounded-lg border border-brand-300 bg-brand-50 px-4 py-2 text-sm font-semibold text-brand-700 hover:bg-brand-100 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {planBusy ? 'Building plan…' : plan ? '↻ Update plan' : '🔍 Review plan'}
                </button>
                <span className="text-[11px] text-slate-400">
                  Nothing generates until you review the plan and trigger it. Upstream outputs are auto-included; @-references, templates &amp; attachments add curated context.
                </span>
              </div>
            </form>

            {/* ---- Plan Review & Edit ---- */}
            {plan && (
              <div className="mt-3 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
                <div className="mb-2 flex items-center gap-2">
                  <span className="text-sm font-bold text-brand-800">Execution plan</span>
                  <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-600">agent: {plan.agent.persona}</span>
                  <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold text-slate-600">model tier: {plan.agent.tier}</span>
                  {plan.overlay.origin !== 'new' && (
                    <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-semibold text-orange-700">{plan.overlay.origin}</span>
                  )}
                </div>

                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Skills</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {plan.skills.length ? plan.skills.map((s) => (
                        <span key={s.id} className="rounded bg-white px-1.5 py-0.5 text-[11px] text-slate-600">{s.name} · {s.tier}</span>
                      )) : <span className="text-[11px] text-slate-400">—</span>}
                    </div>
                    <div className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">Expected tools</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {plan.expectedTools.length ? plan.expectedTools.map((t) => (
                        <span key={t} className="rounded bg-white px-1.5 py-0.5 text-[11px] text-slate-600">🔌 {t}</span>
                      )) : <span className="text-[11px] text-slate-400">—</span>}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Context the agent will use</div>
                    <ul className="mt-1 space-y-0.5 text-[11px] text-slate-600">
                      <li>📄 {plan.context.priorArtifacts.length} prior-stage artifact(s) (auto-included)</li>
                      <li>📖 Canon rules: {plan.context.canonApplied ? 'applied' : 'none'}</li>
                      <li>📐 {plan.context.formworks.length} template(s) available · 🔎 {plan.context.ragSnippets} RAG snippet(s)</li>
                      <li>📎 {plan.context.attachments.length} attachment(s) · ✚ {plan.context.curatedInjectedChars} chars curated context injected</li>
                    </ul>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => setShowSystemPrompt((v) => !v)}
                  className="mt-3 rounded border border-slate-300 px-2 py-1 text-[11px] font-semibold text-slate-600 hover:bg-white"
                >
                  {showSystemPrompt ? '▾ Hide system-generated prompt' : '▸ View system-generated prompt'}
                </button>
                {showSystemPrompt && (
                  <div className="mt-2 max-h-72 overflow-auto rounded-lg border border-slate-200 bg-white p-3">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">System prompt (read-only)</div>
                    <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug text-slate-700">{plan.prompt.system}</pre>
                    <div className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">User turn</div>
                    <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug text-slate-700">{plan.prompt.user}</pre>
                  </div>
                )}

                <div className="mt-3 flex items-center gap-2 border-t border-brand-200 pt-3">
                  <button
                    type="button"
                    onClick={triggerPlan}
                    disabled={streaming || !plan.canEdit}
                    className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
                    title={plan.canEdit ? 'Run this stage using the reviewed plan' : 'You need write permission to trigger this stage'}
                  >
                    {streaming ? 'Agents working…' : '▶ Trigger generation'}
                  </button>
                  <span className="text-[11px] text-slate-400">
                    {plan.canEdit ? 'Edit the instructions/context above, press "Update plan" to re-render, then trigger.' : `Requires write permission (${plan.stage.writeRoles.join(', ')}).`}
                    {' '}After generation it goes to {plan.stage.reviewerRole} gate review — no advance until approved.
                  </span>
                </div>
              </div>
            )}
          </section>
        ) : blockedReason.length > 0 ? (
          <section className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-500">
            🔒 This stage runs once its upstream gate{blockedReason.length > 1 ? 's are' : ' is'} approved:{' '}
            <span className="font-medium text-slate-700">{blockedReason.join(', ')}</span>.
          </section>
        ) : null}

        {/* ---- live generation ---- */}
        {streamingHere && (
          <section className="rounded-xl border border-blue-200 bg-blue-50/60 p-4">
            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-blue-700">Generating…</div>
            <div className="space-y-0.5">
              {activity.map((a) => (
                <div key={a.id} className="flex items-center gap-1.5 text-xs text-slate-600">
                  <span>{ACTIVITY_ICON[a.kind]}</span>
                  <span className={a.status === 'error' ? 'text-red-600' : a.kind === 'artifact' ? 'text-emerald-700' : ''}>
                    {a.label}
                  </span>
                </div>
              ))}
              {liveResponse && (
                <div className="prose-chat mt-2 text-sm text-slate-700">
                  <ReactMarkdown>{liveResponse}</ReactMarkdown>
                </div>
              )}
            </div>
          </section>
        )}

        {/* ---- gate review ---- */}
        {pendingGate && pendingGate.phase === selectedSeq && (
          <GatePanel
            projectId={projectId}
            pending={pendingGate}
            artefacts={artefacts as never}
            user={user}
          />
        )}

        {/* ---- quality signals & feedback ---- */}
        {(stageArtefacts.length > 0 || ['PENDING_REVIEW', 'APPROVED', 'AMEND_REQUESTED'].includes(stage.status)) && (
          <FeedbackPanel
            projectId={projectId}
            phase={selectedSeq}
            user={user}
            canResolve={
              user.role === 'SUPER_ADMIN' ||
              user.role === 'PROJECT_MANAGER' ||
              Boolean(pendingGate && pendingGate.phase === selectedSeq && pendingGate.canReview)
            }
          />
        )}

        {/* ---- outputs ---- */}
        <section>
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Outputs ({stageArtefacts.length})
          </div>
          {stageArtefacts.length === 0 ? (
            <div className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-xs text-slate-400">
              No outputs yet. {runnable ? 'Run this stage to generate them.' : 'Waiting on upstream stages.'}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {stageArtefacts.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setViewArtefactId(a.id)}
                  className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white p-2.5 text-left transition hover:border-brand-300 hover:shadow-sm"
                >
                  <span className="mt-0.5 rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700">{a.type}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-800">{a.title}</span>
                    <span className="text-[10px] text-brand-600">Open in viewer →</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>

        {/* ---- stage thread ---- */}
        <section>
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Stage conversation ({stageMessages.length})
          </div>
          {stageMessages.length === 0 && !streamingHere ? (
            <div className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-xs text-slate-400">
              The stage's prompts, agent summaries and review feedback will appear here and are fed back to the agent on re-runs.
            </div>
          ) : (
            <div className="space-y-2">
              {stageMessages.map((m) => (
                <div
                  key={m.id}
                  className={`rounded-xl px-3 py-2 text-sm ${
                    m.role === 'user'
                      ? 'ml-8 bg-brand-600 text-white'
                      : 'mr-8 border border-slate-200 bg-white text-slate-800'
                  }`}
                >
                  <div className="prose-chat">
                    <ReactMarkdown>{m.content}</ReactMarkdown>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div ref={threadRef} />
        </section>
      </div>

      {viewArtefactId && (
        <ArtifactViewer projectId={projectId} artefactId={viewArtefactId} onClose={() => setViewArtefactId(null)} canRepair={stage.canRetrigger} />
      )}
    </div>
  );
}
