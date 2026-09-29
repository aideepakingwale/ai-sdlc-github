import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';

/**
 * AI observability dashboard (D-35, SUPER_ADMIN): live view over every LLM
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
  id: string;
  ts: string; projectId: string | null; stage: number | null; kind: string;
  provider: string | null; model: string | null; tier: string | null; tag: string | null;
  promptTokens: number; completionTokens: number; latencyMs: number;
  status: string; error: string | null; costUsd: number;
  hasBodies?: boolean;
}
interface TraceDetail {
  id: string; ts: string; provider: string | null; model: string | null; tag: string | null;
  requestBody: string | null; responseBody: string | null;
}

interface LlmConfig {
  generation_mode: string | null;
  bedrock_model_id: string | null;
  bedrock_region: string | null;
  groq_model: string | null;
  gemini_model: string | null;
  xai_model: string | null;
  groq_api_key_set: boolean;
  gemini_api_key_set: boolean;
  xai_api_key_set: boolean;
  llm_debug_trace: string | null;  // "true" when debug capture is on (D-104)
  per_artifact_generation: string | null;  // "true" when per-artifact split is on (D-106)
}
interface LlmConfigState {
  config: LlmConfig;
  options: string[];
  generationMode: string;   // live effective mode reported by ai-client
  effectiveMock: boolean;
  activeProviders: string[];
}

const TEXT_FIELDS: Array<{ key: keyof LlmConfig; label: string; placeholder: string }> = [
  { key: 'bedrock_region', label: 'Bedrock region', placeholder: 'e.g. us-east-1' },
  { key: 'bedrock_model_id', label: 'Bedrock model id', placeholder: 'e.g. us.anthropic.claude-sonnet-4-6' },
  { key: 'groq_model', label: 'Groq model', placeholder: 'llama-3.3-70b-versatile' },
  { key: 'gemini_model', label: 'Gemini model', placeholder: 'gemini-2.5-flash-lite' },
  { key: 'xai_model', label: 'xAI model', placeholder: 'grok-2-latest' },
];
const SECRET_FIELDS: Array<{ key: string; setKey: keyof LlmConfig; label: string }> = [
  { key: 'groq_api_key', setKey: 'groq_api_key_set', label: 'Groq API key' },
  { key: 'gemini_api_key', setKey: 'gemini_api_key_set', label: 'Gemini API key' },
  { key: 'xai_api_key', setKey: 'xai_api_key_set', label: 'xAI API key' },
];

/** Super-Admin runtime LLM configuration (D-91/D-92): generation mode + Bedrock
 *  region/model + provider model names + write-only API keys. Applies live. */
