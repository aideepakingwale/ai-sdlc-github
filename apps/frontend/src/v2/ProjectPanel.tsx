import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ProjectFlow } from '../api/flow';
import type { Artefact, AuditEvent } from '../api/types';
import CodeExplorer from '../components/CodeExplorer';
import CodeView, { langForExt } from '../components/CodeView';
import FilesPanel from '../components/FilesPanel';
import SkillsPanel from '../components/SkillsPanel';
import TeamPanel from '../components/TeamPanel';
import { Icon } from '../components/ui/Icon';
import type { CodeNode, CodeView as CodeViewData } from '../lib/codeTree';
import { filterTree, iconFor } from '../lib/codeTree';
import { auditCategory, auditCsv, buildPathTree, AUDIT_CATEGORIES, type AuditCategory } from './projectPanelLib';
import { Pill, StageDot, stageTone } from './bits';
import { PROJECT_TOOLS } from './Sidebar';
import { useV2, type ProjectTab } from './store';

const EXPLAIN: Record<ProjectTab, React.ReactNode> = {
  team: <><b>Team.</b> Each stage has a role (PO, SA, TA, QA, DevOps, Dev). People you add here can write their stage and sign off its gate. Only project managers and admins can change this.</>,
  artefacts: <><b>Artefacts.</b> Every document, diagram and file the agents produced, grouped by stage. Open one to read, export or review it. Newer versions replace older ones; the older ones stay in the version menu.</>,
  files: <><b>Files.</b> The same artefacts as real files in their phase folders, with their exact stored names, plus any uploaded codebase. Use this to see what is on disk or in the bucket.</>,
  codebase: <><b>Codebase.</b> <i>Existing</i> is a codebase you upload (a .zip) so the agents ground designs and changes on it. <i>Generated</i> is the project that the implementation stage writes.</>,
  audit: <><b>Audit.</b> The permanent record: each generation, review decision, guardrail action and security review, with the person, role, stage and model. It cannot be edited.</>,
  skills: <><b>Skills.</b> Small on-demand helpers for the stage you are on, such as linting an API or drafting an ADR. Below them, the model providers and services this project is connected to.</>,
};

