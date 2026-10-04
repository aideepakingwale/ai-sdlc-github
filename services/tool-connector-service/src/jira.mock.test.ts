import { beforeEach, describe, expect, it } from 'vitest';
import type { SdlcError } from '@sdlc/shared';
import { atlassianImpl } from './impl/atlassian.js';
import { fakeRedis } from './testing/fake-redis.js';
import { testEnv } from './testing/env.js';

const MIN = 60_000;
const clock = { t: Date.parse('2025-06-01T08:00:00.000Z') };
const code = async (p: Promise<unknown>): Promise<SdlcError> => (await p.then(() => null, (e: unknown) => e)) as SdlcError;
const search = (over: Record<string, unknown> = {}) => ({ maxResults: 50, ...over });

let jira: ReturnType<typeof atlassianImpl>;
beforeEach(() => {
  clock.t = Date.parse('2025-06-01T08:00:00.000Z');
  jira = atlassianImpl({ env: testEnv({ JIRA_PROJECT_KEY: 'SDLC' }), redis: fakeRedis(), live: false, now: () => clock.t });
});

const epic = (title = 'Epic', projectKey?: string) => jira.createEpic({ title, description: 'epic body', priority: 'High', ...(projectKey ? { projectKey } : {}) });
const story = (epicKey: string, text = 'As a user I can x', over: Record<string, unknown> = {}) =>
  jira.createStory({ epicKey, storyText: text, gherkinCriteria: ['Given a\nThen b', 'Given c'], storyPoints: 3, ...over });

describe('mock Jira persistence of created issues', () => {
  it('persists epics, stories and Xray tests so they can be read back', async () => {
    const e = await epic('Payments');
    const s = await story(e.epicKey, 'As a payer I can pay');
    const t = await jira.createXrayTest({ storyKey: s.storyKey, title: 'Pay works', steps: [{ action: 'click pay', expectedResult: 'paid' }] });

    const gotEpic = (await jira.getIssue({ key: e.epicKey })).issue;
    expect(gotEpic).toMatchObject({ key: e.epicKey, id: e.epicId, url: e.url, summary: 'Payments', description: 'epic body', type: 'Epic', status: 'To Do', statusCategory: 'todo', priority: 'High', epicKey: null, storyPoints: null, labels: [], sprint: null, assignee: null, acceptanceCriteria: [] });

    const gotStory = (await jira.getIssue({ key: s.storyKey })).issue;
    expect(gotStory).toMatchObject({ type: 'Story', summary: 'As a payer I can pay', description: 'As a payer I can pay', epicKey: e.epicKey, storyPoints: 3, acceptanceCriteria: ['Given a\nThen b', 'Given c'], priority: 'Medium' });

    const gotTest = (await jira.getIssue({ key: t.xrayTestKey })).issue;
    expect(gotTest).toMatchObject({ type: 'Test', summary: 'Pay works' });
    expect(gotTest.description).toContain('Step 1: click pay');
    expect(gotTest.created).toBe(gotTest.updated);
  });

  it('keeps stories in their epic project and keys unique/sequential', async () => {
    const e = await epic('E', 'aqdp');
    const s = await story(e.epicKey);
    expect(s.storyKey).toMatch(/^AQDP-\d+$/);
    expect(new Set([e.epicKey, s.storyKey]).size).toBe(2);
  });

  it('NOT_FOUND for unknown keys', async () => {
    expect(await code(jira.getIssue({ key: 'SDLC-9999' }))).toMatchObject({ code: 'NOT_FOUND' });
  });
});

