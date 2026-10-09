import { useQuery } from '@tanstack/react-query';
import MiddleText from './MiddleText';
import { useState } from 'react';
import { api } from '../api/client';
import { Pill } from './bits';

interface TreeFile { name: string; ext: string; artefactId?: string; codebaseFileId?: string; sizeBytes?: number }
interface Tree { root: string; storageMode: string; folders: Array<{ name: string; kind: 'phase' | 'codebase'; phase: number | null; files: TreeFile[] }> }

const ICON: Record<string, string> = { '.md': '📝', '.mmd': '📐', '.json': '🧾', '.yaml': '⚙️', '.yml': '⚙️', '.ts': '💻', '.js': '💻', '.py': '💻', '.java': '💻', '.sql': '🗄️', '.dbml': '🗄️', '.puml': '📐', '.dsl': '📐', '.txt': '📄' };
const size = (n?: number) => (n == null ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`);

/** The stored files exactly as they are on disk or in the bucket, in their phase folders. */
export default function ProjectFiles({ projectId, onOpenArtifact }: { projectId: string; onOpenArtifact: (id: string) => void }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState('');
  const [codeFile, setCodeFile] = useState<{ path: string; content: string } | null>(null);
  const tree = useQuery({ queryKey: ['files', projectId], queryFn: () => api.get<Tree>(`/api/projects/${projectId}/files`), refetchInterval: 7_000 });
  if (!tree.data) return <div className="animate-pulse text-sm text-slate-400">Loading files…</div>;
  const q = filter.trim().toLowerCase();
  async function openCode(id: string) {
    const r = await api.get<{ file: { path: string; content: string } }>(`/api/projects/${projectId}/codebase/${id}`);
    setCodeFile(r.file);
  }
  return (
    <div data-testid="v2-files-tab">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs"><Pill>Storage: {tree.data.storageMode.toUpperCase()}</Pill><MiddleText text={tree.data.root} className="flex-1 font-mono text-slate-500" /></div>
      <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter files…" aria-label="Filter files" className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none" />
      {tree.data.folders.length === 0 && <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">No files yet.</div>}
      <ul className="space-y-0.5">
        {tree.data.folders.map((f) => {
          const files = q ? f.files.filter((x) => x.name.toLowerCase().includes(q)) : f.files;
          if (q && files.length === 0) return null;
          const expanded = q ? true : open[f.name] ?? false;
          return (
            <li key={f.name}>
              <button type="button" onClick={() => setOpen((o) => ({ ...o, [f.name]: !expanded }))} aria-expanded={expanded} className="flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-sm hover:bg-slate-100">
                <span className="w-3 text-xs text-slate-400">{expanded ? '▾' : '▸'}</span><span>📁</span><span className="font-medium text-slate-900">{f.name}</span><span className="text-slate-500">({f.files.length}{f.kind === 'codebase' ? ' files, uploaded' : ''})</span>
              </button>
              {expanded && (
                <ul className="ml-6 border-l border-slate-200 pl-2">
                  {files.map((x) => (
                    <li key={x.name}>
                      <button type="button" onClick={() => (x.artefactId ? onOpenArtifact(x.artefactId) : x.codebaseFileId ? void openCode(x.codebaseFileId) : undefined)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-sm hover:bg-slate-100">
                        <span aria-hidden="true">{ICON[x.ext] ?? '📄'}</span><MiddleText text={x.name} className="flex-1 font-mono text-[13px] text-slate-800" />
                        {x.sizeBytes != null && <Pill>{size(x.sizeBytes)}</Pill>}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {codeFile && (
        <div className="mt-3 rounded-xl border border-slate-200 bg-white">
          <div className="flex items-center gap-2 border-b border-slate-200 px-3 py-2"><MiddleText text={codeFile.path} className="flex-1 font-mono text-xs text-slate-600" /><button type="button" onClick={() => setCodeFile(null)} className="text-xs text-slate-500 hover:text-navy">Close</button></div>
          <pre className="max-h-80 overflow-auto p-3 font-mono text-xs text-slate-800">{codeFile.content}</pre>
        </div>
      )}
    </div>
  );
}
