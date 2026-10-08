import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import CodeView from './CodeView';
import SplitPair from '../v2/SplitPair';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { Icon } from './ui/Icon';
import {
  KIND_BADGE, allDirs, countFiles, filterTree, iconFor, langForPath, steps,
  type CodeFile, type CodeNode, type CodeView as CodeViewData,
} from '../lib/codeTree';


/**
 * Two-step code generation, visible: the journey (propose → approve structure → write code → approve & commit),
 * a collapsible file explorer over the directory structure with each file's purpose, the file's content once
 * written, and the whole codebase as a .zip. Approval itself happens at the stage gate (its reviewers).
 */
export default function CodeExplorer({ projectId, phase }: { projectId: string; phase: number }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ['code', projectId, phase],
    queryFn: () => api.get<CodeViewData>(`/api/projects/${projectId}/phase/${phase}/code`),
    refetchInterval: 5_000,
  });
  const v = q.data;
  const [open, setOpen] = useState<Set<string> | null>(null);       // null = default (top level open)
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [showPlan, setShowPlan] = useState(true);

  const tree = v?.tree ?? null;
  const shown = useMemo(() => (tree ? filterTree(tree, query) : null), [tree, query]);
  const files = v?.files ?? [];
  const sel: CodeFile | undefined = files.find((f) => f.path === selected) ?? undefined;
  useEffect(() => { if (!selected && files.length) setSelected(files.find((f) => f.generated)?.path ?? files[0]!.path); }, [files, selected]);

  const reset = useMutation({
    mutationFn: (reason: string) => api.post(`/api/projects/${projectId}/phase/${phase}/code/reset`, { reason }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['code', projectId, phase] }); void qc.invalidateQueries({ queryKey: ['flow', projectId] }); },
  });

  if (!v || !v.enabled || v.status === 'none' || !tree) return null;
  const stepList = steps(v);
  const counts = countFiles(tree);
  const searching = query.trim().length > 0;
  const isOpen = (p: string) => searching || (open ? open.has(p) : p.split('/').length === 1);
  const toggle = (p: string) => setOpen((cur) => {
    const base = cur ?? new Set(allDirs(tree).filter((d) => d.split('/').length === 1));
    const n = new Set(base); if (n.has(p)) n.delete(p); else n.add(p); return n;
  });
  const banner = {
    proposed: ['Step 1 of 2 — review the proposed structure', 'No code has been written yet. Approving the stage at the gate below approves THIS structure and starts code generation; request changes to have it revised.', 'warning'],
    approved: ['Structure approved — code is being written', 'The agent writes exactly the files listed here. Nothing outside the approved structure is accepted.', 'info'],
    implemented: ['Step 2 of 2 — review the generated code', 'Approving the stage at the gate below approves the code and commits it to GitHub.', 'warning'],
    committed: ['Code approved and committed', `Committed to branch ${v.branch ?? ''}${v.commitRef ? ` (${String(v.commitRef).slice(0, 10)})` : ''}.`, 'success'],
  }[v.status as 'proposed' | 'approved' | 'implemented' | 'committed'];
  const tone = { warning: 'border-amber-300 bg-amber-50 text-amber-900', info: 'border-blue-200 bg-blue-50 text-blue-900', success: 'border-emerald-200 bg-emerald-50 text-emerald-900' }[banner[2] as 'warning'];

  return (
    <section className="rounded-xl border border-slate-200 bg-white" data-testid="code-explorer">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-600"><Icon name="code" size={15} /></span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-800">Code structure & files <span className="font-normal text-slate-400">· v{v.version}</span></h3>
          <p className="text-xs text-slate-500">{counts.files} files · {counts.generated} written{v.branch ? ` · branch ${v.branch}` : ''}</p>
        </div>
        {counts.generated > 0 && (
          <a href={`/api/projects/${projectId}/phase/${phase}/code.zip`} download
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400 hover:text-brand-700"
            title="Download the whole codebase as one .zip">
            <Icon name="download" size={13} /> Download .zip
          </a>
        )}
        {v.canReset && v.status !== 'committed' && (
          <Button size="sm" variant="ghost" icon="refresh" loading={reset.isPending}
            onClick={() => { const why = window.prompt('Propose a new structure? The current one (and its approval) is discarded. Reason (optional):'); if (why !== null) reset.mutate(why); }}
            title="Discard this structure and have the agent propose a new one">Re-plan structure</Button>
        )}
      </div>

      <ol className="flex flex-wrap gap-x-2 gap-y-1 border-b border-slate-100 px-4 py-2" aria-label="Steps">
        {stepList.map((s, i) => (
          <li key={s.id} title={s.hint} className="flex items-center gap-1.5 text-[11px]">
            <span className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold ${s.state === 'done' ? 'bg-emerald-500 text-white' : s.state === 'current' ? 'bg-brand-600 text-white' : 'bg-slate-200 text-slate-500'}`}>{s.state === 'done' ? '✓' : i + 1}</span>
            <span className={s.state === 'todo' ? 'text-slate-400' : 'font-semibold text-slate-700'}>{s.label}</span>
            {i < stepList.length - 1 && <span className="text-slate-300">→</span>}
          </li>
        ))}
      </ol>
      <div className={`mx-4 mt-3 rounded-lg border px-3 py-2 text-xs ${tone}`}><div className="font-semibold">{banner[0]}</div><div>{banner[1]}</div></div>

      {v.structure && (v.structure.summary || v.structure.conventions.length > 0) && (
        <div className="mx-4 mt-3">
          <button type="button" onClick={() => setShowPlan((s) => !s)} aria-expanded={showPlan} className="text-xs font-semibold text-slate-600 hover:text-brand-700">
            {showPlan ? '▾' : '▸'} Architecture and naming conventions
          </button>
          {showPlan && (
            <div className="mt-1 rounded-lg bg-slate-50 p-3 text-xs text-slate-700">
              {v.structure.summary && <p className="mb-2">{v.structure.summary}</p>}
              <ul className="list-disc space-y-0.5 pl-4">{v.structure.conventions.map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
          )}
        </div>
      )}

      <SplitPair className="mt-3 min-h-[360px] border-t border-slate-100" storageKey="sdlc:v2:code-split" def={320} min={200} max={620} label="Resize the file tree"
        left={(
          <div className="p-3">
          <div className="mb-2 flex items-center gap-1.5">
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a file or purpose…" aria-label="Find a file"
              className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-xs focus:border-brand-400 focus:outline-none" />
            <button type="button" className="rounded px-1.5 py-1 text-[11px] text-slate-500 hover:bg-slate-100" onClick={() => setOpen(new Set(allDirs(tree)))} title="Expand all folders">Expand</button>
            <button type="button" className="rounded px-1.5 py-1 text-[11px] text-slate-500 hover:bg-slate-100" onClick={() => setOpen(new Set())} title="Collapse all folders">Collapse</button>
          </div>
          <div role="tree" aria-label="Directory structure" className="max-h-[520px] overflow-auto pr-1 text-xs">
            {shown && (shown.children ?? []).map((n) => <Row key={n.path} node={n} depth={0} isOpen={isOpen} toggle={toggle} selected={selected} onSelect={setSelected} />)}
            {shown && (shown.children ?? []).length === 0 && <div className="py-4 text-center text-slate-400">No file matches.</div>}
          </div>
          </div>
        )}
        right={(
          <div className="min-w-0 p-3">
          {sel ? <FileDetail projectId={projectId} file={sel} /> : <div className="py-10 text-center text-xs text-slate-400">Select a file.</div>}
          </div>
        )} />
    </section>
  );
}

function Row({ node, depth, isOpen, toggle, selected, onSelect }: {
  node: CodeNode; depth: number; isOpen: (p: string) => boolean; toggle: (p: string) => void; selected: string | null; onSelect: (p: string) => void;
}) {
  const pad = { paddingLeft: 6 + depth * 14 };
  if (node.type === 'dir') {
    const open = isOpen(node.path);
    const c = countFiles(node);
    return (
      <div role="treeitem" aria-expanded={open}>
        <button type="button" onClick={() => toggle(node.path)} style={pad} data-dir={node.path}
          className="flex w-full items-center gap-1.5 rounded py-0.5 pr-1 text-left hover:bg-slate-50" title={node.purpose || node.path}>
          <span className="w-3 text-slate-400">{open ? '▾' : '▸'}</span><span>{open ? '📂' : '📁'}</span>
          <span className="truncate font-semibold text-slate-700">{node.name}</span>
          <span className="ml-auto shrink-0 text-[10px] text-slate-400">{c.generated}/{c.files}</span>
        </button>
        {open && <div role="group">{(node.children ?? []).map((n) => <Row key={n.path} node={n} depth={depth + 1} isOpen={isOpen} toggle={toggle} selected={selected} onSelect={onSelect} />)}</div>}
      </div>
    );
  }
  const active = selected === node.path;
  return (
    <button type="button" role="treeitem" aria-selected={active} data-file={node.path} onClick={() => onSelect(node.path)} style={pad}
      className={`flex w-full items-center gap-1.5 rounded py-0.5 pr-1 text-left ${active ? 'bg-brand-50 ring-1 ring-brand-200' : 'hover:bg-slate-50'}`} title={node.purpose}>
      <span className="w-3" /><span>{iconFor(node.path)}</span>
      <span className="min-w-0 flex-1"><span className="block truncate text-slate-800">{node.name}</span><span className="block truncate text-[10px] text-slate-400">{node.purpose}</span></span>
      <span className={`shrink-0 text-[11px] ${node.generated ? 'text-emerald-500' : 'text-slate-300'}`} title={node.generated ? 'Written' : 'Planned — not written yet'}>{node.generated ? '✓' : '○'}</span>
    </button>
  );
}

function FileDetail({ projectId, file }: { projectId: string; file: CodeFile }) {
  const content = useQuery({
    queryKey: ['code-file', projectId, file.artefactId],
    queryFn: () => api.get<{ artefact: { content: string } }>(`/api/projects/${projectId}/artefacts/${file.artefactId}`),
    enabled: Boolean(file.artefactId),
    staleTime: 30_000,
  });
  const badge = KIND_BADGE[file.kind] ?? KIND_BADGE.source!;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-semibold text-slate-800 [overflow-wrap:anywhere]">{file.path}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${badge.cls}`}>{badge.label}</span>
        {file.layer && <Badge>{file.layer}</Badge>}
      </div>
      <p className="mb-3 text-xs text-slate-600"><span className="font-semibold text-slate-700">Purpose:</span> {file.purpose}</p>
      {file.generated && content.data ? (
        <CodeView source={content.data.artefact.content ?? ''} lang={langForPath(file.path)} filename={file.path} showDiagnostics={false} />
      ) : file.generated ? (
        <div className="animate-pulse py-8 text-center text-xs text-slate-400">Loading file…</div>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-xs text-slate-500">
          This file is planned but not written yet.<br />It is written after the structure is approved.
        </div>
      )}
    </div>
  );
}
