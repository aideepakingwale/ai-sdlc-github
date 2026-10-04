import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import { api, streamStageProgress } from '../api/client';
import { PartTabs } from './PartTabs';
import { StageDocument } from './StageDocument';
import { Icon, type IconName } from './ui/Icon';
import { Callout } from './ui/Callout';
import { Badge, StatusBadge } from './ui/Badge';
import { Button } from './ui/Button';
import { SectionLabel } from './ui/Card';
import { Stepper } from './ui/Stepper';
import { deriveGuide } from '../lib/stageGuide';
import { useStickToBottom } from '../hooks/useStickToBottom';
import type { ProjectFlow } from '../api/flow';
import type { ChatMessage, PhaseStateView, User } from '../api/types';
import { useApp, type ActivityItem } from '../store';
import ArtifactViewer from './ArtifactViewer';
import FeedbackPanel from './FeedbackPanel';
import GatePanel from './GatePanel';
import ProposalReview from './agile/ProposalReview';
import { useOverview } from './agile/hooks';


/** Plain-language names + icons for the AI-judged project traits. */
const TRAIT_META: Record<string, { label: string; icon: IconName; hint: string }> = {
  ui: { label: 'User interface', icon: 'monitor', hint: 'Has screens users interact with' },
  api: { label: 'API', icon: 'code', hint: 'Exposes or consumes an API' },
  database: { label: 'Database', icon: 'database', hint: 'Stores data in a database' },
  cloud: { label: 'Cloud', icon: 'cloud', hint: 'Runs on a cloud platform' },
  aws: { label: 'AWS', icon: 'cloud', hint: 'Targets Amazon Web Services' },
  container: { label: 'Containers', icon: 'box', hint: 'Ships as containers (Docker / Kubernetes)' },
  service: { label: 'Runs as a service', icon: 'server', hint: 'A long-running service that can be load- or security-tested' },
};

const ACTIVITY_ICON: Record<ActivityItem['kind'], IconName> = { node: 'sparkles', tool: 'zap', artifact: 'file', gate: 'shield' };

// D-112: compact, locale-aware timestamp for the discussion history.
function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// D-56 Plan Review & Edit gate
interface StagePlan {
  phase: number;
  status: string;
  canEdit: boolean;
  blockedOn: string[];
  stage: { name: string; persona: string; reviewerRole: string; writeRoles: string[]; outputs: string[]; inputs: string[] };
  agent: { persona: string; tier: string; nodes: string[] };
  skills: Array<{ id: string; name: string; tier: string }>;
  expectedTools: string[];
  // Pending interactive clarification (D-108); null when none.
  clarification?: Array<{
    id: string;
    question: string;
    header?: string;
    options?: Array<{ label: string; description?: string }>;
    multiSelect?: boolean;
    rationale?: string;
  }> | null;
  // Intelligent, context-aware plan (D-105); null when disabled/unavailable.
  // AI-judged project traits (code enforces them); a project lead can override each.
  planState?: PlanState;
  traits?: Array<{ trait: string; value: boolean | null; source: string; evidence: string; confidence: number }>;
  intel?: {
    understood: string;
    willProduce: Array<{ output: string; recommended: boolean; include: boolean; reason: string }>;
    suggestedArtifacts?: Array<{ name: string; reason: string; include: boolean }>;
    promptChecks?: string[];
    formatSource: string;
    outOfScope: string[];
    recommendation: string;
    summary: string;
    steps: Array<{ id: string; label: string; kind: string; tier: string; rationale: string }>;
    toolRecommendations: Array<{ tool: string; use: boolean; rationale: string }>;
    skillRecommendations: string[];
    assumptions: string[];
    risks: string[];
    cached: boolean;
  } | null;
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
  // A stage runs when its dependencies are approved. Entry stages (no dependsOn)
  // have none, so a dynamic workflow can start at any phase.
  return stage.dependsOn.every((dep) => byKey.get(dep)?.status === 'APPROVED');
}

/**
 * Stage Workspace (D-44): the stage-centric working surface — the primary
 * interaction area. For the selected stage it shows status, a compose/run area
 * (when the stage is runnable), the gate review decision bar (when pending),
 * the produced output files, and the stage's own conversation thread. Upstream
 * navigation is the Pipeline Rail; prev/next moves along the pipeline here.
 */
