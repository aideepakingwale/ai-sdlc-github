import { describe, expect, it } from 'vitest';
import type { FlowStage } from '../api/flow';
import type { AgileOverview, BacklogItem, Iteration } from '../api/agile';
import { applyFilter, capacityMeter, daysLeft, filterCounts, groupStages, nextStep } from './agileView';

const st = (o: Partial<FlowStage>): FlowStage => ({
  phase: 1, key: 'k', name: 'N', persona: 'p', template: 7, reviewerRole: 'PO', team: [], teamMembers: [], inputs: [], outputs: [],
  dependsOn: [], level: 0, status: 'NOT_STARTED', color: 'slate', artifactCount: 0, reviewedBy: null, updatedAt: null,
  assignee: null, canReview: false, canRetrigger: false, ...o,
});
const item = (o: Partial<BacklogItem>): BacklogItem => ({
  id: 'i', key: 'DM-1', type: 'story', title: 't', description: '', acceptanceCriteria: [], estimate: null, rank: 1, status: 'new',
  epicKey: null, components: [], labels: [], iterationId: null, jiraKey: null, version: 1, problems: [], createdAt: '', updatedAt: '', ...o,
});
const it1: Iteration = { id: 'i1', number: 1, label: 'S-001', releaseId: 'r', goal: 'Ship it', status: 'active', capacity: 20, startsOn: null, endsOn: null, summary: {} };
const ov = (o: Partial<AgileOverview>): AgileOverview => ({ enabled: true, methodology: 'scrum', permissions: { canManage: true, canRun: true }, iterations: [], ...o });

describe('capacityMeter', () => {
  it('is red only when over capacity', () => {
    expect(capacityMeter(25, 20)).toMatchObject({ tone: 'error', over: 5, percent: 125 });
    expect(capacityMeter(20, 20)).toMatchObject({ tone: 'warning', over: 0 });
  });
  it('warns on empty and nearly full sprints, is green otherwise', () => {
    expect(capacityMeter(0, 20).tone).toBe('warning');
    expect(capacityMeter(18, 20).tone).toBe('warning');
    expect(capacityMeter(10, 20)).toMatchObject({ tone: 'success', percent: 50, label: '10 of 20 pts' });
  });
  it('handles no capacity without dividing by zero', () => {
    expect(capacityMeter(5, 0)).toMatchObject({ tone: 'neutral', percent: 0 });
  });
});

describe('backlog filters', () => {
  const items = [item({ status: 'new' }), item({ status: 'refined' }), item({ status: 'ready' }), item({ status: 'in_progress' }), item({ status: 'done' }), item({ status: 'dropped' })];
  it('groups statuses the way people think about them', () => {
    expect(applyFilter(items, 'needs-work')).toHaveLength(2);
    expect(applyFilter(items, 'sprint')).toHaveLength(1);
    expect(applyFilter(items, 'dropped')).toHaveLength(1);
  });
  it('"all" hides dropped items', () => {
    expect(applyFilter(items, 'all')).toHaveLength(5);
  });
  it('counts come from the overview', () => {
    expect(filterCounts({ new: 2, refined: 1, ready: 4, in_sprint: 1, in_progress: 2, done: 5, dropped: 3 }))
      .toEqual({ 'needs-work': 3, ready: 4, sprint: 3, done: 5, dropped: 3, all: 15 });
    expect(filterCounts(undefined).all).toBe(0);
  });
});

describe('groupStages', () => {
  const stages = [
    st({ phase: 1, key: 'vision', scope: 'project' }), st({ phase: 2, key: 'runway' }),
    st({ phase: 9, key: 'refine@S-002', scope: 'iteration', iterationLabel: 'S-002', iteration: 2 }),
    st({ phase: 3, key: 'refine@S-001', scope: 'iteration', iterationLabel: 'S-001', iteration: 1 }),
    st({ phase: 8, key: 'release@R-001', scope: 'release', release: 'R-001' }),
  ];
  it('puts foundation first, sprints in order, releases last', () => {
    const g = groupStages(stages, [{ ...it1, label: 'S-001', status: 'closed' }]);
    expect(g.map((x) => x.label)).toEqual(['Foundation', 'S-001', 'S-002', 'Release R-001']);
    expect(g[1]?.status).toBe('closed');
    expect(g[0]?.stages.map((s) => s.key)).toEqual(['vision', 'runway']);
  });
});

describe('nextStep', () => {
  const foundationDone = [st({ phase: 1, scope: 'project', status: 'APPROVED' })];
  it('guides through the foundation first', () => {
    const n = nextStep(ov({}), [st({ phase: 1, name: 'Vision', scope: 'project', status: 'NOT_STARTED' })]);
    expect(n).toMatchObject({ action: 'open-stage', stageSeq: 1 });
    expect(n.title).toContain('Vision');
  });
  it('offers to start a sprint, then points at what is waiting', () => {
    expect(nextStep(ov({}), foundationDone).action).toBe('start-sprint');
    const stages = [...foundationDone, st({ phase: 3, name: 'Refine', iterationLabel: 'S-001', status: 'PENDING_REVIEW', reviewerRole: 'PO' })];
    expect(nextStep(ov({ currentIteration: it1 }), stages)).toMatchObject({ tone: 'warning', action: 'open-stage', stageSeq: 3 });
  });
  it('after a closed sprint it offers the next sprint or a release', () => {
    const n = nextStep(ov({ iterations: [{ ...it1, status: 'closed' }] }), foundationDone);
    expect(n.tone).toBe('success');
    expect(n.action).toBe('start-sprint');
  });
  it('is not enabled → invites to choose a method', () => {
    expect(nextStep(ov({ enabled: false, methodology: 'waterfall' }), []).title).toMatch(/how this project is delivered/);
  });
});

describe('daysLeft', () => {
  it('counts whole days and tolerates a missing date', () => {
    expect(daysLeft('2026-03-10', new Date('2026-03-07T10:00:00'))).toBe(4);
    expect(daysLeft('2026-03-01', new Date('2026-03-07T10:00:00'))).toBeLessThanOrEqual(0);
    expect(daysLeft(null)).toBeNull();
  });
});
