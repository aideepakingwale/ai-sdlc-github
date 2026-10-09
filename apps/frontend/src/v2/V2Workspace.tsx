import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import PortfolioView from './PortfolioView';
import GovernancePanel from '../components/GovernancePanel';
import ModelRoutesControl from '../components/ModelRoutesControl';
import NewProjectModal from '../components/NewProjectModal';
import ObservabilityPanel from '../components/ObservabilityPanel';
import WorkflowDesigner from '../components/WorkflowDesigner';
import ProjectContextPanel from '../components/ProjectContextPanel';
import ProjectExplorer from '../components/ProjectExplorer';
import QualityMetricsPanel from '../components/QualityMetricsPanel';
import ConnectionsPage from './ConnectionsPage';
import StageChat from './stage/StageChat';
import { useApp } from '../store';
import GlobalBar from './GlobalBar';
import PipelineView from './PipelineView';
import PreviewPane from './PreviewPane';
import Splitter from './Splitter';
import Sidebar, { type V2Modal, type V2View } from './Sidebar';
import { SIDE_W, useV2 } from './store';
import { useNarrowSync } from './narrow';
import { applyTheme, watchSystemTheme } from './theme';
import { useWorkspaceData } from './useWorkspaceData';

export default function V2Workspace() {
  const qc = useQueryClient();
  const { user, setUser, activeProjectId, setActiveProject } = useApp();
  const { projects, detail, artefacts, flow } = useWorkspaceData(activeProjectId);
  useNarrowSync();
  const narrow = useV2((s) => s.narrow);
  const drawer = useV2((s) => s.drawer);
  const setDrawer = useV2((s) => s.setDrawer);
  const sideCollapsed = useV2((s) => s.sideCollapsed);
  const sideWidth = useV2((s) => s.sideWidth);
  const setSideWidth = useV2((s) => s.setSideWidth);
  const closePane = useV2((s) => s.closePane);
  const openPane = useV2((s) => s.openPane);
  const [view, setView] = useState<V2View>('dashboard');
  const [selectedStage, setSelectedStage] = useState<number | null>(null);
  const [modal, setModalRaw] = useState<V2Modal | null>(null);
  // Configuration screens (and the workflow designer) open as pages in the centre.
  const setModal = (m: V2Modal | null) => { setModalRaw(m); if (m) setView('stage'); };
  const [newProject, setNewProject] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => { applyTheme(); const off = watchSystemTheme(); return () => { off(); document.documentElement.classList.remove('dark'); }; }, []);
  useEffect(() => { setModalRaw(null); setSelectedStage(null); closePane(); setView(activeProjectId ? 'stage' : 'dashboard'); }, [activeProjectId, closePane]);

  if (!user) return null;
  const canManage = user.role === 'PROJECT_MANAGER' || user.role === 'SUPER_ADMIN';
  const phaseStates = detail.data?.phaseStates ?? [];
  const pendingGate = phaseStates.find((s) => s.status === 'PENDING_REVIEW') ?? null;
  const escalated = phaseStates.find((s) => s.status === 'ESCALATED');
  const currentPhase = detail.data?.project.currentPhase ?? 1;
  const activeStage = selectedStage ?? pendingGate?.phase ?? escalated?.phase ?? currentPhase;
  const stage = flow.data?.stages.find((s) => s.phase === activeStage);
  const goStage = (n: number) => { setModalRaw(null); setSelectedStage(n); setView('stage'); };

  async function logout() {
    await api.post('/api/auth/logout');
    setUser(null); setActiveProject(null); qc.clear();
  }
  async function deleteProject() {
    if (!activeProjectId) return;
    if (!window.confirm(`Permanently delete "${detail.data?.project.name ?? 'this project'}" and ALL its data (artifacts, files, history)? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await api.del(`/api/projects/${activeProjectId}`);
      setActiveProject(null);
      await qc.invalidateQueries({ queryKey: ['projects'] });
    } catch (err) { window.alert(err instanceof Error ? err.message : 'Delete failed'); } finally { setDeleting(false); }
  }

  const PAGE_NAMES: Record<string, string> = { explorer: 'Project Explorer', context: 'Project Context', quality: 'Quality', governance: 'Governance', observability: 'Observability', models: 'Model routes', designer: 'Workflow designer', connections: 'Connections' };
  const crumb = modal && PAGE_NAMES[modal]
    ? `${activeProjectId ? `${detail.data?.project.name ?? ''} › ` : ''}${PAGE_NAMES[modal]}`
    : activeProjectId
    ? `${detail.data?.project.name ?? ''} › ${view === 'pipeline' ? 'Pipeline' : stage ? `Stage ${stage.phase} · ${stage.name}` : ''}`
    : 'All projects';

  return (
    <div className="flex h-full bg-white" data-testid="v2-workspace">
      {(narrow ? drawer : !sideCollapsed) && (
        <div className={narrow ? 'fixed inset-0 z-40 flex' : 'contents'} data-testid={narrow ? 'v2-drawer' : undefined}>
        <Sidebar
          width={narrow ? undefined : sideWidth} projects={projects.data?.projects ?? []} activeProjectId={activeProjectId}
          onPickProject={(id) => { setDrawer(false); setActiveProject(id); }} onNewProject={() => setNewProject(true)}
          canManage={canManage} isAdmin={user.role === 'SUPER_ADMIN'} flow={flow.data}
          selectedStage={activeStage} view={view} onView={(v) => { setDrawer(false); setModalRaw(null); setView(v); }} onStage={(n) => { setDrawer(false); goStage(n); }} onModal={(m) => { setDrawer(false); setModal(m); }}
        />
      
          {narrow && <button type="button" aria-label="Close the sidebar" className="flex-1 bg-black/40" onClick={() => setDrawer(false)} />}
        </div>
      )}

      {!narrow && !sideCollapsed && <Splitter value={sideWidth} min={SIDE_W.min} max={SIDE_W.max} edge="right" onChange={setSideWidth} onReset={() => setSideWidth(SIDE_W.def)} label="Resize the sidebar" testid="v2-split-side" />}

      <div className="flex min-w-0 flex-1 flex-col">
        <GlobalBar
          user={user} detail={detail.data} projectId={activeProjectId} projectName={detail.data?.project.name ?? null}
          crumb={crumb} onGoToStage={goStage} onLogout={() => void logout()} onDelete={() => void deleteProject()} deleting={deleting}
        />
        {detail.isError && activeProjectId && (
          <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
            ⚠ Could not load this project — <button className="underline" onClick={() => void detail.refetch()}>retry</button>
          </div>
        )}
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-hidden" data-testid="v2-main">
            {modal && (modal === 'explorer' || modal === 'governance' || modal === 'observability' || modal === 'models' || activeProjectId) ? (
              modal === 'explorer' ? <ProjectExplorer page onClose={() => setModal(null)} onOpen={(id) => { setActiveProject(id); setModal(null); }} />
              : modal === 'governance' ? <GovernancePanel page onClose={() => setModal(null)} />
              : modal === 'observability' ? <ObservabilityPanel page onClose={() => setModal(null)} />
              : modal === 'models' ? <div className="h-full overflow-y-auto bg-slate-50 p-6"><div className="mx-auto max-w-4xl"><h1 className="mb-1 font-display text-xl font-bold text-navy">Model routes</h1><p className="mb-4 text-sm text-slate-500">Which models serve which kind of work. The first in each chain is preferred, the rest are fallbacks. Changes apply at once.</p><ModelRoutesControl /></div></div>
              : modal === 'designer' ? <WorkflowDesigner page projectId={activeProjectId!} onClose={() => { setModalRaw(null); setView('pipeline'); }} />
              : modal === 'connections' ? <ConnectionsPage projectId={activeProjectId!} projectName={detail.data?.project.name} />
              : modal === 'quality' ? <QualityMetricsPanel page projectId={activeProjectId!} onClose={() => setModal(null)} />
              : <ProjectContextPanel page projectId={activeProjectId!} techStack={detail.data?.project.techStack} techStackDecided={detail.data?.project.techStackDecided}
                  techStackSource={detail.data?.project.techStackSource}
                  canSetStack={(detail.data?.me?.canManageTeam ?? false) || detail.data?.me?.membershipRole === 'TA'} onClose={() => setModal(null)} />
            ) : !activeProjectId ? (
              <PortfolioView onOpen={setActiveProject} onNewProject={() => setNewProject(true)} canManage={canManage} />
            ) : view === 'pipeline' ? (
              flow.data ? <PipelineView projectId={activeProjectId} projectName={detail.data?.project.name} currentPhase={activeStage} flow={flow.data} onOpenStage={goStage} onDesigner={() => setModal('designer')} canDesign={canManage} /> : <div className="mx-auto mt-24 max-w-md px-6 text-center text-slate-500">Loading the pipeline…</div>
            ) : flow.data ? (
              <StageChat
                key={`${activeProjectId}:${activeStage}`} projectId={activeProjectId} flow={flow.data} selectedSeq={activeStage} onSelectStage={goStage}
                user={user} onOpenArtefact={(id) => openPane({ type: 'artefact', id })} onOpenPipeline={() => { setModalRaw(null); setView('pipeline'); }}
                messages={detail.data?.messages ?? []} pendingGate={pendingGate}
                artefacts={(artefacts.data?.artefacts ?? []).map((a) => ({ id: a.id, phase: a.phase, type: a.type, title: a.title, url: a.url }))}
              />
            ) : (
              <div className="mx-auto mt-24 max-w-md px-6 text-center text-slate-500">Loading the pipeline…</div>
            )}
          </main>
          {activeProjectId && (
            <PreviewPane projectId={activeProjectId} flow={flow.data} selectedStage={activeStage}
              canManageTeam={detail.data?.me?.canManageTeam ?? false} canWrite={canManage || Boolean(detail.data?.me?.membershipRole)}
              canRepair={Boolean(stage?.canRetrigger)} onOpenStage={goStage} />
          )}
        </div>
      </div>

      {newProject && <NewProjectModal onClose={() => setNewProject(false)} onCreated={(id) => { setNewProject(false); void qc.invalidateQueries({ queryKey: ['projects'] }); setActiveProject(id); setModal('designer'); }} />}
    </div>
  );
}