describe('mock Jira updated clock', () => {
  it('is strictly increasing even when the wall clock stands still or goes backwards', async () => {
    const e = await epic();
    const stamps: number[] = [Date.parse((await jira.getIssue({ key: e.epicKey })).issue.updated)];
    for (let i = 0; i < 4; i++) {
      if (i === 2) clock.t -= 10 * MIN; // clock skew
      const { issue } = await jira.updateIssue({ key: e.epicKey, fields: { summary: `v${i}` } });
      stamps.push(Date.parse(issue.updated));
    }
    for (let i = 1; i < stamps.length; i++) expect(stamps[i]).toBeGreaterThan(stamps[i - 1] as number);
  });

  it('is monotonic across instances sharing one Redis', async () => {
    const redis = fakeRedis();
    const env = testEnv();
    const a = atlassianImpl({ env, redis, live: false, now: () => clock.t });
    const b = atlassianImpl({ env, redis, live: false, now: () => clock.t });
    const e = await a.createEpic({ title: 'x', description: '', priority: 'Low' });
    const u1 = Date.parse((await a.updateIssue({ key: e.epicKey, fields: { summary: '1' } })).issue.updated);
    const u2 = Date.parse((await b.updateIssue({ key: e.epicKey, fields: { summary: '2' } })).issue.updated);
    expect(u2).toBeGreaterThan(u1);
  });
});

describe('mock jira_update_issue', () => {
  it('applies each field and bumps updated', async () => {
    const s = await story((await epic()).epicKey);
    const before = (await jira.getIssue({ key: s.storyKey })).issue;
    const { issue } = await jira.updateIssue({
      key: s.storyKey,
      fields: { summary: 'S2', description: 'D2', priority: 'Low', storyPoints: 8, labels: ['x', 'y'], acceptanceCriteria: ['only one'] },
    });
    expect(issue).toMatchObject({ summary: 'S2', description: 'D2', priority: 'Low', storyPoints: 8, labels: ['x', 'y'], acceptanceCriteria: ['only one'], created: before.created });
    expect(issue.updated > before.updated).toBe(true);
    expect((await jira.getIssue({ key: s.storyKey })).issue).toEqual(issue);
  });

  it('partial updates leave other fields alone (including clearing with empty values)', async () => {
    const s = await story((await epic()).epicKey);
    const { issue } = await jira.updateIssue({ key: s.storyKey, fields: { labels: [], acceptanceCriteria: [] } });
    expect(issue.summary).toBe('As a user I can x');
    expect(issue.storyPoints).toBe(3);
    expect(issue.labels).toEqual([]);
    expect(issue.acceptanceCriteria).toEqual([]);
  });

  it('expectedUpdated mismatch -> CONFLICT without writing; match succeeds', async () => {
    const s = await story((await epic()).epicKey);
    const current = (await jira.getIssue({ key: s.storyKey })).issue;
    const err = await code(jira.updateIssue({ key: s.storyKey, fields: { summary: 'lost update' }, expectedUpdated: new Date(Date.parse(current.updated) - 1).toISOString() }));
    expect(err).toMatchObject({ code: 'GATE_CONFLICT', httpStatus: 409 });
    expect((await jira.getIssue({ key: s.storyKey })).issue).toEqual(current); // untouched, updated unchanged

    const ok = await jira.updateIssue({ key: s.storyKey, fields: { summary: 'fine' }, expectedUpdated: current.updated });
    expect(ok.issue.summary).toBe('fine');
    // the old token is now stale
    expect(await code(jira.updateIssue({ key: s.storyKey, fields: { summary: 'again' }, expectedUpdated: current.updated }))).toMatchObject({ code: 'GATE_CONFLICT' });
  });

  it('NOT_FOUND for unknown keys', async () => {
    expect(await code(jira.updateIssue({ key: 'SDLC-777', fields: { summary: 'x' } }))).toMatchObject({ code: 'NOT_FOUND' });
  });
});

