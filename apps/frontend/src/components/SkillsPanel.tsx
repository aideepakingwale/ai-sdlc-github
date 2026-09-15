import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../api/client';

interface Skill {
  id: string;
  name: string;
  description: string;
  phase: number | null;
  tier: 'non_llm' | 'local' | 'frontier';
  roles: string[];
  inputHint: string;
  needsInput: boolean;
  canRun: boolean;
}

const TIER_BADGE: Record<Skill['tier'], { icon: string; label: string; cls: string }> = {
  non_llm: { icon: '⚙️', label: 'non-LLM', cls: 'bg-slate-100 text-slate-600' },
  local: { icon: '🖥️', label: 'local', cls: 'bg-indigo-100 text-indigo-700' },
  frontier: { icon: '☁️', label: 'frontier', cls: 'bg-emerald-100 text-emerald-700' },
};

/**
 * Stage- and role-scoped skills. Shows only the skills the current user
 * may run at the focused stage; each carries its model-tier badge. Runs the
 * skill and shows the result inline.
 */
export default function SkillsPanel({ projectId, focusedPhase }: { projectId: string; focusedPhase: number | null }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [result, setResult] = useState<{ name: string; tier: string; output: string; meta?: Record<string, unknown> } | null>(null);

  const skills = useQuery({
    queryKey: ['skills', projectId, focusedPhase],
    queryFn: () =>
      api.get<{ currentPhase: number; skills: Skill[] }>(
        `/api/projects/${projectId}/skills${focusedPhase ? `?phase=${focusedPhase}` : ''}`,
      ),
  });

  const run = useMutation({
    mutationFn: (skillId: string) =>
      api.post<{ name: string; tier: string; output: string; meta?: Record<string, unknown> }>(
        `/api/projects/${projectId}/skills/${skillId}/execute`,
        { input },
      ),
    onSuccess: (res) => {
      setResult(res);
      setOpenId(null);
      setInput('');
    },
  });

  const list = skills.data?.skills ?? [];

  return (
    <div className="bg-slate-50 px-4 py-2">
      {/* Title + stage live in the Panel chrome; keep the tier legend. */}
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <span className="text-[10px] text-slate-400">tier: ⚙️ non-LLM · 🖥️ local · ☁️ frontier</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {list.length === 0 && <span className="text-xs text-slate-400">No skills for this stage.</span>}
        {list.map((s) => {
          const badge = TIER_BADGE[s.tier];
          return (
            <div key={s.id} className="relative">
              <button
                disabled={!s.canRun}
                onClick={() => {
                  setResult(null);
                  if (!s.needsInput) run.mutate(s.id);
                  else setOpenId(openId === s.id ? null : s.id);
                }}
                title={s.canRun ? s.description : `Requires role: ${s.roles.join(' / ')}`}
                className={`flex items-center gap-1 rounded-lg border px-2 py-1 text-xs font-medium transition ${
                  s.canRun
                    ? 'border-slate-200 bg-white text-slate-700 hover:border-brand-300 hover:shadow-sm'
                    : 'cursor-not-allowed border-slate-100 bg-slate-100 text-slate-400'
                }`}
              >
                <span className={`rounded px-1 py-0.5 text-[9px] font-bold ${badge.cls}`}>{badge.icon}</span>
                {s.name}
                {!s.canRun && <span className="text-[9px]">🔒</span>}
              </button>

              {openId === s.id && s.canRun && (
                <div className="absolute left-0 top-full z-20 mt-1 w-72 rounded-lg border border-slate-200 bg-white p-2 shadow-lg">
                  <div className="mb-1 text-[11px] text-slate-500">{s.inputHint || 'Input'}</div>
                  <textarea
                    autoFocus
                    className="w-full rounded border border-slate-300 p-1.5 text-xs focus:border-brand-500 focus:outline-none"
                    rows={3}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder={s.inputHint}
                  />
                  <div className="mt-1 flex justify-end gap-1">
                    <button onClick={() => setOpenId(null)} className="rounded px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-100">
                      Cancel
                    </button>
                    <button
                      onClick={() => run.mutate(s.id)}
                      disabled={run.isPending}
                      className="rounded bg-brand-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
                    >
                      {run.isPending ? 'Running…' : `Run (${badge.label})`}
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {run.isPending && <div className="mt-1 text-[11px] text-slate-500">Executing skill…</div>}
      {run.isError && (
        <div className="mt-1 rounded bg-red-50 px-2 py-1 text-[11px] text-red-700">
          {run.error instanceof Error ? run.error.message : 'Skill failed'}
        </div>
      )}
      {result && (
        <div className="mt-1.5 rounded-lg border border-slate-200 bg-white p-2">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-700">{result.name}</span>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
              {TIER_BADGE[result.tier as Skill['tier']]?.icon} {result.tier}
              {result.meta?.provider ? ` · ${result.meta.provider}` : ''}
            </span>
          </div>
          <div className="prose-chat max-h-56 overflow-y-auto text-xs">
            <ReactMarkdown>{result.output}</ReactMarkdown>
          </div>
          <button onClick={() => setResult(null)} className="mt-1 text-[10px] text-slate-400 hover:text-slate-600">
            dismiss
          </button>
        </div>
      )}
    </div>
  );
}
