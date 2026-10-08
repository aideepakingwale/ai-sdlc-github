import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { ProjectFlow } from '../api/flow';
import { ROLE_LABELS, type Artefact, type Project, type ProjectDetail } from '../api/types';
import Dashboard from '../components/Dashboard';
import HelpPanel from '../components/HelpPanel';
import { Icon } from '../components/ui/Icon';
import GovernancePanel from '../components/GovernancePanel';
import NewProjectModal from '../components/NewProjectModal';
import NotificationBell from '../components/NotificationBell';
import QualityMetricsPanel from '../components/QualityMetricsPanel';
import ObservabilityPanel from '../components/ObservabilityPanel';
import { useResizableWidth } from '../components/Panel';
import ProjectContextPanel from '../components/ProjectContextPanel';
import PhaseTracker from '../components/PhaseTracker';
import PipelineFlow from '../components/PipelineFlow';
import ProjectExplorer from '../components/ProjectExplorer';
import RightPanel from '../components/RightPanel';
import StageWorkspace from '../components/StageWorkspace';
import { useApp } from '../store';
import { switchUiVersion } from '../v2/uiVersion';

function usePersistedFlag(key: string): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(() => {
    try { return localStorage.getItem(key) === '1'; } catch { return false; }
  });
  return [v, (n: boolean) => {
    setV(n);
    try { localStorage.setItem(key, n ? '1' : '0'); } catch { /* ignore */ }
  }];
}

