import { useEffect } from 'react';
import type { ProjectFlow } from '../api/flow';
import ArtifactViewer from '../components/ArtifactViewer';
import ContextPanel from '../components/ContextPanel';
import { Icon } from '../components/ui/Icon';
import ArtefactRun from './ArtefactRun';
import ProjectPanel from './ProjectPanel';
import { PROJECT_TOOLS } from './Sidebar';
import Splitter from './Splitter';
import { PANE_W, useV2 } from './store';

/** The right-hand pane: project tools, one artefact, or the context behind a stage. */
export default function PreviewPane({
  projectId, flow, selectedStage, canManageTeam, canWrite, canRepair, onOpenStage,
}: {
  projectId: string; flow: ProjectFlow | undefined; selectedStage: number | null; canManageTeam: boolean; canWrite: boolean; canRepair: boolean; onOpenStage?: (seq: number) => void;
}) {
  const pane = useV2((s) => s.pane);
  const openPane = useV2((s) => s.openPane);
  const closePane = useV2((s) => s.closePane);
  const narrow = useV2((s) => s.narrow);
  const width = useV2((s) => s.paneWidth);
  const setWidth = useV2((s) => s.setPaneWidth);
  const full = useV2((s) => s.paneFull);
  const setFull = useV2((s) => s.setPaneFull);
  const sideWidth = useV2((s) => s.sideWidth);
  const sideCollapsed = useV2((s) => s.sideCollapsed);
  // Esc leaves full screen
  useEffect(() => {
    if (!full) return;
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') setFull(false); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [full, setFull]);
  if (!pane) return null;
  const cover = narrow || full;
  const maxW = Math.max(PANE_W.min + 40, (typeof window === 'undefined' ? 1400 : window.innerWidth) - (sideCollapsed ? 0 : sideWidth) - 420);
  const w = Math.min(maxW, Math.max(PANE_W.min, width));
  const back = pane.type === 'artefact' && pane.from ? PROJECT_TOOLS.find((t) => t.tab === pane.from) : undefined;
  return (
    <>
    {!cover && <Splitter value={w} min={PANE_W.min} max={maxW} edge="left" onChange={setWidth} onReset={() => setWidth(PANE_W.def)} label="Resize the details pane" testid="v2-split-pane" />}
    <aside style={cover ? undefined : { width: w }} className={cover ? 'fixed inset-0 z-40 flex flex-col bg-white' : 'flex h-full shrink-0 flex-col bg-white'} aria-label="Details" data-testid="v2-pane" data-full={full ? 'true' : 'false'}>
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 px-3 py-2">
        {back && (
          <button type="button" onClick={() => openPane({ type: 'project', tab: back.tab })} className="rounded-lg px-2 py-1 text-xs font-semibold text-brand-600 hover:bg-brand-50" data-testid="v2-pane-back">← {back.label}</button>
        )}
        <div className="min-w-0 flex-1 truncate text-sm font-semibold text-navy">
          {pane.type === 'project' ? 'Project panel' : pane.type === 'artefact' ? 'Artefact' : pane.type === 'context' ? `Context for stage ${pane.seq}` : 'Document'}
        </div>
        {!narrow && (
          <button type="button" onClick={() => setFull(!full)} aria-pressed={full} aria-label={full ? 'Exit full screen' : 'Full screen'} title={full ? 'Exit full screen (Esc)' : 'Full screen'} data-testid="v2-pane-full"
            className="rounded p-1 text-slate-500 hover:bg-slate-100"><Icon name={full ? 'minimize' : 'maximize'} size={16} /></button>
        )}
        <button type="button" onClick={closePane} aria-label="Close the panel" className="rounded p-1 text-slate-500 hover:bg-slate-100" data-testid="v2-pane-close"><Icon name="x" size={16} /></button>
      </div>
      <div className="min-h-0 flex-1">
        {pane.type === 'project' && <ProjectPanel projectId={projectId} flow={flow} selectedStage={selectedStage} canManageTeam={canManageTeam} canWrite={canWrite} onOpenStage={onOpenStage} />}
        {pane.type === 'artefact' && (
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1"><ArtifactViewer key={pane.id} embedded projectId={projectId} artefactId={pane.id} onClose={closePane} canRepair={canRepair} /></div>
            <ArtefactRun key={`run-${pane.id}`} projectId={projectId} artefactId={pane.id} />
          </div>
        )}
        {pane.type === 'context' && <div className="h-full overflow-y-auto p-4"><ContextPanel projectId={projectId} seq={pane.seq} refreshKey="pane" defaultOpen /></div>}
      </div>
    </aside>
    </>
  );
}
