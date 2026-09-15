import { useEffect, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CodeView, { langForExt, langForFile } from './CodeView';
import { isStaleChunkError, recoverFromStaleChunk } from '../staleChunk';

/**
 * Viewer plugin registry: a declarative, extensible mapping from a file
 * (extension + artifact type + name) to how it is rendered. Adding support for
 * a new file type is a single entry here — no changes to ArtifactViewer /
 * FilesPanel. Each plugin can offer a rendered view plus a raw Source tab.
 */

export interface ViewerContext {
  content: string;
  ext: string; // e.g. ".puml"
  type: string; // artifact type, e.g. "PLANTUML"
  filename: string;
}

export interface ViewerPlugin {
  id: string;
  /** Tab label for the rendered view (Source tab is added automatically). */
  label: string;
  /** Offer a raw Source tab alongside the rendered view. */
  hasSource: boolean;
  matches: (ctx: ViewerContext) => boolean;
  render: (ctx: ViewerContext) => ReactNode;
}

// ---------------------------------------------------------------- Mermaid
const MERMAID_KEYWORDS =
  /^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart|gitGraph|C4Context|C4Container|C4Component|C4Dynamic|requirementDiagram|block-beta|xychart-beta|sankey-beta)\b/;

export function sanitizeMermaid(raw: string): string {
  let s = raw.trim();
  const fence = s.match(/```(?:mermaid|mmd)?\s*\n?([\s\S]*?)```/i);
  if (fence?.[1]) s = fence[1].trim();
  const lines = s.split('\n');
  const start = lines.findIndex((l) => MERMAID_KEYWORDS.test(l.trim()));
  if (start > 0) s = lines.slice(start).join('\n');
  return s.trim();
}

let mermaidSeq = 0;

export function MermaidView({ source }: { source: string }) {
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cleaned = sanitizeMermaid(source);
        const mermaid = (await import('mermaid')).default;
        mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose' });
        await mermaid.parse(cleaned);
        const { svg: rendered } = await mermaid.render(`artifact-mmd-${++mermaidSeq}`, cleaned);
        if (!cancelled) setSvg(rendered);
      } catch (err) {
        if (cancelled) return;
        if (isStaleChunkError(err) && recoverFromStaleChunk()) return;
        setError(err instanceof Error ? err.message : 'Diagram failed to render');
      }
    })();
    return () => { cancelled = true; };
  }, [source]);

  if (error) {
    const stale = isStaleChunkError(error);
    return (
      <div>
        <div className="mb-2 flex items-center gap-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {stale ? (
            <>
              <span>This page is running an older version of the app, so the diagram renderer could not load.</span>
              <button onClick={() => window.location.reload()} className="ml-auto shrink-0 rounded bg-amber-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-amber-700">Reload page</button>
            </>
          ) : (
            <span>
              Could not render the diagram ({error.slice(0, 160)}). If you have edit rights, use
              {' '}<strong>🔧 Fix syntax</strong> or <strong>♻ Regenerate</strong> at the top of this dialog; the source is shown below.
            </span>
          )}
        </div>
        <CodeView source={source} lang="plaintext" filename="diagram.mmd" showDiagnostics={false} />
      </div>
    );
  }
  if (!svg) return <div className="animate-pulse p-6 text-sm text-slate-400">Rendering diagram…</div>;
  return (
    <div
      ref={container}
      className="overflow-auto rounded-lg border border-slate-200 bg-white p-4 [&_svg]:mx-auto [&_svg]:max-w-full"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

// ---------------------------------------------------------------- PlantUML
/**
 * PlantUML render endpoint (same-origin by default so it works offline and
 * within the strict CSP; nginx proxies /plantuml → the plantuml container).
 * Override at runtime via `window.__PLANTUML_SERVER__`.
 */
function plantumlServer(): string {
  return (window as unknown as { __PLANTUML_SERVER__?: string }).__PLANTUML_SERVER__ || '/plantuml';
}

function encode6bit(b: number): string {
  if (b < 10) return String.fromCharCode(48 + b);
  b -= 10;
  if (b < 26) return String.fromCharCode(65 + b);
  b -= 26;
  if (b < 26) return String.fromCharCode(97 + b);
  b -= 26;
  return b === 0 ? '-' : b === 1 ? '_' : '?';
}

function encode64(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const b1 = bytes[i]!;
    const b2 = i + 1 < bytes.length ? bytes[i + 1]! : 0;
    const b3 = i + 2 < bytes.length ? bytes[i + 2]! : 0;
    out += encode6bit(b1 >> 2);
    out += encode6bit(((b1 & 0x3) << 4) | (b2 >> 4));
    out += encode6bit(((b2 & 0xf) << 2) | (b3 >> 6));
    out += encode6bit(b3 & 0x3f);
  }
  return out;
}

