import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import AgentsSeeTab from './AgentsSeeTab';
import ProfileTab from './ProfileTab';
import RulesTab from './RulesTab';
import StackTab from './StackTab';
import TemplatesTab from './TemplatesTab';

type Tab = 'stack' | 'profile' | 'rules' | 'templates' | 'see';
const TABS: Array<{ id: Tab; label: string; hint: string }> = [
  { id: 'stack', label: 'Technology', hint: 'The technologies, by layer' },
  { id: 'profile', label: 'Domain and compliance', hint: 'Industry, regulations and what the system does' },
  { id: 'rules', label: 'Rules', hint: 'What every agent must follow' },
  { id: 'templates', label: 'Document formats', hint: 'The layout each kind of document follows' },
  { id: 'see', label: 'Agent briefing', hint: 'The exact text each stage\'s agents are given' },
];

/**
 * Project Mindset: what every agent run is told about this project. The stack by layer (projectconfig.json), the rules agents must
 * follow and the templates their output follows.
 */
export default function ProjectContextPage({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('stack');
  const rules = useQuery({ queryKey: ['rules', projectId], queryFn: () => api.get<{ canAuthor: boolean }>(`/api/projects/${projectId}/rules/overview`) });
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-6" data-testid="v2-context-page">
      <div className="mx-auto w-full max-w-5xl">
        <h1 className="font-display text-xl font-bold text-navy">Project Mindset</h1>
        <p className="mb-3 text-sm text-slate-500">How this project thinks and works: its technology, its domain and compliance profile, its rules (the binding “Canon”) and its document formats (“Formwork”). Every agent run is told this.</p>
        <div role="tablist" aria-label="Project mindset" className="mb-4 flex gap-1 border-b border-slate-200">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)} data-testid={`v2-context-tab-${t.id}`} title={t.hint}
              className={`-mb-px border-b-2 px-4 py-2 text-sm ${tab === t.id ? 'border-brand-600 font-semibold text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>{t.label}</button>
          ))}
        </div>
        {tab === 'stack' && <StackTab projectId={projectId} />}
        {tab === 'profile' && <ProfileTab projectId={projectId} />}
        {tab === 'rules' && <RulesTab projectId={projectId} />}
        {tab === 'templates' && <TemplatesTab projectId={projectId} canEdit={rules.data?.canAuthor ?? false} />}
        {tab === 'see' && <AgentsSeeTab projectId={projectId} />}
      </div>
    </div>
  );
}
