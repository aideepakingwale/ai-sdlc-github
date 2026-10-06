// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { chipKey, chipsIn, joinSegments, mentionQuery, serializeEditor, splitPrompt, tokenFor, type Mention } from './mentions';

const M = (kind: Mention['kind'], id: string, label: string): Mention => ({ kind, id, label, sub: '' });
const prd = M('artifact', 'a1', 'Product Requirements Document');
const design = M('artifact', 'a2', 'Design');
const design2 = M('artifact', 'a3', 'Design v2');
const file = M('file', 'f1', 'spec.docx');

describe('@ references in the prompt', () => {
  it('stores a reference as @ + the label with underscores', () => {
    expect(tokenFor('Product Requirements Document')).toBe('@Product_Requirements_Document');
    expect(tokenFor('  spec.docx ')).toBe('@spec.docx');
  });

  it('turns known tokens into chips and leaves everything else as text', () => {
    const segs = splitPrompt('Base it on @Product_Requirements_Document, and @spec.docx. Email @bob stays text.', [prd, file, design]);
    expect(segs.map((s) => (s.type === 'chip' ? `[${s.mention.id}]` : s.text))).toEqual(['Base it on ', '[a1]', ', and ', '[f1]', '. Email @bob stays text.']);
  });

  it('only matches at a word boundary and prefers the longest label', () => {
    const segs = splitPrompt('see @Design_v2 and @Design_extra and a@Design', [design, design2]);
    expect(segs.map((s) => (s.type === 'chip' ? `[${s.mention.id}]` : s.text))).toEqual(['see ', '[a3]', ' and @Design_extra and a@Design']);
  });

  it('round-trips: joining the segments gives the original prompt', () => {
    const text = 'Use @spec.docx for scope.\nAlso @Product_Requirements_Document';
    expect(joinSegments(splitPrompt(text, [prd, file]))).toBe(text);
  });

  it('detects an @query being typed at the caret', () => {
    expect(mentionQuery('Use @Prod')).toEqual({ query: 'Prod', start: 4 });
    expect(mentionQuery('@')).toEqual({ query: '', start: 0 });
    expect(mentionQuery('mail me@home')).toBeNull();           // not at a word start
    expect(mentionQuery('done @Prod ')).toBeNull();            // the query ended
  });

  it('reads the editor DOM back into prompt text, chips as their token, breaks as newlines', () => {
    const root = document.createElement('div');
    root.innerHTML = 'Use <span data-chip-label="Product Requirements Document" data-chip-kind="artifact" data-chip-id="a1">📄 PRD<button>×</button></span>&nbsp;now<br>second line<div>third</div>';
    expect(serializeEditor(root)).toBe('Use @Product_Requirements_Document now\nsecond line\nthird');
    expect(chipsIn(root).map(chipKey)).toEqual(['artifact:a1']);
  });
});