/** PlantUML's URL encoding: raw-deflate the UTF-8 source, then its base64
 *  variant. Uses the browser's CompressionStream (no dependency). */
async function encodePlantuml(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const cs = new CompressionStream('deflate-raw');
  const writer = cs.writable.getWriter();
  void writer.write(data);
  void writer.close();
  const chunks: Uint8Array[] = [];
  const reader = cs.readable.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) chunks.push(value);
  }
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const deflated = new Uint8Array(total);
  let o = 0;
  for (const c of chunks) { deflated.set(c, o); o += c.length; }
  return encode64(deflated);
}

function stripPuml(raw: string): string {
  const fence = raw.match(/```(?:plantuml|puml)?\s*\n?([\s\S]*?)```/i);
  let s = (fence?.[1] ?? raw).trim();
  if (!/@startuml/i.test(s)) s = `@startuml\n${s}\n@enduml`;
  return s;
}

export function PlantUmlView({ source }: { source: string }) {
  const [src, setSrc] = useState('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (typeof CompressionStream === 'undefined') throw new Error('no CompressionStream');
        const enc = await encodePlantuml(stripPuml(source));
        if (!cancelled) setSrc(`${plantumlServer()}/svg/${enc}`);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; };
  }, [source]);

  if (failed) {
    return (
      <div>
        <div className="mb-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
          The PlantUML renderer is unavailable; showing source.
        </div>
        <CodeView source={source} lang="plaintext" filename="diagram.puml" showDiagnostics={false} />
      </div>
    );
  }
  if (!src) return <div className="animate-pulse p-6 text-sm text-slate-400">Rendering PlantUML…</div>;
  return (
    <div className="overflow-auto rounded-lg border border-slate-200 bg-white p-4">
      <img
        src={src}
        alt="PlantUML diagram"
        className="mx-auto max-w-full"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

// ---------------------------------------------------------------- draw.io (mxGraph)
/**
 * Render a `.drawio` (mxGraph XML) document to a self-contained SVG in the
 * browser — no external service or library, so it works offline and under the
 * strict CSP. It's a faithful topology preview: boxes, dashed boundary
 * containers, labels, and orthogonal connectors with arrowheads. AWS resource
 * icons are shown as labelled service chips (the real stencils appear when the
 * file is opened in diagrams.net via Download).
 */
interface DioVertex {
  id: string; x: number; y: number; w: number; h: number;
  label: string; style: Record<string, string>; container: boolean;
}

function parseStyle(s: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of (s || '').split(';')) {
    if (!part) continue;
    const eq = part.indexOf('=');
    if (eq === -1) out[part] = 'true';
    else out[part.slice(0, eq)] = part.slice(eq + 1);
  }
  return out;
}

function wrapLabel(s: string, w: number): string[] {
  const max = Math.max(6, Math.floor(w / 7));
  const lines: string[] = [];
  let cur = '';
  for (const word of s.split(/\s+/)) {
    if ((`${cur} ${word}`).trim().length > max && cur) {
      lines.push(cur);
      cur = word;
    } else {
      cur = cur ? `${cur} ${word}` : word;
    }
    if (lines.length >= 3) break;
  }
  if (cur && lines.length < 3) lines.push(cur);
  return lines.slice(0, 3);
}

