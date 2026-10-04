// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type * as AgileApi from '../../api/agile';
import type { AgileOverview, Release } from '../../api/agile';
import { ApiError } from '../../api/client';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  agileApi: {
    questions: vi.fn(), previewRelease: vi.fn(), startRelease: vi.fn(), suggestCarry: vi.fn(), carry: vi.fn(), extendCarry: vi.fn(),
    claim: vi.fn(), releaseEpics: vi.fn(), unmapEpic: vi.fn(), backlog: vi.fn(), jira: vi.fn(), createItem: vi.fn(), setStatus: vi.fn(),
    addToSprint: vi.fn(), removeFromSprint: vi.fn(), move: vi.fn(), patchItem: vi.fn(),
  },
}));
vi.mock('../../api/agile', async (orig) => ({ ...(await orig<typeof AgileApi>()), agileApi: api.agileApi }));

import BacklogView from './BacklogView';
import ReleaseBar from './ReleaseBar';
import ReleaseContext from './ReleaseContext';
import ReleaseWizard from './ReleaseWizard';

const rel = (o: Partial<Release>): Release => ({ id: 'r1', number: 1, code: 'R-001', name: 'Shop', goal: '', status: 'open', intakeRule: 'pool', usePool: true, stagePreset: 'inherit', ...o });
const overview = (o: Partial<AgileOverview> = {}): AgileOverview => ({
  enabled: true, methodology: 'scrum', permissions: { canManage: true, canRun: true }, iterations: [], backlog: {},
  releases: [rel({}), rel({ id: 'r2', number: 2, code: 'R-002', name: 'Mobile', forkedFrom: 'R-001', forkedFromId: 'r1', forkBaseline: { sprint: 'S-004' } })],
  currentRelease: rel({}), currentIteration: null, ...o,
});
const questions = (o: Record<string, unknown> = {}) => ({
  version: 1, defaults: {}, locked: [], releases: [{ id: 'r1', code: 'R-001', name: 'Shop', status: 'open', canFork: true }, { id: 'r3', code: 'R-003', name: 'New', status: 'open', canFork: false }],
  questions: [
    { id: 'startFrom', type: 'choice', title: 'Start from', options: [{ id: 'blank', label: 'A blank release' }, { id: 'fork', label: 'A fork of an earlier release' }] },
    { id: 'goal', type: 'text', title: 'What is this release for?', help: 'New scope' },
    { id: 'stagePreset', type: 'choice', title: 'Stages', options: [{ id: 'inherit', label: 'Same stages' }, { id: 'hotfix', label: 'Fix and ship' }, { id: 'custom', label: 'Custom' }] },
    { id: 'intakeRule', type: 'choice', title: 'Intake', options: [{ id: 'pool', label: 'To the shared pool' }, { id: 'epic', label: 'By epic' }] },
    { id: 'firstSprint', type: 'choice', title: 'First sprint', options: [{ id: 'later', label: 'Start it later' }, { id: 'now', label: 'Start it now' }] },
  ],
  ...o,
});

