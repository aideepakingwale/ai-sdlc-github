/**
 * DETERMINISTIC browser-side export — no LLM, no API calls, no tokens. It only converts what is
 * already rendered on screen (DOM → clipboard / .docx / print). The single network read is
 * fetching a diagram image that the page itself already loaded, and only from the same origin
 * (see sameOrigin); a test enforces both properties.
 *
 * Browser-side export of the rendered document: rich copy-paste, Word (.docx) and PDF.
 * What is exported is what is on screen — the rendered DOM — with every diagram
 * (Mermaid, PlantUML, draw.io, C4) turned into an embedded PNG so it survives Word and paste.
 */
import {
  AlignmentType, Document, ExternalHyperlink, HeadingLevel, ImageRun, LevelFormat, Packer, Paragraph, Table,
  TableCell, TableRow, TextRun, WidthType, ShadingType, BorderStyle,
} from 'docx';

// ------------------------------------------------------------------ diagrams → PNG
export interface Raster { dataUrl: string; width: number; height: number }

function sizeOf(svg: SVGSVGElement): { w: number; h: number } {
  const r = svg.getBoundingClientRect();
  if (r.width > 4 && r.height > 4) return { w: r.width, h: r.height };
  const vb = svg.viewBox?.baseVal;
  if (vb && vb.width && vb.height) return { w: vb.width, h: vb.height };
  const w = parseFloat(svg.getAttribute('width') ?? '') || 800;
  const h = parseFloat(svg.getAttribute('height') ?? '') || 600;
  return { w, h };
}

interface SvgSnapshot { markup: string; w: number; h: number }

/** Serialise an SVG NOW (synchronously), so later DOM changes cannot affect the export. */
export function snapshotSvg(src: SVGSVGElement | string): SvgSnapshot {
  if (typeof src === 'string') {
    const doc = new DOMParser().parseFromString(src, 'image/svg+xml');
    const el = doc.documentElement as unknown as SVGSVGElement;
    const { w, h } = sizeOf(el);
    el.setAttribute('width', String(w));
    el.setAttribute('height', String(h));
    if (!el.getAttribute('xmlns')) el.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    return { markup: new XMLSerializer().serializeToString(el), w, h };
  }
  const { w, h } = sizeOf(src);
  const clone = src.cloneNode(true) as SVGSVGElement;
  clone.setAttribute('width', String(w));
  clone.setAttribute('height', String(h));
  clone.removeAttribute('style');
  if (!clone.getAttribute('xmlns')) clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  return { markup: new XMLSerializer().serializeToString(clone), w, h };
}

