/**
 * Pure model for the "read as a document" view: orders a stage's artifacts into one
 * coherent document and builds the Markdown form of it (for copy / .md download).
 * No React and no DOM here, so it is unit-testable.
 */
export interface DocItem {
  id: string;
  type: string;
  title: string;
  content: string;
  ext?: string;
}

export type SectionKind = 'markdown' | 'diagram' | 'code';

/** Reading order: narrative first, then its diagrams, then decisions; source files last. */
const RANK: Record<string, number> = {
  PRD: 10, EPIC: 11, FEATURE: 12, USER_STORY: 13,
  HLD: 10, STRUCTURIZR_DSL: 11, HLD_DIAGRAM: 12, ARCH_DIAGRAM: 13, CLOUDCRAFT_JSON: 14, ADR: 20,
  LLD: 10, COMPONENT_DIAGRAM: 11, LLD_DIAGRAM: 12, PLANTUML: 13,
  TEST_STRATEGY: 10, RTM: 11, PIPELINE_DESIGN: 10, SECURITY_SCAN: 30, PULL_REQUEST: 10,
};

const DIAGRAM_TYPES = new Set([
  'STRUCTURIZR_DSL', 'HLD_DIAGRAM', 'ARCH_DIAGRAM', 'COMPONENT_DIAGRAM', 'LLD_DIAGRAM', 'PLANTUML', 'DRAWIO',
]);
const DIAGRAM_EXTS = new Set(['.mmd', '.mermaid', '.puml', '.plantuml', '.iuml', '.drawio', '.dsl', '.svg']);
const MARKDOWN_TYPES = new Set([
  'PRD', 'HLD', 'LLD', 'TEST_STRATEGY', 'RTM', 'ADR', 'EPIC', 'FEATURE', 'USER_STORY', 'PULL_REQUEST',
  'SECURITY_SCAN', 'TEST_EXECUTION_REPORT', 'QUALITY_REPORT', 'PIPELINE_DESIGN', 'DOCUMENT',
]);

export function sectionKind(item: DocItem): SectionKind {
  const ext = (item.ext ?? '').toLowerCase();
  if (DIAGRAM_TYPES.has(item.type) || DIAGRAM_EXTS.has(ext) || item.content.trimStart().startsWith('<mxfile')) return 'diagram';
  if (MARKDOWN_TYPES.has(item.type) || ext === '.md' || ext === '.markdown') return 'markdown';
  return 'code';
}

const KIND_GROUP: Record<SectionKind, number> = { markdown: 0, diagram: 0, code: 1 };

export function orderForDocument(items: DocItem[]): DocItem[] {
  return items
    .map((it, i) => ({ it, i }))
    .sort((a, b) =>
      KIND_GROUP[sectionKind(a.it)] - KIND_GROUP[sectionKind(b.it)]
      || (RANK[a.it.type] ?? 50) - (RANK[b.it.type] ?? 50)
      || a.i - b.i)
    .map((x) => x.it);
}

/**
 * The section heading replaces the artifact's own H1, and its inner headings move one
 * level down, so the document has ONE clean outline (title → sections → sub-sections).
 * Fenced code is left untouched.
 */
export function prepareMarkdown(content: string): string {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  let fence = false;
  let dropped = false;
  const out: string[] = [];
  for (const line of lines) {
    if (/^\s*```/.test(line)) fence = !fence;
    if (!fence && !/^\s*```/.test(line)) {
      const m = /^(#{1,6})\s+(.*)$/.exec(line);
      if (m) {
        if (!dropped && m[1] === '#') { dropped = true; continue; }
        out.push(`${m[1]!.length < 6 ? m[1] + '#' : m[1]} ${m[2]}`);
        continue;
      }
    }
    out.push(line);
  }
  return out.join('\n').replace(/^\n+/, '');
}

export function humanize(type: string): string {
  return type.toLowerCase().split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

export function sectionTitle(item: DocItem): string {
  return (item.title || '').trim() || humanize(item.type);
}

const FENCE_LANG: Record<string, string> = {
  STRUCTURIZR_DSL: 'text', HLD_DIAGRAM: 'mermaid', LLD_DIAGRAM: 'mermaid', PLANTUML: 'plantuml',
  OPENAPI: 'yaml', DBML: 'text', CDK: 'typescript', DRAWIO: 'xml', ARCH_DIAGRAM: 'xml', COMPONENT_DIAGRAM: 'xml',
};

/** The document as one Markdown file; diagrams appear as fenced source (round-trippable). */
export function composeMarkdown(title: string, items: DocItem[], generatedAt = new Date()): string {
  const parts = [`# ${title}`, `_Generated ${generatedAt.toISOString().slice(0, 10)} · ${items.length} section(s)_`];
  orderForDocument(items).forEach((it, i) => {
    const kind = sectionKind(it);
    parts.push(`## ${i + 1}. ${sectionTitle(it)}`);
    if (kind === 'markdown') parts.push(prepareMarkdown(it.content));
    else {
      const lang = FENCE_LANG[it.type] ?? (it.content.trimStart().startsWith('<mxfile') ? 'xml' : 'text');
      parts.push('```' + lang + '\n' + it.content.trim() + '\n```');
    }
  });
  return parts.join('\n\n') + '\n';
}

export function fileSlug(title: string): string {
  return (title || 'document').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'document';
}
