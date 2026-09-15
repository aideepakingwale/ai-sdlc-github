import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';

/**
 * AI observability dashboard (, SUPER_ADMIN): live view over every LLM
 * call and MCP tool execution — volumes, tokens, latency, error rate,
 * estimated cost, per-provider/per-day breakdowns and a recent-trace feed.
 * Backed by the same llm_traces table that /metrics (Prometheus) exposes.
 */

interface Summary {
  days: number;
  totals: {
    llm_calls: number; tool_calls: number; prompt_tokens: number; completion_tokens: number;
    avg_latency_ms: number; p95_latency_ms: number; errors: number; cost_usd: number;
  };
  providers: Array<{
    provider: string; calls: number; prompt_tokens: number; completion_tokens: number;
    avg_latency_ms: number; errors: number; cost_usd: number;
  }>;
  daily: Array<{ day: string; llm_calls: number; tokens: number }>;
  tools: Array<{ tool: string; calls: number; avg_latency_ms: number; errors: number }>;
}

interface Trace {
  ts: string; projectId: string | null; stage: number | null; kind: string;
  provider: string | null; model: string | null; tier: string | null; tag: string | null;
  promptTokens: number; completionTokens: number; latencyMs: number;
  status: string; error: string | null; costUsd: number;
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-0.5 text-lg font-bold text-slate-800">{value}</div>
      {sub && <div className="text-[10px] text-slate-400">{sub}</div>}
    </div>
  );
}

export default function ObservabilityPanel({ onClose }: { onClose: () => void }) {
  const [days, setDays] = useState(7);

  const summary = useQuery({
    queryKey: ['obs', 'summary', days],
    queryFn: () => api.get<Summary>(`/api/observability/summary?days=${days}`),
    refetchInterval: 10_000,
  });
  const traces = useQuery({
    queryKey: ['obs', 'traces'],
    queryFn: () => api.get<{ traces: Trace[] }>('/api/observability/traces?limit=50'),
    refetchInterval: 5_000,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const t = summary.data?.totals;
  const maxDailyCalls = Math.max(1, ...(summary.data?.daily ?? []).map((d) => Number(d.llm_calls)));

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="flex max-h-[94vh] w-full max-w-5xl flex-col rounded-2xl bg-slate-50 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
          <div>
            <div className="text-sm font-bold text-slate-800">📈 AI Observability</div>
            <div className="text-xs text-slate-500">
              Every LLM call and tool execution — live from the trace store · Prometheus at /metrics
              · optional LangSmith deep traces
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="rounded border border-slate-300 px-2 py-1 text-xs"
            >
              <option value={1}>Last 24h</option>
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
            </select>
            <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {t && (
            <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
              <Kpi label="LLM calls" value={String(t.llm_calls)} />
              <Kpi label="Tool calls" value={String(t.tool_calls)} />
              <Kpi
                label="Tokens"
                value={`${((Number(t.prompt_tokens) + Number(t.completion_tokens)) / 1000).toFixed(1)}k`}
                sub={`${t.prompt_tokens} in / ${t.completion_tokens} out`}
              />
              <Kpi
                label="Latency"
                value={`${Number(t.avg_latency_ms).toFixed(0)}ms`}
                sub={`p95 ${Number(t.p95_latency_ms).toFixed(0)}ms`}
              />
              <Kpi
                label="Error rate"
                value={`${((Number(t.errors) / Math.max(1, Number(t.llm_calls) + Number(t.tool_calls))) * 100).toFixed(1)}%`}
                sub={`${t.errors} errors`}
              />
              <Kpi label="Est. cost" value={`$${Number(t.cost_usd).toFixed(4)}`} sub="list-price estimate" />
            </div>
          )}

          {/* daily activity bars */}
          {summary.data && summary.data.daily.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                Daily LLM calls
              </div>
              <div className="flex h-20 items-end gap-1">
                {summary.data.daily.map((d) => (
                  <div key={String(d.day)} className="flex flex-1 flex-col items-center gap-0.5">
                    <div
                      className="w-full rounded-t bg-brand-400"
                      style={{ height: `${(Number(d.llm_calls) / maxDailyCalls) * 100}%`, minHeight: 2 }}
                      title={`${d.day}: ${d.llm_calls} calls, ${d.tokens} tokens`}
                    />
                    <div className="text-[8px] text-slate-400">{String(d.day).slice(5)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid gap-3 lg:grid-cols-2">
            {/* provider breakdown */}
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                Providers
              </div>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-[9px] uppercase text-slate-400">
                    <th className="py-1">Provider</th><th>Calls</th><th>Tokens</th><th>Avg ms</th><th>Err</th><th>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {(summary.data?.providers ?? []).map((p) => (
                    <tr key={p.provider ?? 'unknown'} className="border-t border-slate-100">
                      <td className="py-1 font-semibold text-slate-700">{p.provider ?? '—'}</td>
                      <td>{p.calls}</td>
                      <td>{Number(p.prompt_tokens) + Number(p.completion_tokens)}</td>
                      <td>{Number(p.avg_latency_ms).toFixed(0)}</td>
                      <td className={Number(p.errors) > 0 ? 'text-red-600' : ''}>{p.errors}</td>
                      <td>${Number(p.cost_usd).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* tool breakdown */}
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                MCP tools
              </div>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-[9px] uppercase text-slate-400">
                    <th className="py-1">Tool</th><th>Calls</th><th>Avg ms</th><th>Err</th>
                  </tr>
                </thead>
                <tbody>
                  {(summary.data?.tools ?? []).map((x) => (
                    <tr key={x.tool ?? 'unknown'} className="border-t border-slate-100">
                      <td className="py-1 font-mono text-[11px] text-slate-700">{x.tool ?? '—'}</td>
                      <td>{x.calls}</td>
                      <td>{Number(x.avg_latency_ms).toFixed(0)}</td>
                      <td className={Number(x.errors) > 0 ? 'text-red-600' : ''}>{x.errors}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* live trace feed */}
          <div className="rounded-xl border border-slate-200 bg-white p-3">
            <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
              Recent spans <span className="animate-pulse rounded bg-emerald-100 px-1 text-[9px] normal-case text-emerald-700">live</span>
            </div>
            <div className="max-h-64 overflow-y-auto">
              <table className="w-full text-left text-[11px]">
                <thead className="sticky top-0 bg-white">
                  <tr className="text-[9px] uppercase text-slate-400">
                    <th className="py-1">Time</th><th>Kind</th><th>Provider / Tool</th><th>Tier</th>
                    <th>Tag</th><th>Tokens</th><th>ms</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(traces.data?.traces ?? []).map((tr, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="py-1 text-slate-400">{tr.ts.slice(11, 19)}</td>
                      <td>{tr.kind === 'llm' ? '🧠' : '🔌'}</td>
                      <td className="font-mono text-[10px]">{tr.kind === 'llm' ? `${tr.provider ?? '—'} / ${tr.model ?? ''}` : tr.tag}</td>
                      <td>{tr.tier ?? ''}</td>
                      <td className="max-w-40 truncate text-slate-500">{tr.kind === 'llm' ? tr.tag : ''}</td>
                      <td>{tr.promptTokens + tr.completionTokens || ''}</td>
                      <td>{tr.latencyMs}</td>
                      <td>
                        <span className={`rounded px-1 text-[9px] font-semibold ${
                          tr.status === 'ok' ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-600'
                        }`} title={tr.error ?? ''}>
                          {tr.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {(summary.isError || traces.isError) && (
            <div className="rounded bg-red-50 px-3 py-2 text-xs text-red-700">
              Failed to load observability data (SUPER_ADMIN required).
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
