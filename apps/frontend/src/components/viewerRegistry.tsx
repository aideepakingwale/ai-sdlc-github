import { useEffect, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CodeView, { langForExt, langForFile } from './CodeView';
import { isStaleChunkError, recoverFromStaleChunk } from '../staleChunk';
import { structurizrToMermaid } from './structurizr';

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
        // htmlLabels:false → plain SVG text, so diagrams can be exported to PNG / Word / PDF intact.
        mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose', htmlLabels: false, flowchart: { htmlLabels: false } });
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
 * Render a `.drawio` (mxGraph XML) document to a self-contained SVG in the browser — no external
 * service or library, so it works offline and under the strict CSP. A faithful preview of what
 * diagrams.net shows: embedded service icons (`shape=image;image=data:…`), nested containers with
 * their fills/dashes/titles (child coordinates are relative to a container parent), title and
 * legend text cells, HTML labels (bold name + muted official service name), and orthogonal edges
 * routed border-to-border with arrowheads, dashed async flows and white label backgrounds.
 * Native cloud stencils (mxgraph.aws4 / azure / gcp, as in hand-authored files) are shown as
 * chips in the cloud's brand colour; the real stencils appear when opened in diagrams.net.
 */
interface DioCell {
  id: string; parent: string; x: number; y: number; w: number; h: number;
  value: string; style: Record<string, string>;
  // page coordinates (resolved through the parent chain)
  ax: number; ay: number;
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

interface LabelLine { text: string; bold: boolean; color?: string; size?: number }

/** Split a label (HTML when style html=1) into lines with their basic styling. */
function labelLines(value: string, isHtml: boolean): LabelLine[] {
  if (!value) return [];
  if (!isHtml) return value.split('\n').map((t) => ({ text: t, bold: false }));
  const root = new DOMParser().parseFromString(`<div>${value}</div>`, 'text/html').body.firstElementChild;
  const lines: LabelLine[][] = [[]];
  const walk = (n: Node, st: { bold: boolean; color?: string; size?: number }) => {
    n.childNodes.forEach((c) => {
      if (c.nodeType === 3) {
        const t = (c.textContent ?? '').replace(/ /g, ' ');
        if (t) lines[lines.length - 1]!.push({ text: t, ...st });
      } else if (c.nodeType === 1) {
        const el = c as HTMLElement;
        const tag = el.tagName.toLowerCase();
        if (tag === 'br') { lines.push([]); return; }
        const next = { ...st };
        if (tag === 'b' || tag === 'strong') next.bold = true;
        if (tag === 'font') {
          if (el.getAttribute('color')) next.color = el.getAttribute('color') ?? undefined;
          const fs = /font-size:\s*(\d+)/.exec(el.getAttribute('style') ?? '');
          if (fs) next.size = Number(fs[1]);
        }
        if (['div', 'p'].includes(tag) && lines[lines.length - 1]!.length) lines.push([]);
        walk(el, next);
      }
    });
  };
  if (root) walk(root, { bold: false });
  return lines
    .map((runs) => {
      const text = runs.map((r) => r.text).join('').replace(/\s+/g, ' ').trim();
      const first = runs.find((r) => r.text.trim());
      return { text, bold: Boolean(first?.bold), color: first?.color, size: first?.size };
    })
    .filter((l) => l.text);
}

function wrapText(s: string, maxChars: number): string[] {
  const words = s.split(/\s+/);
  const lines: string[] = [];
  let cur = '';
  for (const w of words) {
    if ((`${cur} ${w}`).trim().length > maxChars && cur) { lines.push(cur); cur = w; }
    else cur = cur ? `${cur} ${w}` : w;
  }
  if (cur) lines.push(cur);
  return lines;
}

/** draw.io writes `data:image/png,<base64>` (no ";base64", since ";" separates style fields);
 *  browsers need the explicit encoding to decode the image. */
export function normalizeDataUri(uri: string): string {
  const m = /^data:(image\/[a-z+.-]+)(;[^,]*)?,(.*)$/i.exec(uri);
  if (!m) return uri;
  if ((m[2] ?? '').includes('base64') || (m[2] ?? '').includes('charset')) return uri;
  return /^[A-Za-z0-9+/=\s]+$/.test(m[3] ?? '') ? `data:${m[1]};base64,${m[3]}` : uri;
}

const PROVIDER_CHIP: Array<[RegExp, string, string]> = [
  [/aws/, '#ED7100', '#B35400'], [/azure/, '#0078D4', '#005A9E'], [/gcp|google/, '#4285F4', '#2A65C5'],
];

export function renderDrawioSvg(xml: string): { svg?: string; error?: string } {
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) return { error: 'the XML is not well-formed' };
  const raw = Array.from(doc.getElementsByTagName('mxCell'));
  const geo = new Map<string, { x: number; y: number; w: number; h: number; parent: string }>();
  for (const c of raw) {
    const g = c.getElementsByTagName('mxGeometry')[0];
    if (c.getAttribute('vertex') === '1' && g) {
      geo.set(c.getAttribute('id') || '', {
        x: +(g.getAttribute('x') || 0), y: +(g.getAttribute('y') || 0),
        w: +(g.getAttribute('width') || 0), h: +(g.getAttribute('height') || 0),
        parent: c.getAttribute('parent') || '1',
      });
    }
  }
  const absOf = (id: string): { x: number; y: number } => {
    let x = 0; let y = 0; let cur = id; const seen = new Set<string>();
    while (geo.has(cur) && !seen.has(cur)) {
      seen.add(cur);
      const g = geo.get(cur)!;
      x += g.x; y += g.y; cur = g.parent;
    }
    return { x, y };
  };

  const cells: DioCell[] = [];
  const edges: { source: string; target: string; value: string; style: Record<string, string>; points: [number, number][] }[] = [];
  for (const c of raw) {
    const style = parseStyle(c.getAttribute('style') || '');
    if (c.getAttribute('vertex') === '1' && geo.has(c.getAttribute('id') || '')) {
      const id = c.getAttribute('id') || '';
      const g = geo.get(id)!;
      const a = absOf(id);
      cells.push({ id, parent: g.parent, x: g.x, y: g.y, w: g.w, h: g.h, value: c.getAttribute('value') || '', style, ax: a.x, ay: a.y });
    } else if (c.getAttribute('edge') === '1') {
      const points = Array.from(c.getElementsByTagName('mxPoint'))
        .filter((pt) => pt.getAttribute('as') !== 'sourcePoint' && pt.getAttribute('as') !== 'targetPoint')
        .map((pt): [number, number] => [+(pt.getAttribute('x') || 0), +(pt.getAttribute('y') || 0)]);
      edges.push({ source: c.getAttribute('source') || '', target: c.getAttribute('target') || '',
        value: c.getAttribute('value') || '', style, points });
    }
  }
  if (!cells.length) return { error: 'no shapes to render' };

  const isText = (c: DioCell) => 'text' in c.style || c.style.shape === 'text';
  const isContainer = (c: DioCell) =>
    c.style.container === '1' || 'swimlane' in c.style || 'dashed' in c.style || c.style.fillColor === 'none';
  const hasImage = (c: DioCell) => (c.style.image ?? '').startsWith('data:image');
  const byId = new Map(cells.map((v) => [v.id, v]));

  // extents (icon labels hang below the icon)
  const extra = (c: DioCell) => (hasImage(c) && c.style.verticalLabelPosition === 'bottom' ? 46 : 0);
  const minX = Math.min(...cells.map((v) => v.ax));
  const minY = Math.min(...cells.map((v) => v.ay));
  const maxX = Math.max(...cells.map((v) => v.ax + v.w));
  const maxY = Math.max(...cells.map((v) => v.ay + v.h + extra(v)));
  const pad = 24;
  const width = maxX - minX + pad * 2;
  const height = maxY - minY + pad * 2;
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const tx = (x: number) => x - minX + pad;
  const ty = (y: number) => y - minY + pad;

  const markers = new Map<string, string>();
  const marker = (color: string) => {
    const key = color === '#5b6478' ? 'dio-arrow' : `dio-arrow-${color.replace('#', '')}`;
    if (!markers.has(key)) {
      markers.set(key, `<marker id="${key}" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" `
        + `markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 z" fill="${color}"/></marker>`);
    }
    return key;
  };
  marker('#5b6478');

  const out: string[] = [];
  const drawLines = (lines: LabelLine[], x: number, y: number, anchor: 'start' | 'middle', base: number,
    fill: string, maxChars: number, lineH = 14) => {
    let cy = y;
    for (const ln of lines) {
      const size = ln.size ?? base;
      for (const piece of wrapText(ln.text, maxChars)) {
        out.push(`<text x="${x}" y="${cy}" font-size="${size}" ${ln.bold ? 'font-weight="700" ' : ''}`
          + `fill="${ln.color && ln.color !== 'default' ? ln.color : fill}" text-anchor="${anchor}">${esc(piece)}</text>`);
        cy += Math.round(size * 1.25) + (lineH - 14);
      }
    }
  };

  // containers (outermost first so nested ones draw on top)
  const depthOf = (c: DioCell) => { let d = 0; let p = c.parent; const seen = new Set<string>(); while (byId.has(p) && !seen.has(p)) { seen.add(p); d++; p = byId.get(p)!.parent; } return d; };
  for (const v of cells.filter((x) => isContainer(x) && !isText(x)).sort((a, b) => depthOf(a) - depthOf(b))) {
    const stroke = v.style.strokeColor && v.style.strokeColor !== 'none' ? v.style.strokeColor : '#7f8c9a';
    const fill = v.style.fillColor && v.style.fillColor !== 'none' ? v.style.fillColor : 'none';
    const sw = v.style.strokeWidth ?? '1.2';
    const rx = v.style.rounded === '1' ? 6 : 2;
    out.push(`<rect x="${tx(v.ax)}" y="${ty(v.ay)}" width="${v.w}" height="${v.h}" rx="${rx}" fill="${fill}" `
      + `stroke="${stroke}" stroke-width="${sw}"${v.style.dashed === '1' ? ' stroke-dasharray="6 4"' : ''}/>`);
    const lines = labelLines(v.value, v.style.html === '1');
    const fs = Number(v.style.fontSize ?? 12);
    drawLines(lines.map((l) => ({ ...l, bold: l.bold || v.style.fontStyle === '1' })), tx(v.ax) + 12, ty(v.ay) + 8 + fs,
      'start', fs, v.style.fontColor ?? '#5b6478', Math.floor(v.w / (fs * 0.6)));
  }

  // edges: border-to-border orthogonal routes
  const anchor = (c: DioCell, side: 'l' | 'r' | 't' | 'b'): [number, number] => {
    const cx = tx(c.ax + c.w / 2); const cy = ty(c.ay + c.h / 2);
    return side === 'l' ? [tx(c.ax), cy] : side === 'r' ? [tx(c.ax + c.w), cy] : side === 't' ? [cx, ty(c.ay)] : [cx, ty(c.ay + c.h)];
  };
  for (const e of edges) {
    const s = byId.get(e.source); const t = byId.get(e.target);
    if (!s || !t) continue;
    const color = e.style.strokeColor && e.style.strokeColor !== 'none' ? e.style.strokeColor : '#5b6478';
    let d: string;
    let lx: number; let ly: number;
    if (e.points.length) {
      // routed by the generator: border port → waypoints → border port
      const wp = e.points.map(([x, y]) => [tx(x), ty(y)] as [number, number]);
      const side = (c: DioCell, p: [number, number]): 'l' | 'r' | 't' | 'b' => {
        const l = tx(c.ax); const r = tx(c.ax + c.w); const bt = ty(c.ay + c.h);
        return p[0] > r ? 'r' : p[0] < l ? 'l' : p[1] > bt ? 'b' : 't';
      };
      const a0 = anchor(s, side(s, wp[0]!));
      const a1 = anchor(t, side(t, wp[wp.length - 1]!));
      const pts: [number, number][] = [a0, ...wp, a1];
      d = pts.map((pt, i) => `${i ? 'L' : 'M'} ${pt[0]} ${pt[1]}`).join(' ');
      let best = 0; lx = pts[0]![0]; ly = pts[0]![1];
      for (let i = 0; i < pts.length - 1; i++) {
        const len = Math.abs(pts[i + 1]![0] - pts[i]![0]) + Math.abs(pts[i + 1]![1] - pts[i]![1]);
        if (len > best) { best = len; lx = (pts[i]![0] + pts[i + 1]![0]) / 2; ly = (pts[i]![1] + pts[i + 1]![1]) / 2; }
      }
    } else {
      const dx = (t.ax + t.w / 2) - (s.ax + s.w / 2);
      const dy = (t.ay + t.h / 2) - (s.ay + s.h / 2);
      const hint = e.style.exitX === '1' ? 'h' : e.style.exitY === '1' ? 'v' : undefined;
      const horizontal = hint ? hint === 'h' : Math.abs(dx) >= Math.abs(dy);
      const [sx, sy] = anchor(s, horizontal ? (dx >= 0 ? 'r' : 'l') : (dy >= 0 ? 'b' : 't'));
      const [ex, ey] = anchor(t, horizontal ? (dx >= 0 ? 'l' : 'r') : (dy >= 0 ? 't' : 'b'));
      const mx = (sx + ex) / 2; const my = (sy + ey) / 2;
      d = horizontal
        ? (Math.abs(sy - ey) < 2 ? `M ${sx} ${sy} L ${ex} ${ey}` : `M ${sx} ${sy} L ${mx} ${sy} L ${mx} ${ey} L ${ex} ${ey}`)
        : (Math.abs(sx - ex) < 2 ? `M ${sx} ${sy} L ${ex} ${ey}` : `M ${sx} ${sy} L ${sx} ${my} L ${ex} ${my} L ${ex} ${ey}`);
      lx = horizontal ? mx : (sx + ex) / 2;
      ly = horizontal ? (Math.abs(sy - ey) < 2 ? sy : (sy + ey) / 2) : my;
    }
    out.push(`<path d="${d}" fill="none" stroke="${color}" stroke-width="${e.style.strokeWidth ?? 1.5}" `
      + `stroke-linejoin="round"${e.style.dashed === '1' ? ' stroke-dasharray="7 4"' : ''} marker-end="url(#${marker(color)})"/>`);
    const label = labelLines(e.value, e.style.html === '1').map((l) => l.text).join(' ');
    if (label) {
      const w = Math.min(label.length * 6 + 10, 220);
      out.push(`<rect x="${lx - w / 2}" y="${ly - 9}" width="${w}" height="16" rx="3" fill="#ffffff" fill-opacity="0.92"/>`);
      out.push(`<text x="${lx}" y="${ly + 3}" font-size="10.5" fill="#1F2937" text-anchor="middle">${esc(label.slice(0, 36))}</text>`);
    }
  }

  // nodes + text cells on top
  for (const v of cells.filter((x) => !(isContainer(x) && !isText(x)))) {
    const lines = labelLines(v.value, v.style.html === '1');
    if (hasImage(v)) {
      out.push(`<image href="${esc(normalizeDataUri(v.style.image!))}" x="${tx(v.ax)}" y="${ty(v.ay)}" width="${v.w}" height="${v.h}"/>`);
      drawLines(lines, tx(v.ax) + v.w / 2, ty(v.ay) + v.h + 14, 'middle', Number(v.style.fontSize ?? 11), '#1F2937', 20);
      continue;
    }
    if (isText(v)) {
      const stroke = v.style.strokeColor && v.style.strokeColor !== 'none';
      const fill = v.style.fillColor && v.style.fillColor !== 'none';
      if (stroke || fill) {
        out.push(`<rect x="${tx(v.ax)}" y="${ty(v.ay)}" width="${v.w}" height="${v.h}" rx="${v.style.rounded === '1' ? 6 : 0}" `
          + `fill="${fill ? v.style.fillColor : 'none'}" stroke="${stroke ? v.style.strokeColor : 'none'}"/>`);
      }
      const fs = Number(v.style.fontSize ?? 12);
      const sp = Number(v.style.spacing ?? 4) + 4;
      drawLines(lines, tx(v.ax) + sp, ty(v.ay) + sp + fs, 'start', fs, v.style.fontColor ?? '#1F2937',
        Math.floor((v.w - sp * 2) / (fs * 0.55)));
      continue;
    }
    const shape = `${v.style.shape ?? ''} ${v.style.resIcon ?? ''}`.toLowerCase();
    const chip = PROVIDER_CHIP.find(([rx]) => rx.test(shape));
    const fill = chip ? chip[1] : (v.style.fillColor && v.style.fillColor !== 'none' ? v.style.fillColor : '#dae8fc');
    const stroke = chip ? chip[2] : (v.style.strokeColor && v.style.strokeColor !== 'none' ? v.style.strokeColor : '#6c8ebf');
    const rounded = v.style.rounded === '1' || Boolean(chip);
    out.push(`<rect x="${tx(v.ax)}" y="${ty(v.ay)}" width="${v.w}" height="${v.h}" rx="${rounded ? 8 : 2}" fill="${fill}" stroke="${stroke}"/>`);
    const cx = tx(v.ax) + v.w / 2;
    const wrapped = lines.flatMap((l) => wrapText(l.text, Math.max(6, Math.floor(v.w / 7))).map((t) => ({ ...l, text: t }))).slice(0, 4);
    const startY = ty(v.ay) + v.h / 2 - (wrapped.length - 1) * 7 + 4;
    wrapped.forEach((ln, i) => out.push(`<text x="${cx}" y="${startY + i * 14}" font-size="11" ${ln.bold ? 'font-weight="700" ' : ''}`
      + `fill="${chip ? '#ffffff' : (v.style.fontColor ?? '#1a2536')}" text-anchor="middle">${esc(ln.text)}</text>`));
  }

  return {
    svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" `
      + `height="${height}" font-family="ui-sans-serif,system-ui,sans-serif"><defs>${Array.from(markers.values()).join('')}</defs>${out.join('')}</svg>`,
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

// ---------------------------------------------------------------- Structurizr DSL (C4)
/**
 * C4 model authored as Structurizr DSL. No browser renders the DSL directly, so
 * convert it to a Mermaid C4 diagram and render that; on any conversion error
 * fall back to the raw DSL source (like the other diagram renderers).
 */
export function StructurizrView({ source }: { source: string }) {
  try {
    return <MermaidView source={structurizrToMermaid(source)} />;
  } catch (err) {
    return (
      <div>
        <div className="mb-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Could not render the C4 preview from the Structurizr DSL
          ({err instanceof Error ? err.message : 'parse error'}). The editable source is shown below.
        </div>
        <CodeView source={source} lang="plaintext" filename="workspace.dsl" showDiagnostics={false} />
      </div>
    );
  }
}

export function isStructurizr(c: ViewerContext): boolean {
  return c.ext === '.dsl' || c.type === 'STRUCTURIZR_DSL' || /^\s*workspace\b[^]*\bmodel\b/.test(c.content);
}

// ---------------------------------------------------------------- Markdown (diagram-aware)
/**
 * Render a markdown document (HLD/LLD/PRD/ADR …) with GFM tables (remark-gfm)
 * AND inline diagrams: fenced ```mermaid / ```plantuml / ```drawio blocks are
 * rendered visually in place, so the reviewer sees the document WITH its
 * diagrams as one page.
 */
// Module-level on purpose: react-markdown treats a NEW components object as new component
// types, which would remount every inline diagram (re-render Mermaid, flicker, lose exports
// in flight) on each parent re-render.
const MARKDOWN_COMPONENTS = {
  pre({ children }: { children?: unknown }) {
    const child = (Array.isArray(children) ? children[0] : children) as
      | { props?: { className?: string; children?: unknown } }
      | undefined;
    const cls = child?.props?.className || '';
    const lang = /language-(\w+)/.exec(cls)?.[1];
    const src = String(child?.props?.children ?? '').replace(/\n$/, '');
    if (lang === 'mermaid') return <MermaidView source={src} />;
    if (lang === 'plantuml' || lang === 'puml') return <PlantUmlView source={src} />;
    if (lang === 'drawio' || src.trimStart().startsWith('<mxfile')) return <DrawioView source={src} />;
    return <pre>{children as ReactNode}</pre>;
  },
};

export function MarkdownDoc({ content }: { content: string }) {
  return (
    <div className="prose-chat text-sm">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
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
const MARKDOWN_TYPES = new Set(['CODE_STRUCTURE', 'CUSTOM_DOC', 'PRD', 'HLD', 'LLD', 'TEST_STRATEGY', 'RTM', 'ADR', 'EPIC', 'FEATURE', 'USER_STORY', 'PULL_REQUEST', 'SECURITY_SCAN', 'TEST_EXECUTION_REPORT', 'QUALITY_REPORT', 'BRD', 'DEPLOYMENT_PLAN', 'RELEASE_NOTES', 'ROLLBACK_PLAN', 'RUNBOOK', 'MONITORING_PLAN', 'SLO_REPORT']);
/** Plain-text artifacts (".txt", or no extension) that are really markdown - headings plus lists, tables, bold or a
 *  fence. Code is excluded: a "# comment" line alone is not a heading. */
const NOT_MARKDOWN_TYPES = new Set(['APP_CODE', 'DOCKERFILE', 'K6_SCRIPT', 'LOCUSTFILE']);
export function looksLikeMarkdown(content: string): boolean {
  const head = content.slice(0, 20_000);
  if (!/^#{1,6}[ \t]+\S/m.test(head)) return false;
  return /^\s*(?:[-*+]|\d+\.)[ \t]+\S/m.test(head) || /^\s*\|.+\|\s*$/m.test(head) || /\*\*[^*\n]+\*\*/.test(head) || /^```/m.test(head);
}
function isMarkdownCtx(c: ViewerContext): boolean {
  if (c.ext === '.md' || c.ext === '.markdown') return true;
  if (c.ext && c.ext !== '.txt') return false;
  if (MARKDOWN_TYPES.has(c.type)) return true;
  return !NOT_MARKDOWN_TYPES.has(c.type) && looksLikeMarkdown(c.content);
}
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
    // C4 model as Structurizr DSL: rendered by converting to Mermaid C4.
    id: 'structurizr', label: 'C4 diagram', hasSource: true,
    matches: (c) => isStructurizr(c),
    render: (c) => <StructurizrView source={c.content} />,
  },
  {
    id: 'markdown', label: 'Document', hasSource: true,
    matches: isMarkdownCtx,
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

// ---------------------------------------------------------------- Unified Diagram Workbench support
/** The diagram families the unified workbench can render + live-preview. */
export type DiagramKind = 'mermaid' | 'plantuml' | 'drawio' | 'svg' | 'structurizr';

/** Map a resolved viewer plugin id to a diagram kind, or null if it isn't a diagram. */
export function diagramKindOf(pluginId: string | undefined): DiagramKind | null {
  switch (pluginId) {
    case 'mermaid': return 'mermaid';
    case 'plantuml': return 'plantuml';
    case 'drawio': return 'drawio';
    case 'svg': return 'svg';
    case 'structurizr': return 'structurizr';
    default: return null;
  }
}

/**
 * Render a diagram straight from source text — the live-preview half of the
 * unified workbench. Reuses the same renderers as the read-only viewers so the
 * preview matches exactly what will be saved.
 */
export function LivePreview({ kind, source }: { kind: DiagramKind; source: string }) {
  switch (kind) {
    case 'mermaid': return <MermaidView source={source} />;
    case 'plantuml': return <PlantUmlView source={source} />;
    case 'structurizr': return <StructurizrView source={source} />;
    case 'drawio': return <DrawioView source={source} />;
    case 'svg':
      return (
        <div
          className="overflow-auto rounded-lg border border-slate-200 bg-white p-4 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
          dangerouslySetInnerHTML={{ __html: source }}
        />
      );
  }
}
