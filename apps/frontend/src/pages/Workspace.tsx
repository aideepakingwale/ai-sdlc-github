import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { ProjectFlow } from '../api/flow';
import { ROLE_LABELS, TECH_STACKS, type Artefact, type Project, type ProjectDetail } from '../api/types';
import GovernancePanel from '../components/GovernancePanel';
import NotificationBell from '../components/NotificationBell';
import ObservabilityPanel from '../components/ObservabilityPanel';
import { useResizableWidth } from '../components/Panel';
import ProjectContextPanel from '../components/ProjectContextPanel';
import PhaseTracker from '../components/PhaseTracker';
import PipelineFlow from '../components/PipelineFlow';
import ProjectExplorer from '../components/ProjectExplorer';
import RightPanel from '../components/RightPanel';
import StageWorkspace from '../components/StageWorkspace';
import { useApp } from '../store';

export default function Workspace() {
  const qc = useQueryClient();
  const { user, setUser, activeProjectId, setActiveProject } = useApp();
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectStack, setNewProjectStack] = useState<string>(TECH_STACKS[0]);
  // Per-project GitHub/Atlassian targets asked at creation.
  const [newIntegrations, setNewIntegrations] = useState({
    githubRepo: '', atlassianSiteUrl: '', jiraProjectKey: '', confluenceSpaceKey: '',
  });
  const [explorerOpen, setExplorerOpen] = useState(false);
  // Project whose Workflow Designer should auto-open after creation.
  const [autoDesignerId, setAutoDesignerId] = useState<string | null>(null);
  const [governanceOpen, setGovernanceOpen] = useState(false);
  const [obsOpen, setObsOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [focusedPhase, setFocusedPhase] = useState<number | null>(null);
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const rightPanel = useResizableWidth('right', 320);
  const canManage = user?.role === 'PROJECT_MANAGER' || user?.role === 'SUPER_ADMIN';

  // Explicit creation via POST /api/projects: the PM names the project, then
  // staffs the team — no chat message required to bring it into existence.
  const createProject = useMutation({
    mutationFn: (name: string) => {
      // send only the targets the PM actually filled in
      const integrations = Object.fromEntries(
        Object.entries(newIntegrations).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]),
      );
      return api.post<{ project: Project }>('/api/projects', { name, techStack: newProjectStack, integrations });
    },
    onSuccess: (res) => {
      setNewProjectOpen(false);
      setNewProjectName('');
      setNewIntegrations({ githubRepo: '', atlassianSiteUrl: '', jiraProjectKey: '', confluenceSpaceKey: '' });
      void qc.invalidateQueries({ queryKey: ['projects'] });
      setActiveProject(res.project.id);
      // Open the Workflow Designer on the new project so the PM plans phases
      // (built-in or custom) right after creation.
      setAutoDesignerId(res.project.id);
    },
  });

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
      <aside className="flex w-72 shrink-0 flex-col bg-slate-900 text-slate-100">
        <div className="border-b border-white/10 p-4">
          <div className="text-lg font-bold text-white">AI-SDLC</div>
          <div className="text-xs text-slate-400">Agentic pipeline · HITL gates</div>
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
          {user && (user.role === 'PROJECT_MANAGER' || user.role === 'SUPER_ADMIN') ? (
            !newProjectOpen ? (
              <button
                onClick={() => setNewProjectOpen(true)}
                className="w-full rounded-lg bg-brand-600 py-2 text-sm font-semibold text-white hover:bg-brand-700"
              >
                + New project
              </button>
            ) : (
              <div className="space-y-1.5">
                <input
                  autoFocus
                  className="w-full rounded-lg border border-white/20 bg-white/10 px-2.5 py-2 text-sm text-white placeholder:text-slate-400 focus:border-brand-400 focus:outline-none"
                  placeholder="Project name…"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && newProjectName.trim().length >= 3) createProject.mutate(newProjectName.trim());
                    if (e.key === 'Escape') setNewProjectOpen(false);
                  }}
                />
                <select
                  className="w-full rounded-lg border border-white/20 bg-white/10 px-2 py-1.5 text-xs text-white focus:outline-none [&>option]:text-slate-900"
                  value={newProjectStack}
                  onChange={(e) => setNewProjectStack(e.target.value)}
                  title="Target technology stack — all designs and generated code will use it"
                >
                  {TECH_STACKS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                {/* Per-project GitHub + Atlassian targets — all optional */}
                <div className="rounded-lg border border-white/10 bg-white/5 p-2">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                    Integration targets (optional)
                  </div>
                  {([
                    ['githubRepo', 'GitHub repo — owner/name'],
                    ['atlassianSiteUrl', 'Atlassian site — https://acme.atlassian.net'],
                    ['jiraProjectKey', 'Jira project key — e.g. PAY'],
                    ['confluenceSpaceKey', 'Confluence space key — e.g. PAYDOCS'],
                  ] as const).map(([key, ph]) => (
                    <input
                      key={key}
                      className="mb-1 w-full rounded border border-white/15 bg-white/10 px-2 py-1 text-xs text-white placeholder:text-slate-500 focus:border-brand-400 focus:outline-none"
                      placeholder={ph}
                      value={newIntegrations[key]}
                      onChange={(e) => setNewIntegrations((s) => ({ ...s, [key]: e.target.value }))}
                    />
                  ))}
                </div>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => createProject.mutate(newProjectName.trim())}
                    disabled={createProject.isPending || newProjectName.trim().length < 3}
                    className="flex-1 rounded-lg bg-brand-600 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
                  >
                    {createProject.isPending ? 'Creating…' : 'Create'}
                  </button>
                  <button
                    onClick={() => setNewProjectOpen(false)}
                    className="rounded-lg px-2.5 py-1.5 text-xs text-slate-300 hover:bg-white/10"
                  >
                    Cancel
                  </button>
                </div>
                {createProject.isError && (
                  <div className="rounded bg-red-500/20 px-2 py-1 text-[11px] text-red-200">
                    {createProject.error instanceof Error ? createProject.error.message : 'Creation failed'}
                  </div>
                )}
              </div>
            )
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
                  Phase {p.currentPhase}/6 · {p.status}
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
                ? `Phase ${currentPhase}/6 · ${phaseStates.find((s) => s.phase === currentPhase)?.name ?? ''}` +
                  (detail.data?.project.techStack ? ` · ${detail.data.project.techStack}` : '')
                : 'Describe requirements to begin'}
            </div>
          </div>
          {escalated && (
            <span className="rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-700">
              ⚠ Escalated to human developer
            </span>
          )}
          {amendInFlight && !escalated && (
            <span className="rounded-full bg-orange-100 px-3 py-1 text-xs font-semibold text-orange-700">
              ↺ Regenerating with reviewer feedback…
            </span>
          )}
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
                🗺 Pipeline map
              </button>
              <button
                onClick={() => setContextOpen(true)}
                className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-300 hover:text-brand-700"
                title="Canon (binding project rules) and Formwork (output templates) fed to every agent"
              >
                📖 Project Context
              </button>
              {detail.data?.me?.canManageTeam && (
                <button
                  onClick={deleteProject}
                  disabled={deleting}
                  className="rounded-lg border border-red-200 px-2.5 py-1 text-xs font-semibold text-red-600 hover:border-red-400 hover:bg-red-50 disabled:opacity-40"
                  title="Permanently delete this project and all its data"
                >
                  {deleting ? 'Deleting…' : '🗑 Delete'}
                </button>
              )}
            </div>
          )}
        </header>

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
          ) : (
            <div className="mx-auto mt-24 max-w-md px-6 text-center">
              <div className="text-4xl">🚀</div>
              <div className="mt-3 text-lg font-semibold text-slate-700">
                {activeProjectId ? 'Loading the pipeline…' : 'Select or create a project'}
              </div>
              <div className="mt-1 text-sm text-slate-500">
                {activeProjectId
                  ? 'Fetching stages and their status.'
                  : 'Pick a project on the left, or a Project Manager can create one, to open its stage-by-stage SDLC workspace.'}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* ---------- right panel (drag its left edge to resize) ---------- */}
      <div
        onPointerDown={(e) => rightPanel.onPointerDown(e, 'left')}
        onDoubleClick={() => rightPanel.setWidth(320)}
        className="group w-1.5 shrink-0 cursor-col-resize bg-slate-100 hover:bg-brand-200"
        title="Drag to resize · double-click to reset"
      >
        <div className="mx-auto h-8 w-0.5 translate-y-1/2 rounded bg-slate-300 group-hover:bg-brand-400" />
      </div>
      <aside
        className="shrink-0 border-l border-slate-200 bg-slate-50"
        style={{ width: rightPanel.width }}
      >
        <RightPanel
          projectId={activeProjectId}
          canManageTeam={detail.data?.me?.canManageTeam ?? false}
          focusedPhase={focusedPhase}
        />
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
        <ProjectContextPanel projectId={activeProjectId} onClose={() => setContextOpen(false)} />
      )}
    </div>
  );
}
