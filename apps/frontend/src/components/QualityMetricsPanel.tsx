import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { api } from '../api/client';

/**
 * Quality metrics dashboard: validator-score trend, first-pass approval vs.
 * rework rate, issues caught at the gate and stale-propagation churn — all
 * aggregated from the immutable audit trail (real signals, not estimates).
 */
interface QualityMetrics {
  projectId: string;
  coverageTarget: number;
  averageScore: number | null;
  latestScore: number | null;
  belowBarCount: number;
  dimensionsAvg: Record<string, number>;
  scoreTrend: Array<{ phase: number; score: number; timestamp: string | null; belowBar: boolean; issues: number }>;
  perStageScore: Array<{ phase: number; score: number; belowBar: boolean }>;
  gate: {
    approvals: number; amendsRequested: number; pendingReview: number; escalations: number;
    graded: number; firstPassRate: number | null; reworkRate: number | null;
  };
  issuesCaughtAtGate: number;
  staleFlagged: number;
}

function scoreColor(score: number): string {
  if (score >= 80) return 'text-emerald-600';
  if (score >= 70) return 'text-amber-600';
  return 'text-bared-600';
}
function barColor(score: number): string {
  if (score >= 80) return 'bg-emerald-500';
  if (score >= 70) return 'bg-amber-500';
  return 'bg-bared-500';
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-0.5 text-2xl font-bold ${tone ?? 'text-navy'}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-400">{sub}</div>}
    </div>
  );
}

/** Simple inline-SVG sparkline of the score trend, with a 70-line quality bar. */
function Sparkline({ points }: { points: number[] }) {
  if (points.length === 0) return <div className="text-xs text-slate-400">No validator runs yet.</div>;
  const w = 560, h = 120, pad = 8;
  const n = points.length;
  const x = (i: number) => (n === 1 ? w / 2 : pad + (i * (w - 2 * pad)) / (n - 1));
  const y = (v: number) => h - pad - (Math.max(0, Math.min(100, v)) / 100) * (h - 2 * pad);
  const path = points.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const barY = y(70);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" role="img" aria-label="Validator score trend">
      <line x1={pad} y1={barY} x2={w - pad} y2={barY} stroke="currentColor" className="text-amber-400" strokeDasharray="4 4" strokeWidth={1} />
      <text x={w - pad} y={barY - 3} textAnchor="end" className="fill-amber-500 text-[9px]">quality bar 70</text>
      <path d={path} fill="none" stroke="currentColor" className="text-brand-500" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {points.map((v, i) => (
        <circle key={i} cx={x(i)} cy={y(v)} r={2.5} className={v >= 70 ? 'fill-brand-600' : 'fill-bared-500'} />
      ))}
    </svg>
  );
}

function Bar({ label, value, max, suffix, color }: { label: string; value: number; max: number; suffix?: string; color?: string }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="w-28 shrink-0 truncate text-[11px] text-slate-500" title={label}>{label}</div>
      <div className="h-3 flex-1 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${color ?? 'bg-brand-500'}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="w-12 shrink-0 text-right text-[11px] font-semibold text-navy">{value}{suffix ?? ''}</div>
    </div>
  );
}

