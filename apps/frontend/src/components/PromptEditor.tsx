import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { chipKey, chipsIn, mentionQuery, serializeEditor, splitPrompt, tokenFor, type Mention } from '../lib/mentions';

export interface PromptEditorHandle { focus: () => void }

const CHIP_STYLE: Record<Mention['kind'], { cls: string; icon: string; title: string }> = {
  artifact: { cls: 'bg-brand-50 text-brand-700 ring-brand-200', icon: '📄', title: 'Earlier output — pinned into this stage' },
  template: { cls: 'bg-violet-50 text-violet-700 ring-violet-200', icon: '📐', title: 'Output template — applied to this stage' },
  file: { cls: 'bg-pink-50 text-pink-700 ring-pink-200', icon: '📎', title: 'Uploaded file — mentioned in the instructions' },
};

/** One reference rendered as an inline, non-editable chip with a ✕ that removes it from the prompt. */
function makeChip(m: Mention): HTMLSpanElement {
  const s = CHIP_STYLE[m.kind];
  const el = document.createElement('span');
  el.contentEditable = 'false';
  el.dataset.chipLabel = m.label;
  el.dataset.chipKind = m.kind;
  el.dataset.chipId = m.id;
  el.title = `${s.title}: ${m.label}`;
  el.className = `mx-0.5 inline-flex max-w-[16rem] select-none items-center gap-1 rounded-full px-2 py-0.5 align-baseline text-[12px] font-medium ring-1 ring-inset ${s.cls}`;
  const icon = document.createElement('span');
  icon.textContent = s.icon;
  icon.setAttribute('aria-hidden', 'true');
  const text = document.createElement('span');
  text.className = 'truncate';
  text.textContent = m.label;
  const x = document.createElement('button');
  x.type = 'button';
  x.dataset.remove = '1';
  x.setAttribute('aria-label', `Remove ${m.label} from the instructions`);
  x.title = m.kind === 'file' ? 'Remove this mention (the file stays attached)' : 'Remove this reference from the instructions';
  x.className = 'ml-0.5 rounded-full px-1 leading-none opacity-60 hover:bg-white/70 hover:text-red-600 hover:opacity-100';
  x.textContent = '✕';
  el.append(icon, text, x);
  return el;
}

function fill(root: HTMLElement, text: string, mentions: Mention[]): void {
  root.textContent = '';
  for (const seg of splitPrompt(text, mentions)) {
    if (seg.type === 'chip') { root.append(makeChip(seg.mention)); continue; }
    seg.text.split('\n').forEach((line, i) => {
      if (i > 0) root.append(document.createElement('br'));
      if (line) root.append(document.createTextNode(line));
    });
  }
}

/**
 * The instructions box. Plain text with "@" references shown as coloured chips (earlier outputs, templates,
 * uploaded files); a chip's ✕ - or Backspace - removes the reference from the prompt and, for outputs and
 * templates, unpins it from the stage. The value stays a plain string with the "@Label_with_underscores" tokens,
 * so what is saved and sent is unchanged.
 */
