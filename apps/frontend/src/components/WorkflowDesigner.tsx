import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import {
  CONTEXT_SOURCE_OPTIONS,
  TEMPLATE_NAMES,
  type OutputSpec,
  type StageConfig,
  type UserPerm,
  type WorkflowView,
} from '../api/flow';
import type { ProjectMember } from '../api/types';
import WorkflowCanvas, { STAGE_PALETTE, type StagePreset } from './WorkflowCanvas';

/** Searchable user picker (D-90): search project members by name/email and add
 *  them as chips. Reused for stage access, per-output reviewers and stage
 *  reviewers — access is per USER, never per role. */
function UserPicker({
  members, value, onAdd, onRemove, placeholder,
}: {
  members: ProjectMember[];
  value: string[];
  onAdd: (email: string) => void;
  onRemove: (email: string) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const selected = new Set(value.map((e) => e.toLowerCase()));
  const matches = members
    .filter((m) => !selected.has(m.email.toLowerCase()))
    .filter((m) => q.trim() === '' || `${m.displayName} ${m.email}`.toLowerCase().includes(q.trim().toLowerCase()))
    .slice(0, 8);
  return (
    <div className="relative">
      {value.length > 0 && (
        <div className="mb-1 flex flex-wrap gap-1">
          {value.map((e) => {
            const m = members.find((x) => x.email.toLowerCase() === e.toLowerCase());
            return (
              <span key={e} className="inline-flex items-center gap-1 rounded-full bg-brand-100 px-2 py-0.5 text-[11px] text-brand-700">
                {m?.displayName ?? e}
                <button type="button" onClick={() => onRemove(e)} className="text-brand-400 hover:text-brand-700" title="Remove">×</button>
              </span>
            );
          })}
        </div>
      )}
      <input
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder ?? 'Search users by name or email…'}
        className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs focus:border-brand-400 focus:outline-none"
      />
      {open && matches.length > 0 && (
        <div className="absolute z-20 mt-1 max-h-48 w-full overflow-auto rounded-lg border border-slate-200 bg-white shadow-lg">
          {matches.map((m) => (
            <button
              key={m.email}
              type="button"
              onMouseDown={(ev) => { ev.preventDefault(); onAdd(m.email); setQ(''); }}
              className="block w-full px-2 py-1.5 text-left text-xs hover:bg-slate-50"
            >
              <span className="font-medium text-slate-700">{m.displayName}</span>
              <span className="ml-1 text-slate-400">{m.email}</span>
              <span className="ml-1 rounded bg-slate-100 px-1 text-[9px] uppercase text-slate-500">{m.role}</span>
            </button>
          ))}
        </div>
      )}
      {open && q.trim() !== '' && matches.length === 0 && (
        <div className="absolute z-20 mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[11px] text-slate-400 shadow">
          No matching project member — add them to the team first (Team panel).
        </div>
      )}
    </div>
  );
}

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
 * Visual workflow workspace (D-30, redesigned D-40): a three-pane visual editor.
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

  const wf = useQuery({
    queryKey: ['workflow', projectId],
    queryFn: () => api.get<WorkflowView>(`/api/projects/${projectId}/workflow`),
  });

  // Project members drive the per-user ACL / reviewer pickers (D-90).
  const membersQ = useQuery({
    queryKey: ['members', projectId],
    queryFn: () => api.get<{ members: ProjectMember[] }>(`/api/projects/${projectId}/members`),
  });
  const members = membersQ.data?.members ?? [];

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

  /** Draw a dependency edge from → to (to depends on from). Guards self-links,
   *  duplicates and cycles. Used by the canvas drag-to-connect. */
  const connectEdge = useCallback((from: string, to: string) => {
    if (!stages || from === to) return;
    if (ancestorsOf(stages, from).has(to)) return;  // would create a cycle
    const next = stages.map((s) =>
      s.key === to && !s.dependsOn.includes(from)
        ? { ...s, dependsOn: [...s.dependsOn, from] }
        : { ...s, dependsOn: [...s.dependsOn] });
    setStages(autoFixInputs(next));
  }, [stages, ancestorsOf]);

  /** Remove the dependency edge from → to (click an arrow to disconnect). */
  const disconnectEdge = useCallback((from: string, to: string) => {
    if (!stages) return;
    setStages(stages.map((s) =>
      s.key === to ? { ...s, dependsOn: s.dependsOn.filter((d) => d !== from) } : s));
  }, [stages]);

  /** Per-stage issue list (client-side mirror of the server's intent) so each
   *  node/inspector can show precisely what is wrong with it. */
  const issuesFor = useCallback(
    (list: StageConfig[], s: StageConfig): string[] => {
      const out: string[] = [];
      if (!s.name.trim()) out.push('Name is empty');
      if (s.outputs.length === 0) out.push('Produces no outputs');
      // D-90: per-output description + user-based write.
      for (const o of s.outputSpecs ?? []) {
        if (!(o.description ?? '').trim()) out.push(`Output "${o.name}" has no description`);
      }
      const perms = s.userPerms ?? [];
      if (perms.length > 0 && !perms.some((p) => p.write)) out.push('No user has write permission');
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

  // Stable callbacks for the canvas (avoid re-render loops in its reconcile effect).
  const templateShortCb = useCallback((t: number) => TEMPLATE_SHORT[t] ?? `T${t}`, []);
  const getIssuesCb = useCallback((s: StageConfig) => (stages ? issuesFor(stages, s) : []), [issuesFor, stages]);

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

  /** Add a stage from a standard-SDLC palette preset (dropped on the canvas). */
  const addPresetStage = (preset: StagePreset) => {
    let n = stages.length + 1;
    let key = preset.id;
    while (stages.some((s) => s.key === key)) { key = `${preset.id}-${n}`; n += 1; }
    const last = stages[stages.length - 1];
    const stage: StageConfig = {
      key, name: preset.label, template: preset.template,
      persona: preset.persona || undefined, promptId: '', tools: [],
      reviewerRole: preset.role, reviewerRoles: [preset.role], team: [...preset.team],
      readRoles: [], writeRoles: [], reviewerUsers: [],
      inputs: [...preset.inputs], outputs: [...preset.outputs],
      dependsOn: last ? [last.key] : [],
    };
    setStages(autoFixInputs([...stages, stage]));
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

  // ---- D-90: per-user ACL, structured outputs & agent context helpers ----
  const stageUserPerms = (s: StageConfig): UserPerm[] => s.userPerms ?? [];

  const addStageUser = (email: string) => {
    if (!selected) return;
    const cur = stageUserPerms(selected);
    if (cur.some((p) => p.email.toLowerCase() === email.toLowerCase())) return;
    update(selectedIdx, { userPerms: [...cur, { email, read: true, write: false, gate: false }] });
  };
  const removeStageUser = (email: string) => {
    if (!selected) return;
    update(selectedIdx, {
      userPerms: stageUserPerms(selected).filter((p) => p.email.toLowerCase() !== email.toLowerCase()),
    });
  };
  const toggleUserPerm = (email: string, kind: 'read' | 'write' | 'gate') => {
    if (!selected) return;
    update(selectedIdx, {
      userPerms: stageUserPerms(selected).map((p) => {
        if (p.email.toLowerCase() !== email.toLowerCase()) return p;
        const next = { ...p, [kind]: !p[kind] };
        if (kind === 'write' && next.write) next.read = true;   // write implies read
        if (kind === 'read' && !next.read) next.write = false;  // read revoked => write revoked
        return next;
      }),
    });
  };

  /** Outputs are the canonical name list; outputSpecs carries the per-output
   *  metadata + reviewers. specsFor seeds a spec for any output lacking one; once
   *  the user edits, outputSpecs becomes the source of truth (edited BY INDEX so
   *  renaming stays stable). outputs is always kept aligned to the spec names. */
  const specsFor = (s: StageConfig): OutputSpec[] =>
    (s.outputSpecs && s.outputSpecs.length
      ? s.outputSpecs
      : s.outputs.map((name) => ({ name, description: '', spec: '', reviewers: [] })));
  const commitSpecs = (specs: OutputSpec[]) =>
    update(selectedIdx, { outputs: specs.map((o) => o.name), outputSpecs: specs });
  const updateSpecAt = (i: number, patch: Partial<OutputSpec>) => {
    if (!selected) return;
    commitSpecs(specsFor(selected).map((o, idx) => (idx === i ? { ...o, ...patch } : o)));
  };
  const addOutput = () => {
    if (!selected) return;
    const specs = specsFor(selected);
    let name = 'new-output'; let n = 1;
    while (specs.some((o) => o.name === name)) { name = `new-output-${n}`; n += 1; }
    commitSpecs([...specs, { name, description: '', spec: '', reviewers: [] }]);
  };
  const removeOutputAt = (i: number) => {
    if (!selected) return;
    commitSpecs(specsFor(selected).filter((_, idx) => idx !== i));
  };
  const toggleContext = (id: string) => {
    if (!selected) return;
    const cur = selected.contextSources ?? [];
    update(selectedIdx, { contextSources: cur.includes(id) ? cur.filter((c) => c !== id) : [...cur, id] });
  };
  const setStageReviewers = (emails: string[]) => update(selectedIdx, { reviewerUsers: emails });

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
          {/* ===== left: palette + stage rail ===== */}
          <div className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-slate-50">
            {/* Standard SDLC palette — drag a pattern onto the canvas (or click to add) */}
            <div className="border-b border-slate-200">
              <div className="px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                Standard stages · drag to canvas
              </div>
              <div className="max-h-56 space-y-1 overflow-y-auto px-2 pb-2">
                {STAGE_PALETTE.map((p) => (
                  <div
                    key={p.id}
                    draggable
                    onDragStart={(e) => { e.dataTransfer.setData('application/wf-preset', p.id); e.dataTransfer.effectAllowed = 'copy'; }}
                    onClick={() => addPresetStage(p)}
                    title={`${p.label} — ${p.persona || TEMPLATE_SHORT[p.template] || 'stage'} · gate ${p.role}\noutputs: ${p.outputs.join(', ')}\nDrag onto the canvas or click to add.`}
                    className="flex cursor-grab items-center gap-2 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-left shadow-sm transition hover:border-brand-300 active:cursor-grabbing"
                  >
                    <span className="rounded bg-slate-100 px-1 py-0.5 text-[9px] font-semibold uppercase text-slate-500">
                      {TEMPLATE_SHORT[p.template] ?? `T${p.template}`}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-semibold text-slate-700">{p.label}</span>
                      <span className="block truncate text-[10px] text-slate-400">{p.group} · gate {p.role}</span>
                    </span>
                    <span className="text-slate-300">⠿</span>
                  </div>
                ))}
              </div>
            </div>
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

          {/* ===== center: node canvas (React Flow) ===== */}
          <div className="relative min-w-0 flex-1">
            <WorkflowCanvas
              projectId={projectId}
              stages={stages}
              selectedKey={selectedKey}
              onSelect={setSelectedKey}
              onConnectDep={connectEdge}
              onDisconnectDep={disconnectEdge}
              onAddPreset={addPresetStage}
              onDeleteStage={(key) => { const i = stages.findIndex((s) => s.key === key); if (i >= 0) removeStage(i); }}
              getIssues={getIssuesCb}
              templateShort={templateShortCb}
            />
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
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Agent</label>
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
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Project context to feed the agent</label>
                    <div className="mt-1 grid grid-cols-1 gap-1">
                      {CONTEXT_SOURCE_OPTIONS.map((c) => {
                        const on = (selected.contextSources ?? []).includes(c.id);
                        return (
                          <label
                            key={c.id}
                            className={`flex cursor-pointer items-start gap-2 rounded-md border px-2 py-1.5 text-[11px] ${
                              on ? 'border-brand-300 bg-brand-50' : 'border-slate-200 bg-white'
                            }`}
                          >
                            <input type="checkbox" checked={on} onChange={() => toggleContext(c.id)} className="mt-0.5 h-3.5 w-3.5 accent-brand-600" />
                            <span>
                              <span className="font-medium text-slate-700">{c.label}</span>
                              <span className="block text-[10px] text-slate-400">{c.hint}</span>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">
                      Agent notes / metadata <span className="normal-case text-slate-300">(optional)</span>
                    </label>
                    <textarea
                      rows={2}
                      className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs focus:border-brand-400 focus:outline-none"
                      value={selected.agentNotes ?? ''}
                      onChange={(e) => update(selectedIdx, { agentNotes: e.target.value })}
                      placeholder="Extra instructions or context the agent should apply for this stage…"
                    />
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
                      Stage access (per user)
                    </label>
                    <div className="mt-1">
                      <UserPicker
                        members={members}
                        value={[]}
                        onAdd={addStageUser}
                        onRemove={() => {}}
                        placeholder="Add a user to this stage…"
                      />
                    </div>
                    {stageUserPerms(selected).length > 0 ? (
                      <div className="mt-2 overflow-hidden rounded-lg border border-slate-200">
                        <table className="w-full text-[11px]">
                          <thead>
                            <tr className="bg-slate-50 text-[9px] uppercase tracking-wide text-slate-400">
                              <th className="px-2 py-1 text-left font-semibold">User</th>
                              <th className="px-1 py-1 font-semibold" title="Can view this stage's artifacts">Read</th>
                              <th className="px-1 py-1 font-semibold" title="Can run / retrigger this stage">Write</th>
                              <th className="px-1 py-1 font-semibold" title="Can approve or amend this stage's gate">Gate</th>
                              <th className="px-1 py-1" />
                            </tr>
                          </thead>
                          <tbody>
                            {stageUserPerms(selected).map((p) => {
                              const m = members.find((x) => x.email.toLowerCase() === p.email.toLowerCase());
                              return (
                                <tr key={p.email} className="border-t border-slate-100">
                                  <td className="px-2 py-1">
                                    <div className="font-medium text-slate-700">{m?.displayName ?? p.email}</div>
                                    <div className="text-[9px] text-slate-400">{p.email}</div>
                                  </td>
                                  <td className="px-1 py-1 text-center">
                                    <input type="checkbox" checked={p.read} onChange={() => toggleUserPerm(p.email, 'read')}
                                      className="h-3.5 w-3.5 accent-amber-500" />
                                  </td>
                                  <td className="px-1 py-1 text-center">
                                    <input type="checkbox" checked={p.write} onChange={() => toggleUserPerm(p.email, 'write')}
                                      className="h-3.5 w-3.5 accent-emerald-600" />
                                  </td>
                                  <td className="px-1 py-1 text-center">
                                    <input type="checkbox" checked={p.gate} onChange={() => toggleUserPerm(p.email, 'gate')}
                                      className="h-3.5 w-3.5 accent-brand-600" />
                                  </td>
                                  <td className="px-1 py-1 text-center">
                                    <button type="button" onClick={() => removeStageUser(p.email)}
                                      className="text-slate-300 hover:text-red-500" title="Remove user">×</button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="mt-1 text-[10px] text-slate-400">
                        No users added — access falls back to the project defaults. Add specific users to control
                        who can read, run and approve this stage. <span className="font-semibold">Write</span> implies read.
                      </div>
                    )}
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
                    <div className="flex items-center justify-between">
                      <label className="text-[10px] font-semibold uppercase text-slate-400">Output artifacts &amp; reviewers</label>
                      <button
                        type="button"
                        onClick={addOutput}
                        className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-200"
                      >+ Add output</button>
                    </div>
                    <div className="mt-1 space-y-2">
                      {specsFor(selected).map((o, i) => (
                        <div key={i} className="rounded-lg border border-slate-200 bg-slate-50/50 p-2">
                          <div className="flex items-center gap-1">
                            <input
                              className="w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-[11px] focus:border-brand-400 focus:outline-none"
                              value={o.name}
                              onChange={(e) => updateSpecAt(i, { name: e.target.value.trim() })}
                              placeholder="artifact-name e.g. PRD"
                            />
                            <button type="button" onClick={() => removeOutputAt(i)}
                              className="shrink-0 rounded px-1.5 py-1 text-xs text-slate-400 hover:bg-red-50 hover:text-red-600" title="Remove output">🗑</button>
                          </div>
                          <input
                            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-[11px] focus:border-brand-400 focus:outline-none"
                            value={o.description ?? ''}
                            onChange={(e) => updateSpecAt(i, { description: e.target.value })}
                            placeholder="Description — what this artifact is"
                          />
                          <textarea
                            rows={2}
                            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-[11px] focus:border-brand-400 focus:outline-none"
                            value={o.spec ?? ''}
                            onChange={(e) => updateSpecAt(i, { spec: e.target.value })}
                            placeholder="Specification — acceptance criteria / required sections (optional)"
                          />
                          <div className="mt-1">
                            <div className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">Reviewers for this artifact</div>
                            <UserPicker
                              members={members}
                              value={o.reviewers ?? []}
                              onAdd={(email) => updateSpecAt(i, { reviewers: [...(o.reviewers ?? []), email] })}
                              onRemove={(email) => updateSpecAt(i, { reviewers: (o.reviewers ?? []).filter((r) => r.toLowerCase() !== email.toLowerCase()) })}
                              placeholder="Assign a reviewer for this artifact…"
                            />
                          </div>
                        </div>
                      ))}
                      {specsFor(selected).length === 0 && (
                        <div className="text-[10px] text-slate-400">No outputs yet — add at least one artifact this stage produces.</div>
                      )}
                    </div>
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold uppercase text-slate-400">Stage reviewers (optional)</label>
                    <div className="mt-1">
                      <UserPicker
                        members={members}
                        value={selected.reviewerUsers ?? []}
                        onAdd={(email) => setStageReviewers([...(selected.reviewerUsers ?? []), email])}
                        onRemove={(email) => setStageReviewers((selected.reviewerUsers ?? []).filter((r) => r.toLowerCase() !== email.toLowerCase()))}
                        placeholder="Add a stage-level reviewer…"
                      />
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      The stage completes when <span className="font-semibold">every artifact's reviewers</span> have signed,
                      <span className="font-semibold"> or</span> any one stage-level reviewer signs off the whole stage.
                    </div>
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
