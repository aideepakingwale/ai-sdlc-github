import { useState } from 'react';
import { useApp } from '../store';
import AgentsTab from './agents/AgentsTab';

/**
 * MindDesigner: where a project designs, tests and approves its own agents and skills, uses the built-in and shared ones as starting points,
 * runs them, and places them in the stages of the pipeline. (The organisation's side of this is Governance -> MindDesigner.)
 */
export default function MindDesignerPage({ projectId }: { projectId: string }) {
  const [building, setBuilding] = useState(false);
  const { user } = useApp();
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-6" data-testid="v2-minddesigner-page">
      <div className={`mx-auto w-full ${building ? 'max-w-[1400px]' : 'max-w-5xl'}`}>
        <h1 className="font-display text-xl font-bold text-navy">MindDesigner</h1>
        <p className="mb-4 text-sm text-slate-500">Design, test and approve your own agents and skills. Start from a built-in or shared one, run it on its own or in a stage, and place it in your pipeline.</p>
        <AgentsTab projectId={projectId} userId={user?.id ?? ''} onBuilder={setBuilding} />
      </div>
    </div>
  );
}