export function renderDrawioSvg(xml: string): { svg?: string; error?: string } {
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) return { error: 'the XML is not well-formed' };
  const cells = Array.from(doc.getElementsByTagName('mxCell'));
  const verts: DioVertex[] = [];
  const edges: { source: string; target: string; label: string }[] = [];
  for (const c of cells) {
    const style = parseStyle(c.getAttribute('style') || '');
    const geo = c.getElementsByTagName('mxGeometry')[0];
    if (c.getAttribute('vertex') === '1' && geo) {
      verts.push({
        id: c.getAttribute('id') || '',
        x: +(geo.getAttribute('x') || 0), y: +(geo.getAttribute('y') || 0),
        w: +(geo.getAttribute('width') || 0), h: +(geo.getAttribute('height') || 0),
        label: c.getAttribute('value') || '', style,
        container: 'dashed' in style || style.container === '1' || style.fillColor === 'none',
      });
    } else if (c.getAttribute('edge') === '1') {
      edges.push({
        source: c.getAttribute('source') || '', target: c.getAttribute('target') || '',
        label: c.getAttribute('value') || '',
      });
    }
  }
  if (!verts.length) return { error: 'no shapes to render' };

  const byId = new Map(verts.map((v) => [v.id, v]));
  const minX = Math.min(...verts.map((v) => v.x));
  const minY = Math.min(...verts.map((v) => v.y));
  const maxX = Math.max(...verts.map((v) => v.x + v.w));
  const maxY = Math.max(...verts.map((v) => v.y + v.h));
  const pad = 24;
  const width = maxX - minX + pad * 2;
  const height = maxY - minY + pad * 2;
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const tx = (x: number) => x - minX + pad;
  const ty = (y: number) => y - minY + pad;

  const out: string[] = [
    '<defs><marker id="dio-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" '
    + 'markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="#5b6478"/></marker></defs>',
  ];

  // Boundary containers first (behind).
  for (const v of verts.filter((x) => x.container)) {
    out.push(`<rect x="${tx(v.x)}" y="${ty(v.y)}" width="${v.w}" height="${v.h}" rx="4" fill="none" `
      + `stroke="${v.style.strokeColor || '#7f8c9a'}" stroke-dasharray="6 4"/>`);
    if (v.label) out.push(`<text x="${tx(v.x) + 8}" y="${ty(v.y) + 16}" font-size="12" font-weight="600" `
      + `fill="#5b6478">${esc(v.label)}</text>`);
  }
  // Edges (single-elbow orthogonal route) with arrowheads + labels.
  for (const e of edges) {
    const s = byId.get(e.source);
    const t = byId.get(e.target);
    if (!s || !t) continue;
    const sx = tx(s.x + s.w / 2);
    const sy = ty(s.y + s.h / 2);
    const ex = tx(t.x + t.w / 2);
    const ey = ty(t.y + t.h / 2);
    out.push(`<path d="M ${sx} ${sy} L ${ex} ${sy} L ${ex} ${ey}" fill="none" stroke="#5b6478" `
      + `stroke-width="1.5" marker-end="url(#dio-arrow)"/>`);
    if (e.label) out.push(`<text x="${(sx + ex) / 2}" y="${sy - 4}" font-size="10" fill="#5b6478" `
      + `text-anchor="middle">${esc(e.label)}</text>`);
  }
  // Nodes on top.
  for (const v of verts.filter((x) => !x.container)) {
    const isAws = (v.style.shape || '').includes('aws4') || (v.style.resIcon || '').includes('aws4');
    const fill = isAws ? '#ED7100'
      : (v.style.fillColor && v.style.fillColor !== 'none' ? v.style.fillColor : '#dae8fc');
    const stroke = v.style.strokeColor && v.style.strokeColor !== 'none' ? v.style.strokeColor
      : (isAws ? '#B35400' : '#6c8ebf');
    const rounded = v.style.rounded === '1' || isAws;
    const textColor = isAws ? '#ffffff' : '#1a2536';
    out.push(`<rect x="${tx(v.x)}" y="${ty(v.y)}" width="${v.w}" height="${v.h}" rx="${rounded ? 8 : 2}" `
      + `fill="${fill}" stroke="${stroke}"/>`);
    const lines = wrapLabel(v.label, v.w);
    const cx = tx(v.x) + v.w / 2;
    const startY = ty(v.y) + v.h / 2 - (lines.length - 1) * 7 + 4;
    lines.forEach((ln, i) => out.push(`<text x="${cx}" y="${startY + i * 14}" font-size="11" `
      + `fill="${textColor}" text-anchor="middle">${esc(ln)}</text>`));
  }

  return {
    svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" `
      + `height="${height}" font-family="ui-sans-serif,system-ui,sans-serif">${out.join('')}</svg>`,
  };
}

export function DrawioView({ source }: { source: string }) {
  const { svg, error } = renderDrawioSvg(source);
  if (error) {
    return (
      <div>
        <div className="mb-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Could not render the draw.io preview ({error}). The editable source is shown below — use
          {' '}<strong>⬇ Open externally</strong> to open it in diagrams.net.
        </div>
        <CodeView source={source} lang="xml" filename="diagram.drawio" showDiagnostics={false} />
      </div>
    );
  }
  return (
    <div
      className="overflow-auto rounded-lg border border-slate-200 bg-white p-4 [&_svg]:mx-auto [&_svg]:max-w-full"
      dangerouslySetInnerHTML={{ __html: svg! }}
    />
  );
}

// ---------------------------------------------------------------- Markdown (diagram-aware)
/**
 * Render a markdown document (HLD/LLD/PRD/ADR …) with GFM tables (remark-gfm)
 * AND inline diagrams: fenced ```mermaid / ```plantuml / ```drawio blocks are
 * rendered visually in place, so the reviewer sees the document WITH its
 * diagrams as one page.
 */
