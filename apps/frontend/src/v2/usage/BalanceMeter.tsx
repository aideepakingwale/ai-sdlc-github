import { useQuery } from '@tanstack/react-query';
import { api, type TokensEvent } from '../../api/client';
import { balanceLine, isBlocked, orgNote, signedTokens, STATE_LABEL, STATE_TONE, usedShare, type ProjectPool } from '../../lib/pools';
import { Pill } from '../bits';
import { openTokensDialog } from './TokensDialog';

const BAR: Record<string, string> = { green: 'bg-brand-500', slate: 'bg-slate-300', amber: 'bg-amber-500', red: 'bg-red-500' };

/** A project's token balance. `inline` is the slim line beside the composer and MindDesigner; `card` is the larger block on the usage page.
 *  It says nothing at all until an administrator has allocated tokens to the project (or the organisation's pool is the one running low). */
export function useProjectPool(projectId: string | null) {
  return useQuery({
    queryKey: ['project-pool', projectId], enabled: Boolean(projectId), refetchInterval: 60_000, staleTime: 15_000,
    queryFn: () => api.get<ProjectPool>(`/api/projects/${projectId}/token-pool`),
  });
}

/** The refusal a person would get, built from the balance they are looking at (so the dialog is the same wherever it is opened from). */
function blockedEvent(p: ProjectPool): TokensEvent {
  const project = p.state === 'empty' || p.state === 'overdrawn';
  return {
    code: 'TOKENS_EXHAUSTED',
    message: project ? `This project has used all of its tokens (${signedTokens(p.balance ?? 0)} left), so new work can't start.` : "The organisation has used all of its tokens, so new work can't start in any project.",
    details: { pool: project ? 'project' : 'organisation', projectId: p.projectId, canRequest: true },
  };
}

export default function BalanceMeter({ projectId, variant = 'inline' }: { projectId: string; variant?: 'inline' | 'card' }) {
  const q = useProjectPool(projectId);
  const p = q.data;
  if (!p) return null;
  const note = orgNote(p);
  if (!p.metered && !note) return variant === 'card' ? <p className="text-xs text-slate-500" data-testid="v2-balance-none">Tokens are not limited for this project yet; an administrator can allocate some.</p> : null;
  const tone = STATE_TONE[p.state];
  const pct = Math.min(100, Math.round(usedShare(p) * 100));
  if (variant === 'inline') {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600" data-testid="v2-balance" data-state={p.state}>
        {p.metered && (
          <>
            <span className="h-1.5 w-24 shrink-0 rounded-full bg-slate-200" aria-hidden><span className={`block h-1.5 rounded-full ${BAR[tone]}`} style={{ width: `${pct}%` }} /></span>
            <span data-testid="v2-balance-line">{balanceLine(p)}</span>
            {p.state !== 'ok' && <Pill tone={tone}>{STATE_LABEL[p.state]}</Pill>}
          </>
        )}
        {note && <span className="font-medium text-amber-700" data-testid="v2-balance-org">{note}</span>}
        {isBlocked(p) && (
          <>
            <span className="font-medium text-red-700" data-testid="v2-balance-blocked">New work is paused.</span>
            <button type="button" className="text-brand-600 underline" data-testid="v2-balance-request" onClick={() => openTokensDialog(blockedEvent(p))}>Ask for more tokens</button>
          </>
        )}
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-balance" data-state={p.state}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Token balance</div>
        <Pill tone={tone}>{STATE_LABEL[p.state]}</Pill>
      </div>
      {p.metered && (
        <>
          <div className="mt-1 font-display text-3xl font-bold text-navy" data-testid="v2-balance-number">{p.balance! < 0 ? `−${Math.abs(p.balance!).toLocaleString('en-GB')}` : p.balance!.toLocaleString('en-GB')}</div>
          <div className="mt-2 h-2 rounded-full bg-slate-100" aria-hidden><div className={`h-2 rounded-full ${BAR[tone]}`} style={{ width: `${pct}%` }} /></div>
          <p className="mt-2 text-sm text-slate-700" data-testid="v2-balance-line">{balanceLine(p)}</p>
          <p className="text-xs text-slate-500">{p.consumed.toLocaleString('en-GB')} used of {p.allocated.toLocaleString('en-GB')} allocated{p.monthly ? `, topped up by ${p.monthly.toLocaleString('en-GB')} each month` : ''}. Recent use: about {p.burnPerDay.toLocaleString('en-GB')} tokens a day.</p>
        </>
      )}
      {note && <p className="mt-2 text-sm font-medium text-amber-700">{note}</p>}
      {isBlocked(p) && <p className="mt-2 text-sm font-medium text-red-700">New work is paused until tokens are added.</p>}
    </div>
  );
}
