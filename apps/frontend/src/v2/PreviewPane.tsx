import type { ProjectFlow } from '../api/flow';
import ArtifactViewer from '../components/ArtifactViewer';
import ContextPanel from '../components/ContextPanel';
import { Icon } from '../components/ui/Icon';
import ProjectPanel from './ProjectPanel';
import { PROJECT_TOOLS } from './Sidebar';
import { useV2 } from './store';

/** The right-hand pane: project tools, one artefact, or the context behind a stage. */
export default function PreviewPane({
  projectId, flow, selectedStage, canManageTeam, canWrite, canRepair,
}: {
  projectId: string; flow: ProjectFlow | undefined; selectedStage: number | null; canManageTeam: boolean; canWrite: boolean; canRepair: boolean;
}) {
  const pane = useV2((s) => s.pane);
  const openPane = useV2((s) => s.openPane);
  const closePane = useV2((s) => s.closePane);
  const narrow = useV2((s) => s.narrow);
  if (!pane) return null;
  const back = pane.type === 'artefact' && pane.from ? PROJECT_TOOLS.find((t) => t.tab === pane.from) : undefined;
  return (
    <aside className={narrow ? 'fixed inset-0 z-40 flex flex-col bg-white' : 'flex h-full w-[min(640px,48vw)] min-w-[340px] shrink-0 flex-col border-l border-slate-200 bg-white'} aria-label="Details" data-testid="v2-pane">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 px-3 py-2">
        {back && (
          <button type="button" onClick={() => openPane({ type: 'project', tab: back.tab })} className="rounded-lg px-2 py-1 text-xs font-semibold text-brand-600 hover:bg-brand-50" data-testid="v2-pane-back">← {back.label}</button>
        )}
        <div className="min-w-0 flex-1 truncate text-sm font-semibold text-navy">
          {pane.type === 'project' ? 'Project panel' : pane.type === 'artefact' ? 'Artefact' : pane.type === 'context' ? `Context for stage ${pane.seq}` : 'Document'}
        </div>
        <button type="button" onClick={closePane} aria-label="Close the panel" className="rounded p-1 text-slate-500 hover:bg-slate-100" data-testid="v2-pane-close"><Icon name="x" size={16} /></button>
      </div>
      <div className="min-h-0 flex-1">
        {pane.type === 'project' && <ProjectPanel projectId={projectId} flow={flow} selectedStage={selectedStage} canManageTeam={canManageTeam} canWrite={canWrite} />}
        {pane.type === 'artefact' && <ArtifactViewer key={pane.id} embedded projectId={projectId} artefactId={pane.id} onClose={closePane} canRepair={canRepair} />}
        {pane.type === 'context' && <div className="h-full overflow-y-auto p-4"><ContextPanel projectId={projectId} seq={pane.seq} refreshKey="pane" defaultOpen /></div>}
      </div>
    </aside>
  );
}
