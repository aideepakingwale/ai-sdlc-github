import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { TechCatalog } from '../api/types';

/**
 * The project's technology stack. It is NOT chosen at creation: the Technical
 * Architect stage decides it (and asks the reviewer when the requirements don't
 * say). Until then this shows "not decided yet"; a manager / Technical Architect
 * can also pin or correct it here, and a pinned stack is never overwritten by a
 * later Technical Architect run.
 */
export default function TechStackBar({
  projectId, techStack, decided, source, canSet,
}: {
  projectId: string;
  techStack?: string;
  decided: boolean;
  source?: string;
  canSet: boolean;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [language, setLanguage] = useState('');
  const [version, setVersion] = useState('');
  const [frameworks, setFrameworks] = useState('');

  const catalog = useQuery({
    queryKey: ['tech-catalog'],
    queryFn: () => api.get<TechCatalog>('/api/meta/tech-catalog'),
    staleTime: Infinity,
    enabled: editing,
  });
  const languages = catalog.data?.languages ?? [];
  const selected = languages.find((l) => l.name === language);

  const save = useMutation({
    mutationFn: (clear: boolean) =>
      api.put(`/api/projects/${projectId}/tech-stack`, clear ? {} : {
        language: language.trim(),
        languageVersion: version.trim(),
        frameworks: frameworks.split(',').map((f) => f.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      void qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });

  const who = source === 'user' ? 'Set manually' : source === 'ta' ? 'Decided by the Technical Architect stage' : '';

  return (
    <div className="border-b border-slate-200 bg-brand-50 px-5 py-2.5" data-testid="tech-stack-bar">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-700">Technology stack</span>
        {decided ? (
          <span className="rounded-full bg-brand-600 px-2.5 py-0.5 text-xs font-semibold text-white">{techStack}</span>
        ) : (
          <span className="rounded-full border border-dashed border-slate-400 px-2.5 py-0.5 text-xs text-slate-600">
            Not decided yet
          </span>
        )}
        {canSet && !editing && (
          <button
            onClick={() => setEditing(true)}
            className="ml-auto text-[11px] font-semibold text-brand-700 hover:underline"
          >
            {decided ? 'Change' : 'Set manually'}
          </button>
        )}
      </div>
      <div className="mt-1 text-[11px] text-slate-500">
        {decided
          ? `${who ? `${who} — ` : ''}every later stage targets this stack.`
          : 'The Technical Architect stage decides this from the requirements and the solution design, and asks you if they do not say.'}
      </div>

      {editing && (
        <div className="mt-2 space-y-2 rounded-md border border-slate-200 bg-white p-2.5">
          <div className="flex flex-wrap gap-2">
            <input
              list="stack-languages"
              className="w-40 rounded-md border border-slate-300 px-2 py-1 text-xs"
              placeholder="Language"
              value={language}
              onChange={(e) => {
                setLanguage(e.target.value);
                const l = languages.find((x) => x.name === e.target.value);
                if (l) setVersion(l.versions[0] ?? '');
              }}
            />
            <datalist id="stack-languages">
              {languages.map((l) => <option key={l.name} value={l.name} />)}
            </datalist>
            <input
              list="stack-versions"
              className="w-28 rounded-md border border-slate-300 px-2 py-1 text-xs"
              placeholder="Version"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
            <datalist id="stack-versions">
              {(selected?.versions ?? []).map((v) => <option key={v} value={v} />)}
            </datalist>
            <input
              className="min-w-[10rem] flex-1 rounded-md border border-slate-300 px-2 py-1 text-xs"
              placeholder={`Frameworks, comma separated${selected ? ` — e.g. ${selected.frameworks.slice(0, 2).join(', ')}` : ''}`}
              value={frameworks}
              onChange={(e) => setFrameworks(e.target.value)}
            />
          </div>
          {save.isError && (
            <div className="text-[11px] text-red-600">{save.error instanceof Error ? save.error.message : 'Could not save'}</div>
          )}
          <div className="flex items-center justify-end gap-2">
            {decided && (
              <button
                onClick={() => save.mutate(true)}
                disabled={save.isPending}
                className="mr-auto text-[11px] text-slate-500 hover:text-red-600"
              >
                Clear (let the Technical Architect decide)
              </button>
            )}
            <button onClick={() => setEditing(false)} className="rounded px-2 py-1 text-xs text-slate-600 hover:bg-slate-100">
              Cancel
            </button>
            <button
              onClick={() => save.mutate(false)}
              disabled={!language.trim() || save.isPending}
              className="rounded bg-brand-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
            >
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