export function MarkdownDoc({ content }: { content: string }) {
  return (
    <div className="prose-chat text-sm">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre({ children }) {
            const child = (Array.isArray(children) ? children[0] : children) as
              | { props?: { className?: string; children?: unknown } }
              | undefined;
            const cls = child?.props?.className || '';
            const lang = /language-(\w+)/.exec(cls)?.[1];
            const src = String(child?.props?.children ?? '').replace(/\n$/, '');
            if (lang === 'mermaid') return <MermaidView source={src} />;
            if (lang === 'plantuml' || lang === 'puml') return <PlantUmlView source={src} />;
            if (lang === 'drawio' || src.trimStart().startsWith('<mxfile')) return <DrawioView source={src} />;
            return <pre>{children}</pre>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------- CSV table
function CsvView({ source }: { source: string }) {
  const rows = source.trim().split('\n').map((r) => r.split(','));
  if (rows.length === 0) return <CodeView source={source} lang="plaintext" showDiagnostics={false} />;
  const [head, ...body] = rows;
  return (
    <div className="overflow-auto rounded-lg border border-slate-200">
      <table className="w-full text-xs">
        <thead className="bg-slate-100">
          <tr>{head!.map((c, i) => <th key={i} className="border-b border-slate-200 px-2 py-1 text-left font-semibold text-slate-600">{c}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((r, i) => (
            <tr key={i} className={i % 2 ? 'bg-slate-50' : ''}>
              {r.map((c, j) => <td key={j} className="border-b border-slate-100 px-2 py-1 text-slate-700">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- registry
const MARKDOWN_TYPES = new Set(['PRD', 'HLD', 'LLD', 'TEST_STRATEGY', 'RTM', 'ADR', 'EPIC', 'FEATURE', 'USER_STORY', 'PULL_REQUEST', 'SECURITY_SCAN', 'TEST_EXECUTION_REPORT', 'QUALITY_REPORT']);
const JSON_TYPES = new Set(['CLOUDCRAFT_JSON', 'GRAFANA_DASHBOARD', 'POSTMAN_COLLECTION', 'XRAY_TESTS']);

function pretty(json: string): string {
  try { return JSON.stringify(JSON.parse(json), null, 2); } catch { return json; }
}

/**
 * Ordered plugin list — the first match wins; the code plugin is the catch-all.
 * To support a new file type, add an entry (no other file changes needed).
 */
export const VIEWERS: ViewerPlugin[] = [
  {
    // Server-rendered architecture SVGs: real AWS icons, nested clusters.
    // Self-contained (icons inlined), so render inline; zoom-scrollable.
    id: 'svg', label: 'Diagram', hasSource: true,
    matches: (c) => c.ext === '.svg' || c.type === 'ARCH_DIAGRAM' || c.type === 'COMPONENT_DIAGRAM',
    render: (c) => (
      <div
        className="overflow-auto rounded-lg border border-slate-200 bg-white p-4 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
        dangerouslySetInnerHTML={{ __html: c.content }}
      />
    ),
  },
  {
    id: 'mermaid', label: 'Diagram', hasSource: true,
    matches: (c) => c.ext === '.mmd' || c.type === 'HLD_DIAGRAM' || c.type === 'LLD_DIAGRAM',
    render: (c) => <MermaidView source={c.content} />,
  },
  {
    id: 'plantuml', label: 'Diagram', hasSource: true,
    matches: (c) => c.ext === '.puml' || c.ext === '.plantuml' || c.ext === '.iuml' || c.type === 'PLANTUML',
    render: (c) => <PlantUmlView source={c.content} />,
  },
  {
    // Editable draw.io: rendered to an SVG preview in-browser; Source tab
    // shows the mxGraph XML, and Download opens the real file in diagrams.net.
    id: 'drawio', label: 'Diagram', hasSource: true,
    matches: (c) => c.ext === '.drawio' || c.type === 'DRAWIO' || c.content.trimStart().startsWith('<mxfile'),
    render: (c) => <DrawioView source={c.content} />,
  },
  {
    id: 'markdown', label: 'Document', hasSource: true,
    matches: (c) => c.ext === '.md' || c.ext === '.markdown' || (!c.ext && MARKDOWN_TYPES.has(c.type)),
    render: (c) => <MarkdownDoc content={c.content} />,
  },
  {
    id: 'csv', label: 'Table', hasSource: true,
    matches: (c) => c.ext === '.csv' || c.ext === '.tsv',
    render: (c) => <CsvView source={c.content} />,
  },
  {
    id: 'json', label: 'JSON', hasSource: false,
    matches: (c) => c.ext === '.json' || (!c.ext && JSON_TYPES.has(c.type)),
    render: (c) => <CodeView source={pretty(c.content)} lang="json" filename={c.filename} />,
  },
  {
    id: 'code', label: 'Code', hasSource: false,
    matches: () => true, // catch-all
    render: (c) => <CodeView source={c.content} lang={langForFile(c.filename) || langForExt(c.ext)} filename={c.filename} />,
  },
];

export function resolveViewer(ctx: ViewerContext): ViewerPlugin {
  return VIEWERS.find((v) => v.matches(ctx)) ?? VIEWERS[VIEWERS.length - 1]!;
}
