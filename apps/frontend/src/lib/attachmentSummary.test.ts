import { describe, expect, it } from 'vitest';
import { summariseExtraction } from './attachmentSummary';

describe('summariseExtraction', () => {
  it('summarises a document with pages, tables and figures', () => {
    expect(summariseExtraction({ pages: 42, tables: 6, figures_found: 11, figures_described: 9 }))
      .toBe('42 pages · 6 tables · 9 of 11 figures read');
  });
  it('uses singular forms and handles decks and diagrams', () => {
    expect(summariseExtraction({ slides: 1, diagram_edges: 1, figures_found: 1, figures_described: 0 }))
      .toBe('1 slide · 1 diagram connection · 0 of 1 figure read');
  });
  it('is empty for plain text or missing stats', () => {
    expect(summariseExtraction({})).toBe('');
    expect(summariseExtraction(undefined)).toBe('');
  });
});
