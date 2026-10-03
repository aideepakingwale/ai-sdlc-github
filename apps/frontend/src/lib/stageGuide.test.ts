import { describe, expect, it } from 'vitest';
import { deriveGuide, type GuideInput } from './stageGuide';

const base: GuideInput = {
  stageStatus: 'NOT_STARTED', canEdit: true, blockedOn: [], hasPlan: false, planBuilding: false,
  planOutdated: false, planFresh: false, generating: false, hasOutputs: false, reviewerRole: 'TA',
};
const states = (i: Partial<GuideInput>) => deriveGuide({ ...base, ...i }).steps.map((s) => s.state);
const next = (i: Partial<GuideInput>) => deriveGuide({ ...base, ...i }).next;

describe('stage guidance', () => {
  it('always has the same four steps', () => {
    expect(deriveGuide(base).steps.map((s) => s.id)).toEqual(['describe', 'plan', 'generate', 'review']);
  });

  it('starts at Describe and says exactly what to do', () => {
    expect(states({})).toEqual(['current', 'todo', 'todo', 'todo']);
    expect(next({})).toMatchObject({ action: 'review-plan', tone: 'info' });
    expect(next({}).title).toMatch(/describe/i);
  });

  it('flags an out-of-date plan in yellow and offers Update plan', () => {
    expect(states({ hasPlan: true, planOutdated: true })).toEqual(['done', 'attention', 'todo', 'todo']);
    expect(next({ hasPlan: true, planOutdated: true })).toMatchObject({ tone: 'warning', action: 'update-plan' });
  });

  it('a final plan means Generate is next (green)', () => {
    expect(states({ hasPlan: true, planFresh: true })).toEqual(['done', 'done', 'current', 'todo']);
    expect(next({ hasPlan: true, planFresh: true })).toMatchObject({ tone: 'success', action: 'generate' });
  });

  it('shows progress while the plan builds and while generating', () => {
    expect(next({ planBuilding: true })).toMatchObject({ action: 'wait' });
    expect(states({ generating: true, stageStatus: 'IN_PROGRESS' })).toEqual(['done', 'done', 'current', 'todo']);
    expect(next({ generating: true }).body).toMatch(/leave|background/i);
  });

  it('awaiting review is a yellow call to action; approved is green and all done', () => {
    expect(states({ stageStatus: 'PENDING_REVIEW' })).toEqual(['done', 'done', 'done', 'attention']);
    expect(next({ stageStatus: 'PENDING_REVIEW' })).toMatchObject({ tone: 'warning', action: 'open-gate' });
    expect(states({ stageStatus: 'APPROVED' })).toEqual(['done', 'done', 'done', 'done']);
    expect(next({ stageStatus: 'APPROVED' }).tone).toBe('success');
  });

  it('escalation is red; blocked and read-only explain why nothing can be done', () => {
    expect(next({ stageStatus: 'ESCALATED' }).tone).toBe('error');
    expect(states({ stageStatus: 'ESCALATED' })[2]).toBe('error');
    expect(next({ blockedOn: ['Solution Architecture'] }).title).toMatch(/Waiting for Solution Architecture/);
    expect(next({ canEdit: false })).toMatchObject({ tone: 'neutral', action: 'none' });
  });

  it('changes requested guides the user to update the instructions', () => {
    expect(next({ stageStatus: 'AMEND_REQUESTED' }).title).toMatch(/changes were requested/i);
  });
});
