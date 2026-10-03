import { createPortal } from 'react-dom';
import { useQueries } from '@tanstack/react-query';
import { api } from '../api/client';
import DocumentViewer from './DocumentViewer';
import type { DocItem } from '../lib/docModel';

interface ArtefactRef { id: string; type: string; title: string; phase: number }

/**
 * Loads every artifact of a stage (read-only GETs of already-stored content), then shows them
 * as one document. This is the ONLY network access in the document feature; exporting itself
 * (DocumentViewer + lib/docExport) makes no requests.
 */
export function StageDocument({
  projectId, artefacts, title, subtitle, onClose,
}: { projectId: string; artefacts: ArtefactRef[]; title: string; subtitle?: string; onClose: () => void }) {
  const results = useQueries({
    queries: artefacts.map((a) => ({
      queryKey: ['artefact', projectId, a.id],
      queryFn: () => api.get<{ artefact: { id: string; type: string; title: string; content: string; storageKey?: string | null } }>(
        `/api/projects/${projectId}/artefacts/${a.id}`),
    })),
  });
  const loading = results.some((r) => r.isLoading);
  const items: DocItem[] = results.flatMap((r) => {
    const a = r.data?.artefact;
    if (!a) return [];
    const dot = a.storageKey ? a.storageKey.lastIndexOf('.') : -1;
    return [{ id: a.id, type: a.type, title: a.title, content: a.content ?? '', ext: dot > 0 ? a.storageKey!.slice(dot).toLowerCase() : '' }];
  });
  if (loading) {
    return createPortal(
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/30" onClick={onClose}>
        <div className="animate-pulse rounded-lg bg-white px-6 py-4 text-sm text-slate-500">Loading {artefacts.length} artifact(s)…</div>
      </div>, document.body);
  }
  return <DocumentViewer title={title} subtitle={subtitle} items={items} onClose={onClose} />;
}
