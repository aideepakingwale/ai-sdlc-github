import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { ProjectDetail, User } from '../api/types';
import { ROLE_LABELS } from '../api/types';
import HelpPanel from '../components/HelpPanel';
import NotificationBell from '../components/NotificationBell';
import TechStackBar from '../components/TechStackBar';
import { Icon } from '../components/ui/Icon';
import { initials } from './bits';
import { switchUiVersion } from './uiVersion';
import { useV2 } from './store';
import { getThemeChoice, setThemeChoice, type ThemeChoice } from './theme';

function Popover({ label, button, children, align = 'right', testid }: { label: string; button: ReactNode; children: ReactNode; align?: 'left' | 'right'; testid?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', off); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', key); };
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button type="button" aria-label={label} aria-expanded={open} aria-haspopup="true" data-testid={testid} onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-300 hover:text-brand-700">
        {button}
      </button>
      {open && <div className={`absolute top-full z-30 mt-1.5 min-w-[18rem] rounded-xl border border-slate-200 bg-white p-3 shadow-xl ${align === 'right' ? 'right-0' : 'left-0'}`}>{children}</div>}
    </div>
  );
}

export default function GlobalBar({
  user, detail, projectId, projectName, crumb, onGoToStage, onLogout, onDelete, deleting,
}: {
  user: User;
  detail: ProjectDetail | undefined;
  projectId: string | null;
  projectName: string | null;
  crumb?: string;
  onGoToStage: (seq: number) => void;
  onLogout: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const narrow = useV2((s) => s.narrow);
  const drawer = useV2((s) => s.drawer);
  const setDrawer = useV2((s) => s.setDrawer);
  const side = useV2((s) => s.sideCollapsed);
  const setSide = useV2((s) => s.setSideCollapsed);
  const togglePane = useV2((s) => s.togglePane);
  const pane = useV2((s) => s.pane);
  const [help, setHelp] = useState(false);
  const [theme, setTheme] = useState<ThemeChoice>(getThemeChoice);
  const stack = detail?.project.techStack;
  const decided = Boolean(detail?.project.techStackDecided);
  const canSetStack = (detail?.me?.canManageTeam ?? false) || detail?.me?.membershipRole === 'TA';

  return (
    <header className="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-2 max-[899px]:px-3" data-testid="v2-globalbar">
      <button type="button" onClick={() => (narrow ? setDrawer(!drawer) : setSide(!side))} aria-label={(narrow ? !drawer : side) ? 'Show the sidebar' : 'Hide the sidebar'} data-testid="v2-side-toggle" className="rounded-lg border border-slate-200 p-1.5 text-slate-600 hover:bg-slate-100">
        <Icon name="layers" size={15} />
      </button>
      <div className="min-w-0 truncate text-sm font-semibold text-navy" data-testid="v2-crumb">{crumb ?? projectName ?? 'All projects'}</div>
      <div className="ml-auto flex items-center gap-2">
        {projectId && (
          <Popover label="Technology stack" testid="v2-stack" button={<><Icon name="server" size={13} /><span className="max-[899px]:hidden">Stack: {decided && stack ? stack : 'not decided yet'}</span><Icon name="chevron-down" size={12} /></>}>
            <div className="w-80 text-xs text-slate-500">
              <TechStackBar projectId={projectId} techStack={stack} decided={decided} source={detail?.project.techStackSource} canSet={canSetStack} />
            </div>
          </Popover>
        )}
        {projectId && (
          <button type="button" onClick={() => togglePane({ type: 'project', tab: 'team' })} aria-pressed={pane?.type === 'project'} data-testid="v2-open-project-panel"
            className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-semibold ${pane?.type === 'project' ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 bg-white text-slate-600 hover:border-brand-300'}`}>
            <Icon name="folder" size={13} /><span className="max-[899px]:hidden">Project panel</span>
          </button>
        )}
        <button type="button" onClick={() => setHelp(true)} className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-300">
          <Icon name="help" size={13} /><span className="max-[899px]:hidden">Help</span>
        </button>
        {projectId && <NotificationBell projectId={projectId} onGoToStage={onGoToStage} />}
        <Popover label="Account" testid="v2-user" button={<span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-600 text-[10px] font-bold text-white">{initials(user.displayName)}</span>}>
          <div className="w-64">
            <div className="text-sm font-semibold text-navy">{user.displayName}</div>
            <div className="text-xs text-slate-500">{user.email}</div>
            <div className="mt-0.5 text-xs font-semibold text-brand-600">{ROLE_LABELS[user.role]}</div>
            <div className="mt-3 border-t border-slate-100 pt-2" role="radiogroup" aria-label="Theme">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Theme</div>
              <div className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs font-semibold">
                {(['light', 'dark', 'system'] as const).map((t) => (
                  <button key={t} type="button" role="radio" aria-checked={theme === t} data-testid={`v2-theme-${t}`}
                    onClick={() => { setTheme(t); setThemeChoice(t); }}
                    className={`px-3 py-1 capitalize ${theme === t ? 'bg-brand-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-100'}`}>{t}</button>
                ))}
              </div>
            </div>
            <div className="mt-2 space-y-1 border-t border-slate-100 pt-2">
              <button type="button" onClick={() => switchUiVersion('classic')} className="block w-full rounded-lg px-2 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-100" data-testid="v2-back-classic">Switch to the classic workspace</button>
              {projectId && (detail?.me?.canManageTeam ?? false) && (
                <button type="button" onClick={onDelete} disabled={deleting} className="block w-full rounded-lg px-2 py-1.5 text-left text-sm text-bared-600 hover:bg-bared-200/50 disabled:opacity-50">{deleting ? 'Deleting…' : 'Delete this project'}</button>
              )}
              <button type="button" onClick={onLogout} className="block w-full rounded-lg px-2 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-100">Sign out</button>
            </div>
          </div>
        </Popover>
      </div>
      {help && <HelpPanel onClose={() => setHelp(false)} />}
    </header>
  );
}
