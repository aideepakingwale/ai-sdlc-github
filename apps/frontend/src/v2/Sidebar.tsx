import { useEffect, useRef, useState } from 'react';
import type { ProjectFlow } from '../api/flow';
import { COLOR_CLASSES } from '../api/flow';
import type { Project } from '../api/types';
import { Icon, type IconName } from '../components/ui/Icon';
import { StageDot } from './bits';
import { useV2, type ProjectTab } from './store';

export type V2View = 'dashboard' | 'pipeline' | 'stage';
export type V2Modal = 'explorer' | 'context' | 'quality' | 'governance' | 'observability' | 'models' | 'designer';

export const PROJECT_TOOLS: Array<{ tab: ProjectTab; label: string; hint: string; icon: IconName }> = [
  { tab: 'team', label: 'Team', hint: 'Who is on this project and which stage each person covers', icon: 'users' },
  { tab: 'artefacts', label: 'Artefacts', hint: 'Everything the agents generated, by stage', icon: 'box' },
  { tab: 'files', label: 'Files', hint: 'The stored files, exactly as they are on disk', icon: 'folder' },
  { tab: 'codebase', label: 'Codebase', hint: 'Browse an uploaded codebase or the code stage 6 writes', icon: 'code' },
  { tab: 'memory', label: 'Memory', hint: 'What the team has decided and learned, and what you prefer', icon: 'advice' },
  { tab: 'audit', label: 'Audit', hint: 'Every action, who did it and which model ran', icon: 'log-out' },
  { tab: 'skills', label: 'Skills', hint: 'Small helpers you can run on a stage, and connected services', icon: 'zap' },
];

function Item({ active, onClick, children, testid }: { active?: boolean; onClick: () => void; children: React.ReactNode; testid?: string }) {
  return (
    <button
      type="button" onClick={onClick} data-testid={testid} aria-current={active ? 'true' : undefined}
      className={`mx-2 flex w-[calc(100%-1rem)] items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left transition ${active ? 'bg-white shadow-sm ring-1 ring-slate-200' : 'hover:bg-slate-200/60'}`}
    >
      {children}
    </button>
  );
}

const Heading = ({ children }: { children: React.ReactNode }) => (
  <div className="mx-4 mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{children}</div>
);

