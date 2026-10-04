// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type * as AgileApi from '../../api/agile';
import type { AgileOverview, BacklogItem, Proposal } from '../../api/agile';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  agileApi: {
    backlog: vi.fn(), proposal: vi.fn(), editProposal: vi.fn(), setStatus: vi.fn(), addToSprint: vi.fn(),
    removeFromSprint: vi.fn(), move: vi.fn(), createItem: vi.fn(), patchItem: vi.fn(), jira: vi.fn(), jiraSync: vi.fn(),
    startSprint: vi.fn(), harden: vi.fn(), cancelSprint: vi.fn(), overview: vi.fn(), enable: vi.fn(), index: vi.fn(), updateSettings: vi.fn(),
    questions: vi.fn(), previewRelease: vi.fn(), startRelease: vi.fn(), suggestCarry: vi.fn(), carry: vi.fn(), extendCarry: vi.fn(),
    claim: vi.fn(), releaseEpics: vi.fn(), mapEpic: vi.fn(), unmapEpic: vi.fn(), reconcile: vi.fn(),
  },
}));
vi.mock('../../api/agile', async (orig) => ({ ...(await orig<typeof AgileApi>()), agileApi: api.agileApi }));

import BacklogView from './BacklogView';
import ProposalReview from './ProposalReview';
import SprintBoard from './SprintBoard';

const now = new Date().toISOString();
const item = (o: Partial<BacklogItem>): BacklogItem => ({
  id: o.key ?? 'DM-1', key: 'DM-1', type: 'story', title: 'A story', description: '', acceptanceCriteria: ['ok'], estimate: 3, rank: 1,
  status: 'refined', epicKey: null, components: [], labels: [], iterationId: null, jiraKey: null, version: 1, problems: [],
  createdAt: now, updatedAt: now, ...o,
});
const overview = (o: Partial<AgileOverview> = {}): AgileOverview => ({
  enabled: true, methodology: 'scrum', permissions: { canManage: true, canRun: true }, iterations: [], backlog: { refined: 2, ready: 1 },
  currentIteration: null, ...o,
});
const sprint = { id: 'i1', number: 1, label: 'S-001', releaseId: 'r', goal: 'g', status: 'active' as const, capacity: 10, startsOn: null, endsOn: null, summary: {} };

let root: Root; let host: HTMLElement;
const render = async (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  await act(async () => { root.render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>); });
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
};
const click = async (el: Element | null) => { expect(el).not.toBeNull(); await act(async () => { (el as HTMLElement).click(); await new Promise((r) => setTimeout(r, 10)); }); };
const byText = (sel: string, text: string | RegExp) => [...host.querySelectorAll(sel)].find((e) => (typeof text === 'string' ? e.textContent?.trim() === text : text.test(e.textContent ?? ''))) ?? null;

beforeEach(() => {
  vi.clearAllMocks();
  api.agileApi.jira.mockResolvedValue({ enabled: false, projectKey: null });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

describe('BacklogView', () => {
  it('offers "Mark ready" only for items that satisfy the Definition of Ready', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 2, points: 3 }, items: [
      item({ key: 'DM-1', title: 'Good one' }),
      item({ key: 'DM-2', title: 'Needs work', problems: ['it has no estimate'], estimate: null }),
    ] });
    await render(<BacklogView projectId="p" overview={overview()} />);
    expect(host.textContent).toContain('Good one');
    expect(byText('button', 'Mark ready')).not.toBeNull();
    expect(byText('button', 'Fix to make ready')).not.toBeNull();
    expect(host.textContent).toContain('Not ready to plan: it has no estimate');
  });

  it('hides every editing control from people who cannot run the backlog', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 1, points: 3 }, items: [item({ key: 'DM-1' })] });
    await render(<BacklogView projectId="p" overview={overview({ permissions: { canManage: false, canRun: false } })} />);
    expect(host.querySelector('input[aria-label="New backlog item"]')).toBeNull();
    expect(byText('button', 'Mark ready')).toBeNull();
    expect(host.querySelector('button[aria-label="Move DM-1 up"]')).toBeNull();
  });

  it('asks before overcommitting the sprint and only then forces it', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 1, points: 3 }, items: [item({ key: 'DM-5', status: 'ready' })] });
    api.agileApi.addToSprint
      .mockRejectedValueOnce(new Error('Adding DM-5 would put the sprint at 13 of 10 points. Confirm to overcommit.'))
      .mockResolvedValueOnce(item({ key: 'DM-5', status: 'in_sprint' }));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await render(<BacklogView projectId="p" overview={overview({ currentIteration: sprint })} />);
    await click(byText('button', /Ready/));
    await click(byText('button', 'Add to sprint'));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(api.agileApi.addToSprint.mock.calls).toEqual([['p', 'DM-5', false, 'i1'], ['p', 'DM-5', true, 'i1']]);
  });

  it('does not force the commitment when the person declines', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 1, points: 3 }, items: [item({ key: 'DM-5', status: 'ready' })] });
    api.agileApi.addToSprint.mockRejectedValue(new Error('would exceed. Confirm to overcommit.'));
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    await render(<BacklogView projectId="p" overview={overview({ currentIteration: sprint })} />);
    await click(byText('button', /Ready/));
    await click(byText('button', 'Add to sprint'));
    expect(api.agileApi.addToSprint).toHaveBeenCalledTimes(1);
  });
});