export default function ProjectPanel({
  projectId, flow, selectedStage, canManageTeam, canWrite,
}: {
  projectId: string; flow: ProjectFlow | undefined; selectedStage: number | null; canManageTeam: boolean; canWrite: boolean;
}) {
  const pane = useV2((s) => s.pane);
  const openPane = useV2((s) => s.openPane);
  const tab: ProjectTab = pane?.type === 'project' ? pane.tab : 'team';
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="v2-project-panel">
      <div role="tablist" aria-label="Project tools" className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-slate-200 px-2">
        {PROJECT_TOOLS.map((t) => (
          <button key={t.tab} role="tab" aria-selected={tab === t.tab} onClick={() => openPane({ type: 'project', tab: t.tab })} data-testid={`v2-ptab-${t.tab}`}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm ${tab === t.tab ? 'border-brand-600 font-semibold text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mb-4 rounded-lg bg-brand-50 px-3 py-2 text-[13px] text-slate-700" data-testid="v2-explain">{EXPLAIN[tab]}</div>
        {tab === 'team' && <TeamPanel projectId={projectId} canManage={canManageTeam} />}
        {tab === 'artefacts' && <ArtefactsTab projectId={projectId} flow={flow} selectedStage={selectedStage} />}
        {tab === 'files' && <FilesPanel projectId={projectId} onOpenArtifact={(id) => openPane({ type: 'artefact', id, from: 'files' })} />}
        {tab === 'codebase' && <CodebaseTab projectId={projectId} flow={flow} canWrite={canWrite} />}
        {tab === 'audit' && <AuditTab projectId={projectId} flow={flow} />}
        {tab === 'skills' && <SkillsTab projectId={projectId} selectedStage={selectedStage} />}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- artefacts */
function ArtefactsTab({ projectId, flow, selectedStage }: { projectId: string; flow: ProjectFlow | undefined; selectedStage: number | null }) {
  const openPane = useV2((s) => s.openPane);
  const [scope, setScope] = useState<'all' | 'stage'>('all');
  const q = useQuery({
    queryKey: ['artefacts', projectId],
    queryFn: () => api.get<{ artefacts: Artefact[] }>(`/api/projects/${projectId}/artefacts`),
    refetchInterval: 5_000,
  });
  const all = q.data?.artefacts ?? [];
  const stage = flow?.stages.find((s) => s.phase === selectedStage);
  const shown = scope === 'stage' && selectedStage != null ? all.filter((a) => a.phase === selectedStage) : all;
  const groups = (flow?.stages ?? []).map((s) => ({ s, list: shown.filter((a) => a.phase === s.phase) })).filter((g) => g.list.length > 0);
  return (
    <div data-testid="v2-artefacts-tab">
      <div className="mb-3 flex items-center justify-between">
        <div className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs font-semibold">
          <button type="button" aria-pressed={scope === 'all'} onClick={() => setScope('all')} className={`px-3 py-1 ${scope === 'all' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'}`}>All stages</button>
          <button type="button" aria-pressed={scope === 'stage'} onClick={() => setScope('stage')} className={`px-3 py-1 ${scope === 'stage' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'}`} disabled={!stage}>{stage?.name ?? 'This stage'}</button>
        </div>
        <span className="text-xs text-slate-500">{shown.length} artefact{shown.length === 1 ? '' : 's'}</span>
      </div>
      {q.isLoading && <div className="animate-pulse text-sm text-slate-400">Loading…</div>}
      {!q.isLoading && shown.length === 0 && <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">No artefacts yet. They appear here once a stage has generated.</div>}
      {groups.map(({ s, list }) => (
        <section key={s.phase} className="mb-4">
          <div className="mb-1.5 flex items-center gap-2">
            <StageDot n={s.phase} color={s.color} size={20} />
            <span className="text-sm font-semibold text-navy">{s.name}</span>
            <Pill tone={stageTone(s.color)}>{s.status === 'APPROVED' ? 'Approved' : s.status.replace(/_/g, ' ').toLowerCase()}</Pill>
          </div>
          <div className="space-y-1.5">
            {list.map((a) => (
              <button key={a.id} type="button" onClick={() => openPane({ type: 'artefact', id: a.id, from: 'artefacts' })} data-testid={`v2-art-${a.id}`}
                className="flex w-full items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-left hover:border-brand-400">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-slate-800" title={a.title}>{a.title}</span>
                  <span className="block text-xs text-slate-500">{a.type} · v{a.version}</span>
                </span>
                <span className="text-xs text-brand-600">Open ↗</span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- codebase */
function TreeNodes({ node, open, toggle, selected, onSelect, searching }: {
  node: CodeNode; open: Set<string>; toggle: (p: string) => void; selected: string | null; onSelect: (p: string) => void; searching: boolean;
}) {
  return (
    <ul className="text-sm">
      {(node.children ?? []).map((c) => c.type === 'dir' ? (
        <li key={c.path}>
          <button type="button" onClick={() => toggle(c.path)} className="flex w-full items-center gap-1.5 rounded px-1.5 py-0.5 text-left hover:bg-slate-100">
            <Icon name={searching || open.has(c.path) ? 'chevron-down' : 'chevron-right'} size={12} className="text-slate-400" />
            <Icon name="folder" size={13} className="text-brand-500" />
            <span className="truncate">{c.name}</span>
          </button>
          {(searching || open.has(c.path)) && <div className="ml-4 border-l border-slate-200 pl-1"><TreeNodes node={c} open={open} toggle={toggle} selected={selected} onSelect={onSelect} searching={searching} /></div>}
        </li>
      ) : (
        <li key={c.path}>
          <button type="button" onClick={() => onSelect(c.path)} aria-current={selected === c.path} data-file={c.path}
            className={`flex w-full items-center gap-1.5 rounded px-1.5 py-0.5 pl-6 text-left hover:bg-slate-100 ${selected === c.path ? 'bg-brand-50 text-brand-700' : ''}`}>
            <span aria-hidden="true">{iconFor(c.path)}</span><span className="truncate">{c.name}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function CodebaseTab({ projectId, flow, canWrite }: { projectId: string; flow: ProjectFlow | undefined; canWrite: boolean }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<'existing' | 'generated'>('existing');
  const codeStage = flow?.stages.find((s) => s.template === 6);
  return (
    <div data-testid="v2-codebase-tab">
      <div className="mb-3 inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs font-semibold">
        <button type="button" aria-pressed={mode === 'existing'} onClick={() => setMode('existing')} className={`px-3 py-1 ${mode === 'existing' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'}`}>Existing (uploaded)</button>
        <button type="button" aria-pressed={mode === 'generated'} onClick={() => setMode('generated')} className={`px-3 py-1 ${mode === 'generated' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'}`}>Generated ({codeStage ? `stage ${codeStage.phase}` : 'no code stage'})</button>
      </div>
      {mode === 'existing' ? <ExistingCodebase projectId={projectId} canWrite={canWrite} onChanged={() => void qc.invalidateQueries({ queryKey: ['codebase', projectId] })} /> : <GeneratedCodebase projectId={projectId} phase={codeStage?.phase ?? null} />}
    </div>
  );
}

function GeneratedCodebase({ projectId, phase }: { projectId: string; phase: number | null }) {
  const v = useQuery({
    queryKey: ['code', projectId, phase],
    queryFn: () => api.get<CodeViewData>(`/api/projects/${projectId}/phase/${phase}/code`),
    enabled: phase != null,
    refetchInterval: 5_000,
  });
  if (phase == null) return <div className="rounded-lg bg-slate-100 p-4 text-sm text-slate-500">This project has no code-writing stage.</div>;
  if (!v.data || !v.data.enabled || v.data.status === 'none') return <div className="rounded-lg bg-slate-100 p-4 text-sm text-slate-500">The code stage has not proposed a project structure yet. Once it does, the directories and files appear here.</div>;
  return <CodeExplorer projectId={projectId} phase={phase} />;
}

function ExistingCodebase({ projectId, canWrite, onChanged }: { projectId: string; canWrite: boolean; onChanged: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const list = useQuery({
    queryKey: ['codebase', projectId],
    queryFn: () => api.get<{ files: Array<{ id: string; path: string }> }>(`/api/projects/${projectId}/codebase`),
  });
  const files = list.data?.files ?? [];
  const tree = useMemo(() => buildPathTree(files.map((f) => f.path)), [files]);
  const shown = useMemo(() => (query.trim() ? filterTree(tree, query) : tree), [tree, query]);
  const sel = files.find((f) => f.path === selected);
  const content = useQuery({
    queryKey: ['codebase-file', projectId, sel?.id],
    queryFn: () => api.get<{ file: { id: string; path: string; content: string } }>(`/api/projects/${projectId}/codebase/${sel!.id}`),
    enabled: Boolean(sel),
  });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData(); form.append('file', file);
      const res = await fetch(`/api/projects/${projectId}/codebase`, { method: 'POST', credentials: 'include', body: form });
      const body = (await res.json()) as { files?: number; error?: { message?: string } };
      if (!res.ok) throw new Error(body.error?.message ?? 'Upload failed');
      return body;
    },
    onSuccess: (r) => { setMsg(`Indexed ${r.files} source files. The agents now ground on this codebase.`); onChanged(); },
    onError: (e) => setMsg(`${e instanceof Error ? e.message : 'Upload failed'}`),
  });
  const toggle = (p: string) => setOpen((cur) => { const n = new Set(cur); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  return (
    <div>
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-navy">{files.length ? `${files.length.toLocaleString()} files indexed` : 'No codebase uploaded'}</div>
            <div className="text-xs text-slate-500">Upload a .zip so the agents design and change code with your existing code in view (brownfield mode).</div>
          </div>
          <button type="button" onClick={() => input.current?.click()} disabled={!canWrite || upload.isPending} data-testid="v2-codebase-upload"
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">
            {upload.isPending ? 'Indexing…' : files.length ? 'Replace .zip' : 'Upload .zip'}
          </button>
          <input ref={input} type="file" accept=".zip" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ''; }} />
        </div>
        {msg && <div className="mt-2 text-xs text-slate-600" role="status">{msg}</div>}
      </div>
      {files.length > 0 && (
        <div className="mt-3">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search file names…" aria-label="Search the codebase"
            className="mb-2 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none" />
          <div className="grid gap-3 md:grid-cols-[minmax(180px,260px)_minmax(0,1fr)]">
            <div className="max-h-[28rem] overflow-auto rounded-lg border border-slate-200 bg-white p-1.5">
              {shown ? <TreeNodes node={shown} open={open} toggle={toggle} selected={selected} onSelect={setSelected} searching={Boolean(query.trim())} /> : <div className="p-2 text-xs text-slate-500">No file matches.</div>}
            </div>
            <div className="min-w-0">
              {!sel && <div className="rounded-lg bg-slate-100 p-4 text-sm text-slate-500">Select a file to read it.</div>}
              {sel && (
                <>
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-600">{sel.path}</span>
                    <a href={`/api/projects/${projectId}/codebase/${sel.id}/download`} download={sel.path.split('/').pop()} className="rounded-lg border border-slate-300 px-2 py-0.5 text-xs font-semibold text-slate-600 hover:border-brand-400">Download</a>
                  </div>
                  {content.isLoading ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> : (
                    <CodeView source={content.data?.file.content ?? ''} lang={langForExt(sel.path.includes('.') ? sel.path.slice(sel.path.lastIndexOf('.')) : '')} />
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- audit */
function AuditTab({ projectId, flow }: { projectId: string; flow: ProjectFlow | undefined }) {
  const [cat, setCat] = useState<AuditCategory>('All');
  const [stage, setStage] = useState<string>('all');
  const [openId, setOpenId] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ['audit', projectId],
    queryFn: () => api.get<{ events: AuditEvent[] }>(`/api/projects/${projectId}/audit`),
    refetchInterval: 5_000,
  });
  const events = (q.data?.events ?? []).filter((e) => (cat === 'All' || auditCategory(e) === cat) && (stage === 'all' || String(e.phase) === stage));
  function exportCsv() {
    const blob = new Blob([auditCsv(events)], { type: 'text/csv' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'audit.csv'; a.click(); URL.revokeObjectURL(a.href);
  }
  return (
    <div data-testid="v2-audit-tab">
      <div className="mb-2 flex items-center gap-2 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" />Live · refreshes every 5 s</span>
        <button type="button" onClick={exportCsv} className="ml-auto rounded-lg border border-slate-300 px-2.5 py-1 font-semibold text-slate-600 hover:border-brand-400">Export CSV</button>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {AUDIT_CATEGORIES.map((c) => (
          <button key={c} type="button" onClick={() => setCat(c)} aria-pressed={cat === c} data-testid={`v2-audit-cat-${c}`}
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${cat === c ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{c}</button>
        ))}
        <select value={stage} onChange={(e) => setStage(e.target.value)} aria-label="Stage" className="rounded-lg border border-slate-300 px-2 py-0.5 text-xs">
          <option value="all">All stages</option>
          {(flow?.stages ?? []).map((s) => <option key={s.phase} value={s.phase}>Stage {s.phase}</option>)}
        </select>
      </div>
      {q.isLoading && <div className="animate-pulse text-sm text-slate-400">Loading…</div>}
      {!q.isLoading && events.length === 0 && <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">No events match.</div>}
      <div className="space-y-2">
        {events.map((e) => {
          const open = openId === e.id;
          return (
            <div key={e.id} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
              <button type="button" onClick={() => setOpenId(open ? null : e.id)} aria-expanded={open} className="flex w-full items-center justify-between gap-2 text-left">
                <span className="font-semibold text-slate-800">{e.event}</span>
                <span className="text-slate-400">{new Date(e.timestamp).toLocaleTimeString()} {open ? '▾' : '▸'}</span>
              </button>
              <div className="mt-0.5 text-slate-500">
                {e.agentRole}{e.phase ? ` · P${e.phase}` : ''}{e.provider ? ` · ${e.provider}/${e.model}` : ''}{e.promptTokens != null ? ` · ${e.promptTokens}→${e.completionTokens} tok` : ''}
              </div>
              {e.humanReviewer && <div className="mt-0.5 text-emerald-700">👤 {e.humanReviewer}</div>}
              {Array.isArray(e.detail?.artifacts) && (e.detail.artifacts as Array<Record<string, string>>).length > 0 && (
                <ul className="mt-1 space-y-0.5 rounded bg-slate-50 p-1.5 text-[11px] text-slate-600" aria-label="Artefacts and formats selected">
                  {(e.detail.artifacts as Array<Record<string, string>>).map((a) => <li key={a.artifact}><span className="font-medium text-slate-700">{a.artifact}</span> — {a.layout} · {a.deliveredAs}</li>)}
                </ul>
              )}
              {open && (
                <pre className="mt-2 max-h-56 overflow-auto rounded bg-slate-50 p-2 font-mono text-[11px] text-slate-700" data-testid="v2-audit-detail">{JSON.stringify({ event: e.event, phase: e.phase, agentRole: e.agentRole, artefactHash: e.artefactHash, detail: e.detail }, null, 2)}</pre>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-slate-400">Events are append-only. Each carries a hash of the artefact it relates to.</p>
    </div>
  );
}

/* ---------------------------------------------------------------- skills */
function SkillsTab({ projectId, selectedStage }: { projectId: string; selectedStage: number | null }) {
  const [kbQuery, setKbQuery] = useState('');
  const providers = useQuery({
    queryKey: ['providers'],
    queryFn: () => api.get<{ llm: Array<{ provider: string; configured: boolean; breaker: string }>; tools: Record<string, string>; generationMode?: string; effectiveMock?: boolean; activeProviders?: string[] }>('/api/providers'),
    staleTime: 60_000,
  });
  const tools = useQuery({
    queryKey: ['skills'],
    queryFn: () => api.get<{ tools: Array<{ name: string; description: string }> }>('/api/skills'),
    staleTime: 300_000,
  });
  const kb = useQuery({
    queryKey: ['kb', kbQuery],
    queryFn: () => api.get<{ standards: Array<{ id: string; title: string; body: string }> }>(`/api/kb/search?q=${encodeURIComponent(kbQuery)}`),
  });
  const p = providers.data;
  return (
    <div data-testid="v2-skills-tab" className="space-y-5">
      <section>
        <h3 className="mb-1.5 text-sm font-semibold text-navy">Skills for this stage</h3>
        <SkillsPanel projectId={projectId} focusedPhase={selectedStage} />
      </section>
      <section>
        <h3 className="mb-1.5 flex items-center gap-2 text-sm font-semibold text-navy">
          Connected services
          {p && <Pill tone={p.effectiveMock ? 'amber' : 'green'} title={`GENERATION_MODE=${p.generationMode ?? 'auto'}`}>{p.effectiveMock ? 'Mock generation' : `Real LLM · ${(p.activeProviders ?? []).join(', ') || 'configured'}`}</Pill>}
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {(p?.llm ?? []).map((x) => (
            <span key={x.provider} title={x.configured ? `breaker: ${x.breaker}` : 'no API key configured'}
              className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${x.provider === 'mock' ? 'bg-slate-200 text-slate-600' : x.configured && x.breaker === 'closed' ? 'bg-emerald-100 text-emerald-700' : x.configured ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-400'}`}>
              {x.provider} {x.configured ? (x.breaker === 'closed' ? '● live' : `⚠ ${x.breaker}`) : '○ not set up'}
            </span>
          ))}
          {Object.entries(p?.tools ?? {}).map(([name, mode]) => (
            <span key={name} className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${mode === 'live' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-600'}`}>{name} {mode === 'live' ? '● live' : '○ mock'}</span>
          ))}
        </div>
      </section>
      <section>
        <h3 className="mb-1.5 text-sm font-semibold text-navy">Enterprise knowledge base</h3>
        <input value={kbQuery} onChange={(e) => setKbQuery(e.target.value)} placeholder="Search standards and allow-lists…" aria-label="Search the knowledge base"
          className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none" />
        <div className="mt-2 space-y-1.5">
          {(kb.data?.standards ?? []).map((s) => <div key={s.id} className="rounded-lg bg-slate-100 p-2 text-xs"><div className="font-semibold text-slate-700">{s.title}</div><div className="mt-0.5 text-slate-500">{s.body}</div></div>)}
        </div>
      </section>
      <section>
        <h3 className="mb-1.5 text-sm font-semibold text-navy">Active tools ({tools.data?.tools.length ?? 0})</h3>
        <div className="space-y-1">
          {(tools.data?.tools ?? []).map((t) => <div key={t.name} className="rounded-lg border border-slate-200 bg-white p-2 text-xs"><div className="font-mono font-semibold text-brand-700">{t.name}</div><div className="mt-0.5 text-slate-500">{t.description}</div></div>)}
        </div>
      </section>
    </div>
  );
}