export default function Sidebar({
  width, projects, activeProjectId, onPickProject, onNewProject, canManage, isAdmin, flow, selectedStage, view, onView, onStage, onModal,
}: {
  /** Pixel width; the workspace owns it so a divider can resize it. */
  width?: number;
  projects: Project[];
  activeProjectId: string | null;
  onPickProject: (id: string | null) => void;
  onNewProject: () => void;
  canManage: boolean;
  flow: ProjectFlow | undefined;
  selectedStage: number | null;
  view: V2View;
  onView: (v: V2View) => void;
  onStage: (seq: number) => void;
  onModal: (m: V2Modal) => void;
  isAdmin?: boolean;
}) {
  const pane = useV2((s) => s.pane);
  const togglePane = useV2((s) => s.togglePane);
  const [pick, setPick] = useState(false);
  const pickRef = useRef<HTMLDivElement>(null);
  // The project list closes on Escape and when you click anywhere else.
  useEffect(() => {
    if (!pick) return;
    const off = (e: MouseEvent) => { if (!pickRef.current?.contains(e.target as Node)) setPick(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setPick(false); };
    document.addEventListener('mousedown', off); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', key); };
  }, [pick]);
  const active = projects.find((p) => p.id === activeProjectId);
  const doneCount = flow?.stages.filter((s) => s.status === 'APPROVED').length ?? 0;

  return (
    <aside style={width ? { width } : undefined} className={`flex ${width ? '' : 'w-72'} max-w-[85vw] shrink-0 flex-col overflow-y-auto bg-slate-100`} aria-label="Projects and stages" data-testid="v2-sidebar">
      <div className="px-4 pb-1 pt-4">
        <div className="font-display text-xl font-bold text-navy">DevMind</div>
      </div>
      <div className="relative mx-3 mt-1" ref={pickRef}>
        <button
          type="button" onClick={() => setPick((v) => !v)} aria-haspopup="listbox" aria-expanded={pick}
          className="flex w-full items-center justify-between rounded-lg border border-slate-300 bg-white px-3 py-2 text-left text-sm font-semibold text-navy"
          data-testid="v2-project-switcher"
        >
          <span className="truncate">{active?.name ?? 'Choose a project'}</span>
          <Icon name="chevron-down" size={14} />
        </button>
        {pick && (
          <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-xl border border-slate-200 bg-white p-1 shadow-lg" role="listbox">
            {projects.map((p) => (
              <button key={p.id} type="button" role="option" aria-selected={p.id === activeProjectId}
                onClick={() => { onPickProject(p.id); setPick(false); }}
                className={`block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100 ${p.id === activeProjectId ? 'bg-brand-50 font-semibold' : ''}`}>
                <div className="truncate">{p.name}</div>
                <div className="text-xs text-slate-500">Stage {p.currentPhase} · {p.status}</div>
              </button>
            ))}
            {projects.length === 0 && <div className="px-3 py-2 text-sm text-slate-500">No projects yet.</div>}
          </div>
        )}
      </div>

      {canManage && (
        <div className="mx-3 mt-2">
          <button type="button" onClick={onNewProject} data-testid="v2-new-project"
            className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-700">
            <span aria-hidden="true" className="text-base leading-none">+</span> New project
          </button>
        </div>
      )}

      <div className="mt-3">
        <Item active={view === 'dashboard' && !activeProjectId} onClick={() => { onPickProject(null); onView('dashboard'); }} testid="v2-nav-all">
          <Icon name="layers" size={16} className="text-slate-500" />
          <span><span className="block text-sm font-medium text-navy">All projects</span><span className="block text-xs text-slate-500">Portfolio dashboard</span></span>
        </Item>
        {canManage && (
          <Item onClick={() => onModal('explorer')} testid="v2-nav-explorer">
            <Icon name="search" size={16} className="text-slate-500" />
            <span><span className="block text-sm font-medium text-navy">Project Explorer</span><span className="block text-xs text-slate-500">Every project’s stages</span></span>
          </Item>
        )}
      </div>

      {activeProjectId && (
        <>
          <Heading>Project</Heading>
          <Item active={view === 'pipeline'} onClick={() => onView('pipeline')} testid="v2-nav-pipeline">
            <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-brand-600 text-white"><Icon name="map" size={12} /></span>
            <span><span className="block text-sm font-medium text-navy">Pipeline</span><span className="block text-xs text-slate-500">{flow ? `${doneCount} of ${flow.stages.length} approved` : 'Loading…'}</span></span>
          </Item>

          <Heading>Stages</Heading>
          {(flow?.stages ?? []).map((s) => (
            <Item key={s.phase} active={view === 'stage' && selectedStage === s.phase} onClick={() => onStage(s.phase)} testid={`v2-stage-${s.phase}`}>
              <StageDot n={s.phase} color={s.color} />
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-navy">{s.name}</span>
                <span className="block text-xs text-slate-500">{COLOR_CLASSES[s.color].label}{s.stale ? ' · outdated' : ''}</span>
              </span>
            </Item>
          ))}

          <Heading>Configure</Heading>
          <Item onClick={() => onModal('context')} testid="v2-nav-context">
            <Icon name="book" size={16} className="text-slate-500" />
            <span><span className="block text-sm font-medium text-navy">Project Context</span><span className="block text-xs text-slate-500">Canon and templates</span></span>
          </Item>
          <Item onClick={() => onModal('quality')} testid="v2-nav-quality">
            <Icon name="chart" size={16} className="text-slate-500" />
            <span><span className="block text-sm font-medium text-navy">Quality</span><span className="block text-xs text-slate-500">Scores and rework</span></span>
          </Item>
          {canManage && (
            <Item onClick={() => onModal('designer')} testid="v2-nav-designer">
              <Icon name="sliders" size={16} className="text-slate-500" />
              <span><span className="block text-sm font-medium text-navy">Workflow designer</span><span className="block text-xs text-slate-500">Stages and reviewers</span></span>
            </Item>
          )}

          <Heading>Project tools</Heading>
          {PROJECT_TOOLS.map((t) => (
            <Item key={t.tab} active={pane?.type === 'project' && pane.tab === t.tab} onClick={() => togglePane({ type: 'project', tab: t.tab })} testid={`v2-tool-${t.tab}`}>
              <Icon name={t.icon} size={16} className="text-brand-600" />
              <span><span className="block text-sm font-medium text-navy">{t.label}</span><span className="block text-xs leading-snug text-slate-500">{t.hint}</span></span>
            </Item>
          ))}
          <Item active={pane?.type === 'context'} onClick={() => selectedStage != null && togglePane({ type: 'context', seq: selectedStage })} testid="v2-nav-stage-context">
            <Icon name="layers" size={16} className="text-brand-600" />
            <span><span className="block text-sm font-medium text-navy">What this stage knows</span><span className="block text-xs leading-snug text-slate-500">The context behind the stage you are on</span></span>
          </Item>
        </>
      )}

      <div className="mt-auto border-t border-slate-200 p-2 pt-2">
        <button type="button" onClick={() => onModal('governance')} className="block w-full rounded-lg px-3 py-1.5 text-left text-sm text-slate-600 hover:bg-slate-200/60" data-testid="v2-nav-governance">Governance</button>
        {isAdmin && <button type="button" onClick={() => onModal("observability")} className="block w-full rounded-lg px-3 py-1.5 text-left text-sm text-slate-600 hover:bg-slate-200/60" data-testid="v2-nav-observability">Observability</button>}
        {isAdmin && <button type="button" onClick={() => onModal('models')} className="block w-full rounded-lg px-3 py-1.5 text-left text-sm text-slate-600 hover:bg-slate-200/60" data-testid="v2-nav-models">Model routes</button>}
      </div>
    </aside>
  );
}
