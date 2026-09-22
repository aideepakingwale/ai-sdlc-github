import { useEffect, useMemo, useRef, useState } from 'react';
import DrawioEditor from './DrawioEditor';
import { LivePreview, type DiagramKind } from './viewerRegistry';

/**
 * Unified Diagram Workbench — one editing experience for every diagram type.
 *
 * - Text formats (Mermaid, PlantUML, Structurizr/C4, SVG): a split pane with the
 *   source on the left and a live preview on the right that re-renders as you
 *   type, plus Save-in-place, Cancel and Export SVG.
 * - draw.io: the visual (WYSIWYG) editor, unchanged.
 *
 * The preview reuses the exact renderers the read-only viewers use, so what you
 * see is what gets saved. For Structurizr the source of truth stays the DSL; the
 * preview shows the converted C4.
 */
export default function DiagramWorkbench({
  kind,
  source,
  saving,
  onSave,
  onExit,
}: {
  kind: DiagramKind;
  source: string;
  saving: boolean;
  onSave: (source: string) => void;
  onExit: () => void;
}) {
  const [draft, setDraft] = useState(source);
  const [preview, setPreview] = useState(source);
  const previewRef = useRef<HTMLDivElement>(null);

  // Debounce the preview so it doesn't re-render on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setPreview(draft), 300);
    return () => clearTimeout(t);
  }, [draft]);

  const dirty = draft !== source;
  const hint = useMemo(() => {
    if (kind === 'structurizr') return 'Editing Structurizr DSL — the live preview shows the converted C4.';
    if (kind === 'plantuml') return 'PlantUML renders server-side; preview updates a moment after you pause.';
    return 'Live preview on the right. Secrets/PII are masked automatically on save.';
  }, [kind]);

  // draw.io keeps its dedicated visual editor.
  if (kind === 'drawio') {
    return (
      <DrawioEditor xml={source} saving={saving} onSave={(x) => onSave(x)} onExit={onExit} />
    );
  }

  function exportSvg() {
    const host = previewRef.current;
    if (!host) return;
    const svg = host.querySelector('svg');
    let href: string | null = null;
    let revoke: string | null = null;
    if (svg) {
      const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' });
      href = revoke = URL.createObjectURL(blob);
    } else {
      const img = host.querySelector('img');
      if (img?.src) href = img.src; // same-origin (/plantuml/svg/…)
    }
    if (!href) return;
    const a = document.createElement('a');
    a.href = href;
    a.download = 'diagram.svg';
    document.body.appendChild(a);
    a.click();
    a.remove();
    if (revoke) setTimeout(() => URL.revokeObjectURL(revoke!), 1000);
  }

  return (
    <div className="flex h-full min-h-[60vh] flex-col gap-2">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span className="rounded bg-brand-50 px-2 py-0.5 font-semibold text-brand-700">Editing · {kind}</span>
        <span className="truncate">{hint}</span>
        <span className="ml-auto flex shrink-0 gap-2">
          <button
            onClick={exportSvg}
            className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-600 hover:border-brand-400 hover:text-brand-700"
            title="Download the current preview as an SVG"
          >
            ⬇ Export SVG
          </button>
          <button
            onClick={() => onSave(draft)}
            disabled={saving || !dirty}
            className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            {saving ? 'Saving…' : '💾 Save'}
          </button>
          <button
            onClick={onExit}
            disabled={saving}
            className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-100 disabled:opacity-40"
          >
            Cancel
          </button>
        </span>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          className="min-h-0 w-full resize-none rounded-lg border border-slate-300 bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-800 focus:border-brand-500 focus:outline-none"
        />
        <div ref={previewRef} className="min-h-0 overflow-auto rounded-lg border border-slate-200 bg-white p-3">
          <LivePreview kind={kind} source={preview} />
        </div>
      </div>
    </div>
  );
}
