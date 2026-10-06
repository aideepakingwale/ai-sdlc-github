import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { Project } from '../api/types';

/**
 * Project creation as a focused modal (lifted out of the sidebar so the shell
 * stays a clean navigation surface). Collects the name, the pipeline and optional
 * integration targets. The technology stack is deliberately NOT asked here: the
 * Technical Architect stage decides it (and asks if the requirements don't say).
 */
export default function NewProjectModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (projectId: string) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [integrations, setIntegrations] = useState({
    githubRepo: '', atlassianSiteUrl: '', jiraProjectKey: '', confluenceSpaceKey: '',
  });
  // Pipeline: the full SDLC template, or a custom pipeline the PM builds from a
  // single starter stage in the Workflow Designer (fully dynamic).
  const [pipeline, setPipeline] = useState<'default' | 'custom'>('default');

  const create = useMutation({
    mutationFn: () => {
      const cleanIntegrations = Object.fromEntries(
        Object.entries(integrations).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]),
      );
      // Custom pipeline → seed a single starter stage; the PM builds the rest in
      // the Workflow Designer (which auto-opens after creation). Default → omit the
      // workflow so the full SDLC template is used.
      const workflow = pipeline === 'custom'
        ? { stages: [{
            key: 'stage1', name: 'Stage 1', template: 1,
            reviewerRole: 'PO', reviewerRoles: ['PO'], team: ['PO'],
            inputs: ['requirements'], outputs: ['PRD'], dependsOn: [],
          }] }
        : undefined;
      return api.post<{ project: Project }>('/api/projects', {
        name: name.trim(),
        integrations: cleanIntegrations,
        ...(workflow ? { workflow } : {}),
      });
    },
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ['projects'] });
      onCreated(res.project.id);
    },
  });

  const canCreate = name.trim().length >= 3 && !create.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy/40 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-xl bg-white shadow-2xl ring-1 ring-slate-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-navy">New project</h2>
            <p className="text-xs text-slate-500">Name it, pick a pipeline and connect delivery targets. The technology stack is decided later by the Technical Architect.</p>
          </div>
          <button onClick={onClose} className="rounded-lg px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-navy" aria-label="Close">
            ✕
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Project name</span>
            <input
              autoFocus
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-navy placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
              placeholder="e.g. Payments Modernisation"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && canCreate) create.mutate();
                if (e.key === 'Escape') onClose();
              }}
            />
          </label>

          {/* Pipeline — default full SDLC or a custom, build-your-own workflow */}
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Pipeline</div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {([
                ['default', 'Full SDLC template', 'The standard end-to-end phases (requirements → delivery). You can still edit them later.'],
                ['custom', 'Custom — build my own', 'Start from a single stage and define your own phases, order and reviewers in the Workflow Designer.'],
              ] as const).map(([val, title, desc]) => (
                <button
                  key={val}
                  type="button"
                  onClick={() => setPipeline(val)}
                  className={`rounded-lg border p-2.5 text-left transition ${
                    pipeline === val ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-200' : 'border-slate-300 bg-white hover:border-brand-300'
                  }`}
                >
                  <div className="flex items-center gap-1.5 text-sm font-semibold text-navy">
                    <span className={`h-3 w-3 rounded-full border ${pipeline === val ? 'border-brand-500 bg-brand-500' : 'border-slate-400'}`} />
                    {title}
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-500">{desc}</div>
                </button>
              ))}
            </div>
            {pipeline === 'custom' && (
              <div className="mt-2 text-[11px] text-brand-700">
                The Workflow Designer opens right after creation so you can add stages, set outputs and assign reviewers.
              </div>
            )}
          </div>

          {/* Integration targets — optional */}
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Integration targets (optional)</div>
            {([
              ['githubRepo', 'GitHub repo — owner/name'],
              ['atlassianSiteUrl', 'Atlassian site — https://acme.atlassian.net'],
              ['jiraProjectKey', 'Jira project key — e.g. PAY'],
              ['confluenceSpaceKey', 'Confluence space key — e.g. PAYDOCS'],
            ] as const).map(([key, ph]) => (
              <input
                key={key}
                className="mb-2 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-navy placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
                placeholder={ph}
                value={integrations[key]}
                onChange={(e) => setIntegrations((s) => ({ ...s, [key]: e.target.value }))}
              />
            ))}
          </div>

          {create.isError && (
            <div className="rounded-md bg-bared-200 px-3 py-2 text-xs text-bared-700">
              {create.error instanceof Error ? create.error.message : 'Creation failed'}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-3">
          <button onClick={onClose} className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100">
            Cancel
          </button>
          <button
            onClick={() => create.mutate()}
            disabled={!canCreate}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
          >
            {create.isPending ? 'Creating…' : 'Create project'}
          </button>
        </div>
      </div>
    </div>
  );
}
