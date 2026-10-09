import { useState } from 'react';
import ProjectContextPanel from '../components/ProjectContextPanel';
import StackTab from './StackTab';

type Tab = 'stack' | 'rules';
const TABS: Array<{ id: Tab; label: string; hint: string }> = [
  { id: 'stack', label: 'Stack', hint: 'The technologies, by layer' },
  { id: 'rules', label: 'Rules and templates', hint: 'Binding rules and output templates' },
];

/**
 * Project Context: what every agent run is told about this project. The stack by layer (projectconfig.json), the rules agents must
 * follow and the templates their output follows.
 */
export default function ProjectContextPage({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('stack');
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-6" data-testid="v2-context-page">
      <div className="mx-auto w-full max-w-5xl">
        <h1 className="font-display text-xl font-bold text-navy">Project Context</h1>
        <p className="mb-3 text-sm text-slate-500">What every agent run is told about this project: its stack, its rules and its output templates.</p>
        <div role="tablist" aria-label="Project context" className="mb-4 flex gap-1 border-b border-slate-200">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)} data-testid={`v2-context-tab-${t.id}`} title={t.hint}
              className={`-mb-px border-b-2 px-4 py-2 text-sm ${tab === t.id ? 'border-brand-600 font-semibold text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>{t.label}</button>
          ))}
        </div>
        {tab === 'stack' && <StackTab projectId={projectId} />}
        {tab === 'rules' && <ProjectContextPanel embedded page projectId={projectId} onClose={onClose} />}
      </div>
    </div>
  );
}
