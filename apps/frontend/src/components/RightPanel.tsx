import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { Artefact, AuditEvent } from '../api/types';
import ArtifactViewer from './ArtifactViewer';
import FilesPanel from './FilesPanel';
import PhasePanel from './PhasePanel';
import TeamPanel from './TeamPanel';

type Tab = 'stage' | 'team' | 'artifacts' | 'files' | 'audit' | 'skills';

export default function RightPanel({
  projectId,
  canManageTeam,
  focusedPhase,
}: {
  projectId: string | null;
  canManageTeam: boolean;
  focusedPhase?: number | null;
}) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>('artifacts');
  const [kbQuery, setKbQuery] = useState('');

  // Clicking a pipeline stage focuses it → open the Stage view.
  useEffect(() => {
    if (focusedPhase != null) setTab('stage');
    else setTab((t) => (t === 'stage' ? 'artifacts' : t));
  }, [focusedPhase]);
  const [viewerId, setViewerId] = useState<string | null>(null);
  const [uploadMsg, setUploadMsg] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);

  const codebase = useQuery({
    queryKey: ['codebase', projectId],
    queryFn: () => api.get<{ files: Array<{ id: string; path: string }> }>(`/api/projects/${projectId}/codebase`),
    enabled: Boolean(projectId),
  });

  const providers = useQuery({
    queryKey: ['providers'],
    queryFn: () =>
      api.get<{
        llm: Array<{ provider: string; configured: boolean; breaker: string }>;
        tools: Record<string, string>;
        generationMode?: string;
        effectiveMock?: boolean;
        activeProviders?: string[];
      }>('/api/providers'),
    enabled: tab === 'skills',
    staleTime: 60_000,
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch(`/api/projects/${projectId}/codebase`, {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      const body = (await res.json()) as { files?: number; error?: { message?: string } };
      if (!res.ok) throw new Error(body.error?.message ?? 'Upload failed');
      return body;
    },
    onSuccess: (res) => {
      setUploadMsg(`✓ Indexed ${res.files} source files — agents now ground on this codebase.`);
      void qc.invalidateQueries({ queryKey: ['codebase', projectId] });
    },
    onError: (err) => setUploadMsg(`✗ ${err instanceof Error ? err.message : 'Upload failed'}`),
  });

  const artefacts = useQuery({
    queryKey: ['artefacts', projectId],
    queryFn: () => api.get<{ artefacts: Artefact[] }>(`/api/projects/${projectId}/artefacts`),
    enabled: Boolean(projectId),
    refetchInterval: 5_000,
  });

  const audit = useQuery({
    queryKey: ['audit', projectId],
    queryFn: () => api.get<{ events: AuditEvent[] }>(`/api/projects/${projectId}/audit`),
    enabled: Boolean(projectId) && tab === 'audit',
    refetchInterval: tab === 'audit' ? 5_000 : false,
  });

  const skills = useQuery({
    queryKey: ['skills'],
    queryFn: () => api.get<{ tools: Array<{ name: string; description: string }> }>('/api/skills'),
    enabled: tab === 'skills',
    staleTime: 300_000,
  });

  const kb = useQuery({
    queryKey: ['kb', kbQuery],
    queryFn: () =>
      api.get<{ standards: Array<{ id: string; title: string; body: string }> }>(
        `/api/kb/search?q=${encodeURIComponent(kbQuery)}`,
      ),
    enabled: tab === 'skills',
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex border-b border-slate-200">
        {(focusedPhase != null
          ? (['stage', 'team', 'artifacts', 'files', 'audit', 'skills'] as const)
          : (['team', 'artifacts', 'files', 'audit', 'skills'] as const)
        ).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 px-2 py-2.5 text-xs font-semibold uppercase tracking-wide ${
              tab === t ? 'border-b-2 border-brand-600 text-brand-700' : 'text-slate-400 hover:text-slate-600'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {tab === 'stage' && projectId && focusedPhase != null && (
          <PhasePanel projectId={projectId} phase={focusedPhase} onOpenArtifact={setViewerId} />
        )}

        {tab === 'files' &&
          (projectId ? (
            <FilesPanel projectId={projectId} onOpenArtifact={setViewerId} />
          ) : (
            <Empty text="Select a project to browse its files." />
          ))}

        {tab === 'team' &&
          (projectId ? (
            <TeamPanel projectId={projectId} canManage={canManageTeam} />
          ) : (
            <Empty text="Select a project to manage its team." />
          ))}

        {tab === 'artifacts' && (
          <div className="space-y-2">
            {!projectId && <Empty text="Select a project to see its artifacts." />}
            {projectId && (
              <div className="rounded-lg border border-dashed border-slate-300 bg-white p-2.5">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-semibold text-slate-600">
                    📦 Existing codebase{' '}
                    <span className="text-slate-400">({codebase.data?.files.length ?? 0} files indexed)</span>
                  </div>
                  <button
                    onClick={() => fileInput.current?.click()}
                    disabled={upload.isPending}
                    className="rounded bg-brand-50 px-2 py-1 text-[11px] font-semibold text-brand-700 hover:bg-brand-100 disabled:opacity-50"
                  >
                    {upload.isPending ? 'Indexing…' : 'Upload .zip'}
                  </button>
                  <input
                    ref={fileInput}
                    type="file"
                    accept=".zip"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) upload.mutate(f);
                      e.target.value = '';
                    }}
                  />
                </div>
                <div className="mt-1 text-[10px] text-slate-400">
                  Upload an existing codebase; agents will ground designs and code changes on it (brownfield mode).
                </div>
                {uploadMsg && <div className="mt-1 text-[11px] text-slate-600">{uploadMsg}</div>}
              </div>
            )}
            {projectId && focusedPhase != null && (
              <div className="flex items-center justify-between rounded-lg bg-brand-50 px-2.5 py-1.5 text-[11px] text-brand-700">
                <span>Showing Phase {focusedPhase} artifacts (clicked in the pipeline flow)</span>
              </div>
            )}
            {projectId && (artefacts.data?.artefacts ?? []).length === 0 && <Empty text="No artifacts yet — send the first requirement." />}
            {(artefacts.data?.artefacts ?? [])
              .filter((a) => focusedPhase == null || a.phase === focusedPhase)
              .map((a) => (
              <button
                key={a.id}
                onClick={() => setViewerId(a.id)}
                className="block w-full rounded-lg border border-slate-200 bg-white p-2.5 text-left transition hover:border-brand-300 hover:shadow-sm"
              >
                <div className="flex items-center gap-1.5">
                  <span className="rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-bold text-brand-700">P{a.phase}</span>
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">{a.type}</span>
                  {(a.type === 'HLD_DIAGRAM' || a.type === 'LLD_DIAGRAM') && <span className="text-[10px]">📐</span>}
                </div>
                <div className="mt-1 truncate text-sm font-medium text-slate-800" title={a.title}>
                  {a.title}
                </div>
                <div className="mt-0.5 text-[11px] text-brand-600">View / review ↗</div>
              </button>
            ))}
          </div>
        )}

        {tab === 'audit' && (
          <div className="space-y-2">
            {!projectId && <Empty text="Select a project to see its audit trail." />}
            {(audit.data?.events ?? []).map((e) => (
              <div key={e.id} className="rounded-lg border border-slate-200 bg-white p-2.5 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-700">{e.event}</span>
                  <span className="text-slate-400">{new Date(e.timestamp).toLocaleTimeString()}</span>
                </div>
                <div className="mt-0.5 text-slate-500">
                  {e.agentRole}
                  {e.phase ? ` · P${e.phase}` : ''}
                  {e.provider ? ` · ${e.provider}/${e.model}` : ''}
                  {e.promptTokens != null ? ` · ${e.promptTokens}→${e.completionTokens} tok` : ''}
                </div>
                {e.humanReviewer && <div className="mt-0.5 text-emerald-700">👤 {e.humanReviewer}</div>}
                {e.artefactHash && (
                  <div className="mt-0.5 truncate font-mono text-[10px] text-slate-400" title={e.artefactHash}>
                    #{e.artefactHash.slice(0, 16)}…
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {tab === 'skills' && (
          <div className="space-y-3">
            <div>
              <div className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Providers &amp; connectors
                {providers.data && (
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-bold normal-case ${
                      providers.data.effectiveMock
                        ? 'bg-amber-100 text-amber-700'
                        : 'bg-emerald-100 text-emerald-700'
                    }`}
                    title={`GENERATION_MODE=${providers.data.generationMode ?? 'auto'}`}
                  >
                    {providers.data.effectiveMock
                      ? '● Mock generation'
                      : `● Real LLM · ${(providers.data.activeProviders ?? []).join(', ') || 'configured'}`}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(providers.data?.llm ?? []).map((p) => (
                  <span
                    key={p.provider}
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      p.provider === 'mock'
                        ? 'bg-slate-200 text-slate-600'
                        : p.configured && p.breaker === 'closed'
                          ? 'bg-emerald-100 text-emerald-700'
                          : p.configured
                            ? 'bg-amber-100 text-amber-700'
                            : 'bg-slate-100 text-slate-400'
                    }`}
                    title={p.configured ? `breaker: ${p.breaker}` : 'no API key configured'}
                  >
                    {p.provider} {p.configured ? (p.breaker === 'closed' ? '● live' : `⚠ ${p.breaker}`) : '○ no key'}
                  </span>
                ))}
                {Object.entries(providers.data?.tools ?? {}).map(([name, mode]) => (
                  <span
                    key={name}
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      mode === 'live' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-600'
                    }`}
                  >
                    {name} {mode === 'live' ? '● live' : '○ mock'}
                  </span>
                ))}
              </div>
              <div className="mt-1 text-[10px] text-slate-400">
                Set GROQ/GEMINI/XAI keys and JIRA/GITHUB credentials in .env to switch mocks off — no code changes.
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Enterprise KB</div>
              <input
                className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
                placeholder="Search standards & allowlists…"
                value={kbQuery}
                onChange={(e) => setKbQuery(e.target.value)}
              />
              <div className="mt-2 space-y-1.5">
                {(kb.data?.standards ?? []).map((s) => (
                  <div key={s.id} className="rounded-lg bg-slate-100 p-2 text-xs">
                    <div className="font-semibold text-slate-700">{s.title}</div>
                    <div className="mt-0.5 text-slate-500">{s.body}</div>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Active MCP tools ({skills.data?.tools.length ?? 0})
              </div>
              <div className="space-y-1">
                {(skills.data?.tools ?? []).map((t) => (
                  <div key={t.name} className="rounded-lg border border-slate-200 bg-white p-2 text-xs">
                    <div className="font-mono font-semibold text-brand-700">{t.name}</div>
                    <div className="mt-0.5 text-slate-500">{t.description}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {viewerId && projectId && (
        <ArtifactViewer projectId={projectId} artefactId={viewerId} onClose={() => setViewerId(null)} />
      )}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-lg bg-slate-100 p-3 text-center text-xs text-slate-500">{text}</div>;
}
