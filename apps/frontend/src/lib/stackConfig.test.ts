import { describe, expect, it } from 'vitest';
import { decidedCount, draftError, fromDraft, groupLayers, identifiedIds, render, sourceLabel, toDraft, type LayerDef, type StackEntry } from './stackConfig';

const e = (over: Partial<StackEntry>): StackEntry => ({ id: 'backend', layer: 'backend', component: '', technology: 'Python', version: '3.12', extras: ['FastAPI'], notes: '', status: 'identified',
  source: 'llm', sourceStage: 2, rationale: '', evidence: '', confidence: 'high', updatedAt: '', updatedBy: '', ...over });
const LAYERS: LayerDef[] = [{ id: 'frontend', label: 'Frontend', hint: '', options: [] }, { id: 'backend', label: 'Backend', hint: '', options: [] }];

describe('stack entries', () => {
  it('renders technology, version and frameworks', () => {
    expect(render(e({}))).toBe('Python 3.12 + FastAPI');
    expect(render(e({ extras: [], version: '' }))).toBe('Python');
    expect(render(e({ technology: '', version: '', extras: [] }))).toBe('');
  });
  it('says where a value came from', () => {
    expect(sourceLabel(e({}))).toBe('Identified in stage 2');
    expect(sourceLabel(e({ source: 'ta' }))).toBe('Decided by the Technical Architect');
    expect(sourceLabel(e({ source: 'detected' }))).toBe('Detected in the uploaded codebase');
    expect(sourceLabel(e({ status: 'pinned', updatedBy: 'pm@x' }))).toBe('Pinned by pm@x');
    expect(sourceLabel(e({ status: 'open' }))).toMatch(/open/i);
  });
  it('groups by catalog layer and keeps unknown layers in a trailing row', () => {
    const rows = groupLayers(LAYERS, [e({}), e({ id: 'x', layer: 'cache' })]);
    expect(rows.map((r) => r.layer.id)).toEqual(['frontend', 'backend', 'other']);
    expect(rows[1]!.entries).toHaveLength(1);
  });
  it('counts what is decided and what needs confirming', () => {
    const list = [e({}), e({ id: 'cache', layer: 'cache', status: 'open', technology: '' }), e({ id: 'hosting', layer: 'hosting', status: 'pinned', technology: 'AWS', version: '', extras: [] })];
    expect(decidedCount(list)).toBe(2);
    expect(identifiedIds(list)).toEqual(['backend']);
  });
});

describe('the edit form', () => {
  it('round-trips an entry and splits frameworks', () => {
    const d = toDraft('backend', e({ status: 'pinned' }));
    expect(d.extras).toBe('FastAPI');
    expect(fromDraft({ ...d, extras: ' FastAPI , Pydantic ,' }).extras).toEqual(['FastAPI', 'Pydantic']);
  });
  it('needs a technology unless the layer is left open', () => {
    expect(draftError(toDraft('cache'))).toMatch(/technology/);
    expect(draftError({ ...toDraft('cache'), status: 'open' })).toBe('');
  });
});
