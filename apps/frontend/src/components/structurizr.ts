/**
 * Structurizr DSL → Mermaid C4 converter (client-side, offline).
 *
 * The Solution Architect emits the C4 model as Structurizr DSL
 * (`workspace { model { … } views { … } }`), which no browser renderer draws.
 * This converts the common DSL subset — person / softwareSystem / container /
 * component elements and their relationships — into a Mermaid C4 diagram
 * (`C4Container` / `C4Context`) so the unified Diagram Workbench can render and
 * live-preview it with everything else. Structurizr DSL stays the source of
 * truth (this is a render/edit convenience, not a replacement).
 *
 * Deliberately a pragmatic parser, not a full Structurizr implementation: it
 * handles the shapes the agents produce and throws on anything it can't map, so
 * the viewer falls back to the raw source (as other diagram renderers do).
 */

export interface C4Element {
  id: string;
  kind: 'person' | 'system' | 'container' | 'component';
  name: string;
  desc?: string;
  tech?: string;
  parent?: string;
}
export interface C4Relationship {
  from: string;
  to: string;
  label?: string;
  tech?: string;
}
export interface C4Model {
  title: string;
  elements: C4Element[];
  relationships: C4Relationship[];
}

const KIND_MAP: Record<string, C4Element['kind']> = {
  person: 'person',
  softwaresystem: 'system',
  container: 'container',
  component: 'component',
};

/** Strip /* *\/ and line (# or //) comments that sit outside quotes. */
function stripComments(dsl: string): string {
  const noBlock = dsl.replace(/\/\*[\s\S]*?\*\//g, '');
  return noBlock
    .split('\n')
    .map((line) => {
      let inStr = false;
      for (let i = 0; i < line.length; i++) {
        const c = line[i];
        if (c === '"') inStr = !inStr;
        else if (!inStr && (c === '#' || (c === '/' && line[i + 1] === '/'))) return line.slice(0, i);
      }
      return line;
    })
    .join('\n');
}

/** Pull the double-quoted strings out of a fragment, in order. */
function quoted(s: string): string[] {
  return Array.from(s.matchAll(/"((?:[^"\\]|\\.)*)"/g)).map((m) => m[1] ?? '');
}

function safeId(raw: string, seq: number): string {
  const id = (raw || '').replace(/[^A-Za-z0-9_]/g, '_');
  return id && /[A-Za-z_]/.test(id.charAt(0)) ? id : `el_${seq}`;
}

export function parseStructurizr(dsl: string): C4Model {
  const src = stripComments(dsl);
  const titleMatch = src.match(/workspace\s+"([^"]*)"/i);
  const title = titleMatch?.[1] || 'Architecture';

  const elements: C4Element[] = [];
  const relationships: C4Relationship[] = [];
  const parentStack: string[] = [];
  let inViews = false;
  let viewsDepth = -1;
  let depth = 0;
  let seq = 0;

  const elDef =
    /^(?:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?(person|softwareSystem|container|component)\b(.*)$/i;
  const relDef = /^([A-Za-z_][A-Za-z0-9_]*)\s*->\s*([A-Za-z_][A-Za-z0-9_]*)\b(.*)$/;

  for (const rawLine of src.split('\n')) {
    const line = rawLine.trim();
    if (!line) continue;

    // Ignore the `views { … }` block — it only lays out, it declares nothing.
    if (/^views\b/i.test(line)) {
      inViews = true;
      viewsDepth = depth;
    }

    const opens = (line.match(/\{/g) || []).length;
    const closes = (line.match(/\}/g) || []).length;

    if (!inViews) {
      const em = line.match(elDef);
      if (em) {
        const kind = KIND_MAP[(em[2] ?? '').toLowerCase()];
        if (!kind) { depth += opens - closes; continue; }
        const parts = quoted(em[3] ?? '');
        const id = safeId(em[1] ?? parts[0] ?? `el${seq}`, seq);
        seq++;
        const el: C4Element = {
          id,
          kind,
          name: parts[0] || id,
          desc: kind === 'container' || kind === 'component' ? parts[2] : parts[1],
          tech: kind === 'container' || kind === 'component' ? parts[1] : undefined,
          parent: parentStack[parentStack.length - 1],
        };
        elements.push(el);
        // A block that opens on this line makes this element the parent of what follows.
        if (opens > closes) parentStack.push(id);
        depth += opens - closes;
        continue;
      }
      const rm = line.match(relDef);
      if (rm) {
        const parts = quoted(rm[3] ?? '');
        relationships.push({ from: safeId(rm[1] ?? '', seq), to: safeId(rm[2] ?? '', seq), label: parts[0], tech: parts[1] });
        depth += opens - closes;
        continue;
      }
    }

    // Plain braces (block open/close) — maintain nesting + parent stack.
    for (let i = 0; i < closes; i++) {
      if (!inViews && parentStack.length) parentStack.pop();
      depth--;
      if (inViews && depth <= viewsDepth) inViews = false;
    }
    depth += opens;
  }

  if (!elements.length) throw new Error('No C4 elements found in the Structurizr DSL.');
  return { title, elements, relationships };
}

function esc(s?: string): string {
  return (s || '').replace(/"/g, "'").trim();
}

export function structurizrToMermaid(dsl: string): string {
  const { title, elements, relationships } = parseStructurizr(dsl);
  const byId = new Map(elements.map((e) => [e.id, e]));
  const hasContainers = elements.some((e) => e.kind === 'container' || e.kind === 'component');
  const out: string[] = [hasContainers ? 'C4Container' : 'C4Context', `  title ${esc(title)}`];

  const line = (e: C4Element): string => {
    if (e.kind === 'person') return `Person(${e.id}, "${esc(e.name)}", "${esc(e.desc)}")`;
    if (e.kind === 'container') return `Container(${e.id}, "${esc(e.name)}", "${esc(e.tech)}", "${esc(e.desc)}")`;
    if (e.kind === 'component') return `Component(${e.id}, "${esc(e.name)}", "${esc(e.tech)}", "${esc(e.desc)}")`;
    return `System(${e.id}, "${esc(e.name)}", "${esc(e.desc)}")`;
  };

  const children = (pid: string) => elements.filter((e) => e.parent === pid);
  const top = elements.filter((e) => !e.parent || !byId.has(e.parent));

  for (const e of top) {
    const kids = children(e.id);
    if (e.kind === 'system' && kids.length) {
      out.push(`  System_Boundary(${e.id}, "${esc(e.name)}") {`);
      for (const c of kids) {
        const gkids = children(c.id);
        if (gkids.length) {
          out.push(`    Container_Boundary(${c.id}, "${esc(c.name)}") {`);
          for (const g of gkids) out.push(`      ${line(g)}`);
          out.push('    }');
        } else {
          out.push(`    ${line(c)}`);
        }
      }
      out.push('  }');
    } else {
      out.push(`  ${line(e)}`);
    }
  }

  for (const r of relationships) {
    if (!byId.has(r.from) || !byId.has(r.to)) continue;
    out.push(r.tech
      ? `Rel(${r.from}, ${r.to}, "${esc(r.label)}", "${esc(r.tech)}")`
      : `Rel(${r.from}, ${r.to}, "${esc(r.label)}")`);
  }

  return out.join('\n');
}