async function rasterize(snap: SvgSnapshot, scale = 2): Promise<Raster | null> {
  try {
    const img = new Image();
    await new Promise<void>((res, rej) => {
      img.onload = () => res();
      img.onerror = () => rej(new Error('svg image failed'));
      img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(snap.markup)}`;
    });
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(snap.w * scale);
    canvas.height = Math.ceil(snap.h * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    return { dataUrl: canvas.toDataURL('image/png'), width: Math.round(snap.w), height: Math.round(snap.h) };
  } catch {
    return null;
  }
}

/** Rasterise an inline <svg> (or SVG markup) to a PNG data URL. Returns null if the browser can't. */
export async function svgToPng(src: SVGSVGElement | string, scale = 2): Promise<Raster | null> {
  try { return await rasterize(snapshotSvg(src), scale); } catch { return null; }
}

/** Only same-origin URLs may be fetched during export (the page's own PlantUML proxy, never a third party). */
export function sameOrigin(url: string, base: string = window.location.href): boolean {
  try { return new URL(url, base).origin === new URL(base).origin; } catch { return false; }
}

async function imgElementToPng(img: HTMLImageElement): Promise<Raster | null> {
  try {
    if (!sameOrigin(img.currentSrc || img.src)) return null;
    const res = await fetch(img.currentSrc || img.src);
    const text = await res.text();
    if (text.trimStart().startsWith('<svg') || text.includes('<svg')) return await svgToPng(text);
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext('2d')?.drawImage(img, 0, 0);
    return { dataUrl: canvas.toDataURL('image/png'), width: img.naturalWidth, height: img.naturalHeight };
  } catch {
    return null;
  }
}

/** Wait until every diagram has finished rendering (placeholders gone, images loaded). */
export async function waitForDiagrams(root: HTMLElement, timeoutMs = 10_000): Promise<void> {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const pending = root.querySelector('.animate-pulse');
    const loading = Array.from(root.querySelectorAll('img')).some((i) => !i.complete);
    if (!pending && !loading) return;
    await new Promise((r) => setTimeout(r, 150));
  }
}

/**
 * A detached copy of the document with every diagram replaced by an embedded PNG <img>
 * and UI-only elements removed — safe to paste, save or convert.
 */
export async function prepareExportRoot(root: HTMLElement): Promise<HTMLElement> {
  await waitForDiagrams(root);
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('[data-export-skip]').forEach((n) => n.remove());
  const origSvgs = Array.from(root.querySelectorAll('svg')).filter((s) => !s.closest('[data-export-skip]'));
  const cloneSvgs = Array.from(clone.querySelectorAll('svg'));
  // Snapshot every diagram synchronously, right now: if the page re-renders while we rasterise
  // (async), the snapshots are unaffected and no diagram is lost.
  const snaps = origSvgs.map((s) => { try { return snapshotSvg(s); } catch { return null; } });
  for (let i = 0; i < snaps.length; i++) {
    const target = cloneSvgs[i];
    const snap = snaps[i];
    if (!target || !snap) continue;
    const r = (await rasterize(snap)) ?? (await rasterize(snap));   // one retry for a flaky first decode
    if (r) target.replaceWith(pngImg(r));
  }
  const origImgs = Array.from(root.querySelectorAll('img')).filter((s) => !s.closest('[data-export-skip]'));
  const cloneImgs = Array.from(clone.querySelectorAll('img')).filter((i) => !i.src.startsWith('data:'));
  for (const target of cloneImgs) {
    const orig = origImgs.find((o) => o.src === target.src);
    const r = orig ? await imgElementToPng(orig) : null;
    if (r) target.replaceWith(pngImg(r));
  }
  return clone;
}

function pngImg(r: Raster): HTMLImageElement {
  const img = document.createElement('img');
  img.src = r.dataUrl;
  img.width = Math.min(r.width, 720);
  img.height = Math.round((r.height * Math.min(r.width, 720)) / r.width);
  img.alt = 'diagram';
  return img;
}

// ------------------------------------------------------------------ clipboard
const PASTE_CSS = `
body{font-family:Calibri,Arial,sans-serif;font-size:11pt;color:#111}
h1{font-size:22pt}h2{font-size:16pt;margin-top:18pt}h3{font-size:13pt}
table{border-collapse:collapse}td,th{border:1px solid #999;padding:4px 8px;vertical-align:top}th{background:#eee}
pre{background:#f4f4f4;border:1px solid #ddd;padding:8px;font-family:Consolas,monospace;font-size:9.5pt;white-space:pre-wrap}
code{font-family:Consolas,monospace}img{max-width:100%}`;

export function htmlForPaste(clone: HTMLElement): string {
  clone.querySelectorAll('table').forEach((t) => { t.setAttribute('border', '1'); t.setAttribute('cellpadding', '4'); });
  return `<html><head><meta charset="utf-8"><style>${PASTE_CSS}</style></head><body>${clone.innerHTML}</body></html>`;
}

/** Copy the document as formatted content (headings, tables, diagram images) + Markdown text. */
export async function copyRich(root: HTMLElement, markdown: string): Promise<'rich' | 'text'> {
  const clone = await prepareExportRoot(root);
  const html = htmlForPaste(clone);
  const Item = (window as unknown as { ClipboardItem?: typeof ClipboardItem }).ClipboardItem;
  if (Item && navigator.clipboard?.write) {
    await navigator.clipboard.write([new Item({
      'text/html': new Blob([html], { type: 'text/html' }),
      'text/plain': new Blob([markdown], { type: 'text/plain' }),
    })]);
    return 'rich';
  }
  const holder = document.createElement('div');
  holder.style.cssText = 'position:fixed;left:-9999px;top:0';
  holder.innerHTML = clone.innerHTML;
  document.body.appendChild(holder);
  const range = document.createRange();
  range.selectNodeContents(holder);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
  const ok = document.execCommand('copy');
  sel?.removeAllRanges();
  holder.remove();
  if (!ok) { await navigator.clipboard.writeText(markdown); return 'text'; }
  return 'rich';
}

export async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5_000);
}

// ------------------------------------------------------------------ PDF (browser print)
/** Print only the document (see the @media print rules in index.css) → "Save as PDF". */
export async function printDocument(root: HTMLElement, title: string): Promise<void> {
  await waitForDiagrams(root);
  const prev = document.title;
  document.title = title;
  const restore = () => { document.title = prev; window.removeEventListener('afterprint', restore); };
  window.addEventListener('afterprint', restore);
  window.print();
}

// ------------------------------------------------------------------ DOM → blocks → .docx
export interface Run { text: string; bold?: boolean; italic?: boolean; code?: boolean; link?: string }
export type Block =
  | { t: 'h'; level: 1 | 2 | 3 | 4 | 5 | 6; runs: Run[] }
  | { t: 'p'; runs: Run[] }
  | { t: 'li'; ordered: boolean; depth: number; listId: number; runs: Run[] }
  | { t: 'code'; text: string }
  | { t: 'table'; header: boolean; rows: Run[][][] }
  | { t: 'img'; dataUrl: string; width: number; height: number }
  | { t: 'quote'; runs: Run[] }
  | { t: 'hr' };

function inlineRuns(node: Node, style: Omit<Run, 'text'> = {}, out: Run[] = []): Run[] {
  node.childNodes.forEach((c) => {
    if (c.nodeType === 3) {
      const text = (c.textContent ?? '').replace(/\s+/g, ' ');
      if (text) out.push({ text, ...style });
      return;
    }
    if (c.nodeType !== 1) return;
    const el = c as HTMLElement;
    const tag = el.tagName.toLowerCase();
    if (tag === 'br') out.push({ text: '\n', ...style });
    else if (tag === 'strong' || tag === 'b') inlineRuns(el, { ...style, bold: true }, out);
    else if (tag === 'em' || tag === 'i') inlineRuns(el, { ...style, italic: true }, out);
    else if (tag === 'code') inlineRuns(el, { ...style, code: true }, out);
    else if (tag === 'a') inlineRuns(el, { ...style, link: el.getAttribute('href') ?? undefined }, out);
    else if (tag === 'ul' || tag === 'ol' || tag === 'img' || tag === 'table' || tag === 'pre') return;
    else inlineRuns(el, style, out);
  });
  return out;
}

const trimRuns = (runs: Run[]): Run[] => {
  const r = runs.map((x) => ({ ...x }));
  if (r[0]) r[0].text = r[0].text.replace(/^\s+/, '');
  const last = r[r.length - 1];
  if (last) last.text = last.text.replace(/\s+$/, '');
  return r.filter((x) => x.text.length > 0);
};

/** Walk the rendered (export-prepared) DOM into a flat, format-neutral block list. */
export function domToBlocks(root: HTMLElement): Block[] {
  const blocks: Block[] = [];
  let listSeq = 0;

  const imgBlock = (img: HTMLImageElement) => {
    if (!img.src.startsWith('data:image/png')) return;
    const width = Number(img.getAttribute('width')) || img.width || 600;
    const height = Number(img.getAttribute('height')) || img.height || 400;
    blocks.push({ t: 'img', dataUrl: img.src, width, height });
  };

  const walkList = (list: HTMLElement, depth: number, listId: number) => {
    const ordered = list.tagName.toLowerCase() === 'ol';
    Array.from(list.children).forEach((li) => {
      if (li.tagName.toLowerCase() !== 'li') return;
      blocks.push({ t: 'li', ordered, depth, listId, runs: trimRuns(inlineRuns(li)) });
      li.querySelectorAll(':scope > img').forEach((i) => imgBlock(i as HTMLImageElement));
      li.querySelectorAll(':scope > ul, :scope > ol').forEach((sub) => walkList(sub as HTMLElement, depth + 1, ordered ? listId : ++listSeq));
    });
  };

  const walk = (el: HTMLElement) => {
    Array.from(el.children).forEach((child) => {
      const c = child as HTMLElement;
      const tag = c.tagName.toLowerCase();
      if (/^h[1-6]$/.test(tag)) blocks.push({ t: 'h', level: Number(tag[1]) as 1, runs: trimRuns(inlineRuns(c)) });
      else if (tag === 'p') {
        const runs = trimRuns(inlineRuns(c));
        if (runs.length) blocks.push({ t: 'p', runs });
        c.querySelectorAll(':scope > img').forEach((i) => imgBlock(i as HTMLImageElement));
      } else if (tag === 'ul' || tag === 'ol') walkList(c, 0, ++listSeq);
      else if (tag === 'pre') blocks.push({ t: 'code', text: (c.textContent ?? '').replace(/\n$/, '') });
      else if (tag === 'table') {
        const rows = Array.from(c.querySelectorAll('tr')).map((tr) =>
          Array.from(tr.children).map((cell) => trimRuns(inlineRuns(cell))));
        blocks.push({ t: 'table', header: c.querySelector('thead') !== null || c.querySelector('th') !== null, rows });
      } else if (tag === 'img') imgBlock(c as HTMLImageElement);
      else if (tag === 'blockquote') blocks.push({ t: 'quote', runs: trimRuns(inlineRuns(c)) });
      else if (tag === 'hr') blocks.push({ t: 'hr' });
      else walk(c);   // div / section / figure / article …
    });
  };
  walk(root);
  return blocks;
}

function dataUrlBytes(dataUrl: string): Uint8Array {
  const b64 = dataUrl.slice(dataUrl.indexOf(',') + 1);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const HEADINGS = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3,
  HeadingLevel.HEADING_4, HeadingLevel.HEADING_5, HeadingLevel.HEADING_6];

function textRuns(runs: Run[], size?: number): (TextRun | ExternalHyperlink)[] {
  return runs.map((r) => {
    const base = new TextRun({
      text: r.text, bold: r.bold, italics: r.italic, size,
      font: r.code ? 'Consolas' : undefined,
      color: r.link ? '0563C1' : undefined, underline: r.link ? {} : undefined,
      shading: r.code ? { type: ShadingType.CLEAR, fill: 'F0F0F0' } : undefined,
    });
    return r.link ? new ExternalHyperlink({ link: r.link, children: [base] }) : base;
  });
}

const MAX_IMG_WIDTH = 600;     // px; fits an A4/Letter page with margins

/** Build a real .docx (Word, Google Docs, Pages, LibreOffice) from the block list. */
export function blocksToDocx(blocks: Block[], meta: { title: string; subtitle?: string }): Document {
  const listIds = Array.from(new Set(blocks.filter((b) => b.t === 'li').map((b) => (b as { listId: number }).listId)));
  const numbering = {
    config: [
      { reference: 'bullets', levels: [0, 1, 2, 3].map((level) => ({
        level, format: LevelFormat.BULLET, text: level % 2 ? '–' : '•', alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 360 * (level + 1), hanging: 260 } } } })) },
      // one numbering instance per ordered list so each list restarts at 1
      ...listIds.map((id) => ({ reference: `ol-${id}`, levels: [0, 1, 2, 3].map((level) => ({
        level, format: level % 2 ? LevelFormat.LOWER_LETTER : LevelFormat.DECIMAL, text: `%${level + 1}.`,
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 360 * (level + 1), hanging: 300 } } } })) })),
    ],
  };

  const children: (Paragraph | Table)[] = [
    new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun({ text: meta.title })] }),
  ];
  if (meta.subtitle) children.push(new Paragraph({ children: [new TextRun({ text: meta.subtitle, italics: true, color: '666666' })] }));

  for (const b of blocks) {
    if (b.t === 'h') children.push(new Paragraph({ heading: HEADINGS[b.level - 1]!, children: textRuns(b.runs) }));
    else if (b.t === 'p') children.push(new Paragraph({ spacing: { after: 120 }, children: textRuns(b.runs) }));
    else if (b.t === 'quote') children.push(new Paragraph({ indent: { left: 480 }, children: textRuns(b.runs.map((r) => ({ ...r, italic: true }))) }));
    else if (b.t === 'li') {
      children.push(new Paragraph({
        numbering: { reference: b.ordered ? `ol-${b.listId}` : 'bullets', level: Math.min(b.depth, 3) },
        children: textRuns(b.runs),
      }));
    } else if (b.t === 'code') {
      b.text.split('\n').forEach((line, i, all) => children.push(new Paragraph({
        shading: { type: ShadingType.CLEAR, fill: 'F4F4F4' },
        spacing: { after: i === all.length - 1 ? 160 : 0, line: 240 },
        children: [new TextRun({ text: line || ' ', font: 'Consolas', size: 18 })],
      })));
    } else if (b.t === 'hr') {
      children.push(new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'CCCCCC', space: 1 } }, children: [] }));
    } else if (b.t === 'img') {
      const w = Math.min(b.width, MAX_IMG_WIDTH);
      const h = Math.round((b.height * w) / b.width);
      children.push(new Paragraph({
        alignment: AlignmentType.CENTER, spacing: { before: 120, after: 160 },
        children: [new ImageRun({ type: 'png', data: dataUrlBytes(b.dataUrl), transformation: { width: w, height: h },
          altText: { title: 'diagram', description: 'diagram', name: 'diagram' } })],
      }));
    } else if (b.t === 'table' && b.rows.length) {
      const cols = Math.max(...b.rows.map((r) => r.length));
      const border = { style: BorderStyle.SINGLE, size: 4, color: '999999' };
      children.push(new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: b.rows.map((r, ri) => new TableRow({
          tableHeader: b.header && ri === 0,
          children: Array.from({ length: cols }, (_, ci) => new TableCell({
            borders: { top: border, bottom: border, left: border, right: border },
            shading: b.header && ri === 0 ? { type: ShadingType.CLEAR, fill: 'E8E8E8' } : undefined,
            margins: { top: 60, bottom: 60, left: 100, right: 100 },
            children: [new Paragraph({ children: textRuns((r[ci] ?? []).map((x) => (b.header && ri === 0 ? { ...x, bold: true } : x)), 20) })],
          })),
        })),
      }));
      children.push(new Paragraph({ children: [] }));
    }
  }

  return new Document({
    creator: 'AI-SDLC Platform', title: meta.title, numbering,
    styles: { default: { document: { run: { font: 'Calibri', size: 22 } } } },
    sections: [{ children }],
  });
}

export async function exportDocx(root: HTMLElement, meta: { title: string; subtitle?: string }): Promise<Blob> {
  const clone = await prepareExportRoot(root);
  clone.querySelectorAll('[data-docx-skip]').forEach((n) => n.remove());   // the .docx has its own title block
  return Packer.toBlob(blocksToDocx(domToBlocks(clone), meta));
}
