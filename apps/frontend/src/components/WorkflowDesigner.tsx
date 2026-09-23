import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import {
  PHASE_ROLE_OPTIONS,
  TEMPLATE_NAMES,
  type StageConfig,
  type WorkflowView,
} from '../api/flow';

interface ValidateResult {
  valid: boolean;
  errors: string[];
  levels?: number[][];
  stages?: Array<StageConfig & { seq: number; level: number }>;
}

/** Short badge label per agent template (rail + node chrome). */
const TEMPLATE_SHORT: Record<number, string> = {
  1: 'PO', 2: 'SA', 3: 'TA', 4: 'QA', 5: 'DevOps', 6: 'Dev', 7: 'Custom',
};

/**
 * Client-side layout: assign each stage to a column = longest dependency depth.
 * Runs independent of server validation so the canvas always renders something
 * meaningful (server validation additionally checks input-satisfaction and gates
 * Save). Reports `cyclic` when the dependsOn graph has a cycle.
 */
function computeColumns(stages: StageConfig[]): { columns: string[][]; cyclic: boolean } {
  const byKey = new Map(stages.map((s) => [s.key, s]));
  const depthOf = new Map<string, number>();
  const onStack = new Set<string>();
  let cyclic = false;
  const depth = (k: string): number => {
    if (depthOf.has(k)) return depthOf.get(k)!;
    if (onStack.has(k)) { cyclic = true; return 0; }
    onStack.add(k);
    const deps = byKey.get(k)?.dependsOn ?? [];
    const d = deps.length ? Math.max(...deps.map(depth)) + 1 : 0;
    onStack.delete(k);
    depthOf.set(k, d);
    return d;
  };
  stages.forEach((s) => depth(s.key));
  const maxL = stages.length ? Math.max(...stages.map((s) => depthOf.get(s.key) ?? 0)) : 0;
  const columns: string[][] = Array.from({ length: maxL + 1 }, () => []);
  stages.forEach((s) => columns[depthOf.get(s.key) ?? 0]!.push(s.key));
  return { columns: columns.filter((c) => c.length), cyclic };
}

/**
 * Visual workflow workspace (, redesigned): a three-pane visual editor.
 *   • left rail — ordered stage list, click to select, drag ⠿ to reorder, add;
 *   • center canvas — level columns with real dependency arrows (parallel stages
 *     stack in a column); click a node to select it;
 *   • right inspector — edits ONLY the selected stage (name, agent, team & gate,
 *     dependencies, inputs, outputs) with inline per-stage issue hints.
 * Live validation mirrors the server rules and gates Save; amendments bump the
 * persisted version.
 */