function LlmConfigControl() {
  const qc = useQueryClient();
  const state = useQuery({
    queryKey: ['admin', 'llm-config'],
    queryFn: () => api.get<LlmConfigState>('/api/admin/llm-config'),
    refetchInterval: 10_000,
  });
  const save = useMutation({
    mutationFn: (patch: Record<string, string>) => api.put<LlmConfigState>('/api/admin/llm-config', patch),
    onSuccess: (data) => { qc.setQueryData(['admin', 'llm-config'], data); setSecrets({}); },
  });
  // Editable drafts for text fields, seeded when data loads; secrets are write-only.
  const [text, setText] = useState<Record<string, string>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [seeded, setSeeded] = useState(false);
  const s = state.data;
  useEffect(() => {
    if (s && !seeded) {
      const t: Record<string, string> = {};
      for (const f of TEXT_FIELDS) t[f.key] = (s.config[f.key] as string | null) ?? '';
      setText(t);
      setSeeded(true);
    }
  }, [s, seeded]);

  const setMode = (mode: string) => save.mutate({ generation_mode: mode });
  const selectedMode = s?.config.generation_mode ?? 'env';
  const modeChoices = [
    { id: 'env', label: 'Env default' }, { id: 'auto', label: 'Auto' },
    { id: 'llm', label: 'LLM only' }, { id: 'mock', label: 'Mock' },
  ];
  const dirtyText = s
    ? TEXT_FIELDS.filter((f) => (text[f.key] ?? '') !== ((s.config[f.key] as string | null) ?? ''))
    : [];
  const dirtySecrets = Object.entries(secrets).filter(([, v]) => v.trim() !== '');
  const canSave = dirtyText.length > 0 || dirtySecrets.length > 0;
  const onSave = () => {
    const patch: Record<string, string> = {};
    for (const f of dirtyText) patch[f.key] = text[f.key] ?? '';
    for (const [k, v] of dirtySecrets) patch[k] = v.trim();
    save.mutate(patch);
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
          LLM configuration <span className="normal-case text-slate-300">(super-admin · applies live, no restart)</span>
        </div>
        {s && (
          <div className="text-[11px] text-slate-500">
            live: <span className="font-semibold text-slate-700">{s.generationMode}</span>{' · '}
            <span className={s.effectiveMock ? 'font-semibold text-amber-600' : 'font-semibold text-emerald-600'}>
              {s.effectiveMock ? 'mock serving' : 'real LLM serving'}
            </span>{' · providers: '}
            <span className="font-mono">{s.activeProviders.length ? s.activeProviders.join(', ') : 'none'}</span>
          </div>
        )}
      </div>

      {/* generation mode — applies immediately on click */}
      <div className="mb-3 inline-flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1">
        {modeChoices.map((c) => (
          <button
            key={c.id} type="button" disabled={save.isPending}
            onClick={() => setMode(c.id)}
            className={`rounded-md px-3 py-1 text-xs font-semibold transition disabled:opacity-50 ${
              selectedMode === c.id ? 'bg-white text-brand-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
            }`}
          >{c.label}</button>
        ))}
      </div>

      {/* debug capture toggle (D-104) — applies live */}
      <label className="mb-3 flex items-start gap-2 text-[11px] text-slate-600">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={s?.config.llm_debug_trace === 'true'}
          disabled={save.isPending}
          onChange={(e) => save.mutate({ llm_debug_trace: e.target.checked ? 'true' : 'false' })}
        />
        <span>
          <span className="font-semibold text-slate-700">Debug: capture request &amp; response</span>{' '}
          <span className="text-slate-400">
            — stores the full prompt + output on every LLM span (expand a trace below to view). Leave off in normal use.
          </span>
        </span>
      </label>

      {/* per-artifact split toggle (D-106) — applies live to the next run */}
      <label className="mb-3 flex items-start gap-2 text-[11px] text-slate-600">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={s?.config.per_artifact_generation === 'true'}
          disabled={save.isPending}
          onChange={(e) => save.mutate({ per_artifact_generation: e.target.checked ? 'true' : 'false' })}
        />
        <span>
          <span className="font-semibold text-slate-700">Per-artifact parallel generation</span>{' '}
          <span className="text-slate-400">
            — generate each stage artifact in its own parallel call instead of one combined call. Recommended for heavy
            stages (e.g. Solution Architecture) that otherwise truncate or time out.
          </span>
        </span>
      </label>

      {/* text config */}
      <div className="grid gap-2 sm:grid-cols-2">
        {TEXT_FIELDS.map((f) => (
          <label key={f.key} className="block">
            <span className="text-[10px] font-semibold uppercase text-slate-400">{f.label}</span>
            <input
              className="mt-0.5 w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-[11px] focus:border-brand-400 focus:outline-none"
              value={text[f.key] ?? ''}
              onChange={(e) => setText((p) => ({ ...p, [f.key]: e.target.value }))}
              placeholder={f.placeholder}
            />
          </label>
        ))}
      </div>

      {/* write-only API keys */}
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        {SECRET_FIELDS.map((f) => {
          const isSet = Boolean(s?.config[f.setKey]);
          return (
            <label key={f.key} className="block">
              <span className="text-[10px] font-semibold uppercase text-slate-400">
                {f.label}{' '}
                <span className={isSet ? 'text-emerald-600' : 'text-slate-300'}>
                  {isSet ? '● set' : 'not set'}
                </span>
              </span>
              <div className="mt-0.5 flex items-center gap-1">
                <input
                  type="password" autoComplete="new-password"
                  className="w-full rounded-md border border-slate-300 px-2 py-1 text-[11px] focus:border-brand-400 focus:outline-none"
                  value={secrets[f.key] ?? ''}
                  onChange={(e) => setSecrets((p) => ({ ...p, [f.key]: e.target.value }))}
                  placeholder={isSet ? '•••••••• (unchanged)' : 'paste key…'}
                />
                {isSet && (
                  <button
                    type="button" title="Remove this key"
                    onClick={() => save.mutate({ [f.key]: '' })}
                    className="shrink-0 rounded px-1.5 py-1 text-[11px] text-slate-400 hover:bg-red-50 hover:text-red-600"
                  >✕</button>
                )}
              </div>
            </label>
          );
        })}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button" disabled={!canSave || save.isPending}
          onClick={onSave}
          className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
        >{save.isPending ? 'Saving…' : 'Save configuration'}</button>
        <span className="text-[10px] text-slate-400">
          Blank field = clears the override (reverts to the deployment default). Keys are write-only and never shown.
          {save.isError && <span className="text-red-600"> Save failed.</span>}
        </span>
      </div>
    </div>
  );
}

