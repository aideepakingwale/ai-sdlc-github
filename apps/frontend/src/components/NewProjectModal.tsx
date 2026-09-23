import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Project, TechCatalog } from '../api/types';

/**
 * Project creation as a focused modal (lifted out of the sidebar so the shell
 * stays a clean navigation surface). Collects name, a structured technology
 * stack (language → version → frameworks) and optional integration targets.
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
  const [language, setLanguage] = useState('');
  const [customLanguage, setCustomLanguage] = useState(false);
  const [version, setVersion] = useState('');
  const [frameworks, setFrameworks] = useState<string[]>([]);
  const [customFramework, setCustomFramework] = useState('');
  const [integrations, setIntegrations] = useState({
    githubRepo: '', atlassianSiteUrl: '', jiraProjectKey: '', confluenceSpaceKey: '',
  });

  const techCatalog = useQuery({
    queryKey: ['tech-catalog'],
    queryFn: () => api.get<TechCatalog>('/api/meta/tech-catalog'),
    staleTime: Infinity,
  });
  const languages = techCatalog.data?.languages ?? [];
  const selectedLang = languages.find((l) => l.name === language);

  useEffect(() => {
    const first = languages[0];
    if (!customLanguage && !language && first) {
      setLanguage(first.name);
      setVersion(first.versions[0] ?? '');
    }
  }, [languages, language, customLanguage]);

  function toggleFramework(fw: string) {
    setFrameworks((s) => (s.includes(fw) ? s.filter((f) => f !== fw) : [...s, fw]));
  }

  const create = useMutation({
    mutationFn: () => {
      const cleanIntegrations = Object.fromEntries(
        Object.entries(integrations).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]),
      );
      return api.post<{ project: Project }>('/api/projects', {
        name: name.trim(),
        language: language.trim() || undefined,
        languageVersion: version.trim() || undefined,
        frameworks,
        integrations: cleanIntegrations,
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
            <p className="text-xs text-slate-500">Name it, set the technology stack, and connect delivery targets.</p>
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

          {/* Technology stack — language → version → frameworks */}
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Technology stack</div>
            <select
              className="mb-2 w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-sm text-navy focus:border-brand-500 focus:outline-none"
              value={customLanguage ? '__other__' : language}
              onChange={(e) => {
                const v = e.target.value;
                if (v === '__other__') {
                  setCustomLanguage(true); setLanguage(''); setVersion(''); setFrameworks([]);
                  return;
                }
                setCustomLanguage(false);
                setLanguage(v);
                const lang = languages.find((l) => l.name === v);
                setVersion(lang?.versions[0] ?? '');
                setFrameworks([]);
              }}
            >
              {languages.map((l) => (
                <option key={l.name} value={l.name}>{l.name}</option>
              ))}
              <option value="__other__">Other…</option>
            </select>
            {customLanguage && (
              <input
                autoFocus
                className="mb-2 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-navy placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
                placeholder="Language name…"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              />
            )}
            <input
              list="npm-tech-versions"
              className="mb-2 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-navy placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
              placeholder="Version — e.g. 3.12"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
            <datalist id="npm-tech-versions">
              {(selectedLang?.versions ?? []).map((v) => <option key={v} value={v} />)}
            </datalist>
            <div className="flex flex-wrap gap-1.5">
              {(selectedLang?.frameworks ?? []).map((fw) => (
                <button
                  key={fw}
                  type="button"
                  onClick={() => toggleFramework(fw)}
                  className={`rounded-full px-2.5 py-1 text-xs transition ${
                    frameworks.includes(fw)
                      ? 'bg-brand-500 text-white'
                      : 'border border-slate-300 bg-white text-slate-600 hover:border-brand-400'
                  }`}
                >
                  {fw}
                </button>
              ))}
              {frameworks
                .filter((fw) => !(selectedLang?.frameworks ?? []).includes(fw))
                .map((fw) => (
                  <button
                    key={fw}
                    type="button"
                    onClick={() => toggleFramework(fw)}
                    className="rounded-full bg-brand-500 px-2.5 py-1 text-xs text-white"
                    title="Remove"
                  >
                    {fw} ✕
                  </button>
                ))}
            </div>
            <input
              className="mt-2 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-navy placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
              placeholder="Add framework + Enter…"
              value={customFramework}
              onChange={(e) => setCustomFramework(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  const v = customFramework.trim();
                  if (v && !frameworks.includes(v)) toggleFramework(v);
                  setCustomFramework('');
                }
              }}
            />
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
