import { useState } from 'react';
import type { ProjectFlow } from '../../api/flow';
import AgileSetup from './AgileSetup';
import BacklogView from './BacklogView';
import MemoryPanel from './MemoryPanel';
import SprintBoard from './SprintBoard';
import SprintHeader from './SprintHeader';
import ReleaseBar from './ReleaseBar';
import ReleaseContext from './ReleaseContext';
import ReleaseWizard from './ReleaseWizard';
import { useOverview, usePersistedRelease } from './hooks';
import { Callout } from '../ui/Callout';

type Tab = 'board' | 'backlog' | 'context' | 'memory';
const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'board', label: 'Sprint board' }, { id: 'backlog', label: 'Backlog' }, { id: 'context', label: 'Release context' }, { id: 'memory', label: 'Project memory' },
];

/** The Agile workspace: sprint header + board / backlog / project memory. */
export default function AgileHub({ projectId, flow, onOpenStage }: {
  projectId: string; flow: ProjectFlow; onOpenStage: (seq: number) => void;
}) {
  const [tab, setTab] = useState<Tab>('board');
  const [focus, setFocus] = usePersistedRelease(projectId);
  const [wizard, setWizard] = useState(false);
  const ov = useOverview(projectId, focus);
  if (ov.isError) return <div className="p-6"><Callout tone="error" title="Could not load Agile delivery">{ov.error instanceof Error ? ov.error.message : 'Request failed'}</Callout></div>;
  if (!ov.data) return <div className="p-6 text-sm text-slate-400">Loading…</div>;
  const o = ov.data;
  if (!o.enabled) {
    return o.permissions.canManage
      ? <AgileSetup projectId={projectId} onDone={() => void ov.refetch()} />
      : <div className="p-6"><Callout tone="info" title="This project uses the classic pipeline">Only the managing Project Manager can switch a new project to Scrum or Kanban.</Callout></div>;
  }
  return (
    <div className="flex h-full flex-col overflow-hidden bg-slate-50">
      <ReleaseBar overview={o} focus={focus} onFocus={setFocus} onNew={() => setWizard(true)} />
      <SprintHeader projectId={projectId} overview={o} stages={flow.stages} onOpenStage={onOpenStage} />
      <div className="flex gap-1 border-b border-slate-200 bg-white px-4" role="tablist" aria-label="Agile views">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold transition ${tab === t.id ? 'border-brand-600 text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>{t.label}</button>))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === 'board' && <SprintBoard projectId={projectId} overview={o} />}
        {tab === 'backlog' && <BacklogView projectId={projectId} overview={o} />}
        {tab === 'context' && <ReleaseContext projectId={projectId} overview={o} />}
        {tab === 'memory' && <MemoryPanel projectId={projectId} />}
      </div>
      {wizard && <ReleaseWizard projectId={projectId} focusRelease={o.currentRelease?.id} onClose={() => setWizard(false)}
        onDone={(id) => { setFocus(id); setWizard(false); setTab('context'); void ov.refetch(); }} />}
    </div>
  );
}
