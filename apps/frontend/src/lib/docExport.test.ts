// @vitest-environment happy-dom
import { Packer } from 'docx';
import { describe, expect, it } from 'vitest';
import { blocksToDocx, domToBlocks, htmlForPaste } from './docExport';
import { composeMarkdown, orderForDocument, prepareMarkdown, sectionKind, type DocItem } from './docModel';

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

const item = (type: string, title: string, content = 'x', ext = ''): DocItem => ({ id: `${type}-${title}`, type, title, content, ext });

describe('document model', () => {
  it('reads narrative and diagrams first (in a sensible order) and source files last', () => {
    const out = orderForDocument([
      item('OPENAPI', 'API contract', 'openapi: 3.0.3'), item('LLD_DIAGRAM', 'Sequence'), item('LLD', 'Low-Level Design'),
      item('DBML', 'Schema', 'Table a {}'), item('COMPONENT_DIAGRAM', 'Components'),
    ]).map((i) => i.type);
    expect(out).toEqual(['LLD', 'COMPONENT_DIAGRAM', 'LLD_DIAGRAM', 'OPENAPI', 'DBML']);
  });

  it('classifies markdown, diagrams (by type, extension or draw.io content) and code', () => {
    expect(sectionKind(item('HLD', 'h'))).toBe('markdown');
    expect(sectionKind(item('PLANTUML', 'p'))).toBe('diagram');
    expect(sectionKind(item('X', 'm', 'graph TD', '.mmd'))).toBe('diagram');
    expect(sectionKind(item('X', 'd', '<mxfile><diagram/></mxfile>'))).toBe('diagram');
    expect(sectionKind(item('OPENAPI', 'o'))).toBe('code');
  });

  it('gives the document one clean outline: artifact H1 dropped, inner headings one level down, code untouched', () => {
    const md = '# Low-Level Design\n\n## Components\ntext\n\n```md\n# not a heading\n```\n\n### Detail';
    expect(prepareMarkdown(md)).toBe('### Components\ntext\n\n```md\n# not a heading\n```\n\n#### Detail');
  });

  it('composes Markdown with numbered sections and diagrams as fenced source', () => {
    const md = composeMarkdown('Design', [item('PLANTUML', 'Flow', '@startuml\nA->B\n@enduml'), item('LLD', 'LLD', '# LLD\n\n## Intro\nhello')],
      new Date('2026-10-02'));
    expect(md).toContain('# Design');
    expect(md).toContain('## 1. LLD\n\n### Intro\nhello');
    expect(md).toContain('## 2. Flow\n\n```plantuml\n@startuml');
  });
});

describe('DOM → blocks → .docx', () => {
  const dom = () => {
    const root = document.createElement('div');
    root.innerHTML = `
      <h1>Title</h1><h2>Section</h2>
      <p>Hello <strong>bold</strong> and <em>italic</em> with <code>code</code> and <a href="https://x.test">link</a></p>
      <ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul>
      <ol><li>first</li><li>second</li></ol>
      <table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>
      <pre><code>line1\nline2</code></pre>
      <img src="${PNG}" width="300" height="150" alt="diagram">
      <blockquote>quoted</blockquote><hr>`;
    return root;
  };

  it('captures every rendered construct, including diagrams as images', () => {
    const kinds = domToBlocks(dom()).map((b) => b.t);
    expect(kinds).toEqual(['h', 'h', 'p', 'li', 'li', 'li', 'li', 'li', 'table', 'code', 'img', 'quote', 'hr']);
    const p = domToBlocks(dom()).find((b) => b.t === 'p')!;
    expect(p.t === 'p' && p.runs.map((r) => [r.text, r.bold, r.italic, r.code, r.link])).toEqual([
      ['Hello ', undefined, undefined, undefined, undefined], ['bold', true, undefined, undefined, undefined],
      [' and ', undefined, undefined, undefined, undefined], ['italic', undefined, true, undefined, undefined],
      [' with ', undefined, undefined, undefined, undefined], ['code', undefined, undefined, true, undefined],
      [' and ', undefined, undefined, undefined, undefined], ['link', undefined, undefined, undefined, 'https://x.test'],
    ]);
    const table = domToBlocks(dom()).find((b) => b.t === 'table')!;
    expect(table.t === 'table' && table.header && table.rows.length).toBe(2);
  });

  it('keeps list nesting and restarts numbering per list', () => {
    const lis = domToBlocks(dom()).filter((b) => b.t === 'li') as Array<{ ordered: boolean; depth: number; listId: number }>;
    expect(lis.map((l) => [l.ordered, l.depth])).toEqual([[false, 0], [false, 1], [false, 0], [true, 0], [true, 0]]);
    expect(new Set(lis.filter((l) => l.ordered).map((l) => l.listId)).size).toBe(1);
    expect(lis[0]!.listId).not.toBe(lis[3]!.listId);
  });

  it('produces a real .docx package with the image and numbering parts', async () => {
    const buf = await Packer.toBuffer(blocksToDocx(domToBlocks(dom()), { title: 'Design', subtitle: 'LLD' }));
    const head = new TextDecoder('latin1').decode(buf.subarray(0, 2));
    const names = new TextDecoder('latin1').decode(buf);
    expect(head).toBe('PK');                                   // zip container
    expect(names).toContain('word/document.xml');
    expect(names).toContain('word/numbering.xml');
    expect(names).toContain('word/media/');                    // the diagram PNG is embedded
    expect(buf.length).toBeGreaterThan(2_000);
  });

  it('prepares clipboard HTML with bordered tables so they survive pasting into Word', () => {
    const html = htmlForPaste(dom());
    expect(html).toContain('<table border="1"');
    expect(html).toContain('<img src="data:image/png');
  });
});
