/**
 * Atlassian Document Format helpers: a tolerant ADF -> plain text converter, a
 * plain text -> ADF builder, and acceptance-criteria section handling.
 * Nothing here throws on malformed or unknown input.
 */
export interface AdfNode {
  type?: string;
  text?: string;
  attrs?: Record<string, unknown>;
  content?: unknown[];
}

const MAX_DEPTH = 40;
export const AC_HEADING = 'Acceptance criteria';

function isNode(value: unknown): value is AdfNode {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function children(node: AdfNode): AdfNode[] {
  return Array.isArray(node.content) ? node.content.filter(isNode) : [];
}

function attrString(node: AdfNode, key: string): string | undefined {
  const v = node.attrs?.[key];
  return typeof v === 'string' && v !== '' ? v : undefined;
}

function inline(node: AdfNode, depth: number): string {
  if (depth > MAX_DEPTH) return '';
  switch (node.type) {
    case 'text':
      return typeof node.text === 'string' ? node.text : '';
    case 'hardBreak':
      return '\n';
    case 'mention': {
      const text = attrString(node, 'text');
      if (text) return text.startsWith('@') ? text : `@${text}`;
      const id = attrString(node, 'id');
      return id ? `@${id}` : '@unknown';
    }
    case 'emoji':
      return attrString(node, 'text') ?? attrString(node, 'shortName') ?? '';
    case 'inlineCard':
      return attrString(node, 'url') ?? '';
    case 'status':
      return attrString(node, 'text') ?? '';
    case 'date': {
      const ts = Number(node.attrs?.['timestamp']);
      return Number.isFinite(ts) ? new Date(ts).toISOString().slice(0, 10) : '';
    }
    default:
      // Unknown inline (or block-in-inline) node: render whatever text it carries.
      if (typeof node.text === 'string') return node.text;
      return children(node).map((c) => inline(c, depth + 1)).join('');
  }
}

function listLines(node: AdfNode, depth: number, ordered: boolean): string {
  const start = ordered && typeof node.attrs?.['order'] === 'number' ? (node.attrs['order'] as number) : 1;
  return children(node)
    .map((item, i) => {
      const marker = ordered ? `${start + i}. ` : '- ';
      const body = block(item, depth + 1);
      const [first = '', ...rest] = body.split('\n');
      const pad = ' '.repeat(marker.length);
      return [marker + first, ...rest.map((l) => (l === '' ? l : pad + l))].join('\n');
    })
    .join('\n');
}

function blocks(nodes: AdfNode[], depth: number, separator: string): string {
  return nodes
    .map((n) => block(n, depth + 1))
    .filter((s) => s !== '')
    .join(separator);
}

function block(node: AdfNode, depth: number): string {
  if (depth > MAX_DEPTH) return '';
  switch (node.type) {
    case 'doc':
    case 'panel':
    case 'expand':
    case 'nestedExpand':
    case 'layoutSection':
    case 'layoutColumn':
      return blocks(children(node), depth, '\n\n');
    case 'paragraph':
    case 'heading':
    case 'caption':
      return children(node).map((c) => inline(c, depth + 1)).join('');
    case 'bulletList':
      return listLines(node, depth, false);
    case 'orderedList':
      return listLines(node, depth, true);
    case 'listItem':
    case 'taskItem':
    case 'decisionItem':
      // Items may hold inline content directly (taskItem) or blocks (listItem).
      return children(node).some((c) => ['paragraph', 'bulletList', 'orderedList', 'codeBlock', 'blockquote'].includes(c.type ?? ''))
        ? blocks(children(node), depth, '\n')
        : children(node).map((c) => inline(c, depth + 1)).join('');
    case 'taskList':
    case 'decisionList':
      return children(node).map((c) => `- ${block(c, depth + 1)}`).join('\n');
    case 'codeBlock': {
      const code = children(node).map((c) => inline(c, depth + 1)).join('');
      const lang = attrString(node, 'language') ?? '';
      return '```' + lang + '\n' + code + '\n```';
    }
    case 'blockquote':
      return blocks(children(node), depth, '\n')
        .split('\n')
        .map((l) => `> ${l}`.trimEnd())
        .join('\n');
    case 'rule':
      return '---';
    case 'table':
      return children(node)
        .map((row) => children(row).map((cell) => blocks(children(cell), depth, ' ').replace(/\n/g, ' ')).join(' | '))
        .join('\n');
    case 'mediaSingle':
    case 'mediaGroup':
    case 'media':
      return '';
    default: {
      // Unknown node: prefer block children, fall back to inline text.
      const kids = children(node);
      if (kids.length === 0) return typeof node.text === 'string' ? node.text : inline(node, depth + 1);
      return kids.some((k) => k.type === 'text' || k.type === 'hardBreak' || k.type === 'mention')
        ? kids.map((c) => inline(c, depth + 1)).join('')
        : blocks(kids, depth, '\n');
    }
  }
}

/** Convert ADF (or a plain string, passed through) to readable plain text. */
export function adfToText(doc: unknown): string {
  if (typeof doc === 'string') return doc;
  if (!isNode(doc)) return '';
  return block(doc, 0).replace(/[ \t]+$/gm, '').trim();
}

function textNode(text: string): AdfNode {
  return { type: 'text', text };
}

/** Lines separated by hardBreak nodes; empty lines are skipped (ADF forbids empty text nodes). */
function paragraphContent(text: string): AdfNode[] {
  const out: AdfNode[] = [];
  for (const line of text.split('\n')) {
    if (line === '') continue;
    if (out.length > 0) out.push({ type: 'hardBreak' });
    out.push(textNode(line));
  }
  return out;
}

/** Plain text -> ADF block nodes: blank lines split paragraphs, newlines become hard breaks. */
export function textToAdfBlocks(text: string): AdfNode[] {
  return text
    .replace(/\r\n?/g, '\n')
    .split(/\n{2,}/)
    .map((p) => paragraphContent(p.replace(/^\n+|\n+$/g, '')))
    .filter((content) => content.length > 0)
    .map((content) => ({ type: 'paragraph', content }));
}

export function acceptanceCriteriaBlocks(items: readonly string[]): AdfNode[] {
  if (items.length === 0) return [];
  return [
    { type: 'heading', attrs: { level: 3 }, content: [textNode(AC_HEADING)] },
    {
      type: 'bulletList',
      content: items.map((item) => ({
        type: 'listItem',
        content: [{ type: 'paragraph', content: paragraphContent(item.replace(/\r\n?/g, '\n')) }],
      })),
    },
  ];
}

export function adfDoc(content: AdfNode[]): { type: 'doc'; version: 1; content: AdfNode[] } {
  return { type: 'doc', version: 1, content };
}

/** Description (+ optional AC section) -> ADF document, or null when there is nothing to write. */
export function buildDescriptionAdf(description: string, acceptanceCriteria: readonly string[]): ReturnType<typeof adfDoc> | null {
  const content = [...textToAdfBlocks(description), ...acceptanceCriteriaBlocks(acceptanceCriteria)];
  return content.length === 0 ? null : adfDoc(content);
}

/** Acceptance criteria for a dedicated custom field (rich-text fields take ADF). */
export function buildAcFieldAdf(items: readonly string[]): ReturnType<typeof adfDoc> | null {
  if (items.length === 0) return null;
  const list = acceptanceCriteriaBlocks(items)[1];
  return list ? adfDoc([list]) : null;
}

const AC_HEADING_RE = /^acceptance criteria\s*:?$/i;

function cleanItem(item: string): string {
  return item.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, '').trim();
}

