import { describe, expect, it } from 'vitest';
import type { FlowStage } from '../api/flow';
import type { AgileOverview, BacklogItem, Iteration, Release } from '../api/agile';
import { applyFilter, capacityMeter, carryBudgetUsed, countByStatus, daysLeft, filterCounts, groupStages, nextStep, releaseTree, stepProblem, wizardSteps } from './agileView';

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
  it('only counts sprints closed in the CURRENT release', () => {
    const rel2 = { id: 'r2', number: 2, code: 'R-002', name: 'Release 2', goal: '', status: 'open' as const };
    const n = nextStep(ov({ currentRelease: rel2, iterations: [{ ...it1, releaseId: 'r1', status: 'closed' }] }), foundationDone);
    expect(n.title).toMatch(/first sprint/i);
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


describe('releases as a lineage', () => {
  const rel = (o: Partial<Release>): Release => ({ id: 'r', number: 1, code: 'R-001', name: 'n', goal: '', status: 'open', ...o });
  it('nests every fork under the release it came from, in creation order', () => {
    const t = releaseTree([rel({ id: 'c', number: 3, code: 'R-003', forkedFromId: 'a' }), rel({ id: 'b', number: 2, code: 'R-002' }),
      rel({ id: 'a', number: 1, code: 'R-001' }), rel({ id: 'd', number: 4, code: 'R-004', forkedFromId: 'c' })]);
    expect(t.map((n) => [n.release.code, n.depth])).toEqual([['R-001', 0], ['R-003', 1], ['R-004', 2], ['R-002', 0]]);
  });
  it('treats a fork of an unknown release as a root', () => {
    expect(releaseTree([rel({ id: 'x', forkedFromId: 'gone' })])[0]?.depth).toBe(0);
  });
  it('labels sprints with their release only when several releases run', () => {
    const one = groupStages([st({ phase: 3, key: 'a@S-001', iterationLabel: 'S-001', iteration: 1, release: 'R-001' })]);
    expect(one.find((g) => g.key === 'S-001')?.label).toBe('S-001');
    const two = groupStages([st({ phase: 3, key: 'a@S-001', iterationLabel: 'S-001', iteration: 1, release: 'R-001' }),
      st({ phase: 9, key: 'a@S-002', iterationLabel: 'S-002', iteration: 2, release: 'R-002' })]);
    expect(two.map((g) => g.label)).toEqual(['S-001 · R-001', 'S-002 · R-002']);
  });
  it('counts a scoped list itself instead of using the project-wide numbers', () => {
    expect(countByStatus([item({ status: 'ready' }), item({ status: 'ready' }), item({ status: 'done' })])).toEqual({ ready: 2, done: 1 });
  });
});

describe('start-release wizard rules', () => {
  const canFork = (id: string): boolean => id === 'closed';
  it('only a fork has context and items to carry', () => {
    expect(wizardSteps({ startFrom: 'blank' })).toEqual(['basics', 'source', 'stages', 'intake', 'review']);
    expect(wizardSteps({ startFrom: 'fork' })).toEqual(['basics', 'source', 'carry', 'items', 'stages', 'intake', 'review']);
  });
  it('says why the person cannot go on', () => {
    expect(stepProblem('basics', { name: '  ' }, canFork)).toMatch(/name/);
    expect(stepProblem('basics', { name: 'x' }, canFork)).toBeNull();
    expect(stepProblem('source', { startFrom: 'fork' }, canFork)).toMatch(/Choose/);
    expect(stepProblem('source', { startFrom: 'fork', sourceRelease: 'open' }, canFork)).toMatch(/closed sprint/);
    expect(stepProblem('source', { startFrom: 'fork', sourceRelease: 'closed' }, canFork)).toBeNull();
    expect(stepProblem('items', { unfinishedItems: 'selected', items: [] }, canFork)).toMatch(/Pick at least one/);
    expect(stepProblem('items', { unfinishedItems: 'all' }, canFork)).toBeNull();
  });
  it('stops a selection before it exceeds the carry budget', () => {
    const cands = [{ id: 'a', kind: 'spec-section' as const, title: 'a', bytes: 600, tooLarge: false }, { id: 'b', kind: 'decision' as const, title: 'b', bytes: 600, tooLarge: false }];
    expect(carryBudgetUsed(['a'], cands, { maxItems: 2, maxBytes: 1000 })).toMatchObject({ items: 1, bytes: 600, over: null });
    expect(carryBudgetUsed(['a', 'b'], cands, { maxItems: 2, maxBytes: 1000 }).over).toMatch(/KB/);
    expect(carryBudgetUsed(['a', 'b'], cands, { maxItems: 1, maxBytes: 5000 }).over).toMatch(/At most 1/);
    expect(carryBudgetUsed(['ghost'], cands, { maxItems: 2, maxBytes: 1000 })).toMatchObject({ items: 0 });
  });
});
