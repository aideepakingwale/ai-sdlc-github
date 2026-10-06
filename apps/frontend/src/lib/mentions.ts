/**
 * Pure helpers for "@" references inside the prompt. The prompt stays a plain string ("... use @PRD_v2 for ...");
 * the editor shows each known reference as a removable chip. Nothing here touches React.
 */
export interface Mention { kind: 'artifact' | 'template' | 'file'; id: string; label: string; sub: string }
export type Segment = { type: 'text'; text: string } | { type: 'chip'; mention: Mention };

/** The text a reference is stored as in the prompt: "@" + the label with whitespace as underscores. */
export function tokenFor(label: string): string {
  return `@${label.trim().replace(/\s+/g, '_')}`;
}

export const chipKey = (m: { kind: string; id: string }) => `${m.kind}:${m.id}`;

const AFTER = /[\s.,;:!?)\]}"']/;

/** Split a prompt into plain text and known references. A token only counts at a word boundary, and the
 *  longest matching label wins ("@Design_v2" beats "@Design"). Unknown "@words" stay plain text. */
export function splitPrompt(text: string, mentions: Mention[]): Segment[] {
  const byLen = [...mentions].sort((a, b) => tokenFor(b.label).length - tokenFor(a.label).length);
  const out: Segment[] = [];
  let buf = '';
  let i = 0;
  while (i < text.length) {
    if (text[i] === '@' && (i === 0 || /\s/.test(text[i - 1]!))) {
      const hit = byLen.find((m) => {
        const tok = tokenFor(m.label);
        return text.startsWith(tok, i) && (i + tok.length === text.length || AFTER.test(text[i + tok.length]!));
      });
      if (hit) {
        if (buf) { out.push({ type: 'text', text: buf }); buf = ''; }
        out.push({ type: 'chip', mention: hit });
        i += tokenFor(hit.label).length;
        continue;
      }
    }
    buf += text[i];
    i++;
  }
  if (buf) out.push({ type: 'text', text: buf });
  return out;
}

export function joinSegments(segs: Segment[]): string {
  return segs.map((s) => (s.type === 'text' ? s.text : tokenFor(s.mention.label))).join('');
}

/** An "@query" being typed right before the caret (start of text or after whitespace), or null. */
export function mentionQuery(textBefore: string): { query: string; start: number } | null {
  const m = /(^|\s)@([\w.-]*)$/.exec(textBefore);
  if (!m) return null;
  const query = m[2] ?? '';
  return { query, start: textBefore.length - query.length - 1 };
}

/** The prompt text of an editor DOM: text nodes as written, a chip as its token, <br> and block breaks as newlines. */
export function serializeEditor(root: Node): string {
  let out = '';
  const walk = (node: Node, first: boolean): void => {
    node.childNodes.forEach((child, idx) => {
      if (child.nodeType === 3) { out += (child.nodeValue ?? '').replace(/ /g, ' '); return; }
      if (child.nodeType !== 1) return;
      const el = child as HTMLElement;
      if (el.dataset?.chipLabel !== undefined) { out += tokenFor(el.dataset.chipLabel); return; }
      if (el.tagName === 'BR') { out += '\n'; return; }
      const block = el.tagName === 'DIV' || el.tagName === 'P';
      if (block && !(first && idx === 0) && out.length > 0 && !out.endsWith('\n')) out += '\n';
      walk(el, false);
    });
  };
  walk(root, true);
  return out;
}

export function chipsIn(root: Node): Array<{ kind: Mention['kind']; id: string }> {
  const found: Array<{ kind: Mention['kind']; id: string }> = [];
  (root as Element).querySelectorAll?.('[data-chip-label]').forEach((el) => {
    const d = (el as HTMLElement).dataset;
    found.push({ kind: d.chipKind as Mention['kind'], id: d.chipId ?? '' });
  });
  return found;
}