export default function Workspace() {
  const qc = useQueryClient();
  const { user, setUser, activeProjectId, setActiveProject } = useApp();
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(false);
  // Project whose Workflow Designer should auto-open after creation.
  const [autoDesignerId, setAutoDesignerId] = useState<string | null>(null);
  const [governanceOpen, setGovernanceOpen] = useState(false);
  const [obsOpen, setObsOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [qualityOpen, setQualityOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [focusedPhase, setFocusedPhase] = useState<number | null>(null);
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const rightPanel = useResizableWidth('right', 320);
  const [leftCollapsed, setLeftCollapsed] = usePersistedFlag('sdlc:collapsed:left');
  const [rightCollapsed, setRightCollapsed] = usePersistedFlag('sdlc:collapsed:right');
  const canManage = user?.role === 'PROJECT_MANAGER' || user?.role === 'SUPER_ADMIN';

  // Creation happens in NewProjectModal; on success we focus the new project and
  // auto-open its Workflow Designer so the PM plans phases right after creation.
  function onProjectCreated(id: string) {
    setNewProjectOpen(false);
    void qc.invalidateQueries({ queryKey: ['projects'] });
    setActiveProject(id);
    // Open the pipeline map so the Workflow Designer auto-opens on first creation
    // (the designer lives inside the map view and only auto-opens once it mounts).
    setMapOpen(true);
    setAutoDesignerId(id);
  }

  const projects = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get<{ projects: Project[] }>('/api/projects'),
    refetchInterval: 10_000,
  });

  const detail = useQuery({
    queryKey: ['project', activeProjectId],
    queryFn: () => api.get<ProjectDetail>(`/api/projects/${activeProjectId}`),
    enabled: Boolean(activeProjectId),
    refetchInterval: 5_000,
  });

  const artefacts = useQuery({
    queryKey: ['artefacts', activeProjectId],
    queryFn: () => api.get<{ artefacts: Artefact[] }>(`/api/projects/${activeProjectId}/artefacts`),
    enabled: Boolean(activeProjectId),
  });

  const flow = useQuery({
    queryKey: ['flow', activeProjectId],
    queryFn: () => api.get<ProjectFlow>(`/api/projects/${activeProjectId}/flow`),
    enabled: Boolean(activeProjectId),
    refetchInterval: 5_000,
  });

  // Selecting a project focuses its current stage; keep the selection valid.
  useEffect(() => {
    setSelectedStage(null);
    setFocusedPhase(null);
  }, [activeProjectId]);
  const selectStage = (seq: number) => {
    setSelectedStage(seq);
    setFocusedPhase(seq);
  };

  async function logout() {
    await api.post('/api/auth/logout');
    setUser(null);
    setActiveProject(null);
    qc.clear();
  }

  async function deleteProject() {
    const name = detail.data?.project.name ?? 'this project';
    if (!activeProjectId) return;
    if (!window.confirm(`Permanently delete "${name}" and ALL its data (artifacts, files, history)? This cannot be undone.`)) {
      return;
    }
    setDeleting(true);
    try {
      await api.del(`/api/projects/${activeProjectId}`);
      setActiveProject(null);
      setSelectedStage(null);
      await qc.invalidateQueries({ queryKey: ['projects'] });
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setDeleting(false);
    }
  }

  const phaseStates = detail.data?.phaseStates ?? [];
  const currentPhase = detail.data?.project.currentPhase ?? 1;
  const pendingGate = phaseStates.find((s) => s.status === 'PENDING_REVIEW') ?? null;
  const escalated = phaseStates.find((s) => s.status === 'ESCALATED');
  const amendInFlight = phaseStates.find((s) => s.status === 'AMEND_REQUESTED');

  // The stage shown in the workspace: explicit selection, else a stage needing
  // attention (pending gate / escalation), else the project's current stage.
  const activeStage = selectedStage ?? pendingGate?.phase ?? escalated?.phase ?? currentPhase;

  return (
    <div className="flex h-full">
      {/* ---------- left sidebar ---------- */}
      <aside className={`relative flex shrink-0 flex-col bg-slate-900 text-slate-100 transition-[width] ${leftCollapsed ? 'w-10' : 'w-72'}`}>
        <button
          type="button" onClick={() => setLeftCollapsed(!leftCollapsed)}
          aria-label={leftCollapsed ? 'Expand projects panel' : 'Collapse projects panel'}
          title={leftCollapsed ? 'Show projects & pipeline' : 'Hide projects & pipeline'}
          className={`absolute z-10 rounded p-1 text-slate-400 hover:bg-white/10 hover:text-white ${leftCollapsed ? 'left-1.5 top-3' : 'right-2 top-3'}`}
        >
          <Icon name={leftCollapsed ? 'chevron-right' : 'chevron-left'} size={16} />
        </button>
        {leftCollapsed && (
          <div className="mt-14 flex flex-1 justify-center">
            <span className="select-none text-xs font-semibold tracking-widest text-slate-500 [writing-mode:vertical-rl]">PROJECTS · PIPELINE</span>
          </div>
        )}
        <div className={leftCollapsed ? 'hidden' : 'flex min-h-0 flex-1 flex-col'}>
        <div className="border-b border-white/10 p-4">
          <div className="text-lg font-bold text-white">DevMind</div>
          <div className="text-xs text-slate-400">Developer’s Mind</div>
        </div>

        {canManage && (
          <div className="border-b border-white/10 px-3 pt-3">
            <button
              onClick={() => setExplorerOpen(true)}
              className="w-full rounded-lg border border-white/15 bg-white/5 py-1.5 text-xs font-semibold text-slate-200 hover:bg-white/10"
            >
              🗂 Project Explorer
            </button>
          </div>
        )}

        <div className="flex gap-1.5 border-b border-white/10 px-3 py-2">
          <button
            onClick={() => setGovernanceOpen(true)}
            className="flex-1 rounded-lg border border-white/15 bg-white/5 py-1.5 text-xs font-semibold text-slate-200 hover:bg-white/10"
            title="Guardrail rules and the full prompt library (Responsible AI transparency)"
          >
            🛡 Governance
          </button>
          {user?.role === 'SUPER_ADMIN' && (
            <button
              onClick={() => setObsOpen(true)}
              className="flex-1 rounded-lg border border-white/15 bg-white/5 py-1.5 text-xs font-semibold text-slate-200 hover:bg-white/10"
              title="AI usage & execution monitoring: calls, tokens, latency, cost, live traces"
            >
              📈 Observability
            </button>
          )}
        </div>

        <div className="border-b border-white/10 p-3">
          {canManage ? (
            <button
              onClick={() => setNewProjectOpen(true)}
              className="w-full rounded-lg bg-brand-600 py-2 text-sm font-semibold text-white hover:bg-brand-700"
            >
              + New project
            </button>
          ) : (
            <div className="rounded-lg bg-white/5 px-2.5 py-2 text-[11px] text-slate-400">
              Projects are created by a Project Manager, who assigns you to a team.
            </div>
          )}
          <div className="mt-3 max-h-44 space-y-1 overflow-y-auto">
            {(projects.data?.projects ?? []).map((p) => (
              <button
                key={p.id}
                onClick={() => setActiveProject(p.id)}
                className={`w-full rounded-lg px-2.5 py-2 text-left text-xs transition ${
                  p.id === activeProjectId ? 'bg-white/15 text-white' : 'text-slate-300 hover:bg-white/5'
                }`}
              >
                <div className="truncate font-medium">{p.name}</div>
                <div className="text-[10px] text-slate-400">
                  Phase {p.currentPhase} · {p.status}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Pipeline phases
          </div>
          {activeProjectId && phaseStates.length > 0 ? (
            <PhaseTracker states={phaseStates} currentPhase={currentPhase} selected={activeStage} onSelect={selectStage} />
          ) : (
            <div className="rounded-lg bg-white/5 p-3 text-xs text-slate-400">
              Start a conversation to launch Phase 1 (Product Owner agent).
            </div>
          )}

          {activeProjectId && (detail.data?.contextWindow.length ?? 0) > 0 && (
            <>
              <div className="mb-2 mt-4 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Context window ({detail.data?.contextWindow.length})
              </div>
              <div className="space-y-1">
                {detail.data?.contextWindow.slice(-12).map((c, i) => (
                  <div key={i} className="truncate rounded bg-white/5 px-2 py-1 text-[11px] text-slate-300" title={c.title}>
                    <span className="text-slate-500">P{c.phase}</span> {c.type} · {c.title}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="border-t border-white/10 p-3">
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-white">{user?.displayName}</div>
              <div className="text-[11px] text-slate-400">
                {user?.email} ·{' '}
                <span className="font-semibold text-brand-100">{user ? ROLE_LABELS[user.role] : ''}</span>
              </div>
            </div>
            <button onClick={logout} className="rounded px-2 py-1 text-xs text-slate-400 hover:bg-white/10 hover:text-white">
              Sign out
            </button>
          </div>
        </div>
        </div>
      </aside>

      {/* ---------- main chat column ---------- */}
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-800">
              {detail.data?.project.name ?? 'New project'}
            </div>
            <div className="text-xs text-slate-500">
              {activeProjectId
                ? `Phase ${currentPhase}${phaseStates.length ? `/${phaseStates.length}` : ''} · ${phaseStates.find((s) => s.phase === currentPhase)?.name ?? ''}` +
                  (detail.data?.project.techStack ? ` · ${detail.data.project.techStack}` : '')
                : 'Describe requirements to begin'}
            </div>
          </div>
          {escalated && (
            <span className="rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-700">
              <Icon name="warning" size={13} className="mr-1 inline" />Escalated to human developer
            </span>
          )}
          {amendInFlight && !escalated && (
            <span className="rounded-full bg-orange-100 px-3 py-1 text-xs font-semibold text-orange-700">
              <Icon name="refresh" size={13} spin className="mr-1 inline" />Regenerating with reviewer feedback…
            </span>
          )}
          <div className="ml-3 flex shrink-0 items-center gap-2">
          <button
            onClick={() => switchUiVersion('v2')} data-testid="try-v2"
            className="rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 hover:border-brand-400"
            title="Preview the redesigned workspace"
          >
            Try the new workspace (beta)
          </button>
          <button
            onClick={() => setHelpOpen(true)}
            className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-300 hover:text-brand-700"
            title="How this works, and what the colours mean"
          >
            <Icon name="help" size={13} className="mr-1 inline" />Help
          </button>
          </div>
          {activeProjectId && (
            <div className="ml-3 flex shrink-0 items-center gap-2">
              <NotificationBell projectId={activeProjectId} onGoToStage={selectStage} />
              <button
                onClick={() => setMapOpen((v) => !v)}
                className={`rounded-lg border px-2.5 py-1 text-xs font-semibold ${
                  mapOpen ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-600 hover:border-brand-300 hover:text-brand-700'
                }`}
                title="Show the full pipeline map (parallel groups, dependencies) and the workflow designer"
              >
                <Icon name="map" size={13} className="mr-1 inline" />Pipeline map
              </button>
              <button
                onClick={() => setContextOpen(true)}
                className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-300 hover:text-brand-700"
                title="Canon (binding project rules) and Formwork (output templates) fed to every agent"
              >
                <Icon name="book" size={13} className="mr-1 inline" />Project Context
              </button>
              <button
                onClick={() => setQualityOpen(true)}
                className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-300 hover:text-brand-700"
                title="Quality metrics — validator-score trend, first-pass vs. rework rate, issues caught at the gate"
              >
                <Icon name="chart" size={13} className="mr-1 inline" />Quality
              </button>
              {detail.data?.me?.canManageTeam && (
                <button
                  onClick={deleteProject}
                  disabled={deleting}
                  className="rounded-lg border border-red-200 px-2.5 py-1 text-xs font-semibold text-red-600 hover:border-red-400 hover:bg-red-50 disabled:opacity-40"
                  title="Permanently delete this project and all its data"
                >
                  <Icon name="trash" size={13} className="mr-1 inline" />{deleting ? 'Deleting…' : 'Delete'}
                </button>
              )}
            </div>
          )}
        </header>
        {helpOpen && <HelpPanel onClose={() => setHelpOpen(false)} />}

        {detail.isError && activeProjectId && (
          <div className="border-b border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
            ⚠ Could not load this project:{' '}
            {detail.error instanceof Error ? detail.error.message : 'request failed'} —{' '}
            <button className="underline" onClick={() => void detail.refetch()}>
              retry
            </button>
          </div>
        )}

        {/* Optional pipeline map: the horizontal DAG with parallel groups
            and the workflow designer; the Pipeline Rail is the primary nav. */}
        {activeProjectId && mapOpen && (
          <div className="border-b border-slate-200">
            <PipelineFlow
              projectId={activeProjectId}
              focusedPhase={activeStage}
              onFocusPhase={(p) => { if (p != null) selectStage(p); }}
              autoOpenDesigner={autoDesignerId === activeProjectId}
              onDesignerAutoOpened={() => setAutoDesignerId(null)}
            />
          </div>
        )}

        {/* Stage-centric workspace: the primary interaction surface. */}
        <div className="min-h-0 flex-1">
          {activeProjectId && flow.data && user ? (
            <StageWorkspace
              key={activeProjectId}              // a different project is a different workspace: nothing typed or selected carries over
              projectId={activeProjectId}
              flow={flow.data}
              selectedSeq={activeStage}
              onSelectStage={selectStage}
              user={user}
              messages={detail.data?.messages ?? []}
              pendingGate={pendingGate}
              artefacts={(artefacts.data?.artefacts ?? []).map((a) => ({
                id: a.id, phase: a.phase, type: a.type, title: a.title, url: a.url,
              }))}
            />
          ) : activeProjectId ? (
            <div className="mx-auto mt-24 max-w-md px-6 text-center">
              <div className="text-4xl">🚀</div>
              <div className="mt-3 text-lg font-semibold text-slate-700">Loading the pipeline…</div>
              <div className="mt-1 text-sm text-slate-500">Fetching stages and their status.</div>
            </div>
          ) : (
            <Dashboard
              projects={projects.data?.projects ?? []}
              onOpen={setActiveProject}
              onNewProject={() => setNewProjectOpen(true)}
              canManage={canManage}
              loading={projects.isLoading}
            />
          )}
        </div>
      </main>

      {/* ---------- right panel (drag its left edge to resize) ---------- */}
      {!rightCollapsed && (
        <div
          onPointerDown={(e) => rightPanel.onPointerDown(e, 'left')}
          onDoubleClick={() => rightPanel.setWidth(320)}
          className="group w-1.5 shrink-0 cursor-col-resize bg-slate-100 hover:bg-brand-200"
          title="Drag to resize · double-click to reset"
        >
          <div className="mx-auto h-8 w-0.5 translate-y-1/2 rounded bg-slate-300 group-hover:bg-brand-400" />
        </div>
      )}
      <aside
        className="flex shrink-0 flex-col border-l border-slate-200 bg-slate-50 transition-[width]"
        style={{ width: rightCollapsed ? 40 : rightPanel.width }}
      >
        <div className={`flex h-7 shrink-0 items-center border-b border-slate-200 ${rightCollapsed ? 'justify-center' : 'justify-end px-1.5'}`}>
          <button
            type="button" onClick={() => setRightCollapsed(!rightCollapsed)}
            aria-label={rightCollapsed ? 'Expand details panel' : 'Collapse details panel'}
            title={rightCollapsed ? 'Show team, artifacts, files & audit' : 'Hide this panel'}
            className="rounded p-0.5 text-slate-500 hover:bg-slate-200"
          >
            <Icon name={rightCollapsed ? 'chevron-left' : 'chevron-right'} size={16} />
          </button>
        </div>
        {rightCollapsed && (
          <div className="mt-6 flex justify-center">
            <span className="select-none text-xs font-semibold tracking-widest text-slate-400 [writing-mode:vertical-rl]">ARTIFACTS · TEAM · FILES</span>
          </div>
        )}
        <div className={rightCollapsed ? 'hidden' : 'min-h-0 flex-1'}>
          <RightPanel
            projectId={activeProjectId}
            canManageTeam={detail.data?.me?.canManageTeam ?? false}
            focusedPhase={focusedPhase}
          />
        </div>
      </aside>

      {explorerOpen && (
        <ProjectExplorer
          onClose={() => setExplorerOpen(false)}
          onOpen={(id) => {
            setActiveProject(id);
            setExplorerOpen(false);
          }}
        />
      )}
      {governanceOpen && <GovernancePanel onClose={() => setGovernanceOpen(false)} />}
      {obsOpen && <ObservabilityPanel onClose={() => setObsOpen(false)} />}
      {contextOpen && activeProjectId && (
        <ProjectContextPanel
          projectId={activeProjectId}
          techStack={detail.data?.project.techStack}
          techStackDecided={detail.data?.project.techStackDecided}
          techStackSource={detail.data?.project.techStackSource}
          canSetStack={(detail.data?.me?.canManageTeam ?? false) || detail.data?.me?.membershipRole === 'TA'}
          onClose={() => setContextOpen(false)}
        />
      )}
      {newProjectOpen && (
        <NewProjectModal onClose={() => setNewProjectOpen(false)} onCreated={onProjectCreated} />
      )}
      {qualityOpen && activeProjectId && (
        <QualityMetricsPanel projectId={activeProjectId} onClose={() => setQualityOpen(false)} />
      )}
    </div>
  );
}