/** Split a block of lines into AC items: blank-line separated groups (keeps multi-line Gherkin together). */
function itemsFromText(text: string): string[] {
  const lines = text.split('\n');
  const bulleted = lines.some((l) => /^\s*(?:[-*•]|\d+[.)])\s+/.test(l));
  if (bulleted) {
    const items: string[] = [];
    for (const line of lines) {
      if (/^\s*(?:[-*•]|\d+[.)])\s+/.test(line)) items.push(cleanItem(line));
      else if (line.trim() !== '' && items.length > 0) items[items.length - 1] += `\n${line.trim()}`;
    }
    return items.filter((i) => i !== '');
  }
  return text
    .split(/\n{2,}/)
    .map((g) => g.trim())
    .filter((g) => g !== '');
}

/** Parse an AC custom-field value: ADF document, plain string, or array of strings. */
export function parseAcField(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === 'string' && v.trim() !== '').map((v) => v.trim());
  if (typeof value === 'string') return itemsFromText(value.replace(/\r\n?/g, '\n'));
  if (isNode(value)) {
    const lists = children(value).filter((n) => n.type === 'bulletList' || n.type === 'orderedList');
    if (lists.length > 0) return lists.flatMap((l) => children(l).map((i) => block(i, 1).trim()).filter((s) => s !== ''));
    return itemsFromText(adfToText(value));
  }
  return [];
}

/**
 * Split an ADF description into plain description text and the acceptance
 * criteria that live inside it. Supports an "Acceptance criteria" heading
 * followed by a list (what we write) and the legacy "Acceptance Criteria:" line
 * inside flat text (what jira_create_story historically wrote).
 */
export function splitDescription(raw: unknown): { text: string; acceptanceCriteria: string[] } {
  if (raw === null || raw === undefined) return { text: '', acceptanceCriteria: [] };
  if (isNode(raw) && raw.type === 'doc') {
    const nodes = children(raw);
    const idx = nodes.findIndex((n) => n.type === 'heading' && AC_HEADING_RE.test(inline(n, 0).trim()));
    if (idx >= 0) {
      let end = idx + 1;
      const items: string[] = [];
      while (end < nodes.length) {
        const n = nodes[end] as AdfNode;
        if (n.type !== 'bulletList' && n.type !== 'orderedList') break;
        items.push(...children(n).map((i) => block(i, 1).trim()).filter((s) => s !== ''));
        end++;
      }
      const rest = [...nodes.slice(0, idx), ...nodes.slice(end)];
      return { text: adfToText({ type: 'doc', content: rest }), acceptanceCriteria: items };
    }
  }
  const text = adfToText(raw);
  const match = /^([\s\S]*?)(?:^|\n)[ \t]*acceptance criteria[ \t]*:?[ \t]*\n([\s\S]*)$/i.exec(text);
  if (match) {
    const before = (match[1] ?? '').trim();
    return { text: before, acceptanceCriteria: itemsFromText(match[2] ?? '') };
  }
  return { text, acceptanceCriteria: [] };
}