export default function WorkflowDesigner({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [stages, setStages] = useState<StageConfig[] | null>(null);
  const [validation, setValidation] = useState<ValidateResult | null>(null);
  const [saveError, setSaveError] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dropIdx, setDropIdx] = useState<number | null>(null);
  // Canvas node-to-node wiring: drag a node onto another to run it AFTER
  // (sequential) or IN PARALLEL WITH it.
  const [canvasDrag, setCanvasDrag] = useState<string | null>(null);
  const [dropZone, setDropZone] = useState<{ key: string; mode: 'after' | 'parallel' } | null>(null);

  const canvasRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [edges, setEdges] = useState<Array<{ id: string; d: string; active: boolean }>>([]);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });

  const wf = useQuery({
    queryKey: ['workflow', projectId],
    queryFn: () => api.get<WorkflowView>(`/api/projects/${projectId}/workflow`),
  });

  useEffect(() => {
    if (wf.data && stages === null) {
      const loaded = wf.data.config.stages.map((s) => ({ ...s }));
      setStages(loaded);
      setSelectedKey(loaded[0]?.key ?? null);
    }
  }, [wf.data, stages]);

  // Live validation (debounced) — the same rules the server enforces on save.
  useEffect(() => {
    if (!stages) return;
    const t = setTimeout(() => {
      void api
        .post<ValidateResult>(`/api/projects/${projectId}/workflow/validate`, { stages })
        .then(setValidation)
        .catch(() => setValidation({ valid: false, errors: ['validation request failed'] }));
    }, 350);
    return () => clearTimeout(t);
  }, [stages, projectId]);

  const doSave = useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/projects/${projectId}/workflow`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ stages }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.error?.message ?? 'Save failed');
      return body as WorkflowView;
    },
    onSuccess: () => {
      setSaveError('');
      void qc.invalidateQueries({ queryKey: ['workflow', projectId] });
      void qc.invalidateQueries({ queryKey: ['flow', projectId] });
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      onClose();
    },
    onError: (e) => setSaveError(e instanceof Error ? e.message : 'Save failed'),
  });

  const outputsUniverse = useMemo(() => {
    const set = new Set<string>(['requirements']);
    (stages ?? []).forEach((s) => s.outputs.forEach((o) => set.add(o)));
    return [...set];
  }, [stages]);

  const { columns, cyclic } = useMemo(
    () => (stages ? computeColumns(stages) : { columns: [], cyclic: false }),
    [stages],
  );

  /** Transitive ancestors of a stage across the dependsOn graph. */
  const ancestorsOf = useCallback((list: StageConfig[], key: string): Set<string> => {
    const byKey = new Map(list.map((s) => [s.key, s]));
    const acc = new Set<string>();
    const walk = (k: string) => {
      for (const dep of byKey.get(k)?.dependsOn ?? []) {
        if (!acc.has(dep)) { acc.add(dep); walk(dep); }
      }
    };
    walk(key);
    return acc;
  }, []);

  /** Per-stage issue list (client-side mirror of the server's intent) so each
   *  node/inspector can show precisely what is wrong with it. */
  const issuesFor = useCallback(
    (list: StageConfig[], s: StageConfig): string[] => {
      const out: string[] = [];
      if (!s.name.trim()) out.push('Name is empty');
      if (s.team.length === 0) out.push('Team is empty');
      const reviewers = s.reviewerRoles?.length ? s.reviewerRoles : [s.reviewerRole];
      for (const r of reviewers) {
        if (!s.team.includes(r)) out.push(`Gate reviewer ${r} is not in the team`);
      }
      if (reviewers.length === 0) out.push('No gate reviewer selected');
      if (s.writeRoles?.length === 0) out.push('No role has write permission');
      if (s.outputs.length === 0) out.push('Produces no outputs');
      const producible = new Set(['requirements']);
      for (const anc of ancestorsOf(list, s.key)) {
        list.find((x) => x.key === anc)?.outputs.forEach((o) => producible.add(o));
      }
      const unmet = s.inputs.filter((i) => !producible.has(i));
      if (unmet.length) out.push(`Input(s) no upstream stage produces: ${unmet.join(', ')}`);
      return out;
    },
    [ancestorsOf],
  );

  // ---- measure dependency arrows between node boxes ----
  const measure = useCallback(() => {
    const cv = canvasRef.current;
    if (!cv || !stages) return;
    const base = cv.getBoundingClientRect();
    const es: Array<{ id: string; d: string; active: boolean }> = [];
    for (const s of stages) {
      const tgt = nodeRefs.current.get(s.key);
      if (!tgt) continue;
      const tb = tgt.getBoundingClientRect();
      for (const dep of s.dependsOn) {
        const src = nodeRefs.current.get(dep);
        if (!src) continue;
        const sb = src.getBoundingClientRect();
        const x1 = sb.right - base.left + cv.scrollLeft;
        const y1 = sb.top + sb.height / 2 - base.top + cv.scrollTop;
        const x2 = tb.left - base.left + cv.scrollLeft;
        const y2 = tb.top + tb.height / 2 - base.top + cv.scrollTop;
        const mx = (x1 + x2) / 2;
        es.push({
          id: `${dep}->${s.key}`,
          d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`,
          active: selectedKey === s.key || selectedKey === dep,
        });
      }
    }
    setEdges(es);
    setCanvasSize({ w: cv.scrollWidth, h: cv.scrollHeight });
  }, [stages, selectedKey]);

  useLayoutEffect(() => {
    const id = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(id);
  }, [measure, columns]);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(cv);
    window.addEventListener('resize', measure);
    return () => { ro.disconnect(); window.removeEventListener('resize', measure); };
  }, [measure]);

  if (!stages) return null;

  const selectedIdx = stages.findIndex((s) => s.key === selectedKey);
  const selected = selectedIdx >= 0 ? stages[selectedIdx]! : null;

  const update = (idx: number, patch: Partial<StageConfig>) =>
    setStages(stages.map((s, i) => (i === idx ? { ...s, ...patch } : s)));

  const addStage = () => {
    let n = stages.length + 1;
    while (stages.some((s) => s.key === `stage-${n}`)) n += 1;
    const key = `stage-${n}`;
    setStages([
      ...stages,
      {
        key, name: `New Stage ${n}`, template: 1, reviewerRole: 'PO',
        team: ['PO'], inputs: ['requirements'], outputs: ['PRD'],
        dependsOn: stages.length ? [stages[stages.length - 1]!.key] : [],
      },
    ]);
    setSelectedKey(key);
  };

  const duplicateStage = (idx: number) => {
    const src = stages[idx]!;
    let n = stages.length + 1;
    while (stages.some((s) => s.key === `stage-${n}`)) n += 1;
    const key = `stage-${n}`;
    const copy: StageConfig = {
      ...src, key, name: `${src.name} (copy)`,
      team: [...src.team], inputs: [...src.inputs], outputs: [...src.outputs],
      dependsOn: [...src.dependsOn],
    };
    const next = [...stages];
    next.splice(idx + 1, 0, copy);
    setStages(next);
    setSelectedKey(key);
  };

  /** Keep every stage's inputs satisfiable after a rewire. */
  const autoFixInputs = (list: StageConfig[]): StageConfig[] => {
    const byKey = new Map(list.map((s) => [s.key, s]));
    return list.map((s) => {
      const producible = new Set(['requirements']);
      for (const anc of ancestorsOf(list, s.key)) {
        byKey.get(anc)?.outputs.forEach((o) => producible.add(o));
      }
      let inputs = s.inputs.filter((i) => producible.has(i));
      if (inputs.length === 0) {
        const direct = [...new Set(s.dependsOn.flatMap((d) => byKey.get(d)?.outputs ?? []))];
        inputs = direct.length > 0 ? direct : ['requirements'];
      }
      return inputs.length === s.inputs.length && inputs.every((i, n) => i === s.inputs[n]) ? s : { ...s, inputs };
    });
  };

  /** Drag reorder in the rail — splices a stage into a new position and rewires
   *  dependencies so the derived order follows the new sequence. */
  const moveStage = (from: number, to: number) => {
    if (from === to) return;
    const next = stages.map((s) => ({ ...s, dependsOn: [...s.dependsOn], inputs: [...s.inputs] }));
    const moved = next[from]!;
    for (const s of next) {
      if (s !== moved && s.dependsOn.includes(moved.key)) {
        s.dependsOn = [...new Set([...s.dependsOn.filter((d) => d !== moved.key), ...moved.dependsOn])];
      }
    }
    next.splice(from, 1);
    next.splice(to, 0, moved);
    const i = next.indexOf(moved);
    const prev = next[i - 1];
    const succ = next[i + 1];
    moved.dependsOn = prev ? [prev.key] : [];
    if (succ) {
      succ.dependsOn = [...new Set([...succ.dependsOn.filter((d) => d !== prev?.key), moved.key])];
    }
    setStages(autoFixInputs(next));
  };

  /** Explicit permission lists are stored only once the user diverges from the
   *  default ("empty = the whole team"), so untouched stages stay compatible
   *  with configs saved before per-role permissions existed. */
  const resolvedPerm = (list: string[] | undefined, team: string[]): string[] =>
    list?.length ? list.filter((r) => team.includes(r)) : [...team];

  const togglePerm = (field: 'readRoles' | 'writeRoles', role: string) => {
    if (!selected) return;
    const current = resolvedPerm(selected[field], selected.team);
    const next = current.includes(role) ? current.filter((r) => r !== role) : [...current, role];
    // Write implies read — granting write also grants read.
    if (field === 'writeRoles' && !current.includes(role)) {
      const reads = resolvedPerm(selected.readRoles, selected.team);
      if (!reads.includes(role)) {
        update(selectedIdx, { writeRoles: next, readRoles: [...reads, role] });
        return;
      }
    }
    // Revoking read also revokes write (write without read is meaningless).
    if (field === 'readRoles' && current.includes(role)) {
      const writes = resolvedPerm(selected.writeRoles, selected.team);
      if (writes.includes(role)) {
        update(selectedIdx, { readRoles: next, writeRoles: writes.filter((r) => r !== role) });
        return;
      }
    }
    update(selectedIdx, { [field]: next } as Partial<StageConfig>);
  };

  const toggleReviewer = (role: string) => {
    if (!selected) return;
    const current = selected.reviewerRoles?.length ? selected.reviewerRoles : [selected.reviewerRole];
    const next = current.includes(role) ? current.filter((r) => r !== role) : [...current, role];
    if (next.length === 0) return; // a gate always needs at least one reviewer
    // Keep the primary reviewer inside the set (runtime/gate state uses it).
    update(selectedIdx, {
      reviewerRoles: next,
      reviewerRole: next.includes(selected.reviewerRole) ? selected.reviewerRole : next[0]!,
    });
  };

  /** Adding/removing a team role keeps every permission list consistent. */
  const toggleTeamRole = (role: string) => {
    if (!selected) return;
    const inTeam = selected.team.includes(role);
    const team = inTeam ? selected.team.filter((r) => r !== role) : [...selected.team, role];
    if (team.length === 0) return;
    const reviewers = resolvedPerm(
      selected.reviewerRoles?.length ? selected.reviewerRoles : [selected.reviewerRole], team,
    );
    const nextReviewers = reviewers.length ? reviewers : [team[0]!];
    update(selectedIdx, {
      team,
      readRoles: selected.readRoles?.length ? resolvedPerm(selected.readRoles, team) : undefined,
      writeRoles: selected.writeRoles?.length ? resolvedPerm(selected.writeRoles, team) : undefined,
      reviewerRoles: nextReviewers,
      reviewerRole: nextReviewers.includes(selected.reviewerRole) ? selected.reviewerRole : nextReviewers[0]!,
    });
  };

  /** Drop `movedKey` off the graph, re-linking its former dependents to its
   *  former dependencies so nothing is orphaned. */
  const detach = (list: StageConfig[], movedKey: string) => {
    const moved = list.find((s) => s.key === movedKey)!;
    const oldDeps = [...moved.dependsOn];
    for (const s of list) {
      if (s.key !== movedKey && s.dependsOn.includes(movedKey)) {
        s.dependsOn = [...new Set([...s.dependsOn.filter((d) => d !== movedKey), ...oldDeps])];
      }
    }
  };

  /** Canvas drag → run `movedKey` sequentially AFTER `targetKey`: it depends on
   *  the target, and the target's former dependents now depend on it. */
  const linkAfter = (movedKey: string, targetKey: string) => {
    if (movedKey === targetKey) return;
    const next = stages.map((s) => ({ ...s, dependsOn: [...s.dependsOn], inputs: [...s.inputs] }));
    detach(next, movedKey);
    for (const s of next) {
      if (s.key !== movedKey && s.key !== targetKey && s.dependsOn.includes(targetKey)) {
        s.dependsOn = [...new Set([...s.dependsOn.filter((d) => d !== targetKey), movedKey])];
      }
    }
    next.find((s) => s.key === movedKey)!.dependsOn = [targetKey];
    setStages(autoFixInputs(next));
    setSelectedKey(movedKey);
  };

  /** Canvas drag → run `movedKey` IN PARALLEL WITH `targetKey`: it inherits the
   *  target's dependencies (same level) and the target's dependents also wait
   *  on it, so the branches converge. */
  const linkParallel = (movedKey: string, targetKey: string) => {
    if (movedKey === targetKey) return;
    const next = stages.map((s) => ({ ...s, dependsOn: [...s.dependsOn], inputs: [...s.inputs] }));
    detach(next, movedKey);
    const target = next.find((s) => s.key === targetKey)!;
    next.find((s) => s.key === movedKey)!.dependsOn = target.dependsOn.filter((d) => d !== movedKey);
    for (const s of next) {
      if (s.key !== movedKey && s.key !== targetKey && s.dependsOn.includes(targetKey)) {
        s.dependsOn = [...new Set([...s.dependsOn, movedKey])];
      }
    }
    setStages(autoFixInputs(next));
    setSelectedKey(movedKey);
  };

  const removeStage = (idx: number) => {
    const gone = stages[idx]!.key;
    const next = stages
      .filter((_, i) => i !== idx)
      .map((s) => ({ ...s, dependsOn: s.dependsOn.filter((d) => d !== gone) }));
    setStages(next);
    if (selectedKey === gone) setSelectedKey(next[Math.min(idx, next.length - 1)]?.key ?? null);
  };

  const errors = validation?.errors ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-3" onClick={onClose}>
      <div
        className="flex h-[95vh] w-full max-w-[1440px] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ---------------- top bar ---------------- */}
        <div className="flex items-center justify-between gap-4 border-b border-slate-200 px-5 py-2.5">
          <div className="flex items-center gap-3">
            <div>
              <div className="text-sm font-bold text-slate-800">⚙ Workflow Designer</div>
              <div className="text-[11px] text-slate-500">
                Drag a node onto another to run it <span className="font-semibold text-brand-600">→ after</span> or{' '}
                <span className="font-semibold text-indigo-600">∥ in parallel</span> · v{wf.data?.version ?? 0}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            {/* validation pill */}
            {validation && (
              <div className="relative">
                <button
                  onClick={() => setShowErrors((v) => !v && !validation.valid ? true : false)}
                  className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${
                    validation.valid
                      ? 'bg-emerald-50 text-emerald-700'
                      : 'bg-red-50 text-red-700 hover:bg-red-100'
                  }`}
                  title={validation.valid ? 'Workflow is valid' : 'Click to see what to fix'}
                >
                  <span className={`h-2 w-2 rounded-full ${validation.valid ? 'bg-emerald-500' : 'bg-red-500'}`} />
                  {validation.valid ? 'Valid flow' : `${errors.length} issue${errors.length === 1 ? '' : 's'}`}
                </button>
                {showErrors && !validation.valid && (
                  <div className="absolute right-0 top-9 z-10 w-80 rounded-lg border border-red-200 bg-white p-2 shadow-xl">
                    <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-red-400">
                      Fix before saving
                    </div>
                    <div className="space-y-1">
                      {errors.map((e, i) => (
                        <div key={i} className="rounded bg-red-50 px-2 py-1 text-[11px] text-red-700">✗ {e}</div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            <button
              onClick={() => doSave.mutate()}
              disabled={!validation?.valid || doSave.isPending}
              className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
              title={validation?.valid ? 'Persist workflow config' : 'Fix validation issues first'}
            >
              {doSave.isPending ? 'Saving…' : 'Save workflow'}
            </button>
            <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
          </div>
        </div>

        {saveError && (
          <div className="border-b border-red-200 bg-red-50 px-5 py-1.5 text-xs text-red-700">{saveError}</div>
        )}

        {/* ---------------- three-pane body ---------------- */}
        <div className="flex min-h-0 flex-1">
          {/* ===== left: stage rail ===== */}
          <div className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-slate-50">
            <div className="px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
              Stages · {stages.length}
            </div>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-2">
              {stages.map((s, idx) => {
                const issues = issuesFor(stages, s);
                const isSel = s.key === selectedKey;
                return (
                  <button
                    key={s.key}
                    onClick={() => setSelectedKey(s.key)}
                    draggable
                    onDragStart={(e) => { setDragIdx(idx); if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; }}
                    onDragEnd={() => { setDragIdx(null); setDropIdx(null); }}
                    onDragOver={(e) => { e.preventDefault(); if (dropIdx !== idx) setDropIdx(idx); }}
                    onDrop={(e) => { e.preventDefault(); if (dragIdx !== null) moveStage(dragIdx, idx); setDragIdx(null); setDropIdx(null); }}
                    className={`flex w-full items-center gap-2 rounded-lg border px-2 py-1.5 text-left transition ${
                      dragIdx === idx
                        ? 'opacity-40'
                        : dropIdx === idx && dragIdx !== null
                          ? 'border-brand-400 ring-2 ring-brand-200'
                          : isSel
                            ? 'border-brand-500 bg-white shadow-sm'
                            : 'border-transparent hover:bg-white'
                    }`}
                  >
                    <span className="cursor-grab select-none text-slate-300 active:cursor-grabbing" title="Drag to reorder">⠿</span>
                    <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                      isSel ? 'bg-brand-600 text-white' : 'bg-slate-200 text-slate-500'
                    }`}>
                      {idx + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-semibold text-slate-700">{s.name || 'Untitled'}</span>
                      <span className="block truncate text-[10px] text-slate-400">
                        {TEMPLATE_SHORT[s.template] ?? `T${s.template}`} · gate {s.reviewerRole}
                      </span>
                    </span>
                    {issues.length > 0 && <span className="text-red-500" title={issues.join('; ')}>⚠</span>}
                  </button>
                );
              })}
            </div>
            <button
              onClick={addStage}
              className="m-2 rounded-lg border-2 border-dashed border-slate-300 py-2 text-xs font-semibold text-slate-500 hover:border-brand-300 hover:text-brand-600"
            >
              + Add stage
            </button>
          </div>

          {/* ===== center: node canvas ===== */}
          <div ref={canvasRef} className="relative min-w-0 flex-1 overflow-auto bg-[radial-gradient(#e2e8f0_1px,transparent_1px)] [background-size:18px_18px]">
            {cyclic && (
              <div className="sticky top-0 z-10 m-3 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-[11px] text-red-700">
                ⚠ Dependency cycle detected — the layout is approximate until you break the loop.
              </div>
            )}
            {/* arrows */}
            <svg
              className="pointer-events-none absolute left-0 top-0"
              width={canvasSize.w} height={canvasSize.h}
            >
              <defs>
                <marker id="wf-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M0,0 L8,4 L0,8 Z" className="fill-slate-400" />
                </marker>
                <marker id="wf-arrow-active" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M0,0 L8,4 L0,8 Z" className="fill-brand-500" />
                </marker>
              </defs>
              {edges.map((e) => (
                <path
                  key={e.id}
                  d={e.d}
                  fill="none"
                  className={e.active ? 'stroke-brand-500' : 'stroke-slate-300'}
                  strokeWidth={e.active ? 2 : 1.5}
                  markerEnd={`url(#${e.active ? 'wf-arrow-active' : 'wf-arrow'})`}
                />
              ))}
            </svg>

            <div className="relative inline-flex items-start gap-16 p-8">
              {columns.map((col, ci) => (
                <div key={ci} className="flex flex-col gap-4">
                  <div className="text-center text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                    {ci === 0 ? 'Start' : `Level ${ci + 1}`}
                    {col.length > 1 && <span className="ml-1 text-indigo-400">∥</span>}
                  </div>
                  {col.map((key) => {
                    const idx = stages.findIndex((s) => s.key === key);
                    const s = stages[idx]!;
                    const issues = issuesFor(stages, s);
                    const isSel = key === selectedKey;
                    return (
                      <div
                        key={key}
                        ref={(el) => { if (el) nodeRefs.current.set(key, el); else nodeRefs.current.delete(key); }}
                        onClick={() => setSelectedKey(key)}
                        draggable
                        onDragStart={(e) => { setCanvasDrag(key); if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; }}
                        onDragEnd={() => { setCanvasDrag(null); setDropZone(null); }}
                        className={`relative w-52 cursor-grab rounded-xl border-2 bg-white px-3 py-2 shadow-sm transition active:cursor-grabbing ${
                          canvasDrag === key
                            ? 'opacity-40'
                            : isSel
                              ? 'border-brand-500 ring-2 ring-brand-200'
                              : issues.length
                                ? 'border-red-300 hover:border-red-400'
                                : 'border-slate-200 hover:border-brand-300'
                        }`}
                      >
                        {/* drop zones — appear on other nodes while dragging one */}
                        {canvasDrag && canvasDrag !== key && (
                          <div className="absolute inset-0 z-10 flex overflow-hidden rounded-[10px]">
                            <div
                              onDragOver={(e) => { e.preventDefault(); setDropZone({ key, mode: 'parallel' }); }}
                              onDrop={(e) => { e.preventDefault(); linkParallel(canvasDrag, key); setCanvasDrag(null); setDropZone(null); }}
                              className={`flex flex-1 items-center justify-center text-[10px] font-bold ${
                                dropZone?.key === key && dropZone.mode === 'parallel'
                                  ? 'bg-indigo-500/90 text-white' : 'bg-indigo-100/85 text-indigo-700'
                              }`}
                            >∥ parallel</div>
                            <div
                              onDragOver={(e) => { e.preventDefault(); setDropZone({ key, mode: 'after' }); }}
                              onDrop={(e) => { e.preventDefault(); linkAfter(canvasDrag, key); setCanvasDrag(null); setDropZone(null); }}
                              className={`flex flex-1 items-center justify-center text-[10px] font-bold ${
                                dropZone?.key === key && dropZone.mode === 'after'
                                  ? 'bg-brand-500/90 text-white' : 'bg-brand-100/85 text-brand-700'
                              }`}
                            >→ after</div>
                          </div>
                        )}
                        <div className="flex items-center justify-between">
                          <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-slate-100 px-1 text-[10px] font-bold text-slate-500">
                            {idx + 1}
                          </span>
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-slate-500">
                            {TEMPLATE_SHORT[s.template] ?? `T${s.template}`}
                          </span>
                        </div>
                        <div className="mt-1 truncate text-sm font-semibold text-slate-800" title={s.name}>
                          {s.name || 'Untitled'}
                        </div>
                        <div className="mt-1 flex items-center gap-1 text-[10px] text-slate-400">
                          <span
                            className="rounded-full bg-brand-50 px-1.5 py-0.5 font-semibold text-brand-600"
                            title={`Gate reviewers: ${(s.reviewerRoles?.length ? s.reviewerRoles : [s.reviewerRole]).join(', ')}`}
                          >
                            ⭑ {(s.reviewerRoles?.length ? s.reviewerRoles : [s.reviewerRole]).join('/')}
                          </span>
                          {issues.length > 0 && <span className="text-red-500" title={issues.join('; ')}>⚠ {issues.length}</span>}
                        </div>
                        <div className="mt-1.5 truncate text-[10px] text-slate-400" title={s.inputs.join(', ')}>
                          in: {s.inputs.join(', ') || '—'}
                        </div>
                        <div className="truncate text-[10px] text-amber-600" title={s.outputs.join(', ')}>
                          out: {s.outputs.join(', ') || '—'}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>

          {/* ===== right: inspector ===== */}
          <div className="flex w-80 shrink-0 flex-col border-l border-slate-200 bg-white">
            {!selected ? (
              <div className="flex flex-1 flex-col items-center justify-center px-6 text-center text-slate-400">
                <div className="text-3xl">🧩</div>
                <div className="mt-2 text-sm font-semibold text-slate-500">No stage selected</div>
                <div className="mt-1 text-xs">Pick a stage on the left or a node on the canvas to edit it.</div>
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Editing stage</div>
                    <div className="truncate font-mono text-[11px] text-slate-500">{selected.key}</div>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => duplicateStage(selectedIdx)}
                      className="rounded px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                      title="Duplicate stage"
                    >⧉</button>
                    <button
                      onClick={() => removeStage(selectedIdx)}
                      className="rounded px-1.5 py-1 text-xs text-slate-400 hover:bg-red-50 hover:text-red-600"
                      title="Delete stage"
                    >🗑</button>
                  </div>
                </div>

                <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
                  {(() => {
                    const issues = issuesFor(stages, selected);
                    return issues.length > 0 ? (
                      <div className="space-y-1 rounded-lg border border-red-200 bg-red-50 p-2">
                        {issues.map((i, n) => (
                          <div key={n} className="text-[11px] text-red-700">✗ {i}</div>
                        ))}
                      </div>
                    ) : (
                      <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-700">
                        ✓ This stage is well-formed
                      </div>
                    );
                  })()}

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Stage name</label>
                    <input
                      className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm font-medium focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-200"
                      value={selected.name}
                      onChange={(e) => update(selectedIdx, { name: e.target.value })}
                      placeholder="e.g. Requirements & PRD"
                    />
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Agent template</label>
                    <select
                      className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs focus:border-brand-400 focus:outline-none"
                      value={selected.template}
                      onChange={(e) => update(selectedIdx, { template: Number(e.target.value) })}
                    >
                      {Object.entries(TEMPLATE_NAMES).map(([id, name]) => (
                        <option key={id} value={id}>{name}</option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">
                      Reviewer users (emails, optional)
                    </label>
                    <input
                      className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs focus:border-brand-400 focus:outline-none"
                      value={(selected.reviewerUsers ?? []).join(', ')}
                      onChange={(e) => update(selectedIdx, {
                        reviewerUsers: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                      })}
                      placeholder="e.g. po@acme.com, sa@acme.com — blank = all members with the reviewer role(s)"
                    />
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      Every listed user must sign off every artifact before the stage completes. Leave blank to default to
                      the project members holding the reviewer role(s).
                    </div>
                  </div>

                  {selected.template === 7 && (
                    <div className="space-y-2 rounded-lg border border-indigo-200 bg-indigo-50/50 p-2">
                      <div className="text-[10px] font-semibold uppercase tracking-wide text-indigo-500">
                        Custom phase
                      </div>
                      <div>
                        <label className="text-[10px] font-semibold uppercase text-slate-400">Agent persona</label>
                        <input
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs focus:border-brand-400 focus:outline-none"
                          value={selected.persona ?? ''}
                          onChange={(e) => update(selectedIdx, { persona: e.target.value })}
                          placeholder="e.g. Security Architect"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] font-semibold uppercase text-slate-400">
                          Prompt template id <span className="normal-case text-slate-300">(optional)</span>
                        </label>
                        <input
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 font-mono text-[11px] focus:border-brand-400 focus:outline-none"
                          value={selected.promptId ?? ''}
                          onChange={(e) => update(selectedIdx, { promptId: e.target.value })}
                          placeholder="e.g. phase.system.craft"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] font-semibold uppercase text-slate-400">
                          Tools <span className="normal-case text-slate-300">(comma-separated, optional)</span>
                        </label>
                        <input
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 font-mono text-[11px] focus:border-brand-400 focus:outline-none"
                          value={(selected.tools ?? []).join(', ')}
                          onChange={(e) => update(selectedIdx, { tools: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })}
                          placeholder="e.g. confluence_publish_prd"
                        />
                      </div>
                      <div className="text-[10px] text-slate-400">
                        A custom phase generates a Markdown deliverable for its declared outputs, then goes to its gate.
                      </div>
                    </div>
                  )}

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">
                      Team, permissions &amp; gate reviewers
                    </label>
                    <div className="mt-1 overflow-hidden rounded-lg border border-slate-200">
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="bg-slate-50 text-[9px] uppercase tracking-wide text-slate-400">
                            <th className="px-2 py-1 text-left font-semibold">Role</th>
                            <th className="px-1 py-1 font-semibold" title="Can view this stage's artifacts">Read</th>
                            <th className="px-1 py-1 font-semibold" title="Can run / retrigger this stage">Write</th>
                            <th className="px-1 py-1 font-semibold" title="Can approve or amend this stage's gate">Gate</th>
                          </tr>
                        </thead>
                        <tbody>
                          {PHASE_ROLE_OPTIONS.map((r) => {
                            const inTeam = selected.team.includes(r);
                            const canRead = inTeam && (selected.readRoles?.length ? selected.readRoles.includes(r) : true);
                            const canWrite = inTeam && (selected.writeRoles?.length ? selected.writeRoles.includes(r) : true);
                            const isReviewer = inTeam && (selected.reviewerRoles?.length
                              ? selected.reviewerRoles.includes(r)
                              : r === selected.reviewerRole);
                            return (
                              <tr key={r} className={inTeam ? 'border-t border-slate-100' : 'border-t border-slate-100 opacity-50'}>
                                <td className="px-2 py-1">
                                  <button
                                    onClick={() => toggleTeamRole(r)}
                                    className={`rounded px-1.5 py-0.5 font-semibold ${
                                      inTeam ? 'bg-brand-100 text-brand-700' : 'bg-slate-100 text-slate-400'
                                    }`}
                                    title={inTeam ? 'Remove from the stage team' : 'Add to the stage team'}
                                  >
                                    {r}
                                  </button>
                                </td>
                                <td className="px-1 py-1 text-center">
                                  <input
                                    type="checkbox" checked={canRead} disabled={!inTeam}
                                    onChange={() => togglePerm('readRoles', r)}
                                    className="h-3.5 w-3.5 accent-amber-500 disabled:opacity-40"
                                  />
                                </td>
                                <td className="px-1 py-1 text-center">
                                  <input
                                    type="checkbox" checked={canWrite} disabled={!inTeam}
                                    onChange={() => togglePerm('writeRoles', r)}
                                    className="h-3.5 w-3.5 accent-emerald-600 disabled:opacity-40"
                                  />
                                </td>
                                <td className="px-1 py-1 text-center">
                                  <input
                                    type="checkbox" checked={isReviewer} disabled={!inTeam}
                                    onChange={() => toggleReviewer(r)}
                                    className="h-3.5 w-3.5 accent-brand-600 disabled:opacity-40"
                                  />
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <div className="mt-1 text-[10px] text-slate-400">
                      Click a role to add/remove it from the team. Multiple roles can hold each permission;
                      any <span className="font-semibold text-brand-600">Gate</span> role may sign the stage.
                      Write implies read.
                    </div>
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Depends on</label>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {stages.filter((o) => o.key !== selected.key).map((other) => {
                        const has = selected.dependsOn.includes(other.key);
                        return (
                          <button
                            key={other.key}
                            onClick={() =>
                              update(selectedIdx, {
                                dependsOn: has
                                  ? selected.dependsOn.filter((d) => d !== other.key)
                                  : [...selected.dependsOn, other.key],
                              })
                            }
                            className={`rounded-md px-2 py-1 text-[11px] ${
                              has ? 'bg-indigo-100 font-semibold text-indigo-700' : 'bg-slate-100 text-slate-400'
                            }`}
                          >
                            {other.name || other.key}
                          </button>
                        );
                      })}
                      {stages.length === 1 && <span className="text-[10px] text-slate-400">entry stage (no dependencies)</span>}
                    </div>
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Inputs (from upstream outputs)</label>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {outputsUniverse.map((o) => {
                        const has = selected.inputs.includes(o);
                        return (
                          <button
                            key={o}
                            onClick={() =>
                              update(selectedIdx, {
                                inputs: has ? selected.inputs.filter((x) => x !== o) : [...selected.inputs, o],
                              })
                            }
                            className={`rounded-md px-2 py-1 text-[11px] ${
                              has ? 'bg-amber-100 font-semibold text-amber-700' : 'bg-slate-100 text-slate-400'
                            }`}
                          >
                            {o}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Outputs (comma-separated)</label>
                    <input
                      className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 font-mono text-[11px] focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-200"
                      value={selected.outputs.join(', ')}
                      onChange={(e) =>
                        update(selectedIdx, { outputs: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })
                      }
                      placeholder="PRD, acceptance-criteria"
                    />
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