export const PromptEditor = forwardRef<PromptEditorHandle, {
  value: string; onChange: (text: string) => void; mentions: Mention[];
  onPick: (m: Mention) => void; onUnpick: (m: { kind: Mention['kind']; id: string }) => void;
  placeholder: string; disabled?: boolean; ariaLabel?: string;
}>(function PromptEditor({ value, onChange, mentions, onPick, onUnpick, placeholder, disabled, ariaLabel = 'Instructions for the agent' }, ref) {
  const root = useRef<HTMLDivElement>(null);
  const emitted = useRef<string | null>(null);              // the text this editor last reported (or built)
  const known = useRef<Set<string>>(new Set());             // chips currently in the editor
  const mentionsKey = mentions.map(chipKey).join('|');
  const [query, setQuery] = useState<{ q: string } | null>(null);
  const anchor = useRef<{ node: Text; start: number; caret: number } | null>(null);

  useImperativeHandle(ref, () => ({ focus: () => root.current?.focus() }));

  // Rebuild from the string when it changed from OUTSIDE (reset, amend pre-fill, plan load) or when the set of
  // known references changed and the user is not typing; never while typing, so the caret never jumps.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const typing = document.activeElement === el;
    if (value === emitted.current && (typing || known.current.size >= 0)) {
      if (!typing && mentionsKey) { fill(el, value, mentions); known.current = new Set(chipsIn(el).map(chipKey)); }
      return;
    }
    fill(el, value, mentions);
    emitted.current = value;
    known.current = new Set(chipsIn(el).map(chipKey));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, mentionsKey]);

  const sync = useCallback(() => {
    const el = root.current;
    if (!el) return;
    const text = serializeEditor(el);
    const now = new Set(chipsIn(el).map(chipKey));
    for (const k of known.current) {
      if (!now.has(k)) { const [kind, ...id] = k.split(':'); onUnpick({ kind: kind as Mention['kind'], id: id.join(':') }); }
    }
    known.current = now;
    emitted.current = text;
    onChange(text);
    // an "@query" before the caret opens the suggestions
    const sel = window.getSelection();
    const node = sel?.anchorNode;
    if (sel && sel.isCollapsed && node && node.nodeType === 3 && el.contains(node)) {
      const before = (node.nodeValue ?? '').slice(0, sel.anchorOffset);
      const mq = mentionQuery(before);
      if (mq) { anchor.current = { node: node as Text, start: mq.start, caret: sel.anchorOffset }; setQuery({ q: mq.query }); return; }
    }
    anchor.current = null;
    setQuery(null);
  }, [onChange, onUnpick]);

  function pick(m: Mention) {
    const a = anchor.current;
    const el = root.current;
    if (!a || !el) return;
    const node = a.node;
    const after = (node.nodeValue ?? '').slice(a.caret);
    node.nodeValue = (node.nodeValue ?? '').slice(0, a.start);
    const chip = makeChip(m);
    const tail = document.createTextNode(` ${after}`);
    node.after(chip, tail);
    const r = document.createRange();
    r.setStart(tail, 1);
    r.collapse(true);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(r);
    onPick(m);
    el.focus();
    sync();
  }

  const matches = query
    ? mentions.filter((m) => m.label.toLowerCase().includes(query.q.toLowerCase()) || tokenFor(m.label).toLowerCase().includes(query.q.toLowerCase())).slice(0, 8)
    : [];

  return (
    <div className="relative">
      <div
        ref={root}
        role="textbox"
        aria-multiline="true"
        aria-label={ariaLabel}
        aria-disabled={disabled}
        data-testid="prompt-editor"
        data-placeholder={placeholder}
        contentEditable={!disabled}
        suppressContentEditableWarning
        spellCheck
        onInput={sync}
        onClick={(e) => {
          const btn = (e.target as HTMLElement).closest('[data-remove]');
          if (!btn || disabled) return;
          e.preventDefault();
          const chip = btn.closest('[data-chip-label]');
          // don't leave a double space where the chip was
          const prev = chip?.previousSibling; const next = chip?.nextSibling;
          if (prev?.nodeType === 3 && next?.nodeType === 3 && (prev.nodeValue ?? '').endsWith(' ') && (next.nodeValue ?? '').startsWith(' ')) {
            next.nodeValue = (next.nodeValue ?? '').slice(1);
          }
          chip?.remove();
          sync();
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && query) { setQuery(null); return; }
          if (e.key === 'Enter' && !e.shiftKey && matches.length > 0 && query) { e.preventDefault(); pick(matches[0]!); return; }
          if (e.key === 'Enter') { e.preventDefault(); document.execCommand('insertLineBreak'); }
        }}
        onPaste={(e) => { e.preventDefault(); document.execCommand('insertText', false, e.clipboardData.getData('text/plain')); }}
        className={`min-h-[5.5rem] w-full whitespace-pre-wrap break-words rounded-lg border border-slate-300 bg-white p-3 text-sm leading-7 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-200 empty:before:pointer-events-none empty:before:text-slate-400 empty:before:content-[attr(data-placeholder)] ${disabled ? 'cursor-not-allowed bg-slate-50 opacity-70' : ''}`}
      />
      {query && matches.length > 0 && (
        <div className="absolute left-2 top-full z-30 mt-1 max-h-64 w-80 overflow-auto rounded-lg border border-slate-200 bg-white shadow-xl" role="listbox" aria-label="References">
          <div className="border-b border-slate-100 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            Reference — generated content, templates &amp; files
          </div>
          {matches.map((m) => (
            <button type="button" role="option" key={chipKey(m)} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(m)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-50">
              <span className="text-sm">{CHIP_STYLE[m.kind].icon}</span>
              <span className="min-w-0 flex-1 truncate text-slate-700">{m.label}</span>
              <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{m.sub}</span>
            </button>
          ))}
        </div>
      )}
      {query && matches.length === 0 && (
        <div className="absolute left-2 top-full z-30 mt-1 w-80 rounded-lg border border-slate-200 bg-white px-3 py-2 text-[11px] text-slate-400 shadow-xl">
          No references match “{query.q}”. Attach a file or generate upstream stages first.
        </div>
      )}
    </div>
  );
});