describe('SprintBoard', () => {
  it('puts items in the right columns and lets only the PO accept work', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 3, points: 11 }, items: [
      item({ key: 'DM-1', title: 'Todo item', status: 'in_sprint', estimate: 3 }),
      item({ key: 'DM-2', title: 'Doing item', status: 'in_progress', estimate: 5 }),
      item({ key: 'DM-3', title: 'Done item', status: 'done', estimate: 3 }),
    ] });
    await render(<SprintBoard projectId="p" overview={overview({ currentIteration: sprint })} />);
    const col = (name: string) => host.querySelector(`section[aria-label="${name}"]`)?.textContent ?? '';
    expect(col('To do')).toContain('Todo item'); expect(col('In progress')).toContain('Doing item'); expect(col('Done')).toContain('Done item');
    expect(byText('button', 'Accept as done')).not.toBeNull();
    expect(host.textContent).toContain('11 pts'); expect(host.textContent).toContain('11 of 10 pts — 1 over capacity');
  });

  it('shows no "Accept as done" to someone who cannot run the sprint', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 1, points: 5 }, items: [item({ key: 'DM-2', status: 'in_progress', estimate: 5 })] });
    await render(<SprintBoard projectId="p" overview={overview({ currentIteration: sprint, permissions: { canManage: false, canRun: false } })} />);
    expect(byText('button', 'Accept as done')).toBeNull();
  });

  it('explains an empty state instead of showing a blank board', async () => {
    await render(<SprintBoard projectId="p" overview={overview({ currentIteration: null })} />);
    expect(host.textContent).toContain('No sprint is running');
  });
});

describe('ProposalReview', () => {
  const plan = (o: Partial<Proposal> = {}): Proposal => ({
    id: 'pr', kind: 'plan', phase: 4, iterationId: 'i1', status: 'proposed', version: 3, warnings: ['heads up'], createdAt: now,
    payload: { goal: 'Ship', capacity: 10, points: 8, items: [{ key: 'DM-1', title: 'One', estimate: 5 }, { key: 'DM-2', title: 'Two', estimate: 3 }] }, ...o,
  });

  it('shows warnings and the capacity result; nothing is applied yet', async () => {
    api.agileApi.proposal.mockResolvedValue({ proposal: plan() });
    await render(<ProposalReview projectId="p" phase={4} role="plan" canEdit />);
    expect(host.textContent).toContain('heads up'); expect(host.textContent).toContain('8 of 10 pts');
    expect(host.textContent).toContain('Not applied yet'); expect(host.textContent).toContain('Applied when you approve this stage');
  });

  it('unticking an item and re-checking sends the remaining items with the version', async () => {
    api.agileApi.proposal.mockResolvedValue({ proposal: plan() });
    api.agileApi.editProposal.mockResolvedValue(plan({ version: 4 }));
    await render(<ProposalReview projectId="p" phase={4} role="plan" canEdit />);
    await click(host.querySelector('input[aria-label="Include Two"]'));
    expect(host.textContent).toContain('5 of 10 pts');          // live capacity feedback before saving
    await click(byText('button', /Remove 1 and re-check/));
    const [proj, id, payload, version] = api.agileApi.editProposal.mock.calls[0]!;
    expect([proj, id, version]).toEqual(['p', 'pr', 3]);
    expect((payload as { items: Array<{ key: string }> }).items.map((i) => i.key)).toEqual(['DM-1']);
  });

  it('is read-only once applied or for people who cannot edit', async () => {
    api.agileApi.proposal.mockResolvedValue({ proposal: plan({ status: 'applied' }) });
    await render(<ProposalReview projectId="p" phase={4} role="plan" canEdit />);
    expect((host.querySelector('input[aria-label="Include One"]') as HTMLInputElement).disabled).toBe(true);
    expect(host.textContent).toContain('Applied');
  });

  it('renders refine operations with their acceptance criteria and a design delta with its new text', async () => {
    api.agileApi.proposal.mockResolvedValue({ proposal: plan({ kind: 'refine', payload: { summary: 's', ops: [
      { ref: 'a', op: 'create', target: null, type: 'story', title: 'Pay', description: '', acceptanceCriteria: ['card works'], estimate: 5, components: [], epic: null, rationale: 'because' }] } }) });
    await render(<ProposalReview projectId="p" phase={4} role="refine" canEdit={false} />);
    expect(host.textContent).toContain('new story'); expect(host.textContent).toContain('card works'); expect(host.textContent).toContain('because');
    api.agileApi.proposal.mockResolvedValue({ proposal: plan({ kind: 'delta', payload: { summary: 'd', changes: [
      { component: 'orders', section: 'Endpoints', op: 'replace', content: 'GET /orders', rationale: 'new route' }] } }) });
    await act(async () => root.unmount()); root = createRoot(host);
    await render(<ProposalReview projectId="p" phase={5} role="build" canEdit={false} />);
    expect(host.textContent).toContain('orders'); expect(host.textContent).toContain('Endpoints'); expect(host.textContent).toContain('Show the new text');
  });

  it('renders nothing when there is no proposal yet', async () => {
    api.agileApi.proposal.mockResolvedValue({ proposal: null });
    await render(<ProposalReview projectId="p" phase={4} role="plan" canEdit />);
    expect(host.textContent).toBe('');
  });
});
