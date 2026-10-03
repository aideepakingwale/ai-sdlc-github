// @vitest-environment happy-dom
import fs from 'node:fs';
import path from 'node:path';
import { Packer } from 'docx';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { blocksToDocx, domToBlocks, htmlForPaste, sameOrigin } from './docExport';
import { composeMarkdown } from './docModel';

const here = path.dirname(new URL(import.meta.url).pathname);
const src = (rel: string) => fs.readFileSync(path.resolve(here, rel), 'utf8');

/** Files that make up the document/export feature (everything except the artifact loader). */
const EXPORT_FILES = ['./docExport.ts', './docModel.ts', '../components/DocumentViewer.tsx'];

describe('document export is deterministic: no LLM, no API, no tokens', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('the export modules never import the API client or anything LLM/chat related', () => {
    for (const f of EXPORT_FILES) {
      const code = src(f);
      const imports = [...code.matchAll(/from\s+['"]([^'"]+)['"]/g)].map((m) => m[1]!);
      expect(imports.filter((i) => /api\/|client|llm|chat|stream/i.test(i)), f).toEqual([]);
      expect(code, f).not.toMatch(/api\.(get|post|put|del|upload)\(|streamChat|streamStageProgress|\/api\//);
    }
  });

  it('only the artifact loader talks to the server, and only to read stored artifacts', () => {
    const loader = src('../components/StageDocument.tsx');
    const calls = [...loader.matchAll(/\bapi\.(get|post|put|del|upload)\b/g)].map((m) => m[1]);
    expect(calls).toEqual(['get']);
    expect(loader).toMatch(/\/artefacts\//);
    expect(loader).not.toMatch(/retrigger|regenerate|repair|chat|plan/i);
  });

  it('building Markdown, clipboard HTML and a .docx makes zero network requests', async () => {
    const fetchSpy = vi.fn(() => { throw new Error('network call during export'); });
    vi.stubGlobal('fetch', fetchSpy);
    const root = document.createElement('div');
    root.innerHTML = '<h2>1. LLD</h2><p>Hello <b>x</b></p><table><tr><th>A</th></tr><tr><td>1</td></tr></table><pre>code</pre>';
    const md = composeMarkdown('T', [{ id: '1', type: 'LLD', title: 'LLD', content: '# LLD\nhi' }]);
    const html = htmlForPaste(root.cloneNode(true) as HTMLElement);
    const buf = await Packer.toBuffer(blocksToDocx(domToBlocks(root), { title: 'T' }));
    expect(md).toContain('## 1. LLD');
    expect(html).toContain('<table border="1"');
    expect(buf.length).toBeGreaterThan(1000);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('may only ever fetch same-origin images (never a third-party or model endpoint)', () => {
    const base = 'https://sdlc.example.com/app';
    expect(sameOrigin('/plantuml/svg/abc', base)).toBe(true);
    expect(sameOrigin('https://sdlc.example.com/plantuml/svg/abc', base)).toBe(true);
    expect(sameOrigin('https://api.groq.com/openai/v1/chat/completions', base)).toBe(false);
    expect(sameOrigin('https://www.plantuml.com/plantuml/svg/abc', base)).toBe(false);
    expect(sameOrigin('not a url', 'also not a url')).toBe(false);
  });
});