/** Live container logs via the read-only docker-socket-proxy (D-92, SSE). */
const LOG_SERVICES = ['orchestrator', 'ai-client', 'tool-connector', 'frontend', 'postgres', 'redis'];
function LogsViewer() {
  const [service, setService] = useState('ai-client');
  const [tail, setTail] = useState(200);
  const [lines, setLines] = useState<string[]>([]);
  const [streaming, setStreaming] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const boxRef = useRef<HTMLPreElement | null>(null);

  const stop = () => { esRef.current?.close(); esRef.current = null; setStreaming(false); };
  const start = () => {
    stop();
    setLines([]);
    const es = new EventSource(`/api/admin/logs?service=${encodeURIComponent(service)}&tail=${tail}`, { withCredentials: true });
    es.onmessage = (e) => setLines((p) => (p.length > 3000 ? [...p.slice(-2500), e.data] : [...p, e.data]));
    es.onerror = () => { setLines((p) => [...p, '[stream ended or unavailable — is DOCKER_PROXY_URL set?]']); stop(); };
    esRef.current = es;
    setStreaming(true);
  };
  useEffect(() => () => stop(), []); // cleanup on unmount
  useEffect(() => { boxRef.current?.scrollTo(0, boxRef.current.scrollHeight); }, [lines]);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Live container logs</div>
        <select value={service} onChange={(e) => setService(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1 text-xs">
          {LOG_SERVICES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={tail} onChange={(e) => setTail(Number(e.target.value))}
          className="rounded border border-slate-300 px-2 py-1 text-xs">
          {[100, 200, 500, 1000].map((n) => <option key={n} value={n}>tail {n}</option>)}
        </select>
        {streaming
          ? <button type="button" onClick={stop} className="rounded-md bg-red-50 px-3 py-1 text-xs font-semibold text-red-600 hover:bg-red-100">Stop</button>
          : <button type="button" onClick={start} className="rounded-md bg-brand-600 px-3 py-1 text-xs font-semibold text-white hover:bg-brand-700">Start</button>}
        {streaming && <span className="animate-pulse rounded bg-emerald-100 px-1 text-[9px] text-emerald-700">live</span>}
      </div>
      <pre ref={boxRef} className="max-h-72 overflow-auto rounded-lg bg-slate-900 p-2 text-[10px] leading-relaxed text-slate-100">
        {lines.length ? lines.join('\n') : 'Pick a service and press Start to tail its logs.'}
      </pre>
    </div>
  );
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
  // D-104: which trace's captured request/response is expanded.
  const [openTrace, setOpenTrace] = useState<string | null>(null);

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
  const traceDetail = useQuery({
    queryKey: ['obs', 'trace', openTrace],
    queryFn: () => api.get<TraceDetail>(`/api/observability/traces/${openTrace}`),
    enabled: Boolean(openTrace),
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
          <LlmConfigControl />
          <LogsViewer />

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
                  {(traces.data?.traces ?? []).map((tr, i) => {
                    const open = openTrace === tr.id;
                    return (
                    <Fragment key={tr.id || i}>
                    <tr
                      className={`border-t border-slate-100 ${tr.hasBodies ? 'cursor-pointer hover:bg-slate-50' : ''}`}
                      onClick={tr.hasBodies ? () => setOpenTrace(open ? null : tr.id) : undefined}
                      title={tr.hasBodies ? 'Show captured request/response' : ''}
                    >
                      <td className="py-1 text-slate-400">
                        {tr.hasBodies && <span className="mr-0.5 text-slate-400">{open ? '▾' : '▸'}</span>}
                        {tr.ts.slice(11, 19)}
                      </td>
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
                    {open && (
                      <tr className="bg-slate-50">
                        <td colSpan={8} className="px-2 py-2">
                          {traceDetail.isLoading && <div className="text-[10px] text-slate-400">Loading captured bodies…</div>}
                          {traceDetail.data && (
                            <div className="space-y-2">
                              <div>
                                <div className="mb-0.5 text-[9px] font-semibold uppercase text-slate-400">Request (prompt)</div>
                                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-200 bg-white p-2 font-mono text-[10px] leading-snug text-slate-700">{traceDetail.data.requestBody ?? '—'}</pre>
                              </div>
                              <div>
                                <div className="mb-0.5 text-[9px] font-semibold uppercase text-slate-400">Response (output)</div>
                                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-200 bg-white p-2 font-mono text-[10px] leading-snug text-slate-700">{traceDetail.data.responseBody ?? '—'}</pre>
                              </div>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                    );
                  })}
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
