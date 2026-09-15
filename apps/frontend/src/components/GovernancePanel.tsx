import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';

/**
 * Responsible AI governance panel: read-only transparency over the
 * platform's guardrail rules (what is blocked/masked, where) and the complete
 * prompt library (every prompt any agent or skill sends to an LLM).
 */

interface GuardrailInventory {
  version: number;
  enforcementPoints: { input: string[]; output: string[] };
  rules: Array<{
    name: string;
    category: 'pii' | 'injection' | 'secret';
    action: 'block' | 'mask';
    description: string;
    pattern: string;
    appliedTo: 'input' | 'output';
  }>;
}

interface PromptInventory {
  count: number;
  prompts: Array<{ id: string; version: number; description: string; template: string }>;
}

interface SkillInventory {
  count: number;
  skills: Array<{
    id: string; name: string; description: string; file: string;
    phase: number | null; roles: string[]; tier: string; executor: string;
    tools: string[]; markdown: string;
  }>;
}

const CATEGORY_CHIP: Record<string, string> = {
  pii: 'bg-purple-100 text-purple-700',
  injection: 'bg-red-100 text-red-700',
  secret: 'bg-amber-100 text-amber-700',
};

export default function GovernancePanel({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'guardrails' | 'prompts' | 'skills'>('guardrails');
  const [openPrompt, setOpenPrompt] = useState<string | null>(null);

  const guardrails = useQuery({
    queryKey: ['governance', 'guardrails'],
    queryFn: () => api.get<GuardrailInventory>('/api/governance/guardrails'),
  });
  const prompts = useQuery({
    queryKey: ['governance', 'prompts'],
    queryFn: () => api.get<PromptInventory>('/api/governance/prompts'),
  });
  const skills = useQuery({
    queryKey: ['governance', 'skills'],
    queryFn: () => api.get<SkillInventory>('/api/governance/skills'),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="flex max-h-[92vh] w-full max-w-4xl flex-col rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <div>
            <div className="text-sm font-bold text-slate-800">🛡 Responsible AI Governance</div>
            <div className="text-xs text-slate-500">
              Deterministic guardrails and the full prompt library — auditable, versioned, read-only
            </div>
          </div>
          <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
        </div>

        <div className="flex gap-1 border-b border-slate-200 px-5 py-2">
          <button
            onClick={() => setTab('guardrails')}
            className={`rounded-lg px-3 py-1 text-xs font-semibold ${
              tab === 'guardrails' ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500'
            }`}
          >
            Guardrails {guardrails.data ? `(${guardrails.data.rules.length})` : ''}
          </button>
          <button
            onClick={() => setTab('prompts')}
            className={`rounded-lg px-3 py-1 text-xs font-semibold ${
              tab === 'prompts' ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500'
            }`}
          >
            Prompt library {prompts.data ? `(${prompts.data.count})` : ''}
          </button>
          <button
            onClick={() => setTab('skills')}
            className={`rounded-lg px-3 py-1 text-xs font-semibold ${
              tab === 'skills' ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500'
            }`}
          >
            Skill packs {skills.data ? `(${skills.data.count})` : ''}
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {tab === 'guardrails' && guardrails.data && (
            <div className="space-y-3">
              <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                <b>Enforcement points</b> · input (block):{' '}
                {guardrails.data.enforcementPoints.input.join(' · ')} — output (mask):{' '}
                {guardrails.data.enforcementPoints.output.join(' · ')}. Every trigger is written to
                the audit trail.
              </div>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wide text-slate-400">
                    <th className="py-1 pr-2">Rule</th>
                    <th className="py-1 pr-2">Category</th>
                    <th className="py-1 pr-2">Action</th>
                    <th className="py-1 pr-2">Applies to</th>
                    <th className="py-1">Description</th>
                  </tr>
                </thead>
                <tbody>
                  {guardrails.data.rules.map((r, i) => (
                    <tr key={`${r.name}-${r.appliedTo}-${i}`} className="border-t border-slate-100">
                      <td className="py-1.5 pr-2 font-mono text-[11px] text-slate-700">{r.name}</td>
                      <td className="py-1.5 pr-2">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${CATEGORY_CHIP[r.category]}`}>
                          {r.category}
                        </span>
                      </td>
                      <td className="py-1.5 pr-2">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          r.action === 'block' ? 'bg-red-50 text-red-600' : 'bg-emerald-50 text-emerald-600'
                        }`}>
                          {r.action}
                        </span>
                      </td>
                      <td className="py-1.5 pr-2 text-slate-500">{r.appliedTo}</td>
                      <td className="py-1.5 text-slate-600">{r.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {tab === 'prompts' && prompts.data && (
            <div className="space-y-1.5">
              <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                Every prompt the platform sends to an LLM lives in this library — phase agents,
                pipeline nodes, skills and the binding Responsible AI policy preamble. No inline
                prompts exist outside it.
              </div>
              {prompts.data.prompts.map((p) => (
                <div key={p.id} className="rounded-lg border border-slate-200">
                  <button
                    onClick={() => setOpenPrompt(openPrompt === p.id ? null : p.id)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left"
                  >
                    <span className="font-mono text-[11px] font-semibold text-brand-700">{p.id}</span>
                    <span className="rounded bg-slate-100 px-1 text-[9px] text-slate-500">v{p.version}</span>
                    <span className="min-w-0 flex-1 truncate text-[11px] text-slate-500">{p.description}</span>
                    <span className="text-slate-300">{openPrompt === p.id ? '▾' : '▸'}</span>
                  </button>
                  {openPrompt === p.id && (
                    <pre className="overflow-x-auto rounded-b-lg bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">
                      {p.template}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}

          {tab === 'skills' && skills.data && (
            <div className="space-y-1.5">
              <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                Every skill is a <b>markdown file</b> (skills/*.md): frontmatter declares identity,
                RBAC roles, tier and execution wiring — validated fail-fast at boot — and the body is
                the skill's instruction. Execution enforces the declared roles plus stage gating;
                PMs never execute skills.
              </div>
              {skills.data.skills.map((s) => (
                <div key={s.id} className="rounded-lg border border-slate-200">
                  <button
                    onClick={() => setOpenPrompt(openPrompt === s.id ? null : s.id)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left"
                  >
                    <span className="font-mono text-[11px] font-semibold text-brand-700">{s.file}</span>
                    <span className="rounded bg-slate-100 px-1 text-[9px] text-slate-500">
                      {s.phase ? `P${s.phase}` : 'any'} · {s.tier} · {s.executor}
                    </span>
                    <span className="flex gap-0.5">
                      {s.roles.map((r) => (
                        <span key={r} className="rounded bg-brand-50 px-1 text-[9px] font-semibold text-brand-700">{r}</span>
                      ))}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[11px] text-slate-500">{s.description}</span>
                    <span className="text-slate-300">{openPrompt === s.id ? '▾' : '▸'}</span>
                  </button>
                  {openPrompt === s.id && (
                    <pre className="overflow-x-auto rounded-b-lg bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">
                      {s.markdown}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}

          {(guardrails.isLoading || prompts.isLoading || skills.isLoading) && (
            <div className="animate-pulse text-sm text-slate-400">Loading governance data…</div>
          )}
        </div>
      </div>
    </div>
  );
}