let root: Root; let host: HTMLElement;
const render = async (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  await act(async () => { root.render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>); });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
};
const click = async (el: Element | null) => { expect(el).not.toBeNull(); await act(async () => { (el as HTMLElement).click(); await new Promise((r) => setTimeout(r, 15)); }); };
const type = async (el: Element | null, value: string) => {
  await act(async () => {
    const input = el as HTMLInputElement;
    const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
};
const byText = (sel: string, text: string | RegExp) => [...host.querySelectorAll(sel)].find((e) => (typeof text === 'string' ? e.textContent?.trim() === text : text.test(e.textContent ?? ''))) ?? null;

beforeEach(() => {
  vi.clearAllMocks();
  api.agileApi.jira.mockResolvedValue({ enabled: false, projectKey: null });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

describe('ReleaseBar', () => {
  it('shows every release as a lineage and switches the focus', async () => {
    const onFocus = vi.fn();
    await render(<ReleaseBar overview={overview()} focus="r1" onFocus={onFocus} onNew={() => undefined} />);
    const chips = [...host.querySelectorAll('button[aria-pressed]')];
    expect(chips.map((c) => c.getAttribute('aria-pressed'))).toEqual(['true', 'false']);
    expect(chips[1]?.getAttribute('title')).toContain('forked from R-001 at S-004');
    await click(chips[1]!);
    expect(onFocus).toHaveBeenCalledWith('r2');
  });

  it('offers "New release" only to people who can run releases, and flags an unfinished setup', async () => {
    await render(<ReleaseBar overview={overview({ permissions: { canManage: false, canRun: false } })} focus={null} onFocus={() => undefined} onNew={() => undefined} />);
    expect(byText('button', 'New release')).toBeNull();
    await act(async () => root.unmount()); root = createRoot(host);
    const incomplete = overview({ releases: [rel({ setupComplete: false })] });
    await render(<ReleaseBar overview={incomplete} focus={null} onFocus={() => undefined} onNew={() => undefined} />);
    expect(host.textContent).toContain('incomplete');
  });

  it('hides closed releases behind a link', async () => {
    const o = overview({ releases: [rel({}), rel({ id: 'r9', number: 9, code: 'R-009', status: 'closed' })] });
    await render(<ReleaseBar overview={o} focus="r1" onFocus={() => undefined} onNew={() => undefined} />);
    expect(host.textContent).not.toContain('R-009');
    await click(byText('button', '1 closed'));
    expect(host.textContent).toContain('R-009');
  });
});

describe('ReleaseWizard', () => {
  it('walks a blank release through a checked preview to one confirmation', async () => {
    api.agileApi.questions.mockResolvedValue(questions());
    api.agileApi.previewRelease.mockResolvedValue({ valid: true, errors: [], warnings: [], steps: ['Create release “Mobile”', 'Stage set: Same stages'], summary: {} });
    api.agileApi.startRelease.mockResolvedValue({ release: rel({ id: 'rN' }), progress: {} });
    const onDone = vi.fn();
    await render(<ReleaseWizard projectId="p" onClose={() => undefined} onDone={onDone} />);
    expect((byText('button', 'Next') as HTMLButtonElement).disabled).toBe(true);          // a name is required
    expect(host.textContent).toContain('Give the release a name');
    await type(host.querySelector('input'), 'Mobile');
    for (let i = 0; i < 4; i++) await click(byText('button', 'Next'));                   // basics → source → stages → intake → review
    expect(api.agileApi.previewRelease).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('Starting this release will:'); expect(host.textContent).toContain('Stage set: Same stages');
    await click(byText('button', 'Start release'));
    expect(api.agileApi.startRelease.mock.calls[0]![1]).toMatchObject({ name: 'Mobile', startFrom: 'blank', intakeRule: 'pool', usePool: true });
    expect(onDone).toHaveBeenCalledWith('rN');
  });

  it('cannot fork a release that has no closed sprint, and says why', async () => {
    api.agileApi.questions.mockResolvedValue(questions());
    await render(<ReleaseWizard projectId="p" onClose={() => undefined} onDone={() => undefined} />);
    await type(host.querySelector('input'), 'Fork');
    await click(byText('button', 'Next'));
    await click(host.querySelector('input[type="radio"][name="Start from"]:not(:checked)'));
    const select = host.querySelector('select') as HTMLSelectElement;
    expect([...select.options].find((o) => o.value === 'r3')?.disabled).toBe(true);
    expect((byText('button', 'Next') as HTMLButtonElement).disabled).toBe(true);
  });

  it('suggests context for a fork, pre-selects it, and sends only what the person keeps', async () => {
    api.agileApi.questions.mockResolvedValue(questions({ defaults: { startFrom: 'fork' } }));
    api.agileApi.suggestCarry.mockResolvedValue({
      source: 'ai', rejected: [], budget: { maxItems: 40, maxBytes: 60000 },
      suggestions: [{ id: 'spec:orders/Endpoints', kind: 'spec-section', title: 'orders / Endpoints', reason: 'the scope extends the API', bytes: 100 }],
      candidates: [{ id: 'spec:orders/Endpoints', kind: 'spec-section', title: 'orders / Endpoints', bytes: 100, tooLarge: false },
        { id: 'spec:billing/Payments', kind: 'spec-section', title: 'billing / Payments', bytes: 100, tooLarge: false }],
    });
    api.agileApi.previewRelease.mockResolvedValue({ valid: true, errors: [], warnings: [], steps: ['Carry 1 context entry'], summary: {} });
    await render(<ReleaseWizard projectId="p" focusRelease="r1" onClose={() => undefined} onDone={() => undefined} />);
    await type(host.querySelector('input'), 'Orders v2');
    await click(byText('button', 'Next')); await click(byText('button', 'Next'));            // basics → source → carry
    expect(api.agileApi.suggestCarry).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('AI suggestions'); expect(host.textContent).toContain('the scope extends the API');
    const boxes = [...host.querySelectorAll('input[type="checkbox"]')] as HTMLInputElement[];
    expect(boxes.filter((b) => b.checked)).toHaveLength(2);                                  // the suggestion + "offer delivered stories"
    await click(boxes[0]!);                                                                  // drop the suggestion, then pick the other
    await click((host.querySelectorAll('input[type="checkbox"]')[1]) as HTMLInputElement);
    await click(byText('button', 'Next')); await click(byText('button', 'Next')); await click(byText('button', 'Next')); await click(byText('button', 'Next'));
    expect(api.agileApi.previewRelease.mock.calls[0]![1].carry).toEqual(['spec:billing/Payments']);
  });

  it('shows the server\'s validation errors and does not offer to start', async () => {
    api.agileApi.questions.mockResolvedValue(questions());
    api.agileApi.previewRelease.mockResolvedValue({ valid: false, errors: ['carry: not a candidate'], warnings: [], steps: [], summary: {} });
    await render(<ReleaseWizard projectId="p" onClose={() => undefined} onDone={() => undefined} />);
    await type(host.querySelector('input'), 'x');
    for (let i = 0; i < 4; i++) await click(byText('button', 'Next'));
    expect(host.textContent).toContain('Fix this before starting'); expect(host.textContent).toContain('carry: not a candidate');
    expect((byText('button', 'Start release') as HTMLButtonElement).disabled).toBe(true);
  });

  it('resumes a start that stopped part-way instead of creating a second release', async () => {
    api.agileApi.questions.mockResolvedValue(questions());
    api.agileApi.previewRelease.mockResolvedValue({ valid: true, errors: [], warnings: [], steps: ['Create release'], summary: {} });
    api.agileApi.startRelease.mockRejectedValueOnce(new ApiError('INTERNAL', 'Starting the release stopped part-way', 500, { releaseId: 'rX', resumable: true }))
      .mockResolvedValueOnce({ release: rel({ id: 'rX' }), progress: {} });
    const onDone = vi.fn();
    await render(<ReleaseWizard projectId="p" onClose={() => undefined} onDone={onDone} />);
    await type(host.querySelector('input'), 'Mobile');
    for (let i = 0; i < 4; i++) await click(byText('button', 'Next'));
    await click(byText('button', 'Start release'));
    expect(host.textContent).toContain('Starting stopped part-way');
    await click(byText('button', 'Resume'));
    expect(api.agileApi.startRelease.mock.calls[1]![1].resumeReleaseId).toBe('rX');
    expect(onDone).toHaveBeenCalledWith('rX');
  });

  it('locks the questions the project admin has locked', async () => {
    api.agileApi.questions.mockResolvedValue(questions({ defaults: { intakeRule: 'pool' }, locked: ['intakeRule'] }));
    await render(<ReleaseWizard projectId="p" onClose={() => undefined} onDone={() => undefined} />);
    await type(host.querySelector('input'), 'x');
    for (let i = 0; i < 3; i++) await click(byText('button', 'Next'));                       // → intake
    expect(host.textContent).toContain('set by your project admin');
    expect((host.querySelector('fieldset[disabled]') as HTMLFieldSetElement | null)).not.toBeNull();
  });

  it('closes on Escape', async () => {
    api.agileApi.questions.mockResolvedValue(questions());
    const onClose = vi.fn();
    await render(<ReleaseWizard projectId="p" onClose={onClose} onDone={() => undefined} />);
    await act(async () => { host.querySelector('[role="dialog"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(onClose).toHaveBeenCalled();
  });
});

describe('ReleaseContext', () => {
  const view = (o: Record<string, unknown> = {}) => ({
    forkedFrom: 'R-001', baseline: { sprint: 'S-004' }, sourceChanged: [], budget: { maxItems: 40, maxBytes: 60000 },
    counts: { carried: 1, modified: 1, new: 1, retired: 1 },
    carried: [{ id: 'a', kind: 'spec-section', title: 'orders / Endpoints', state: 'modified', reason: '', bytes: 10 },
      { id: 'b', kind: 'spec-section', title: 'orders / Events', state: 'carried', reason: '', bytes: 10 },
      { id: 'c', kind: 'decision', title: 'Use Postgres', state: 'retired', reason: '', bytes: 10 }],
    new: [{ id: 'd', kind: 'spec-section', title: 'orders / Cancellation', state: 'new' }], ...o,
  });
  const forked = overview({ currentRelease: rel({ id: 'r2', code: 'R-002', name: 'Mobile', forkedFrom: 'R-001', forkedFromId: 'r1', forkBaseline: { sprint: 'S-004' } }) });

  it('says where a release came from and which carried parts changed', async () => {
    api.agileApi.carry.mockResolvedValue(view());
    await render(<ReleaseContext projectId="p" overview={forked} />);
    expect(host.textContent).toContain('Forked from R-001 at S-004'); expect(host.textContent).toContain('nothing is merged back');
    expect(host.textContent).toContain('Modified here'); expect(host.textContent).toContain('Retired'); expect(host.textContent).toContain('New here');
    expect(host.textContent).toContain('Carried as is');
  });

  it('advises — and never auto-updates — when the source release has moved on', async () => {
    api.agileApi.carry.mockResolvedValue(view({ sourceChanged: ['b'] }));
    await render(<ReleaseContext projectId="p" overview={forked} />);
    expect(host.textContent).toContain('The source release has changed'); expect(host.textContent).toContain('Nothing is updated automatically');
  });

  it('adds more context from the source release, skipping what is already carried', async () => {
    api.agileApi.carry.mockResolvedValue(view());
    api.agileApi.suggestCarry.mockResolvedValue({
      source: 'rules', rejected: [], budget: { maxItems: 40, maxBytes: 60000 },
      suggestions: [{ id: 'x', kind: 'requirement', title: 'DM-9 Refunds', reason: '', bytes: 5 }],
      candidates: [{ id: 'a', kind: 'spec-section', title: 'orders / Endpoints', bytes: 10, tooLarge: false },
        { id: 'x', kind: 'requirement', title: 'DM-9 Refunds', bytes: 5, tooLarge: false }],
    });
    api.agileApi.extendCarry.mockResolvedValue({ carried: ['x'], rejected: [] });
    await render(<ReleaseContext projectId="p" overview={forked} />);
    await click(byText('button', 'Add context'));
    expect(host.querySelectorAll('section[aria-label="Carried context"] input[type="checkbox"]')).toHaveLength(1);   // 'a' is already carried
    await click(host.querySelector('section[aria-label="Carried context"] input[type="checkbox"]'));
    await click(byText('button', /Add 1 to this release/));
    expect(api.agileApi.extendCarry).toHaveBeenCalledWith('p', 'r2', ['x']);
  });

  it('describes a blank release honestly and hides "Add context" from people who cannot run it', async () => {
    api.agileApi.carry.mockResolvedValue(view({ forkedFrom: null, carried: [], new: [], counts: {} }));
    await render(<ReleaseContext projectId="p" overview={overview({ permissions: { canManage: false, canRun: false } })} />);
    expect(host.textContent).toContain('Started blank'); expect(byText('button', 'Add context')).toBeNull();
  });
});

describe('BacklogView with parallel releases', () => {
  const item = (o: Record<string, unknown>) => ({
    id: 'DM-1', key: 'DM-1', type: 'story', title: 'A story', description: '', acceptanceCriteria: ['ok'], estimate: 3, rank: 1, status: 'ready',
    epicKey: null, components: [], labels: [], iterationId: null, releaseId: null, jiraKey: null, version: 1, problems: [], createdAt: '', updatedAt: '', ...o,
  });
  it('asks the server for the release scope, marks pool items and claims them into the release', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 1, points: 3 }, items: [item({})] });
    api.agileApi.claim.mockResolvedValue({ claimed: ['DM-1'], skipped: [] });
    await render(<BacklogView projectId="p" overview={overview({ backlog: { ready: 1 } })} />);
    expect(api.agileApi.backlog.mock.calls[0]![1]).toMatchObject({ scope: 'eligible', release: 'r1' });
    await click(byText('button', /Ready/));
    expect(host.textContent).toContain('Pool');
    await click(byText('button', 'Claim'));
    expect(api.agileApi.claim).toHaveBeenCalledWith('p', 'r1', ['DM-1']);
    await click(byText('button', 'Shared pool'));
    expect(api.agileApi.backlog.mock.calls.at(-1)![1]).toMatchObject({ scope: 'pool' });
  });

  it('draws only on the release itself when the release does not use the pool', async () => {
    api.agileApi.backlog.mockResolvedValue({ summary: { count: 0, points: 0 }, items: [] });
    await render(<BacklogView projectId="p" overview={overview({ currentRelease: rel({ usePool: false }) })} />);
    expect(api.agileApi.backlog.mock.calls[0]![1]).toMatchObject({ scope: 'release', release: 'r1' });
  });
});
