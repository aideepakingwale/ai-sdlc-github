import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import CodeView, { langForExt } from './CodeView';

interface TreeFile {
  name: string;
  ext: string;
  artefactId?: string;
  codebaseFileId?: string;
  type?: string;
  title?: string;
  sizeBytes?: number;
  storageKey?: string;
}

interface Tree {
  root: string;
  storageMode: string;
  folders: Array<{ name: string; kind: 'phase' | 'codebase'; phase: number | null; files: TreeFile[] }>;
}

const EXT_ICON: Record<string, string> = {
  '.md': '📝', '.mmd': '📐', '.json': '🧾', '.yaml': '⚙️', '.yml': '⚙️',
  '.ts': '💻', '.js': '💻', '.py': '💻', '.go': '💻', '.java': '💻', '.cs': '💻',
  '.sql': '🗄️', '.dbml': '🗄️', '.puml': '📐', '.dsl': '📐', '.txt': '📄',
};

/**
 * File explorer over the dedicated storage layer: artifacts as real
 * files inside phase folders (exact on-disk/S3 names), plus the uploaded
 * codebase. Clicking a file opens the viewer matched to its file type.
 */
export default function FilesPanel({
  projectId,
  onOpenArtifact,
}: {
  projectId: string;
  onOpenArtifact: (id: string) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [codeFile, setCodeFile] = useState<{ id: string; path: string; content: string } | null>(null);

  const tree = useQuery({
    queryKey: ['files', projectId],
    queryFn: () => api.get<Tree>(`/api/projects/${projectId}/files`),
    refetchInterval: 7_000,
  });

  async function openCodebaseFile(id: string) {
    const res = await api.get<{ file: { id: string; path: string; content: string } }>(
      `/api/projects/${projectId}/codebase/${id}`,
    );
    setCodeFile(res.file);
  }

  if (!tree.data) return <div className="animate-pulse p-2 text-xs text-slate-400">Loading file tree…</div>;

  return (
    <div className="space-y-1">
      <div className="mb-1 truncate font-mono text-[10px] text-slate-400" title={tree.data.root}>
        📦 {tree.data.root} <span className="text-slate-300">({tree.data.storageMode})</span>
      </div>
      {tree.data.folders.length === 0 && (
        <div className="rounded bg-slate-100 p-2 text-center text-xs text-slate-400">No files yet.</div>
      )}
      {tree.data.folders.map((folder) => {
        const expanded = open[folder.name] ?? true;
        return (
          <div key={folder.name}>
            <button
              onClick={() => setOpen((o) => ({ ...o, [folder.name]: !expanded }))}
              className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-xs font-semibold text-slate-700 hover:bg-slate-100"
            >
              <span>{expanded ? '📂' : '📁'}</span>
              {folder.name}/
              <span className="font-normal text-slate-400">({folder.files.length})</span>
            </button>
            {expanded && (
              <div className="ml-4 space-y-0.5 border-l border-slate-200 pl-2">
                {folder.files.map((f) => (
                  <button
                    key={f.artefactId ?? f.codebaseFileId ?? f.name}
                    onClick={() => (f.artefactId ? onOpenArtifact(f.artefactId) : f.codebaseFileId && void openCodebaseFile(f.codebaseFileId))}
                    className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-[11px] text-slate-600 hover:bg-brand-50 hover:text-brand-700"
                    title={f.title ?? f.name}
                  >
                    <span>{EXT_ICON[f.ext] ?? '📄'}</span>
                    <span className="truncate font-mono">{f.name}</span>
                    {f.sizeBytes != null && (
                      <span className="ml-auto shrink-0 text-[9px] text-slate-400">{(f.sizeBytes / 1024).toFixed(1)}kB</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {/* code viewer for codebase files */}
      {codeFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setCodeFile(null)}>
          <div
            className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-2.5">
              <span>💻</span>
              <span className="min-w-0 flex-1 truncate font-mono text-sm text-slate-800">{codeFile.path}</span>
              <a
                href={`/api/projects/${projectId}/codebase/${codeFile.id}/download`}
                download={codeFile.path.split('/').pop()}
                className="rounded-lg bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:bg-brand-100 hover:text-brand-700"
                title="Download — opens with the application installed on your desktop"
              >
                ⬇ Open externally
              </a>
              <button onClick={() => setCodeFile(null)} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">
                ✕
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              <CodeView
                source={codeFile.content}
                lang={langForExt(codeFile.path.includes('.') ? codeFile.path.slice(codeFile.path.lastIndexOf('.')) : '')}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