export default function QualityMetricsPanel({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['quality-metrics', projectId],
    queryFn: () => api.get<QualityMetrics>(`/api/projects/${projectId}/quality-metrics`),
    refetchInterval: 10_000,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const m = q.data;
  const gate = m?.gate;
  const hasData = m && (m.scoreTrend.length > 0 || (gate?.graded ?? 0) > 0);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-navy/40 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="flex max-h-[94vh] w-full max-w-4xl flex-col rounded-2xl bg-slate-50 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
          <div>
            <div className="text-sm font-bold text-navy">📊 Quality Metrics</div>
            <div className="text-xs text-slate-500">Validator-score trend, first-pass vs. rework, and issues caught at the gate — from the audit trail.</div>
          </div>
          <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {q.isLoading ? (
            <div className="py-16 text-center text-sm text-slate-400">Loading metrics…</div>
          ) : !hasData ? (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center">
              <div className="text-3xl">📊</div>
              <div className="mt-2 text-sm font-semibold text-navy">No quality data yet</div>
              <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">
                Metrics appear once stages run and go through gate review — validator scores, approvals and rework are captured automatically.
              </p>
            </div>
          ) : (
            <>
              {/* KPI row */}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Kpi label="Avg validator score" value={m!.averageScore != null ? `${m!.averageScore}` : '—'}
                     sub={m!.latestScore != null ? `latest ${m!.latestScore}/100` : undefined}
                     tone={m!.averageScore != null ? scoreColor(m!.averageScore) : undefined} />
                <Kpi label="First-pass approval" value={gate!.firstPassRate != null ? `${gate!.firstPassRate}%` : '—'}
                     sub={`${gate!.approvals} approved / ${gate!.graded} graded`}
                     tone={gate!.firstPassRate != null && gate!.firstPassRate >= 70 ? 'text-emerald-600' : 'text-amber-600'} />
                <Kpi label="Rework rate" value={gate!.reworkRate != null ? `${gate!.reworkRate}%` : '—'}
                     sub={`${gate!.amendsRequested} amend request(s)`}
                     tone={gate!.reworkRate != null && gate!.reworkRate > 30 ? 'text-bared-600' : 'text-navy'} />
                <Kpi label="Coverage target" value={`${m!.coverageTarget}%`} sub="enforced at code/CI gate" />
              </div>

              {/* Score trend */}
              <section className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="mb-2 flex items-center justify-between">
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Validator score trend</div>
                  <div className="text-[11px] text-slate-400">{m!.scoreTrend.length} run(s) · {m!.belowBarCount} below bar</div>
                </div>
                <Sparkline points={m!.scoreTrend.map((s) => s.score)} />
              </section>

              <div className="grid gap-4 md:grid-cols-2">
                {/* Per-stage latest score */}
                <section className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Latest score by stage</div>
                  {m!.perStageScore.length === 0 ? (
                    <div className="text-xs text-slate-400">No scored stages yet.</div>
                  ) : (
                    <div className="space-y-1.5">
                      {m!.perStageScore.map((s) => (
                        <Bar key={s.phase} label={`Stage ${s.phase}`} value={s.score} max={100} color={barColor(s.score)} />
                      ))}
                    </div>
                  )}
                </section>

                {/* Dimension averages */}
                <section className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Quality dimensions (avg)</div>
                  {Object.keys(m!.dimensionsAvg).length === 0 ? (
                    <div className="text-xs text-slate-400">No dimension data yet.</div>
                  ) : (
                    <div className="space-y-1.5">
                      {Object.entries(m!.dimensionsAvg).map(([k, v]) => (
                        <Bar key={k} label={k} value={v} max={100} color={barColor(v)} />
                      ))}
                    </div>
                  )}
                </section>
              </div>

              {/* Gate outcomes */}
              <section className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Gate outcomes &amp; churn</div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div className="rounded-lg bg-emerald-50 p-2 text-center">
                    <div className="text-lg font-bold text-emerald-700">{gate!.approvals}</div>
                    <div className="text-[10px] text-emerald-700/80">approved</div>
                  </div>
                  <div className="rounded-lg bg-orange-50 p-2 text-center">
                    <div className="text-lg font-bold text-orange-700">{gate!.amendsRequested}</div>
                    <div className="text-[10px] text-orange-700/80">changes requested</div>
                  </div>
                  <div className="rounded-lg bg-bared-200/60 p-2 text-center">
                    <div className="text-lg font-bold text-bared-700">{gate!.escalations}</div>
                    <div className="text-[10px] text-bared-700/80">escalated</div>
                  </div>
                  <div className="rounded-lg bg-slate-100 p-2 text-center">
                    <div className="text-lg font-bold text-navy">{m!.staleFlagged}</div>
                    <div className="text-[10px] text-slate-500">stale-flagged</div>
                  </div>
                </div>
                <div className="mt-2 text-[11px] text-slate-500">
                  {m!.issuesCaughtAtGate} issue(s) caught at the gate before release — the value of the human-in-the-loop review.
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
