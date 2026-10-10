import { useEffect, useState } from 'react';
import { Pill } from '../bits';
import type { AuditReport, Finding, VersionView } from '../../lib/agents';

const STEPS = ['Checking the definition, variables and limits', 'Reading it for capability and ambiguity', 'Looking for contradictions with the rules', 'Probing it with five attacks', 'Writing the report'];
const AREA_HELP: Record<string, string> = {
  Capability: 'Can it do what it says with what it is given?', Ambiguity: 'Are the terms and the success criteria clear?',
  Contradiction: 'Do its instructions agree with each other and with the rules?', Security: 'Does it keep its rules when its input tells it not to?',
};

/** The Audit tab: run the audit, read what it found, apply a suggested fix, accept the warnings. */
export default function AuditPanel({ version, running, canEdit, onRun, onFix, onAck, onShow, fixing }: {
  version: VersionView | null; running: boolean; canEdit: boolean; fixing: boolean;
  onRun: () => void; onFix: (id: string) => void; onAck: (ack: boolean) => void; onShow: (snippet: string) => void;
}) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (!running) { setStep(0); return; }
    const t = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 1800);
    return () => clearInterval(t);
  }, [running]);
  const report: AuditReport | null | undefined = version?.auditReport;
  if (running) {
    return (
      <div data-testid="v2-audit-running" role="status">
        <div className="mb-2 text-sm font-semibold text-navy">Auditing…</div>
        <ul className="space-y-1.5">{STEPS.map((s, i) => <li key={s} className="flex items-center gap-2 text-xs"><span className={`h-2 w-2 rounded-full ${i < step ? 'bg-emerald-500' : i === step ? 'animate-pulse bg-amber-500' : 'bg-slate-300'}`} /><span className={i <= step ? 'text-slate-700' : 'text-slate-400'}>{s}</span></li>)}</ul>
      </div>
    );
  }
  if (!report) {
    return (
      <div data-testid="v2-audit-none">
        <p className="text-sm font-medium text-slate-800">Not audited yet.</p>
        <p className="mb-3 mt-1 text-xs text-slate-500">The audit runs automatic checks, has a model read the definition for capability, ambiguity, contradiction and security problems, and probes it with five adversarial runs. It takes under a minute.</p>
        <button type="button" disabled={!canEdit} onClick={onRun} data-testid="v2-audit-run" className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">Run audit</button>
      </div>
    );
  }
  const { block, warn } = report.summary;
  return (
    <div data-testid="v2-audit-report">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <Pill tone={block ? 'red' : 'green'}>{block} blocking</Pill><Pill tone={warn ? 'amber' : 'green'}>{warn} warning{warn === 1 ? '' : 's'}</Pill>
        {report.stale && <Pill tone="amber">Out of date</Pill>}
        <button type="button" disabled={!canEdit} onClick={onRun} data-testid="v2-audit-rerun" className="ml-auto rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50">Run again</button>
      </div>
      {report.notes.map((n) => <div key={n} className="mb-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">{n}</div>)}
      {report.probes.length > 0 && (
        <details className="mb-2 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
          <summary className="cursor-pointer font-medium">{report.probes.filter((p) => p.held).length} of {report.probes.length} adversarial probes held</summary>
          <ul className="mt-1 space-y-0.5">{report.probes.map((p) => <li key={p.id}>{p.held ? 'Held' : 'Failed'}: {p.title}</li>)}</ul>
        </details>
      )}
      <ul className="space-y-2">{report.findings.map((f) => <FindingCard key={f.id} f={f} canEdit={canEdit} fixing={fixing} onFix={onFix} onShow={onShow} />)}</ul>
      {warn > 0 && block === 0 && !report.stale && (
        <label className="mt-3 flex items-center gap-2 text-xs text-slate-700">
          <input type="checkbox" data-testid="v2-audit-ack" checked={report.ack} disabled={!canEdit} onChange={(e) => onAck(e.target.checked)} /> I have read the warnings and accept them
        </label>
      )}
      {block === 0 && !report.stale && (warn === 0 || report.ack) && <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800" data-testid="v2-audit-ready">Ready. Submit it for approval from the top right.</div>}
    </div>
  );
}

function FindingCard({ f, canEdit, fixing, onFix, onShow }: { f: Finding; canEdit: boolean; fixing: boolean; onFix: (id: string) => void; onShow: (s: string) => void }) {
  const tone = f.severity === 'block' ? 'border-l-bared-600' : f.severity === 'warn' ? 'border-l-amber-500' : 'border-l-emerald-500';
  return (
    <li className={`rounded-lg border border-slate-200 border-l-4 bg-white p-2.5 text-xs ${tone}`} data-testid={`v2-finding-${f.severity}`}>
      <div className="mb-0.5 flex items-center justify-between gap-2">
        <span className="font-semibold text-slate-700" title={AREA_HELP[f.area]}>{f.area}</span>
        <Pill tone={f.severity === 'block' ? 'red' : f.severity === 'warn' ? 'amber' : 'green'}>{f.severity === 'block' ? 'Blocks submission' : f.severity === 'warn' ? 'Warns' : 'Passed'}</Pill>
      </div>
      <div className="text-slate-900">{f.title}</div>
      <div className="mt-0.5 text-slate-500">{f.detail}</div>
      {f.snippet && (
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <button type="button" className="font-semibold text-brand-700 underline underline-offset-2" onClick={() => onShow(f.snippet)}>Show in prompt</button>
          {f.fix && canEdit && <button type="button" disabled={fixing} data-testid="v2-finding-fix" onClick={() => onFix(f.id)} className="rounded-lg border border-slate-300 px-2 py-0.5 font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50">{f.fix.label}</button>}
        </div>
      )}
    </li>
  );
}