describe('mock jira_transition_issue', () => {
  it('moves through the default workflow case-insensitively and updates the category', async () => {
    const e = await epic();
    const a = await jira.transitionIssue({ key: e.epicKey, toStatus: 'in progress' });
    expect(a.issue).toMatchObject({ status: 'In Progress', statusCategory: 'inprogress' });
    const b = await jira.transitionIssue({ key: e.epicKey, toStatus: 'DONE' });
    expect(b.issue).toMatchObject({ status: 'Done', statusCategory: 'done' });
    expect(b.issue.updated > a.issue.updated).toBe(true);
    const back = await jira.transitionIssue({ key: e.epicKey, toStatus: 'To Do' });
    expect(back.issue.statusCategory).toBe('todo');
  });

  it('same-status transition is a no-op that does not bump updated', async () => {
    const e = await epic();
    const before = (await jira.getIssue({ key: e.epicKey })).issue;
    const same = await jira.transitionIssue({ key: e.epicKey, toStatus: 'to do' });
    expect(same.issue.updated).toBe(before.updated);
  });

  it('unknown target lists the available statuses', async () => {
    const e = await epic();
    const err = await code(jira.transitionIssue({ key: e.epicKey, toStatus: 'Blocked' }));
    expect(err).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(err.message).toContain('In Progress, Done');
    expect(await code(jira.transitionIssue({ key: 'SDLC-5000', toStatus: 'Done' }))).toMatchObject({ code: 'NOT_FOUND' });
  });
});

describe('mock jira_add_comment', () => {
  it('returns an id and url, bumps updated, and rejects unknown issues', async () => {
    const e = await epic();
    const before = (await jira.getIssue({ key: e.epicKey })).issue.updated;
    const c1 = await jira.addComment({ key: e.epicKey, body: 'hello' });
    const c2 = await jira.addComment({ key: e.epicKey, body: 'again' });
    expect(c1.id).not.toBe(c2.id);
    expect(c1.url).toBe(`https://jira.mock.local/browse/${e.epicKey}?focusedCommentId=${c1.id}`);
    expect((await jira.getIssue({ key: e.epicKey })).issue.updated > before).toBe(true);
    expect(await code(jira.addComment({ key: 'SDLC-4040', body: 'x' }))).toMatchObject({ code: 'NOT_FOUND' });
  });
});

