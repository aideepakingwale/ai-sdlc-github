import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import {
  entryHealth, isDirty, move, seedDraft, toPayload,
  type ChainEntry, type ModelRoutesView, type RoleKey,
} from '../lib/modelRoutes';

const TONE: Record<string, string> = {
  ok: 'bg-emerald-50 text-emerald-700', warn: 'bg-amber-50 text-amber-700', info: 'bg-slate-100 text-slate-500',
};
const SOURCE: Record<string, string> = {
  admin: 'set here', env: 'from .env', default: 'default chain',
};

/**
 * Super-admin multi-model routing: which models serve which kind of work. Each role has
 * an ordered chain (first = preferred, the rest are fallbacks); an empty role uses the
 * normal provider chain. Applies live - no restart.
 */
export default function ModelRoutesControl() {
  const qc = useQueryClient();
  const view = useQuery({
    queryKey: ['admin', 'model-routes'],
    queryFn: () => api.get<ModelRoutesView>('/api/admin/model-routes'),
    refetchInterval: 15_000,
  });
  const save = useMutation({
    mutationFn: (routes: Record<string, string[]>) => api.put<ModelRoutesView>('/api/admin/model-routes', { routes }),
    onSuccess: (data) => { qc.setQueryData(['admin', 'model-routes'], data); setSeeded(false); },
  });
  const [draft, setDraft] = useState<Record<string, ChainEntry[]>>({});
  const [seeded, setSeeded] = useState(false);
  const v = view.data;
  useEffect(() => {
    if (v && !seeded) { setDraft(seedDraft(v.roles)); setSeeded(true); }
  }, [v, seeded]);

  if (view.isError) return null;                      // not a super-admin / gateway unreachable
  if (!v) return <div className="rounded-xl border border-slate-200 bg-white p-3 text-xs text-slate-400">Loading model routing…</div>;

  const dirty = isDirty(draft, v.roles, v.maxChain);
  const setChain = (role: string, chain: ChainEntry[]) => setDraft((d) => ({ ...d, [role]: chain }));
  const knownModels = (provider: string) => v.catalog.filter((m) => m.provider === provider).map((m) => m.model);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3" data-testid="model-routes">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
          Model routing <span className="normal-case text-slate-300">(super-admin · mix fast and reasoning models · applies live)</span>
        </div>
        <div className="flex items-center gap-2">
          {save.isError && <span className="text-[11px] text-red-600">{save.error instanceof Error ? save.error.message : 'Could not save'}</span>}
          <button
            onClick={() => setDraft(seedDraft(v.roles))}
            disabled={!dirty || save.isPending}
            className="rounded px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-100 disabled:opacity-40"
          >
            Discard
          </button>
          <button
            onClick={() => save.mutate(toPayload(draft, v.maxChain))}
            disabled={!dirty || save.isPending}
            className="rounded bg-brand-600 px-3 py-1 text-[11px] font-semibold text-white disabled:opacity-40"
          >
            {save.isPending ? 'Saving…' : 'Save routing'}
          </button>
        </div>
      </div>
      <p className="mb-2 text-[11px] text-slate-500">
        Pick a model per kind of work, as <code>provider/model</code> (e.g. a strong model for reasoning, a small one for fast
        checks). The first model is tried first; if it fails the next one is used, and finally the normal provider chain — so
        a wrong id never breaks a run. Leave a role empty to keep the default.
      </p>

      <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        {v.roles.map((r) => {
          const chain = draft[r.role] ?? [];
          return (
            <div key={r.role} className="rounded-lg border border-slate-200 p-2" data-testid={`route-${r.role}`}>
              <div className="flex items-baseline justify-between gap-2">
                <div className="text-xs font-semibold text-slate-800">{r.label}</div>
                <div className="text-[10px] text-slate-400">
                  {r.source === 'env' && !chain.length ? `${SOURCE.env}: ${r.envDefault.join(', ')}` : SOURCE[r.source]}
                </div>
              </div>
              <div className="mb-1.5 text-[10px] text-slate-400">{r.description}</div>

              {chain.length === 0 && (
                <div className="mb-1 rounded bg-slate-50 px-2 py-1 text-[11px] text-slate-400">
                  {r.envDefault.length ? `Using ${r.envDefault.join(', ')} from .env` : 'Using the normal provider chain'}
                </div>
              )}
              {chain.map((e, i) => {
                const health = entryHealth(e, v.catalog, r.role as RoleKey);
                return (
                  <div key={i} className="mb-1 flex items-center gap-1">
                    <span className="w-4 text-center text-[10px] text-slate-400">{i + 1}</span>
                    <select
                      aria-label={`${r.label} provider ${i + 1}`}
                      className="w-24 rounded border border-slate-300 px-1 py-1 text-[11px]"
                      value={e.provider}
                      onChange={(ev) => setChain(r.role, chain.map((x, j) => (j === i ? { ...x, provider: ev.target.value } : x)))}
                    >
                      <option value="">provider…</option>
                      {v.providers.map((p) => <option key={p} value={p}>{p}</option>)}
                    </select>
                    <input
                      aria-label={`${r.label} model ${i + 1}`}
                      list={`models-${r.role}-${i}`}
                      className="min-w-0 flex-1 rounded border border-slate-300 px-1.5 py-1 font-mono text-[11px]"
                      placeholder="model id"
                      value={e.model}
                      onChange={(ev) => setChain(r.role, chain.map((x, j) => (j === i ? { ...x, model: ev.target.value } : x)))}
                    />
                    <datalist id={`models-${r.role}-${i}`}>
                      {knownModels(e.provider).map((m) => <option key={m} value={m} />)}
                    </datalist>
                    <span className={`hidden rounded px-1 py-0.5 text-[9px] xl:inline ${TONE[health.tone]}`} title={health.text}>
                      {health.tone === 'ok' ? '●' : '▲'}
                    </span>
                    <button aria-label="Move up" disabled={i === 0} onClick={() => setChain(r.role, move(chain, i, -1))}
                      className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30">↑</button>
                    <button aria-label="Move down" disabled={i === chain.length - 1} onClick={() => setChain(r.role, move(chain, i, 1))}
                      className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30">↓</button>
                    <button aria-label="Remove model" onClick={() => setChain(r.role, chain.filter((_, j) => j !== i))}
                      className="px-1 text-slate-400 hover:text-red-600">✕</button>
                  </div>
                );
              })}
              {chain.length < v.maxChain && (
                <button
                  onClick={() => setChain(r.role, [...chain, { provider: chain.at(-1)?.provider ?? 'bedrock', model: '' }])}
                  className="text-[11px] font-semibold text-brand-700 hover:underline"
                >
                  + {chain.length ? 'Add fallback' : 'Set a model'}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
