import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import CodeAssistant from '../components/CodeAssistant';
import CodeView from '../components/CodeView';
import { allDirs, langForPath, type CodeFile, type CodeNode, type CodeView as CodeViewData } from '../lib/codeTree';
import { TreeNodes } from './CodeTree';
import MiddleText from './MiddleText';
import SplitPair from './SplitPair';
import { Pill } from './bits';

const STATUS_LABEL: Record<string, string> = { proposed: 'Structure proposed', approved: 'Writing code', implemented: 'Awaiting code review', committed: 'Committed' };

/**
 * The project the implementation stage writes, as the design draws it: the stage and its state, "Open stage" and "Download .zip",
 * the tree with a planned / written tag on every file, and on the right either why the selected file exists (before it is written)
 * or the code itself. The code assistant sits below.
 */
export default function GeneratedCode({ projectId, phase, canWrite, onOpenStage }: { projectId: string; phase: number; canWrite: boolean; onOpenStage?: (seq: number) => void }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ['code', projectId, phase],
    queryFn: () => api.get<CodeViewData>(`/api/projects/${projectId}/phase/${phase}/code`),
    refetchInterval: 5_000,
  });
  const v = q.data;
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [checkedList, setCheckedList] = useState<string[]>([]);
  const checked = useMemo(() => new Set(checkedList), [checkedList]);
  const tick = (p: string) => setCheckedList((c) => (c.includes(p) ? c.filter((x) => x !== p) : [...c, p]));
  const tree = v?.tree ?? null;
  const files = v?.files ?? [];
  const sel: CodeFile | undefined = files.find((f) => f.path === selected);
  const written = files.filter((f) => f.generated).length;

  // The design opens with every folder unfolded and a file shown.
  useEffect(() => {
    if (!tree || files.length === 0) return;
    if (!selected || !files.some((f) => f.path === selected)) setSelected((files.find((f) => f.generated) ?? files[0]!).path);
    const dirs = allDirs(tree);
    setOpen((cur) => (cur.size ? cur : new Set(dirs.length <= 40 ? dirs : dirs.filter((d) => d.split('/').length === 1))));
  }, [tree, files, selected]);

  const content = useQuery({
    queryKey: ['code-file', projectId, sel?.artefactId],
    queryFn: () => api.get<{ artefact: { content: string } }>(`/api/projects/${projectId}/artefacts/${sel!.artefactId}`),
    enabled: Boolean(sel?.generated && sel.artefactId),
    staleTime: 30_000,
  });
  if (!v || !tree) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;

  const toggle = (p: string) => setOpen((cur) => { const n = new Set(cur); if (n.has(p)) n.delete(p); else n.add(p); return n; });
  const text = content.data?.artefact.content ?? '';
  const download = () => {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    a.download = sel!.path.split('/').pop() ?? 'file'; a.click(); URL.revokeObjectURL(a.href);
  };
  const badge = (n: CodeNode) => <Pill>{n.generated ? 'written' : 'planned'}</Pill>;

  return (
    <div data-testid="v2-generated-code">
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <Pill>Stage {phase} · {STATUS_LABEL[v.status] ?? v.status}</Pill>
        {v.branch && <span className="font-mono text-xs text-slate-500" title="The branch the code is committed to">{v.branch}</span>}
        <span className="ml-auto flex items-center gap-2">
          {onOpenStage && <button type="button" onClick={() => onOpenStage(phase)} className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400" data-testid="v2-open-code-stage">Open stage {phase}</button>}
          {written > 0
            ? <a href={`/api/projects/${projectId}/phase/${phase}/code.zip`} download className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400">Download .zip</a>
            : <span className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-400">Download .zip</span>}
        </span>
      </div>

      <SplitPair storageKey="sdlc:v2:codebase-split" def={300} min={160} max={900} label="Resize the file tree"
        left={(
          <div className="h-[30rem] overflow-auto rounded-xl border border-slate-300 bg-white p-2" data-testid="v2-code-tree">
            <TreeNodes node={tree} open={open} toggle={toggle} selected={selected} onSelect={setSelected} searching={false}
              checked={checked} onCheck={canWrite ? tick : undefined} badge={badge} />
          </div>
        )}
        right={(
          <div className="min-w-0">
            {!sel && <div className="rounded-lg bg-slate-100 p-4 text-sm text-slate-500">Select a file to see why it exists.</div>}
            {sel && sel.generated && (
              <>
                <div className="mb-2 flex items-center gap-2">
                  <MiddleText text={sel.path} className="flex-1 font-mono text-xs text-slate-500" />
                  <button type="button" onClick={() => void navigator.clipboard?.writeText(text)} className="rounded-lg border border-slate-300 px-3 py-1 text-sm text-slate-800 hover:border-brand-400">Copy</button>
                  <button type="button" onClick={download} disabled={!text} className="rounded-lg border border-slate-300 px-3 py-1 text-sm text-slate-800 hover:border-brand-400 disabled:opacity-50">Download</button>
                </div>
                {content.isLoading ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> : <CodeView tone="light" bare source={text} lang={langForPath(sel.path)} />}
              </>
            )}
            {sel && !sel.generated && (
              <>
                <h3 className="mb-1.5 break-all text-base font-semibold text-navy">{sel.path}</h3>
                <dl className="mb-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-1 text-sm">
                  <dt className="text-slate-500">Purpose</dt><dd className="text-slate-800">{sel.purpose}</dd>
                  <dt className="text-slate-500">Kind</dt><dd className="text-slate-800">{sel.kind}</dd>
                  {sel.layer && <><dt className="text-slate-500">Layer</dt><dd className="text-slate-800">{sel.layer}</dd></>}
                </dl>
                <p className="text-sm text-slate-500">{v.status === 'approved' ? 'Written file by file. The tree shows progress.' : 'This file is written after the structure is approved. Say what to change in the stage box before approving.'}</p>
              </>
            )}
          </div>
        )} />

      {written > 0 && (
        <CodeAssistant projectId={projectId} scope="generated" checked={checkedList} onUncheck={tick} onClearChecked={() => setCheckedList([])}
          openFile={sel?.generated ? sel.path : null} onOpenFile={setSelected} canEdit={canWrite}
          blockedReason={v.status === 'committed' ? 'The code is approved and committed. Request changes on the stage first, then edit it here.' : undefined}
          onFilesChanged={() => { void qc.invalidateQueries({ queryKey: ['code', projectId, phase] }); void qc.invalidateQueries({ queryKey: ['code-file', projectId] }); }} />
      )}
    </div>
  );
}