describe('mock jira_search_issues', () => {
  async function seed() {
    const e = await epic('E1'); // SDLC
    clock.t += 5 * MIN;
    const s1 = await story(e.epicKey, 'first story');
    clock.t += 5 * MIN;
    const s2 = await story(e.epicKey, 'second story');
    clock.t += 5 * MIN;
    const other = await epic('Other project epic', 'zeta');
    return { e, s1, s2, other };
  }

  it('scopes to the default project, or an explicit sanitised one', async () => {
    const { e, s1, s2, other } = await seed();
    const def = await jira.searchIssues(search());
    expect(def.issues.map((i) => i.key)).toEqual([e.epicKey, s1.storyKey, s2.storyKey]);
    expect(def.total).toBe(3);
    expect(def.nextPageToken).toBeNull();
    const z = await jira.searchIssues(search({ projectKey: 'zeta' }));
    expect(z.issues.map((i) => i.key)).toEqual([other.epicKey]);
  });

  it('orders by updated ASC and re-orders when an issue is updated', async () => {
    const { e, s1, s2 } = await seed();
    clock.t += MIN;
    await jira.updateIssue({ key: e.epicKey, fields: { summary: 'touched' } });
    const out = await jira.searchIssues(search());
    expect(out.issues.map((i) => i.key)).toEqual([s1.storyKey, s2.storyKey, e.epicKey]);
  });

  it('filters by updatedSince with JQL minute resolution (inclusive)', async () => {
    const { e, s1, s2 } = await seed();
    const s1Updated = (await jira.getIssue({ key: s1.storyKey })).issue.updated;
    const since = await jira.searchIssues(search({ updatedSince: s1Updated }));
    expect(since.issues.map((i) => i.key)).toEqual([s1.storyKey, s2.storyKey]);
    // an instant inside the same minute as e's update still includes it (floored to the minute)
    const sameMinute = new Date(Date.parse(s1Updated) + 30_000).toISOString();
    expect((await jira.searchIssues(search({ updatedSince: sameMinute }))).issues.map((i) => i.key)).toEqual([s1.storyKey, s2.storyKey]);
    const later = await jira.searchIssues(search({ updatedSince: new Date(clock.t + MIN).toISOString() }));
    expect(later.issues).toEqual([]);
    expect(e.epicKey).toBeTruthy();
  });

  it('filters by issue type case-insensitively', async () => {
    const { s1, s2 } = await seed();
    const out = await jira.searchIssues(search({ issueTypes: ['story'] }));
    expect(out.issues.map((i) => i.key)).toEqual([s1.storyKey, s2.storyKey]);
    expect((await jira.searchIssues(search({ issueTypes: ['Epic', 'Story'] }))).issues).toHaveLength(3);
    expect((await jira.searchIssues(search({ issueTypes: ['Bug'] }))).issues).toEqual([]);
  });

  it('paginates with an opaque token without gaps or duplicates, even across writes between pages', async () => {
    const e = await epic();
    const keys: string[] = [e.epicKey];
    for (let i = 0; i < 6; i++) {
      clock.t += MIN;
      keys.push((await story(e.epicKey, `story ${i}`)).storyKey);
    }
    const seen: string[] = [];
    let token: string | undefined;
    let pages = 0;
    do {
      const page = await jira.searchIssues(search({ maxResults: 3, ...(token ? { nextPageToken: token } : {}) }));
      seen.push(...page.issues.map((i) => i.key));
      expect(page.total).toBe(7);
      if (page.nextPageToken) expect(page.nextPageToken).toMatch(/^[A-Za-z0-9_-]+$/);
      token = page.nextPageToken ?? undefined;
      if (pages === 0) {
        clock.t += MIN;
        await jira.updateIssue({ key: keys[0] as string, fields: { summary: 'moved to the end' } }); // already-delivered item re-sorts later
      }
      pages++;
    } while (token);
    expect(pages).toBeGreaterThanOrEqual(3);
    // Every untouched issue appears exactly once, in order; the epic that was updated mid-scan is
    // delivered again at its new position (the standard behaviour of updated-ordered cursors).
    expect(seen.filter((k) => k !== keys[0])).toEqual(keys.slice(1));
    expect(seen.filter((k) => k === keys[0])).toHaveLength(2);
    expect(seen[seen.length - 1]).toBe(keys[0]);
  });

  it('rejects an invalid page token', async () => {
    await epic();
    expect(await code(jira.searchIssues(search({ nextPageToken: 'not-a-token' })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(jira.searchIssues(search({ nextPageToken: Buffer.from('{"u":"x"}').toString('base64url') })))).toMatchObject({ code: 'VALIDATION_FAILED' });
  });

  it('evaluates AND-combined raw JQL, searching all projects when no project is given', async () => {
    const { e, s1, other } = await seed();
    await jira.transitionIssue({ key: s1.storyKey, toStatus: 'Done' });
    await jira.updateIssue({ key: other.epicKey, fields: { labels: ['devmind'] } });
    expect((await jira.searchIssues(search({ jql: 'status = Done' }))).issues.map((i) => i.key)).toEqual([s1.storyKey]);
    expect((await jira.searchIssues(search({ jql: 'labels = devmind' }))).issues.map((i) => i.key)).toEqual([other.epicKey]);
    expect((await jira.searchIssues(search({ projectKey: 'SDLC', jql: 'status = "To Do" AND issuetype = Epic' }))).issues.map((i) => i.key)).toEqual([e.epicKey]);
    expect((await jira.searchIssues(search({ jql: 'summary ~ "second"' }))).issues).toHaveLength(1);
  });

  it('applies the same safety rules as live mode', async () => {
    await epic();
    for (const jql of ['status = Done; x', 'status = Done) OR (a = b', 'summary ~ "x', 'status = Done\nOR 1=1', 'a = b ORDER BY c']) {
      expect(await code(jira.searchIssues(search({ jql })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    }
    expect(await code(jira.searchIssues(search({ projectKey: '!' })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(jira.searchIssues(search({ jql: 'status = Done OR status = "To Do"' })))).toMatchObject({ code: 'VALIDATION_FAILED' });
  });
});
