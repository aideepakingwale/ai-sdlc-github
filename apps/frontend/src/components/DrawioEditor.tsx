import { useEffect, useRef } from 'react';

/**
 * WYSIWYG draw.io editor via the SELF-HOSTED diagrams.net webapp embedded
 * same-origin at `/drawio/` (nginx proxies the `drawio` container). No external
 * network — keeps the offline/CSP posture. Communicates over the draw.io embed
 * JSON protocol (postMessage): we load the current XML on `init`, and on the
 * user's Save we hand the edited XML back to the caller, which persists it
 * through the same ACL-checked endpoint as every other edit.
 */
export default function DrawioEditor({
  xml,
  onSave,
  onExit,
  saving = false,
}: {
  xml: string;
  onSave: (xml: string) => void;
  onExit: () => void;
  saving?: boolean;
}) {
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const origin = window.location.origin;
    const post = (msg: unknown) =>
      frameRef.current?.contentWindow?.postMessage(JSON.stringify(msg), origin);

    function onMessage(e: MessageEvent) {
      // Only trust messages from OUR same-origin editor iframe.
      if (e.source !== frameRef.current?.contentWindow || e.origin !== origin) return;
      let data: { event?: string; xml?: string; data?: string };
      try {
        data = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
      } catch {
        return;
      }
      if (!data || typeof data.event !== 'string') return;
      switch (data.event) {
        case 'init':
          post({ action: 'load', xml: xml || '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel>', autosave: 0 });
          break;
        case 'save':
          if (typeof data.xml === 'string' && data.xml) onSave(data.xml);
          else post({ action: 'export', format: 'xml' }); // ask for XML if not inlined
          break;
        case 'export':
          if (typeof data.data === 'string' && data.data) onSave(data.data);
          else if (typeof data.xml === 'string' && data.xml) onSave(data.xml);
          break;
        case 'exit':
          onExit();
          break;
        default:
          break;
      }
    }

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [xml, onSave, onExit]);

  // embed=1 + proto=json → JSON postMessage protocol; saveAndExit gives a clear
  // Save & Exit affordance; ui=min keeps the chrome light inside the dialog.
  const src = '/drawio/?embed=1&proto=json&spin=1&modified=unsavedChanges&saveAndExit=1&noSaveBtn=0&ui=min';

  return (
    <div className="flex h-full min-h-[60vh] flex-col gap-1">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span className="rounded bg-brand-50 px-2 py-0.5 font-semibold text-brand-700">Visual editor</span>
        <span>Edit the diagram, then <strong>Save</strong> (or Save &amp; Exit) — changes persist through the same access checks.</span>
        {saving && <span className="ml-auto text-slate-500">Saving…</span>}
        <button
          onClick={onExit}
          className="ml-auto rounded-lg border border-slate-300 px-2.5 py-1 font-semibold text-slate-600 hover:bg-slate-100"
        >
          Close editor
        </button>
      </div>
      <iframe
        ref={frameRef}
        src={src}
        title="draw.io visual editor"
        className="min-h-0 flex-1 w-full rounded-lg border border-slate-200 bg-white"
      />
    </div>
  );
}