/** Server-side plan lifecycle (shared by every tab/session of the project). */
type PlanState = {
  building: boolean; ready: boolean; planned: boolean; stale: boolean; fresh: boolean;
  generating: boolean; locked: boolean;
};

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
  const { streaming, activity, liveResponse, liveParts, beginStream, pushEvent, endStream } = useApp();
  const [prompt, setPrompt] = useState('');
  const [viewArtefactId, setViewArtefactId] = useState<string | null>(null);
  const [refIds, setRefIds] = useState<string[]>([]);
  const [formworkIds, setFormworkIds] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [plan, setPlan] = useState<StagePlan | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  // D-112: the reviewer's decision on which outputs to produce (defaults to the
  // agent's recommendation from the proposal); folded into generation on trigger.
  const [produceSel, setProduceSel] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const wp = plan?.intel?.willProduce;
    if (wp && wp.length) {
      setProduceSel(Object.fromEntries(wp.map((a) => [a.output, a.include ?? a.recommended])));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan]);
  // Planner-suggested artifacts that are NOT in the standard template (opt-in).
  const [suggestSel, setSuggestSel] = useState<Record<string, boolean>>({});
  useEffect(() => setSuggestSel({}), [plan?.intel?.suggestedArtifacts]);
  const [showSystemPrompt, setShowSystemPrompt] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  // D-112 Phase C: a conversational thread for the plan. The agent's proposal and
  // the reviewer's free-form refinements render as chat turns; each refinement is
  // APPENDED to the overlay and re-plans, so the discussion actually steers the run.
  type Turn = { role: 'you' | 'agent'; text: string; ts: number };
  const [thread, setThread] = useState<Turn[]>([]);
  const [refineText, setRefineText] = useState('');
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ block: 'nearest' });
  }, [thread]);
  // Reset the discussion when the selected stage changes.
  useEffect(() => { setThread([]); setRefineText(''); setFormatMode(null); }, [selectedSeq]);
  // Inline "@" mention autosuggest (D-56).
  const [mention, setMention] = useState<{ open: boolean; query: string; at: number }>({
    open: false, query: '', at: 0,
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const reconnectKeyRef = useRef<string | null>(null); // guards double-attach (D-97 L2)

  // D-54: files the user attached to THIS stage, and the prior-stage outputs
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
  // D-112: explicit output-format CHOICE (no prose inference). null = use the default
  // (follow the attachment when one is present, else the system template).
  const [formatMode, setFormatMode] = useState<'system' | 'attached' | null>(null);
  const effectiveFormatMode: 'system' | 'attached' =
    formatMode ?? (attachments.length > 0 ? 'attached' : 'system');

  // D-56: templates (formworks) available to @-reference — project + platform.
  const formworksQ = useQuery({
    queryKey: ['formworks', projectId],
    queryFn: () =>
      api.get<{ formworks: Array<{ id: string; name: string; artefactType: string; scope: string }> }>(
        `/api/projects/${projectId}/formworks`,
      ),
    enabled: Boolean(projectId),
  });
  const formworks = formworksQ.data?.formworks ?? [];

  // D-107 step 2: per-artifact-part status (✓/✗) for this stage, with per-part retrigger.
  const partsQ = useQuery({
    queryKey: ['parts', projectId, selectedSeq],
    queryFn: () =>
      api.get<{ parts: Array<{ field: string; status: 'running' | 'done' | 'failed'; error: string | null; text: string; updatedAt: string }> }>(
        `/api/projects/${projectId}/phase/${selectedSeq}/parts`,
      ),
    enabled: Boolean(projectId),
  });
  const parts = partsQ.data?.parts ?? [];
  // 'document' (single attached-format file) has no separate artifacts to pick from.
  const regenerable = parts.filter((p) => p.field !== 'document' && p.status !== 'running');
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => setPicked([]), [projectId, selectedSeq]);

  // D-108: pending interactive clarifying questions for this stage (answer cards).
  type ClarQ = NonNullable<StagePlan['clarification']>[number];
  const clarificationQ = useQuery({
    queryKey: ['clarification', projectId, selectedSeq],
    queryFn: () => api.get<{ clarification: ClarQ[] | null }>(
      `/api/projects/${projectId}/phase/${selectedSeq}/clarification`,
    ),
    enabled: Boolean(projectId),
  });
  const clarification = clarificationQ.data?.clarification ?? null;
  const [clarifyAns, setClarifyAns] = useState<Record<string, { selected: string[]; other: string }>>({});
  // D-112: step through the questions ONE AT A TIME (Claude-Code style) — the LLM
  // returns all gaps with options in one response; the user answers them in sequence
  // and every answer is submitted back together.
  const [clarifyStep, setClarifyStep] = useState(0);
  useEffect(() => { setClarifyStep(0); }, [selectedSeq, clarification?.length]);

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

  // The plan lives on the SERVER (building flag + cached result), not in this tab: opening
  // the same project in another tab/session shows the same state — "Building plan…" while a
  // plan is being built elsewhere, and the built plan (with the saved instructions) once ready.
  useEffect(() => {
    if (!selectedSeq) return;
    let cancelled = false;
    let markedBusy = false;
    const base = `/api/projects/${projectId}/phase/${selectedSeq}/plan`;
    (async () => {
      try {
        let st = await api.get<{ building: boolean; ready: boolean }>(`${base}/state`);
        const wasBuilding = st.building;
        if (st.building) {
          markedBusy = true;
          setPlanBusy(true);
          while (!cancelled && st.building) {
            await new Promise((r) => setTimeout(r, 2000));
            st = await api.get<{ building: boolean; ready: boolean }>(`${base}/state`);
          }
        }
        if (cancelled || (!st.ready && !wasBuilding)) return;
        const p = await api.get<StagePlan>(`${base}?cached=true`);
        if (cancelled) return;
        setPlan(p);
        const ov = p.overlay;
        if (ov) {
          setPrompt((cur) => cur || (ov.promptOverlay ?? '').split(/##\s*Production scope/)[0]!.trim());
          setRefIds((cur) => (cur.length ? cur : ov.referencedArtifactIds ?? []));
          setFormworkIds((cur) => (cur.length ? cur : ov.formworkIds ?? []));
        }
      } catch { /* no shared plan state — the normal Review plan flow applies */ }
      finally { if (markedBusy && !cancelled) setPlanBusy(false); }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, selectedSeq]);

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
  const agileOv = useOverview(projectId);   // permissions for the Agile proposal review (cached; shared with the Agile hub)
  const idx = stages.findIndex((s) => s.phase === stage?.phase);
  const runnable = stage ? isRunnable(stage, byKey) : false;

  const stageMessages = useMemo(
    () => messages.filter((m) => m.phase === selectedSeq),
    [messages, selectedSeq],
  );
  const streamingHere = streaming && stage?.phase === flow.currentPhase;

  // Follow new output only while the user is already at the bottom — never yank them down
  // while they are reading a tab/log higher up (this used to scroll on every streamed update).
  useStickToBottom(threadRef, [stageMessages.length, activity.length, liveResponse]);

  if (!stage) return null;

  const blockedReason = !runnable && stage.status !== 'APPROVED' && stage.status !== 'PENDING_REVIEW' && stage.status !== 'IN_PROGRESS'
    ? stage.dependsOn
        .filter((dep) => byKey.get(dep)?.status !== 'APPROVED')
        .map((dep) => byKey.get(dep)?.name ?? dep)
    : [];

  const overlayBody = () => ({
    promptOverlay: prompt,
    referencedArtifactIds: refIds,
    attachmentIds: attachments.map((a) => a.id),
    formworkIds,
  });

  // D-112: the reviewer's decision (which outputs to produce + the format) as an
  // explicit instruction folded into the overlay at trigger time, so generation
  // honours exactly what was chosen. Phase B enforces this at the schema/runner level;
  // for now it steers the agent directly.
  const productionDirective = (): string => {
    const intel = plan?.intel;
    const wp = intel?.willProduce ?? [];
    if (!intel || wp.length === 0) return '';
    const on = (o: { output: string; include?: boolean; recommended?: boolean }) =>
      produceSel[o.output] ?? o.include ?? o.recommended;
    const include = wp.filter(on).map((a) => a.output);
    const exclude = wp.filter((a) => !on(a)).map((a) => a.output);
    const lines: string[] = [];
    if (include.length) lines.push(`Produce ONLY these artifacts: ${include.join(', ')}.`);
    if (exclude.length) lines.push(`Do NOT produce: ${exclude.join(', ')}.`);
    const extras = (intel.suggestedArtifacts ?? []).filter((x) => suggestSel[x.name]).map((x) => x.name);
    if (extras.length) lines.push(`Also include (approved additions to the standard template): ${extras.join(', ')}.`);
    // D-112: the format is an explicit reviewer CHOICE, not inferred from prose. We emit
    // a deterministic machine token the backend switches on — ATTACHED_DOCUMENT (follow
    // the uploaded file's structure) or SYSTEM_DEFAULT (the stage's recommended template).
    if (effectiveFormatMode === 'attached' && attachments.length) {
      lines.push(`Follow this output format: ATTACHED_DOCUMENT — ${attachments.map((a) => a.filename).join(', ')}`);
    } else {
      lines.push('Follow this output format: SYSTEM_DEFAULT');
    }
    return lines.length ? `\n\n## Production scope (confirmed by the reviewer)\n${lines.map((l) => `- ${l}`).join('\n')}` : '';
  };

  // D-112 Phase C: a concise, chat-style summary of what the agent proposed, logged
  // as an agent turn after each (re)plan so the discussion reads as a conversation.
  const proposalSummary = (intel: StagePlan['intel']): string => {
    if (!intel) return 'Plan ready — review it below, then trigger when you’re happy.';
    const on = (o: { output: string; include?: boolean; recommended?: boolean }) =>
      produceSel[o.output] ?? o.include ?? o.recommended;
    const parts: string[] = [];
    if (intel.understood) parts.push(intel.understood);
    const rec = (intel.willProduce ?? []).filter(on).map((a) => a.output);
    if (rec.length) parts.push(`I’ll produce: ${rec.join(', ')}.`);
    if (intel.formatSource) parts.push(`Format: ${intel.formatSource}.`);
    if (intel.recommendation) parts.push(intel.recommendation);
    return parts.join(' ') || (intel.summary ?? 'Plan ready.');
  };

  // Pin a project trait (or clear it) — wins over the AI's judgement — then re-plan.
  async function overrideTrait(trait: string, value: 'present' | 'absent' | null) {
    try {
      await api.put(`/api/projects/${projectId}/traits/${trait}`, { value });
      await reviewPlan();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Could not change this setting');
    }
  }

  // D-56: save the overlay and (re-)render the full plan — "Review / Update plan".
  // D-112 Phase C: `append` folds a free-form refinement into the overlay (additive,
  // so earlier guidance is kept) and `logTurns` records the exchange in the thread.
  async function reviewPlan(e?: FormEvent, opts?: { append?: string; logTurns?: boolean; userMessage?: string }) {
    e?.preventDefault();
    if (planBusy || locked || promptError) return;
    setPlanBusy(true);
    setMention({ open: false, query: '', at: 0 });
    const append = opts?.append?.trim();
    const nextPrompt = append ? (prompt.trim() ? `${prompt.trim()}\n${append}` : append) : prompt;
    if (append) setPrompt(nextPrompt);
    try {
      // Persist the overlay (fast, no planner), then GET the plan which computes the
      // intelligent plan ONCE (cached) — D-109. Previously the PUT itself ran the ~30s
      // planner, and both review + trigger PUT, so it fired several times per cycle.
      await api.put<StagePlan>(`/api/projects/${projectId}/phase/${selectedSeq}/plan`, { ...overlayBody(), promptOverlay: nextPrompt });
      const p = await api.get<StagePlan>(`/api/projects/${projectId}/phase/${selectedSeq}/plan`);
      setPlan(p);
      if (opts?.logTurns) {
        const summary = proposalSummary(p.intel);
        setThread((t) => [...t, { role: 'agent', text: summary, ts: Date.now() }]);
        // D-112: persist the planning exchange so it survives navigation and shows
        // in the timestamped Discussion history (best-effort — never block planning).
        const um = (opts.userMessage ?? '').trim();
        if (um || summary) {
          try {
            await api.post(`/api/projects/${projectId}/phase/${selectedSeq}/discuss`, { userMessage: um, agentMessage: summary });
            void qc.invalidateQueries({ queryKey: ['project', projectId] });
          } catch { /* history is best-effort */ }
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not build the plan';
      if (opts?.logTurns) setThread((t) => [...t, { role: 'agent', text: `⚠ ${msg}`, ts: Date.now() }]);
      else window.alert(msg);
    } finally {
      setPlanBusy(false);
    }
  }

  // D-112 Phase C: send a free-form refinement as a chat turn — logs it, appends it
  // to the overlay, and re-plans so the agent responds with an updated proposal.
  async function sendRefinement() {
    const text = refineText.trim();
    if (!text || planBusy || locked) return;
    setThread((t) => [...t, { role: 'you', text, ts: Date.now() }]);
    setRefineText('');
    await reviewPlan(undefined, { append: text, logTurns: true, userMessage: text });
  }

  // D-112 Phase C: the composer's "Review / Update plan" — seeds the discussion with
  // the reviewer's initial description, then logs the agent's proposal as a turn.
  async function onReviewSubmit(e?: FormEvent) {
    e?.preventDefault();
    const p = prompt.trim();
    if (p && thread.length === 0) setThread([{ role: 'you', text: p, ts: Date.now() }]);
    await reviewPlan(undefined, { logTurns: true, userMessage: p });
  }

  // D-99: ENQUEUE the run (returns immediately) then watch its progress. The run is
  // executed by a background worker — decoupled from this request — so navigating
  // away never cancels it; the progress stream is just a viewer.
  async function triggerPlan() {
    if (streaming || !plan?.canEdit || !planFresh) return;
    // D-100: flip to the "Generating…" view SYNCHRONOUSLY, before the enqueue
    // round-trips, so the click is never a dead no-op. Without this the two awaits
    // below (save overlay + enqueue) leave the plan sitting unchanged for a beat,
    // then a flash of the empty "not started" composer — which reads as "nothing
    // happened / no artifacts". beginStream() also guards re-clicks (streaming=true).
    beginStream();
    pushEvent({ type: 'node', node: 'queue', label: 'Starting the run…' } as never);
    // persist the latest overlay (with the reviewer's production-scope decision) first,
    // then enqueue the reviewed run.
    try {
      const base = overlayBody();
      await api.put(`/api/projects/${projectId}/phase/${selectedSeq}/plan`, { ...base, promptOverlay: base.promptOverlay + productionDirective() });
    } catch { /* proceed */ }
    try {
      await api.post(`/api/projects/${projectId}/phase/${selectedSeq}/plan/trigger`, {});
    } catch (err) {
      endStream();
      window.alert(err instanceof Error ? err.message : 'Could not start generation');
      return;
    }
    resetComposer(); // the run is queued; clear the composer
    try {
      await streamStageProgress(projectId, selectedSeq, pushEvent);
    } finally {
      // Refresh the stage status BEFORE dropping the streaming view, so it flips
      // straight from "Generating…" to "pending review" with no not-started flash.
      try { await qc.refetchQueries({ queryKey: ['flow', projectId] }); } catch { /* ignore */ }
      endStream();
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
      void qc.invalidateQueries({ queryKey: ['flow', projectId] });
      void qc.invalidateQueries({ queryKey: ['phase', projectId] });
      void qc.invalidateQueries({ queryKey: ['parts', projectId, selectedSeq] });  // D-107 step 2
      void qc.invalidateQueries({ queryKey: ['clarification', projectId, selectedSeq] });  // D-108
    }
  }

  // Run a part-level generation job (retrigger one part, or regenerate a selection) and
  // watch its progress. Only the chosen parts are regenerated; the rest are reused.
  async function runPartsJob(label: string, url: string, body: unknown) {
    if (streaming) return;
    beginStream();
    pushEvent({ type: 'node', node: 'queue', label } as never);
    try {
      await api.post(url, body);
    } catch (err) {
      endStream();
      window.alert(err instanceof Error ? err.message : 'Could not start generation');
      return;
    }
    try {
      await streamStageProgress(projectId, selectedSeq, pushEvent);
    } finally {
      try { await qc.refetchQueries({ queryKey: ['flow', projectId] }); } catch { /* ignore */ }
      endStream();
      void qc.invalidateQueries({ queryKey: ['parts', projectId, selectedSeq] });
      void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      void qc.invalidateQueries({ queryKey: ['phase', projectId] });
    }
  }

  // D-107 step 2: regenerate ONE failed part and merge it.
  const retriggerPart = (field: string) =>
    runPartsJob(`Retriggering ${field}…`,
      `/api/projects/${projectId}/phase/${selectedSeq}/parts/${encodeURIComponent(field)}/retrigger`, {});

  // Regenerate chosen artifacts on demand (also after approval). Empty = all.
  async function regenerateParts(fields: string[]) {
    const all = fields.length === 0 || fields.length === regenerable.length;
    const what = all ? 'ALL artifacts' : `${fields.length} selected artifact(s) (${fields.join(', ')})`;
    const note = stage?.status === 'APPROVED'
      ? ' This stage is approved: it returns to review and downstream stages are flagged stale.' : '';
    if (!window.confirm(`Regenerate ${what} of this stage? The other artifacts are kept.${note}`)) return;
    setPicked([]);
    await runPartsJob(`Regenerating ${all ? 'all artifacts' : fields.join(', ')}…`,
      `/api/projects/${projectId}/phase/${selectedSeq}/regenerate`, { fields: all ? [] : fields });
  }

  // D-108: submit answers to the clarifying questions, then generate.
  async function submitClarification() {
    if (streaming || !clarification) return;
    const answers = clarification.map((q) => {
      const a = clarifyAns[q.id] || { selected: [], other: '' };
      const parts_ = [...a.selected];
      if (a.other.trim()) parts_.push(a.other.trim());
      return { question: q.question, answer: parts_.join('; ') };
    });
    beginStream();
    pushEvent({ type: 'node', node: 'queue', label: 'Applying your answers…' } as never);
    try {
      await api.post(`/api/projects/${projectId}/phase/${selectedSeq}/clarify`, { answers });
    } catch (err) {
      endStream();
      window.alert(err instanceof Error ? err.message : 'Could not submit answers');
      return;
    }
    setClarifyAns({});
    try {
      await streamStageProgress(projectId, selectedSeq, pushEvent);
    } finally {
      try { await qc.refetchQueries({ queryKey: ['flow', projectId] }); } catch { /* ignore */ }
      endStream();
      void qc.invalidateQueries({ queryKey: ['clarification', projectId, selectedSeq] });
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
      void qc.invalidateQueries({ queryKey: ['phase', projectId] });
      void qc.invalidateQueries({ queryKey: ['parts', projectId, selectedSeq] });
    }
  }

  // D-97 L2: if this stage has a generation job running (e.g. it was triggered then
  // the tab/project was switched), auto-attach to its live progress on open so the
  // "Generating…" view resumes instead of looking idle. The server run is durable;
  // this stream is just a viewer — aborting it (navigating away) never cancels the run.
  useEffect(() => {
    if (!selectedSeq || streaming) return;
    const attachKey = `${projectId}:${selectedSeq}`;
    if (reconnectKeyRef.current === attachKey) return;
    const ctrl = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const job = await api.get<{ running: boolean }>(`/api/projects/${projectId}/phase/${selectedSeq}/job`);
        if (cancelled || !job.running || streaming) return;
        reconnectKeyRef.current = attachKey;
        beginStream();
        try {
          await streamStageProgress(projectId, selectedSeq, pushEvent, ctrl.signal);
        } finally {
          endStream();
          reconnectKeyRef.current = null;
          void qc.invalidateQueries({ queryKey: ['flow', projectId] });
          void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
          void qc.invalidateQueries({ queryKey: ['project', projectId] });
          void qc.invalidateQueries({ queryKey: ['phase', projectId] });
          void qc.invalidateQueries({ queryKey: ['parts', projectId, selectedSeq] });  // D-107 step 2
        }
      } catch {
        /* no running job / not reachable — nothing to resume */
      }
    })();
    return () => { cancelled = true; ctrl.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, selectedSeq]);

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

  // --- inline "@" mention autosuggest (D-56) ---
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
  // ---- plan lifecycle: draft → building → ready(fresh) ⇄ out-of-date → generating(locked) ----
  // Server-owned (so every tab agrees); the local edit check just reacts instantly before the
  // next poll. Generation needs a FRESH plan; while generating, editing/re-planning is locked.
  const planStateQ = useQuery({
    queryKey: ['planState', projectId, selectedSeq],
    queryFn: () => api.get<PlanState>(`/api/projects/${projectId}/phase/${selectedSeq}/plan/state`),
    enabled: Boolean(projectId && selectedSeq),
    refetchInterval: 3000,
  });
  const ps: PlanState | undefined = planStateQ.data ?? plan?.planState;
  const locked = streaming || Boolean(ps?.generating);
  const sameIds = (a: string[], b: string[]) => a.length === b.length && [...a].sort().every((x, i) => x === [...b].sort()[i]);
  const planBaseline = (plan?.overlay.promptOverlay ?? '').split(/##\s*Production scope/)[0]!.trim();
  const localDirty = plan
    ? prompt.trim() !== planBaseline
      || !sameIds(refIds, plan.overlay.referencedArtifactIds ?? [])
      || !sameIds(formworkIds, plan.overlay.formworkIds ?? [])
    : false;
  const planBuilding = planBusy || Boolean(ps?.building);
  const planOutdated = Boolean(plan) && !planBuilding && !locked && (localDirty || Boolean(ps?.stale));
  const planFresh = Boolean(plan) && !planBuilding && !locked && !localDirty && Boolean(ps?.fresh);
  const canReviewPlan = !planBusy && !locked && !promptError;

  // ---- guidance: the four-step journey + exactly what to do next, in plain words ----
  const blockedNames = ['NOT_STARTED', 'AMEND_REQUESTED'].includes(stage.status)
    ? stage.dependsOn.filter((d) => byKey.get(d)?.status !== 'APPROVED').map((d) => byKey.get(d)?.name ?? d)
    : [];
  const guide = deriveGuide({
    stageStatus: stage.status, canEdit: plan?.canEdit ?? stage.canRetrigger, blockedOn: blockedNames,
    hasPlan: Boolean(plan), planBuilding, planOutdated, planFresh, generating: locked,
    hasOutputs: stageArtefacts.length > 0, reviewerRole: stage.reviewerRole, staleReason: stage.stale ? stage.staleReason : null,
  });
  const guideButton: { label: string; icon: IconName; run: () => void; disabled?: boolean } | null = (() => {
    switch (guide.next.action) {
      case 'review-plan': return { label: 'Review plan', icon: 'search', run: () => (promptError ? textareaRef.current?.focus() : void reviewPlan()), disabled: planBusy || locked };
      case 'update-plan': return { label: 'Update plan', icon: 'refresh', run: () => void reviewPlan(), disabled: !canReviewPlan };
      case 'generate': return { label: 'Generate', icon: 'play', run: () => void triggerPlan(), disabled: !planFresh || !plan?.canEdit };
      case 'open-gate': return { label: 'Go to review', icon: 'arrow-right', run: () => document.getElementById('gate-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) };
      default: return null;
    }
  })();

  return (
    <div className="flex h-full flex-col bg-slate-50">
      {/* ---- stage header: where am I, what state is it in, who signs it off ---- */}
      <div className="border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <div className="flex items-center">
            <button
              onClick={() => stages[idx - 1] && onSelectStage(stages[idx - 1]!.phase)}
              disabled={idx <= 0}
              className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
              title="Previous stage" aria-label="Previous stage"
            >
              <Icon name="chevron-left" size={18} />
            </button>
            <button
              onClick={() => stages[idx + 1] && onSelectStage(stages[idx + 1]!.phase)}
              disabled={idx >= stages.length - 1}
              className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
              title="Next stage" aria-label="Next stage"
            >
              <Icon name="chevron-right" size={18} />
            </button>
          </div>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-600 text-sm font-bold text-white">
            {stage.phase}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-base font-bold text-slate-800">{stage.name}</h2>
              <StatusBadge status={stage.status} />
              {stage.stale && (
                <Badge tone="warning" icon="warning" title={stage.staleReason ?? 'An upstream input changed'}>Outdated</Badge>
              )}
              {locked && <Badge tone="info" icon="lock" title="Editing is locked while this stage generates">Editing locked</Badge>}
              {stage.iterationLabel && <Badge tone="brand" icon="flag" title="The sprint this stage belongs to">{stage.iterationLabel}</Badge>}
              {stage.release && stage.scope === 'release' && <Badge tone="brand" icon="flag">Release {stage.release}</Badge>}
              {stage.gateMode === 'lightweight' && <Badge icon="check" title="One authorised reviewer's approval completes this gate">Light review</Badge>}
              {stage.gateMode === 'auto' && <Badge tone="info" icon="zap" title="Approved automatically when the independent validator's score meets the project's bar and nothing is open; otherwise a person reviews it">Auto-gate</Badge>}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1" title="The AI agent that works on this stage"><Icon name="sparkles" size={12} />{stage.persona}</span>
              <span className="inline-flex items-center gap-1" title="The role that signs off this stage"><Icon name="shield" size={12} />Sign-off: {stage.reviewerRole}</span>
              <span className="inline-flex items-center gap-1"><Icon name="user" size={12} />{stage.assignee ? stage.assignee.displayName : 'Unassigned'}</span>
              <span>Step {stage.level + 1} of {flow.levels?.length ?? 1}</span>
            </div>
          </div>
        </div>
        {stage.outputs.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500" aria-label="Inputs and outputs of this stage">
            {stage.inputs.length > 0 && (
              <>
                <span className="inline-flex items-center gap-1"><Icon name="inbox" size={12} />Uses</span>
                {stage.inputs.map((i) => <Badge key={i}>{i}</Badge>)}
                <Icon name="arrow-right" size={12} className="text-slate-300" />
              </>
            )}
            <span className="inline-flex items-center gap-1"><Icon name="file" size={12} />Produces</span>
            {stage.outputs.map((o) => <Badge key={o} tone="brand">{o}</Badge>)}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        {/* ---- where you are + what to do next ---- */}
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="stage-guide">
          <Stepper steps={guide.steps} />
          <Callout
            className="mt-4" tone={guide.next.tone} title={guide.next.title}
            icon={guide.next.action === 'wait' ? 'loader' : undefined} spin={guide.next.action === 'wait'}
            action={guideButton && (
              <Button variant="primary" size="sm" icon={guideButton.icon} disabled={guideButton.disabled} onClick={guideButton.run}>
                {guideButton.label}
              </Button>
            )}
          >
            {guide.next.body}
          </Callout>
        </div>

        {/* ---- stale / impact-propagation: advice (yellow), never an error ---- */}
        {stage.stale && (
          <Callout
            tone="warning" title="This stage may be outdated"
            action={
              <div className="flex flex-wrap items-center gap-2">
                {stage.canRetrigger && (
                  <Button variant="warning" size="sm" icon="refresh" loading={retrigger.isPending} onClick={() => retrigger.mutate(stage.phase)}>
                    {retrigger.isPending ? 'Re-running…' : 'Re-run this stage'}
                  </Button>
                )}
                {stage.staleSource != null && (
                  <Button variant="secondary" size="sm" onClick={() => onSelectStage(stage.staleSource!)}>View upstream change</Button>
                )}
              </div>
            }
          >
            {stage.staleReason ?? 'An upstream input was re-generated after this stage ran'}
            {stage.staleSource ? ` (stage ${stage.staleSource})` : ''}. Its outputs were produced from the previous version — re-run
            this stage to bring it back in sync, or approve it as-is to clear this notice.
            {retrigger.isError && (
              <div className="mt-1 font-medium text-red-700">
                {retrigger.error instanceof Error ? retrigger.error.message : 'Re-run failed'}
              </div>
            )}
          </Callout>
        )}

        {/* ---- interactive clarification (D-108/D-112): ONE question at a time
               (Claude-Code style). The LLM returned every gap + options in a single
               response; the user steps through them and all answers submit together. ---- */}
        {clarification && clarification.length > 0 && !streamingHere && (() => {
          const total = clarification.length;
          const step = Math.min(clarifyStep, total - 1);
          const q = clarification[step];
          if (!q) return null;
          const ans = clarifyAns[q.id] || { selected: [], other: '' };
          const answered = (id: string) => {
            const a = clarifyAns[id];
            return !!a && (a.selected.length > 0 || a.other.trim().length > 0);
          };
          const answeredCount = clarification.filter((x) => answered(x.id)).length;
          const toggle = (label: string) => setClarifyAns((prev) => {
            const cur = prev[q.id] || { selected: [], other: '' };
            let selected: string[];
            if (q.multiSelect) {
              selected = cur.selected.includes(label) ? cur.selected.filter((l) => l !== label) : [...cur.selected, label];
            } else {
              selected = cur.selected.includes(label) ? [] : [label];
            }
            return { ...prev, [q.id]: { ...cur, selected } };
          });
          const isLast = step === total - 1;
          return (
            <section className="rounded-xl border border-amber-300 bg-amber-50/60 p-4">
              <div className="mb-1 flex items-center justify-between gap-2">
                <div className="text-sm font-bold text-amber-900">A few questions before generating</div>
                <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                  Question {step + 1} of {total}
                </span>
              </div>
              {/* progress dots — click a dot to jump to that question */}
              <div className="mb-3 flex items-center gap-1.5">
                {clarification.map((x, i) => (
                  <button
                    key={x.id} type="button" onClick={() => setClarifyStep(i)} disabled={streaming}
                    title={x.question}
                    className={`h-1.5 rounded-full transition-all ${
                      i === step ? 'w-6 bg-amber-500' : answered(x.id) ? 'w-3 bg-emerald-400' : 'w-3 bg-amber-200'
                    }`}
                  />
                ))}
                <span className="ml-1 text-[10px] text-amber-700">{answeredCount}/{total} answered · blank = let the agent decide</span>
              </div>

              <div className="rounded-lg border border-amber-200 bg-white p-3">
                <div className="flex items-center gap-2">
                  {q.header && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-800">{q.header}</span>}
                  <span className="text-[13px] font-semibold text-slate-800">{q.question}</span>
                </div>
                {q.rationale && <div className="mt-0.5 text-[11px] text-slate-500">{q.rationale}</div>}
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(q.options || []).map((o) => {
                    const on = ans.selected.includes(o.label);
                    return (
                      <button
                        key={o.label} type="button" onClick={() => toggle(o.label)} disabled={streaming}
                        title={o.description || ''}
                        className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition disabled:opacity-40 ${
                          on ? 'border-brand-500 bg-brand-600 text-white' : 'border-slate-300 bg-white text-slate-600 hover:border-brand-400'
                        }`}
                      >
                        {q.multiSelect ? (on ? '☑ ' : '☐ ') : (on ? '● ' : '○ ')}{o.label}
                      </button>
                    );
                  })}
                </div>
                {(q.options || []).some((o) => !!o.description) && (
                  <div className="mt-1.5 space-y-0.5">
                    {(q.options || []).filter((o) => ans.selected.includes(o.label) && o.description).map((o) => (
                      <div key={o.label} className="text-[10px] text-slate-500">{o.label}: {o.description}</div>
                    ))}
                  </div>
                )}
                <input
                  type="text" disabled={streaming}
                  placeholder="Other / add detail…"
                  value={ans.other}
                  onChange={(e) => setClarifyAns((prev) => ({ ...prev, [q.id]: { ...(prev[q.id] || { selected: [], other: '' }), other: e.target.value } }))}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); if (isLast) submitClarification(); else setClarifyStep(step + 1); } }}
                  className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-[12px] focus:border-brand-400 focus:outline-none"
                />
              </div>

              <div className="mt-3 flex items-center gap-2">
                <button
                  type="button" onClick={() => setClarifyStep(Math.max(0, step - 1))} disabled={streaming || step === 0}
                  className="rounded-lg border border-amber-300 px-3 py-2 text-xs font-semibold text-amber-800 hover:bg-amber-100 disabled:opacity-30"
                >‹ Back</button>
                {!isLast ? (
                  <button
                    type="button" onClick={() => setClarifyStep(step + 1)} disabled={streaming}
                    className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-40"
                  >Next ›</button>
                ) : (
                  <button
                    type="button" onClick={submitClarification} disabled={streaming}
                    className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
                    title="Submit all answers and generate"
                  >{streaming ? 'Generating…' : '✓ Submit all & generate'}</button>
                )}
                <span className="text-[11px] text-amber-800">
                  {isLast ? 'All answers are added to the plan and guide generation.' : 'Answer, or skip with Next; submit on the last question.'}
                </span>
              </div>
            </section>
          );
        })()}

        {/* ---- compose & run ---- */}
        {runnable ? (
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-start gap-3">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white">1</span>
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-semibold text-slate-800">
                  {stage.status === 'AMEND_REQUESTED' ? 'Update your instructions' : 'Describe what you need'}
                </h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  The {stage.persona} agent will produce: {stage.outputs.join(', ')}. A sentence or two is enough — you review a
                  plan before anything is generated.
                </p>
              </div>
            </div>
            {stage.status === 'AMEND_REQUESTED' && (
              <Callout tone="warning" className="mb-3" title="Changes were requested at gate review" compact>
                The reviewer’s feedback is pre-filled below. Adjust it, then review the plan and generate again.
              </Callout>
            )}
            <form onSubmit={onReviewSubmit}>
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
                  disabled={locked}
                />
                {/* inline @ autosuggest (D-56) */}
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

              {/* ---- attach + selected-context chips (D-54/D-56) ---- */}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => onAttach(e.target.files)} />
                <Button size="sm" icon="paperclip" loading={uploading} disabled={locked} onClick={() => fileInputRef.current?.click()}
                  title="Attach a document the agent should read — or follow the format of">
                  {uploading ? 'Uploading…' : 'Attach files'}
                </Button>
                <span className="text-xs text-slate-400">or type <kbd className="rounded bg-slate-100 px-1 font-mono text-slate-500">@</kbd> to reference earlier outputs and templates</span>
              </div>

              {(attachments.length > 0 || selectedRefChips.length > 0 || selectedTemplateChips.length > 0) && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selectedRefChips.map((a) => (
                    <span key={`r-${a.id}`} className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 text-[11px] text-brand-700" title="Pinned prior output">
                      <Icon name="file" size={11} /> {a.title}
                      <button type="button" aria-label={`Remove ${a.title}`} onClick={() => setRefIds((p) => p.filter((id) => id !== a.id))} className="ml-0.5 text-brand-400 hover:text-red-600"><Icon name="x" size={11} /></button>
                    </span>
                  ))}
                  {selectedTemplateChips.map((f) => (
                    <span key={`t-${f.id}`} className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[11px] text-violet-700" title="Template applied">
                      <Icon name="layers" size={11} /> {f.name}
                      <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFormworkIds((p) => p.filter((id) => id !== f.id))} className="ml-0.5 text-violet-400 hover:text-red-600"><Icon name="x" size={11} /></button>
                    </span>
                  ))}
                  {attachments.map((a) => (
                    <span key={`a-${a.id}`} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600" title={a.isText ? 'Inlined into the prompt' : 'Binary — kept but not inlined'}>
                      <Icon name="paperclip" size={11} /> {a.filename}{!a.isText && <span className="text-amber-600">(binary)</span>}
                      <button type="button" aria-label={`Remove ${a.filename}`} onClick={() => removeAttachment(a.id)} disabled={locked} className="ml-0.5 text-slate-400 hover:text-red-600 disabled:opacity-30"><Icon name="x" size={11} /></button>
                    </span>
                  ))}
                </div>
              )}

              {promptError && (
                <Callout tone="error" compact className="mt-2">{promptError}</Callout>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <Button
                  type="submit" variant={plan ? 'secondary' : 'primary'} icon={plan ? 'refresh' : 'search'}
                  loading={planBuilding} disabled={!canReviewPlan}
                  title={locked ? 'Editing is locked while this stage generates' : undefined}
                >
                  {planBuilding ? 'Building plan…' : plan ? 'Update plan' : 'Review plan'}
                </Button>
                <span className="text-xs text-slate-400">
                  Earlier stages’ approved outputs are included automatically. Nothing is generated yet.
                </span>
              </div>
            </form>

            {/* ---- Step 2 · Review the plan ---- */}
            {plan && (
              <div className="mt-5 rounded-xl border border-brand-200 bg-white" id="plan-card">
                <div className="flex flex-wrap items-start gap-3 rounded-t-xl border-b border-brand-100 bg-brand-50/60 px-4 py-3">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white">2</span>
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-semibold text-slate-800">Review the plan</h3>
                    <p className="mt-0.5 text-xs text-slate-500">
                      This is what the agent intends to make. Untick anything you don’t want, ask for changes, then generate.
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {plan.intel && <Badge tone="brand" icon="sparkles" title="Plan tailored to your input, tech stack, earlier outputs and configuration">AI-planned{plan.intel.cached ? ' · cached' : ''}</Badge>}
                    <Badge title="The AI agent that will do the work">Agent: {plan.agent.persona}</Badge>
                    {plan.overlay.origin !== 'new' && <Badge tone="warning" icon="refresh">{plan.overlay.origin}</Badge>}
                  </div>
                </div>

                <div className="space-y-5 p-4">
                  {/* ---- What I understood + which artifacts to make ---- */}
                  {plan.intel && (plan.intel.understood || (plan.intel.willProduce?.length ?? 0) > 0) && (
                    <>
                      {plan.intel.understood && (
                        <div>
                          <SectionLabel icon="target">What I understood</SectionLabel>
                          <p className="text-[13px] leading-snug text-slate-700">{plan.intel.understood}</p>
                        </div>
                      )}

                      {(plan.intel.willProduce?.length ?? 0) > 0 && (
                        <div>
                          <SectionLabel icon="tasks" hint={`${plan.intel.willProduce.filter((a) => produceSel[a.output] ?? a.include ?? a.recommended).length} of ${plan.intel.willProduce.length} selected — tick what you want, untick to skip`}>
                            Artifacts to generate
                          </SectionLabel>
                          <div className="space-y-1.5">
                            {plan.intel.willProduce.map((a) => {
                              const on = produceSel[a.output] ?? a.include ?? a.recommended;
                              return (
                                <label key={a.output} className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 transition ${on ? 'border-brand-300 bg-brand-50/40' : 'border-slate-200 bg-white hover:border-slate-300'}`}>
                                  <input
                                    type="checkbox" className="mt-1 h-4 w-4 accent-brand-600"
                                    checked={on} disabled={locked}
                                    onChange={(e) => setProduceSel((p) => ({ ...p, [a.output]: e.target.checked }))}
                                  />
                                  <span className="min-w-0 flex-1">
                                    <span className="flex flex-wrap items-center gap-2">
                                      <span className="text-[13px] font-semibold text-slate-800">{a.output}</span>
                                      {a.recommended
                                        ? <Badge tone="success" icon="check">Recommended</Badge>
                                        : <Badge title="Not needed for this request, but you can still include it">Optional</Badge>}
                                    </span>
                                    {a.reason && <span className="mt-0.5 block text-xs text-slate-500">{a.reason}</span>}
                                  </span>
                                </label>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {(plan.intel.promptChecks?.length ?? 0) > 0 && (
                        <Callout tone="advice" title="Left out — not applicable to this project">
                          <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
                            {plan.intel.promptChecks!.map((c) => <li key={c}>{c}</li>)}
                          </ul>
                          <span className="mt-1 block text-xs text-slate-500">These are not part of the plan, the prompt or the generation. If one is wrong, change the matching setting under “Project fit”.</span>
                        </Callout>
                      )}

                      {(plan.intel.suggestedArtifacts?.length ?? 0) > 0 && (
                        <div>
                          <SectionLabel icon="advice" hint="not in the standard template — tick to add (written into the stage’s main document)">Suggested additions</SectionLabel>
                          <div className="space-y-1.5">
                            {plan.intel.suggestedArtifacts!.map((x) => (
                              <label key={x.name} className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 transition ${suggestSel[x.name] ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-white hover:border-amber-300'}`}>
                                <input
                                  type="checkbox" className="mt-1 h-4 w-4 accent-amber-500" disabled={locked}
                                  checked={Boolean(suggestSel[x.name])}
                                  onChange={(e) => setSuggestSel((p) => ({ ...p, [x.name]: e.target.checked }))}
                                />
                                <span className="min-w-0 flex-1">
                                  <span className="text-[13px] font-semibold text-slate-800">{x.name}</span>
                                  {x.reason && <span className="mt-0.5 block text-xs text-slate-500">{x.reason}</span>}
                                </span>
                              </label>
                            ))}
                          </div>
                        </div>
                      )}

                      {(plan.traits?.filter((t) => !t.trait.startsWith('_')).length ?? 0) > 0 && (
                        <div>
                          <SectionLabel icon="layers" hint="what the AI judged about your project — only artifacts that apply are planned">Project fit</SectionLabel>
                          <div className="grid gap-2 sm:grid-cols-2">
                            {plan.traits!.filter((t) => !t.trait.startsWith('_')).map((t) => {
                              const m = TRAIT_META[t.trait] ?? { label: t.trait, icon: 'circle' as IconName, hint: '' };
                              return (
                                <div key={t.trait} className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-white px-3 py-2" title={`${m.hint}. Evidence: ${t.evidence}`}>
                                  <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${t.value === true ? 'bg-emerald-50 text-emerald-600' : t.value === false ? 'bg-slate-100 text-slate-400' : 'bg-amber-50 text-amber-600'}`}>
                                    <Icon name={m.icon} size={15} />
                                  </span>
                                  <span className="min-w-0 flex-1">
                                    <span className="flex flex-wrap items-center gap-1.5 text-xs">
                                      <span className="font-semibold text-slate-800">{m.label}</span>
                                      <span className={t.value === true ? 'font-medium text-emerald-700' : t.value === false ? 'text-slate-500' : 'font-medium text-amber-700'}>
                                        {t.value === true ? 'Yes' : t.value === false ? 'No' : 'Not sure'}
                                      </span>
                                      <Badge tone={t.source === 'override' ? 'brand' : 'neutral'}>
                                        {t.source === 'ai' ? `AI ${Math.round(t.confidence * 100)}%` : t.source === 'override' ? 'Set by you' : t.source === 'derived' ? 'Derived' : 'Rules'}
                                      </Badge>
                                    </span>
                                    <span className="block truncate text-[11px] text-slate-400">{t.evidence}</span>
                                  </span>
                                  <select
                                    aria-label={`Override ${m.label}`} disabled={planBusy || locked}
                                    value={t.source === 'override' ? (t.value ? 'present' : 'absent') : ''}
                                    onChange={(e) => overrideTrait(t.trait, (e.target.value || null) as 'present' | 'absent' | null)}
                                    className="rounded-md border border-slate-300 bg-white px-1.5 py-1 text-xs text-slate-600 focus:border-brand-400 focus:outline-none"
                                    title="Auto = let the AI decide. Choose Yes/No to override it."
                                  >
                                    <option value="">Auto</option>
                                    <option value="present">Yes</option>
                                    <option value="absent">No</option>
                                  </select>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Output format — an explicit choice (no prose inference) */}
                      <div>
                        <SectionLabel icon="file" hint="choose one">Output format</SectionLabel>
                        <div className="grid gap-2 sm:grid-cols-2">
                          <label className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 ${effectiveFormatMode === 'system' ? 'border-brand-300 bg-brand-50/40' : 'border-slate-200 hover:border-slate-300'}`}>
                            <input type="radio" name="formatMode" className="mt-1 h-4 w-4 accent-brand-600" disabled={locked} checked={effectiveFormatMode === 'system'} onChange={() => setFormatMode('system')} />
                            <span className="text-xs">
                              <span className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-800"><Icon name="sparkles" size={13} />System’s recommended format</span>
                              <span className="text-slate-500">The standard template for this stage.</span>
                            </span>
                          </label>
                          <label className={`flex items-start gap-3 rounded-lg border px-3 py-2 ${attachments.length ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'} ${effectiveFormatMode === 'attached' ? 'border-brand-300 bg-brand-50/40' : 'border-slate-200 hover:border-slate-300'}`}>
                            <input type="radio" name="formatMode" className="mt-1 h-4 w-4 accent-brand-600" disabled={!attachments.length || locked} checked={effectiveFormatMode === 'attached'} onChange={() => setFormatMode('attached')} />
                            <span className="text-xs">
                              <span className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-800"><Icon name="paperclip" size={13} />Follow my attached document</span>
                              <span className="text-slate-500">
                                {attachments.length ? `Mirror ${attachments.map((a) => a.filename).join(', ')} exactly.` : 'Attach a document above to enable this.'}
                              </span>
                            </span>
                          </label>
                        </div>
                      </div>

                      {plan.intel.recommendation && (
                        <Callout tone="advice" title="Advice">{plan.intel.recommendation}</Callout>
                      )}
                      {(plan.intel.outOfScope?.length ?? 0) > 0 && (
                        <p className="text-xs text-slate-500"><span className="font-semibold text-slate-600">Not covered here:</span> {plan.intel.outOfScope.join('; ')}</p>
                      )}
                    </>
                  )}

                  {/* ---- Ask for changes: a conversation that re-plans ---- */}
                  <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3">
                    <SectionLabel icon="message" hint="reply in plain words — the plan updates">Ask for changes</SectionLabel>
                    {thread.length > 0 && (
                      <div className="mb-2 max-h-56 space-y-1.5 overflow-auto pr-1">
                        {thread.map((m, i) => (
                          <div key={`${m.ts}-${i}`} className={`flex ${m.role === 'you' ? 'justify-end' : 'justify-start'}`}>
                            <div className={'max-w-[85%] rounded-lg px-3 py-2 text-[13px] leading-snug ' + (m.role === 'you' ? 'bg-brand-600 text-white' : 'border border-slate-200 bg-white text-slate-800')}>
                              <div className="mb-0.5 text-[11px] font-semibold opacity-70">{m.role === 'you' ? 'You' : `${plan.agent.persona} agent`}</div>
                              {m.text}
                            </div>
                          </div>
                        ))}
                        <div ref={threadEndRef} />
                      </div>
                    )}
                    <div className="flex items-end gap-2">
                      <textarea
                        className="min-h-[40px] w-full flex-1 resize-y rounded-lg border border-slate-300 bg-white p-2 text-[13px] focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-200 disabled:bg-slate-100"
                        rows={1}
                        placeholder="e.g. “drop the NFR section”, “follow my attached format exactly”, “also add a sequence diagram”"
                        value={refineText}
                        onChange={(e) => setRefineText(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void sendRefinement(); } }}
                        disabled={planBusy || locked}
                      />
                      <Button variant="primary" icon="send" loading={planBusy} disabled={locked || !refineText.trim()} onClick={() => void sendRefinement()}>
                        Send
                      </Button>
                    </div>
                  </div>

                  {/* ---- Technical details: collapsed by default (progressive disclosure) ---- */}
                  {plan.intel && (
                    <details className="group rounded-lg border border-slate-200">
                      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                        <Icon name="chevron-right" size={14} className="transition group-open:rotate-90" />
                        <Icon name="sliders" size={14} className="text-slate-400" />
                        Technical details
                        <span className="font-normal text-slate-400">— model steps, tools, context, risks, system prompt</span>
                      </summary>
                      <div className="space-y-3 border-t border-slate-100 px-3 py-3">
                        {plan.intel.summary && <p className="text-[13px] leading-snug text-slate-700">{plan.intel.summary}</p>}
                        {plan.intel.steps.length > 0 && (
                          <ol className="space-y-1.5">
                            {plan.intel.steps.map((st, i) => (
                              <li key={`${st.id}-${i}`} className="flex items-start gap-2 text-xs text-slate-600">
                                <Icon name={st.kind === 'gate' ? 'shield' : st.kind === 'tool' ? 'zap' : 'sparkles'} size={13} className="mt-0.5 text-slate-400" />
                                <span>
                                  <span className="font-semibold text-slate-700">{st.label}</span>
                                  {st.tier && <span className="ml-1.5"><Badge>{st.tier}</Badge></span>}
                                  {st.rationale && <span className="text-slate-500"> — {st.rationale}</span>}
                                </span>
                              </li>
                            ))}
                          </ol>
                        )}
                        {plan.intel.toolRecommendations.length > 0 && (
                          <div>
                            <SectionLabel icon="zap">Tools</SectionLabel>
                            <div className="space-y-0.5">
                              {plan.intel.toolRecommendations.map((t) => (
                                <div key={t.tool} className="flex items-start gap-1.5 text-xs">
                                  <Icon name={t.use ? 'check' : 'minus'} size={13} className={`mt-0.5 ${t.use ? 'text-emerald-600' : 'text-slate-400'}`} />
                                  <span><span className="font-mono text-slate-700">{t.tool}</span><span className="text-slate-500"> — {t.rationale}</span></span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        {plan.intel.assumptions.length > 0 && (
                          <div>
                            <SectionLabel icon="info">Assumptions</SectionLabel>
                            <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-600">{plan.intel.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
                          </div>
                        )}
                        {plan.intel.risks.length > 0 && (
                          <Callout tone="warning" compact title="Risks to review">
                            <ul className="list-disc space-y-0.5 pl-4">{plan.intel.risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
                          </Callout>
                        )}
                        <div className="grid gap-3 md:grid-cols-2">
                          <div>
                            <SectionLabel icon="sparkles">Skills &amp; tools</SectionLabel>
                            <div className="flex flex-wrap gap-1">
                              {plan.skills.map((s) => <Badge key={s.id}>{s.name} · {s.tier}</Badge>)}
                              {plan.expectedTools.map((t) => <Badge key={t} icon="zap">{t}</Badge>)}
                              {!plan.skills.length && !plan.expectedTools.length && <span className="text-xs text-slate-400">None</span>}
                            </div>
                          </div>
                          <div>
                            <SectionLabel icon="layers">Context the agent will use</SectionLabel>
                            <ul className="space-y-0.5 text-xs text-slate-600">
                              <li>{plan.context.priorArtifacts.length} earlier output(s), included automatically</li>
                              <li>Canon rules: {plan.context.canonApplied ? 'applied' : 'none'}</li>
                              <li>{plan.context.formworks.length} template(s) · {plan.context.ragSnippets} knowledge snippet(s)</li>
                              <li>{plan.context.attachments.length} attachment(s) · {plan.context.curatedInjectedChars} chars of curated context</li>
                            </ul>
                          </div>
                        </div>
                        <Button size="sm" icon={showSystemPrompt ? 'chevron-down' : 'chevron-right'} onClick={() => setShowSystemPrompt((v) => !v)}>
                          {showSystemPrompt ? 'Hide the system prompt' : 'View the system prompt'}
                        </Button>
                        {showSystemPrompt && (
                          <div className="max-h-72 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3">
                            <div className="text-xs font-semibold text-slate-500">System prompt (read-only)</div>
                            <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] leading-snug text-slate-700">{plan.prompt.system}</pre>
                            <div className="mt-2 text-xs font-semibold text-slate-500">User turn</div>
                            <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] leading-snug text-slate-700">{plan.prompt.user}</pre>
                          </div>
                        )}
                      </div>
                    </details>
                  )}

                  {/* ---- Step 3 · Generate ---- */}
                  <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
                    <Button
                      variant="primary" icon="play" loading={locked}
                      disabled={locked || !plan.canEdit || !planFresh}
                      onClick={triggerPlan}
                      title={!plan.canEdit ? 'You need write permission to generate this stage'
                        : locked ? 'Generation is in progress'
                        : !planFresh ? 'Update the plan first — generation uses only a final, up-to-date plan'
                        : 'Generate this stage using the reviewed plan'}
                    >
                      {locked ? 'Generating…' : 'Generate'}
                    </Button>
                    <span className="min-w-0 flex-1 text-xs text-slate-500">
                      {!plan.canEdit
                        ? <span className="text-red-700">You need write access ({plan.stage.writeRoles.join(', ')}) to generate this stage.</span>
                        : planOutdated
                          ? <span className="font-medium text-amber-800">You changed something — click “Update plan” first so the result matches.</span>
                          : planFresh
                            ? <>Afterwards the {plan.stage.reviewerRole} reviews and approves it before the next stage starts.</>
                            : 'Review the plan first.'}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </section>
        ) : blockedReason.length > 0 ? (
          <Callout tone="warning" icon="lock" title="This stage is waiting for an earlier one">
            It runs once the upstream gate{blockedReason.length > 1 ? 's are' : ' is'} approved:{' '}
            <span className="font-semibold">{blockedReason.join(', ')}</span>.
          </Callout>
        ) : null}

        {/* ---- live generation ---- */}
        {streamingHere && (
          <section className="rounded-xl border border-blue-200 bg-blue-50/60 p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-blue-800">
              <Icon name="loader" size={16} spin /> Generating — you can leave this page, it keeps running and your files are saved
            </div>
            <div className="space-y-0.5">
              {activity.map((a) => (
                <div key={a.id} className="flex items-center gap-1.5 text-xs text-slate-600">
                  <Icon name={ACTIVITY_ICON[a.kind]} size={13} className="text-slate-400" />
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
            {/* Every file being generated, one tab each (parallel per-artifact generation) */}
            {liveParts.length > 0 && (
              <div className="mt-3"><PartTabs parts={liveParts} /></div>
            )}
          </section>
        )}

        {/* ---- Agile: what the AI proposes to change (applied only on approval) ---- */}
        {(stage.agileRole === 'refine' || stage.agileRole === 'plan' || stage.agileRole === 'build') && ['PENDING_REVIEW', 'APPROVED', 'AMEND_REQUESTED'].includes(stage.status) && (
          <ProposalReview projectId={projectId} phase={stage.phase} role={stage.agileRole} canEdit={Boolean(agileOv.data?.permissions.canRun)} />
        )}

        {/* ---- gate review ---- */}
        {pendingGate && pendingGate.phase === selectedSeq && (
          <div id="gate-review" className="scroll-mt-4">
            <GatePanel
              projectId={projectId}
              pending={pendingGate}
              artefacts={artefacts as never}
              user={user}
            />
          </div>
        )}

        {/* ---- quality signals & feedback (D-57) ---- */}
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

        {/* ---- generation parts (D-107 step 2): per-part ✓/✗ + retrigger ---- */}
        {parts.length > 0 && (
          <section>
            {/* Files survive navigating away, closing the browser and orchestrator restarts */}
            {!streamingHere && parts.some((p) => p.text) && (
              <div className="mb-3"><PartTabs
                  parts={parts.map((p) => (p.status === 'running'
                    ? { ...p, status: 'failed' as const, error: 'Interrupted — partial text kept' } : p))}
                  retrigger={retriggerPart}
                /></div>
            )}
            <SectionLabel icon="tasks" hint={`${parts.filter((p) => p.status === 'done').length} of ${parts.length} finished`}>
              Generated files
            </SectionLabel>
            {parts.some((p) => p.status === 'failed') && (
              <div className="mb-2">
                <Callout tone="error" compact title={`${parts.filter((p) => p.status === 'failed').length} file(s) failed`}>
                  Use “Retry” on a file to regenerate just that one — the others are kept.
                </Callout>
              </div>
            )}
            {regenerable.length > 0 && !streaming && ['APPROVED', 'PENDING_REVIEW', 'AMEND_REQUESTED', 'ESCALATED'].includes(stage.status) && (
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Button
                  size="sm" variant="secondary" icon="refresh" disabled={picked.length === 0}
                  onClick={() => regenerateParts(picked)}
                  title="Regenerate only the ticked files; the rest are kept"
                >
                  Regenerate selected ({picked.length})
                </Button>
                <Button size="sm" variant="ghost" onClick={() => regenerateParts([])}>Regenerate all</Button>
                <Button
                  size="sm" variant="ghost"
                  onClick={() => setPicked(picked.length === regenerable.length ? [] : regenerable.map((p) => p.field))}
                >
                  {picked.length === regenerable.length ? 'Clear selection' : 'Select all'}
                </Button>
              </div>
            )}
            <div className="space-y-1">
              {parts.map((p) => (
                <div key={p.field} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs">
                  {p.field !== 'document' && p.status !== 'running' && (
                    <input
                      type="checkbox" aria-label={`Select ${p.field}`}
                      checked={picked.includes(p.field)}
                      onChange={(e) => setPicked((cur) => (e.target.checked ? [...cur, p.field] : cur.filter((f) => f !== p.field)))}
                    />
                  )}
                  <Icon
                    name={p.status === 'done' ? 'success' : p.status === 'running' ? 'loader' : 'error'} size={14}
                    spin={p.status === 'running'}
                    className={p.status === 'done' ? 'text-emerald-600' : p.status === 'running' ? 'text-blue-500' : 'text-red-600'}
                    label={p.status === 'done' ? 'Done' : p.status === 'running' ? 'Generating' : 'Failed'}
                  />
                  <span className="font-mono text-slate-700">{p.field}</span>
                  {p.status === 'failed' && p.error && (
                    <span className="min-w-0 flex-1 truncate text-slate-400" title={p.error}>{p.error}</span>
                  )}
                  {p.status === 'failed' && (
                    <Button
                      size="sm" variant="secondary" icon="refresh" className="ml-auto shrink-0"
                      onClick={() => retriggerPart(p.field)} disabled={streaming}
                      title="Regenerate just this file and merge it"
                    >Retry</Button>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ---- outputs ---- */}
        <section>
          <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            <span>Outputs ({stageArtefacts.length})</span>
            {stageArtefacts.length > 0 && (
              <button
                type="button" onClick={() => setDocOpen(true)}
                className="ml-auto rounded-lg border border-brand-300 bg-brand-50 px-2.5 py-1 text-[11px] font-semibold normal-case tracking-normal text-brand-700 hover:bg-brand-100"
                title="Read all outputs of this stage as one document with diagrams; copy, or export to Word / PDF"
              >
                📄 Read as document · export
              </button>
            )}
          </div>
          {docOpen && (
            <StageDocument
              projectId={projectId} artefacts={stageArtefacts} title={stage.name}
              subtitle={`${stage.persona} · ${stage.name}`} onClose={() => setDocOpen(false)}
            />
          )}
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
            Discussion history ({stageMessages.length}) — saved, with timestamps
          </div>
          {stageMessages.length === 0 && !streamingHere ? (
            <div className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-xs text-slate-400">
              The full discussion — your instructions, clarifications, agent summaries and review feedback — is saved here with timestamps and fed back to the agent on re-runs.
            </div>
          ) : (
            <div className="space-y-2">
              {stageMessages.map((m) => (
                <div key={m.id} className={m.role === 'user' ? 'ml-8' : 'mr-8'}>
                  <div className={`flex items-center gap-1.5 px-1 text-[10px] text-slate-400 ${m.role === 'user' ? 'justify-end' : ''}`}>
                    <span className="font-semibold uppercase tracking-wide">{m.role === 'user' ? 'You' : m.role === 'system' ? 'System' : `${stage.persona} agent`}</span>
                    <span>·</span>
                    <span>{fmtTime(m.createdAt)}</span>
                  </div>
                  <div
                    className={`mt-0.5 rounded-xl px-3 py-2 text-sm ${
                      m.role === 'user' ? 'bg-brand-600 text-white' : 'border border-slate-200 bg-white text-slate-800'
                    }`}
                  >
                    <div className="prose-chat">
                      <ReactMarkdown>{m.content}</ReactMarkdown>
                    </div>
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
