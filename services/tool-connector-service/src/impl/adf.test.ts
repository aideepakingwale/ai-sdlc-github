import { describe, expect, it } from 'vitest';
import { acceptanceCriteriaBlocks, adfDoc, adfToText, buildAcFieldAdf, buildDescriptionAdf, parseAcField, splitDescription, textToAdfBlocks } from './adf.js';

const p = (...t: string[]) => ({ type: 'paragraph', content: t.map((text) => ({ type: 'text', text })) });

describe('adfToText', () => {
  it('handles paragraphs, headings, hard breaks and mentions', () => {
    const doc = {
      type: 'doc',
      content: [
        { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: 'Title' }] },
        { type: 'paragraph', content: [{ type: 'text', text: 'line1' }, { type: 'hardBreak' }, { type: 'text', text: 'line2' }] },
        { type: 'paragraph', content: [{ type: 'mention', attrs: { id: 'abc', text: '@Alice' } }, { type: 'text', text: ' hi' }] },
        { type: 'paragraph', content: [{ type: 'mention', attrs: { id: 'u-2' } }] },
      ],
    };
    expect(adfToText(doc)).toBe('Title\n\nline1\nline2\n\n@Alice hi\n\n@u-2');
  });

  it('renders bullet, ordered and nested lists', () => {
    const doc = {
      type: 'doc',
      content: [
        { type: 'bulletList', content: [
          { type: 'listItem', content: [p('a'), { type: 'bulletList', content: [{ type: 'listItem', content: [p('a1')] }] }] },
          { type: 'listItem', content: [p('b')] },
        ] },
        { type: 'orderedList', attrs: { order: 3 }, content: [{ type: 'listItem', content: [p('x')] }, { type: 'listItem', content: [p('y')] }] },
      ],
    };
    expect(adfToText(doc)).toBe('- a\n  - a1\n- b\n\n3. x\n4. y');
  });

  it('renders code blocks, quotes and rules', () => {
    const doc = {
      type: 'doc',
      content: [
        { type: 'codeBlock', attrs: { language: 'ts' }, content: [{ type: 'text', text: 'const a = 1;' }] },
        { type: 'blockquote', content: [p('quoted')] },
        { type: 'rule' },
      ],
    };
    expect(adfToText(doc)).toBe('```ts\nconst a = 1;\n```\n\n> quoted\n\n---');
  });

  it('never throws on unknown, malformed or hostile input', () => {
    expect(adfToText(null)).toBe('');
    expect(adfToText(42)).toBe('');
    expect(adfToText('plain')).toBe('plain');
    expect(adfToText({ type: 'doc', content: [{ type: 'futureNode', content: [p('kept')] }, 'junk', null, { type: 'mystery' }] })).toBe('kept');
    expect(adfToText({ type: 'doc', content: [{ type: 'inlineCard', attrs: { url: 'https://x.y' } }] })).toBe('https://x.y');
    let deep: Record<string, unknown> = { type: 'text', text: 'deep' };
    for (let i = 0; i < 500; i++) deep = { type: 'blockquote', content: [deep] };
    expect(() => adfToText({ type: 'doc', content: [deep] })).not.toThrow();
  });
});

describe('text <-> ADF round trip', () => {
  it.each([
    ['single line', 'hello world'],
    ['hard breaks', 'a\nb\nc'],
    ['paragraphs', 'first para\n\nsecond para'],
    ['mixed', 'p1 line1\np1 line2\n\np2'],
  ])('%s', (_name, text) => {
    expect(adfToText(adfDoc(textToAdfBlocks(text)))).toBe(text);
  });

  it('produces no empty text nodes and tolerates CRLF', () => {
    const json = JSON.stringify(textToAdfBlocks('a\r\n\r\n\r\nb'));
    expect(json).not.toContain('"text":""');
    expect(adfToText(adfDoc(textToAdfBlocks('a\r\n\r\n\r\nb')))).toBe('a\n\nb');
  });

  it('empty input has no document to write', () => {
    expect(buildDescriptionAdf('', [])).toBeNull();
    expect(buildAcFieldAdf([])).toBeNull();
  });
});

describe('acceptance criteria in descriptions', () => {
  it('round-trips AC written as heading + bullet list', () => {
    const doc = buildDescriptionAdf('Body text', ['Given a\nWhen b\nThen c', 'Second']);
    expect(doc).not.toBeNull();
    const split = splitDescription(doc);
    expect(split.text).toBe('Body text');
    expect(split.acceptanceCriteria).toEqual(['Given a\nWhen b\nThen c', 'Second']);
  });

  it('keeps content after the AC list in the description', () => {
    const doc = adfDoc([...textToAdfBlocks('before'), ...acceptanceCriteriaBlocks(['one']), ...textToAdfBlocks('after')]);
    expect(splitDescription(doc)).toEqual({ text: 'before\n\nafter', acceptanceCriteria: ['one'] });
  });

  it('parses the legacy flat "Acceptance Criteria:" text written by jira_create_story', () => {
    const legacy = adfDoc([{ type: 'paragraph', content: [{ type: 'text', text: 'As a user I can log in\n\nAcceptance Criteria:\nGiven x\nThen y\n\nGiven z\nThen w' }] }]);
    expect(splitDescription(legacy)).toEqual({ text: 'As a user I can log in', acceptanceCriteria: ['Given x\nThen y', 'Given z\nThen w'] });
  });

  it('returns no AC when there is no section, and handles null', () => {
    expect(splitDescription(adfDoc(textToAdfBlocks('just text')))).toEqual({ text: 'just text', acceptanceCriteria: [] });
    expect(splitDescription(null)).toEqual({ text: '', acceptanceCriteria: [] });
  });

  it('parses a dedicated AC field in ADF, string and array forms', () => {
    expect(parseAcField(buildAcFieldAdf(['a', 'b']))).toEqual(['a', 'b']);
    expect(parseAcField('- one\n- two')).toEqual(['one', 'two']);
    expect(parseAcField('g1 line1\ng1 line2\n\ng2')).toEqual(['g1 line1\ng1 line2', 'g2']);
    expect(parseAcField(['x', ' ', 'y'])).toEqual(['x', 'y']);
    expect(parseAcField(null)).toEqual([]);
    expect(parseAcField(7)).toEqual([]);
  });
});
