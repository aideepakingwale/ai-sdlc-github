import { useState } from 'react';
import { agileApi, type AgileOverview } from '../../api/agile';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { errorText, useAgileMutation } from './hooks';

type Row = { id: 'stagePreset' | 'intakeRule' | 'usePool'; label: string; options: Array<{ id: string; label: string }> };
const ROWS: Row[] = [
  { id: 'stagePreset', label: 'Stages a new release runs', options: [{ id: 'inherit', label: 'Same as the project' }, { id: 'lean', label: 'No planning ceremony' }, { id: 'hotfix', label: 'Fix and ship' }, { id: 'build-only', label: 'Build and review only' }] },
  { id: 'intakeRule', label: 'Where new Jira issues go', options: [{ id: 'pool', label: 'Shared unassigned pool' }, { id: 'epic', label: 'By epic' }] },
  { id: 'usePool', label: 'Planning may draw on the shared pool', options: [{ id: 'true', label: 'Yes' }, { id: 'false', label: 'No' }] },
];

/** Project-admin policy for the start-release questionnaire: what is pre-filled, and what is locked to it. */
export default function ReleasePolicy({ projectId, overview }: { projectId: string; overview: AgileOverview }) {
  const defaults = (overview.settings?.releaseDefaults ?? {}) as Record<string, unknown>;
  const locks = overview.settings?.releaseLocks ?? [];
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(ROWS.filter((r) => r.id in defaults).map((r) => [r.id, String(defaults[r.id])])));
  const [locked, setLocked] = useState<string[]>(locks);
  // Only the rows shown here are edited: defaults and locks set through the API for other questions are kept.
  const shown = new Set<string>(ROWS.map((r) => r.id));
  const save = useAgileMutation(projectId, () => {
    const edited = Object.fromEntries(Object.entries(vals).filter(([, v]) => v !== '').map(([k, v]) => [k, k === 'usePool' ? v === 'true' : v]));
    const keep = Object.fromEntries(Object.entries(defaults).filter(([k]) => !shown.has(k)));
    return agileApi.updateSettings(projectId, {
      releaseDefaults: { ...keep, ...edited },
      releaseLocks: [...locks.filter((l) => !shown.has(l)), ...locked.filter((l) => shown.has(l) && vals[l])],
    });
  });
  return (
    <section aria-label="Release policy" className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-800">Policy for new releases</h3>
      <p className="mb-3 text-xs text-slate-500">Pre-fill answers of the start-release questions. A locked answer is always the default, whatever a person picks.</p>
      <div className="space-y-2">
        {ROWS.map((r) => (
          <div key={r.id} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="w-64 text-slate-700">{r.label}</span>
            <select aria-label={r.label} value={vals[r.id] ?? ''} onChange={(e) => setVals({ ...vals, [r.id]: e.target.value })}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm">
              <option value="">No default</option>
              {r.options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
            <label className="flex items-center gap-1 text-xs text-slate-600">
              <input type="checkbox" disabled={!vals[r.id]} checked={locked.includes(r.id) && Boolean(vals[r.id])}
                onChange={(e) => setLocked(e.target.checked ? [...locked, r.id] : locked.filter((x) => x !== r.id))} />Lock</label>
          </div>))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save policy</Button>
        {save.isSuccess && <span className="text-xs text-emerald-700" role="status">Saved</span>}
      </div>
      {save.isError && <Callout tone="error" compact className="mt-2" title="Could not save">{errorText(save.error)}</Callout>}
    </section>
  );
}
