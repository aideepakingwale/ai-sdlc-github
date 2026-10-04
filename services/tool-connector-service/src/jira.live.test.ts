import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { SdlcError } from '@sdlc/shared';
import { atlassianImpl } from './impl/atlassian.js';
import { acceptanceCriteriaBlocks, adfDoc, textToAdfBlocks } from './impl/adf.js';
import { startFakeJira, type FakeJira } from './testing/fake-jira.js';
import { fakeRedis } from './testing/fake-redis.js';
import { fastPolicy, testEnv } from './testing/env.js';

let fake: FakeJira;
beforeAll(async () => {
  fake = await startFakeJira();
});
afterAll(async () => {
  await fake.close();
});
beforeEach(() => {
  fake.issues.clear();
  fake.requests = [];
  fake.faults = [];
  fake.comments = [];
  fake.resetClock();
});

const env = (extra: Record<string, string> = {}) =>
  testEnv({ TOOLS_MODE: 'live', JIRA_BASE_URL: fake.url, JIRA_EMAIL: fake.email, JIRA_API_TOKEN: fake.token, JIRA_PROJECT_KEY: 'DEV', ...extra });
const make = (sleeps: number[] = [], extra: Record<string, string> = {}) => atlassianImpl({ env: env(extra), redis: fakeRedis(), live: true, http: fastPolicy(sleeps) });
const code = async (p: Promise<unknown>): Promise<SdlcError> => (await p.then(() => null, (e: unknown) => e)) as SdlcError;
const search = (over: Record<string, unknown> = {}) => ({ maxResults: 50, ...over });
const lastBody = (re: RegExp, method = 'POST') => [...fake.requests].reverse().find((r) => r.method === method && re.test(r.path))?.body ?? {};

const richIssue = () =>
  fake.seed('DEV-1', {
    summary: 'Login page',
    description: adfDoc([...textToAdfBlocks('As a user\nI can log in'), ...acceptanceCriteriaBlocks(['Given a\nThen b', 'Second'])]),
    issuetype: { name: 'Story' },
    status: { name: 'In Progress', statusCategory: { key: 'indeterminate' } },
    priority: { name: 'High' },
    labels: ['auth', 'web'],
    parent: { key: 'DEV-100', fields: { issuetype: { name: 'Epic' } } },
    assignee: { displayName: 'Ann Lee', accountId: 'acc-1' },
    customfield_10016: 5,
    customfield_10020: [
      { id: 3, name: 'Sprint 3', state: 'closed' },
      { id: 4, name: 'Sprint 4', state: 'active' },
    ],
  });

describe('jira_get_issue (live)', () => {
  it('maps every JiraIssue field from the Jira payload', async () => {
    richIssue();
    const { issue } = await make().getIssue({ key: 'DEV-1' });
    expect(issue).toEqual({
      key: 'DEV-1',
      id: expect.any(String) as unknown,
      url: `${fake.url}/browse/DEV-1`,
      summary: 'Login page',
      description: 'As a user\nI can log in',
      type: 'Story',
      status: 'In Progress',
      statusCategory: 'inprogress',
      priority: 'High',
      storyPoints: 5,
      labels: ['auth', 'web'],
      epicKey: 'DEV-100',
      sprint: { id: 4, name: 'Sprint 4', state: 'active' },
      assignee: 'Ann Lee',
      created: '2025-03-01T10:01:00.000Z',
      updated: '2025-03-01T10:01:00.000Z',
      acceptanceCriteria: ['Given a\nThen b', 'Second'],
    });
    const req = fake.requests.find((r) => r.method === 'GET');
    expect(req?.query.get('fields')).toContain('customfield_10016');
    expect(req?.query.get('fields')).toContain('customfield_10020');
    expect(req?.headers.authorization).toBe(`Basic ${Buffer.from(`${fake.email}:${fake.token}`).toString('base64')}`);
  });

  it('tolerates sparse issues (nulls, unknown categories, non-epic parents, missing sprint)', async () => {
    fake.seed('DEV-2', { description: null, priority: null, assignee: null, status: { name: 'Weird', statusCategory: { key: 'zzz' } }, parent: { key: 'DEV-1', fields: { issuetype: { name: 'Story' } } } });
    const { issue } = await make().getIssue({ key: 'DEV-2' });
    expect(issue).toMatchObject({ description: '', priority: null, assignee: null, statusCategory: 'unknown', epicKey: null, sprint: null, storyPoints: null, labels: [], acceptanceCriteria: [] });
  });

  it('honours custom field ids from the environment', async () => {
    fake.seed('DEV-3', { customfield_20001: 8, customfield_20002: [{ id: 9, name: 'S9', state: 'future' }] });
    const { issue } = await make([], { JIRA_STORY_POINTS_FIELD: 'customfield_20001', JIRA_SPRINT_FIELD: 'customfield_20002' }).getIssue({ key: 'DEV-3' });
    expect(issue.storyPoints).toBe(8);
    expect(issue.sprint).toEqual({ id: 9, name: 'S9', state: 'future' });
  });

  it('404 -> NOT_FOUND', async () => {
    expect(await code(make().getIssue({ key: 'DEV-404' }))).toMatchObject({ code: 'NOT_FOUND', httpStatus: 404 });
  });

  it('401 and 403 -> TOOL_ERROR with an actionable hint, no secrets', async () => {
    const bad = atlassianImpl({ env: env({ JIRA_API_TOKEN: 'wrong-token-xyz' }), redis: fakeRedis(), live: true, http: fastPolicy() });
    const err = await code(bad.getIssue({ key: 'DEV-1' }));
    expect(err).toMatchObject({ code: 'TOOL_ERROR' });
    expect(err.message).toContain('JIRA_EMAIL/JIRA_API_TOKEN');
    expect(err.message).not.toContain('wrong-token-xyz');

    fake.faults.push({ when: () => true, status: 403, body: { errorMessages: ['nope'] } });
    const forbidden = await code(make().getIssue({ key: 'DEV-1' }));
    expect(forbidden).toMatchObject({ code: 'TOOL_ERROR' });
    expect(forbidden.message).toContain('permission');
  });

  it('retries 429 with Retry-After and 503 on reads; JIRA_MAX_RETRIES bounds the retries', async () => {
    fake.seed('DEV-5');
    const sleeps: number[] = [];
    fake.faults.push({ when: (r) => r.method === 'GET', status: 429, headers: { 'retry-after': '4' } });
    fake.faults.push({ when: (r) => r.method === 'GET', status: 503, times: 2 });
    await make(sleeps).getIssue({ key: 'DEV-5' });
    expect(sleeps).toHaveLength(3);
    expect(sleeps[0]).toBeGreaterThanOrEqual(4000);
    expect(fake.count('GET', /DEV-5$/)).toBe(4);

    fake.requests = [];
    fake.faults.push({ when: () => true, status: 503, times: 99 });
    expect(await code(make([], { JIRA_MAX_RETRIES: '1' }).getIssue({ key: 'DEV-5' }))).toMatchObject({ code: 'TOOL_ERROR', message: expect.stringContaining('503') as unknown });
    expect(fake.requests).toHaveLength(2);
    fake.requests = [];
    expect(await code(make([], { JIRA_MAX_RETRIES: '0' }).getIssue({ key: 'DEV-5' }))).toMatchObject({ code: 'TOOL_ERROR' });
    expect(fake.requests).toHaveLength(1);
  });
});

describe('jira_search_issues (live)', () => {
  it('builds JQL from project, updatedSince and issueTypes, requesting explicit fields', async () => {
    fake.seed('DEV-1');
    const out = await make().searchIssues(search({ projectKey: 'dev', updatedSince: '2025-03-01T09:30:45Z', issueTypes: ['Story', 'Bug'], maxResults: 10 }));
    expect(out.issues).toHaveLength(1);
    expect(out.total).toBeNull();
    expect(out.nextPageToken).toBeNull();
    const body = lastBody(/search\/jql$/);
    expect(body['jql']).toBe('project = "DEV" AND updated >= "2025-03-01 09:30" AND issuetype in ("Story", "Bug") ORDER BY updated ASC');
    expect(body['maxResults']).toBe(10);
    expect(body['fields']).toEqual(expect.arrayContaining(['summary', 'description', 'status', 'updated', 'customfield_10016', 'customfield_10020']) as unknown);
    expect(body['fields']).not.toContain('*all');
    expect(body['nextPageToken']).toBeUndefined();
  });

  it('defaults to JIRA_PROJECT_KEY and AND-combines raw JQL', async () => {
    await make().searchIssues(search());
    expect(lastBody(/search\/jql$/)['jql']).toBe('project = "DEV" ORDER BY updated ASC');
    await make().searchIssues(search({ jql: 'labels = devmind OR labels = index', issueTypes: ['Story'] }));
    expect(lastBody(/search\/jql$/)['jql']).toBe('issuetype in ("Story") AND (labels = devmind OR labels = index) ORDER BY updated ASC');
  });

  it('paginates with Jira tokens until exhausted, preserving updated-ASC order', async () => {
    for (let i = 1; i <= 5; i++) fake.seed(`DEV-${i}`);
    const api = make();
    const seen: string[] = [];
    let token: string | undefined;
    let pages = 0;
    do {
      const page = await api.searchIssues(search({ maxResults: 2, ...(token ? { nextPageToken: token } : {}) }));
      seen.push(...page.issues.map((i) => i.key));
      token = page.nextPageToken ?? undefined;
      pages++;
    } while (token);
    expect(seen).toEqual(['DEV-1', 'DEV-2', 'DEV-3', 'DEV-4', 'DEV-5']);
    expect(pages).toBe(3);
    expect(lastBody(/search\/jql$/)['nextPageToken']).toEqual(expect.any(String) as unknown);
  });

  it('retries a 429 on the (read-only) search POST and a 503', async () => {
    fake.seed('DEV-1');
    const sleeps: number[] = [];
    fake.faults.push({ when: (r) => r.method === 'POST', status: 429, headers: { 'retry-after': '1' } });
    fake.faults.push({ when: (r) => r.method === 'POST', status: 503 });
    const out = await make(sleeps).searchIssues(search());
    expect(out.issues).toHaveLength(1);
    expect(fake.count('POST', /search\/jql$/)).toBe(3);
  });

  describe('injection attempts', () => {
    it.each([
      ['statement separator', 'project = A; DELETE'],
      ['control character', 'status = Done\r\nOR 1=1'],
      ['paren breakout', 'status = Done) OR (project = SECRET'],
      ['quote breakout', 'summary ~ "x'],
      ['embedded ORDER BY', 'status = Done ORDER BY created'],
    ])('rejects raw jql with %s before any request', async (_n, jql) => {
      expect(await code(make().searchIssues(search({ jql })))).toMatchObject({ code: 'VALIDATION_FAILED' });
      expect(fake.requests).toHaveLength(0);
    });

    it('neutralises hostile project keys and issue types', async () => {
      const api = make();
      await api.searchIssues(search({ projectKey: 'AB" OR 1=1' }));
      expect(lastBody(/search\/jql$/)['jql']).toBe('project = "ABOR11" ORDER BY updated ASC');
      expect(await code(api.searchIssues(search({ projectKey: '"; --' })))).toMatchObject({ code: 'VALIDATION_FAILED' });
      await api.searchIssues(search({ projectKey: 'AB', issueTypes: ['Story") OR project = ("X'] }));
      expect(lastBody(/search\/jql$/)['jql']).toBe('project = "AB" AND issuetype in ("Story\\") OR project = (\\"X") ORDER BY updated ASC');
      expect(await code(api.searchIssues(search({ projectKey: 'AB', issueTypes: ['a\nb'] })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    });
  });

  it('surfaces Jira 400 details (errorMessages and errors)', async () => {
    fake.faults.push({ when: () => true, status: 400, body: { errorMessages: ['Error in the JQL Query: bad'], errors: { jql: 'unknown field' } } });
    const err = await code(make().searchIssues(search({ jql: 'foo = bar' })));
    expect(err).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(err.message).toContain('Error in the JQL Query: bad');
    expect(err.message).toContain('jql: unknown field');
  });
});

describe('jira_update_issue (live)', () => {
  it('writes scalar fields, labels and story points, and returns the refreshed issue', async () => {
    richIssue();
    const { issue } = await make().updateIssue({
      key: 'DEV-1',
      fields: { summary: 'New title', priority: 'Highest', labels: ['a', 'b'], storyPoints: 13 },
    });
    const put = lastBody(/issue\/DEV-1$/, 'PUT') as { fields: Record<string, unknown> };
    expect(put.fields).toEqual({ summary: 'New title', priority: { name: 'Highest' }, labels: ['a', 'b'], customfield_10016: 13 });
    expect(put.fields['description']).toBeUndefined();
    expect(issue).toMatchObject({ summary: 'New title', priority: 'Highest', labels: ['a', 'b'], storyPoints: 13 });
    expect(issue.updated > '2025-03-01T10:01:00.000Z').toBe(true);
  });

  it('writes description as ADF and round-trips it, keeping existing acceptance criteria', async () => {
    richIssue();
    const text = 'Line one\nLine two\n\nSecond paragraph';
    const { issue } = await make().updateIssue({ key: 'DEV-1', fields: { description: text } });
    const put = lastBody(/issue\/DEV-1$/, 'PUT') as { fields: { description: { type: string; version: number; content: Array<{ type: string }> } } };
    expect(put.fields.description.type).toBe('doc');
    expect(put.fields.description.version).toBe(1);
    expect(put.fields.description.content.map((n) => n.type)).toEqual(['paragraph', 'paragraph', 'heading', 'bulletList']);
    expect(issue.description).toBe(text);
    expect(issue.acceptanceCriteria).toEqual(['Given a\nThen b', 'Second']);
  });

  it('writes acceptance criteria as a bullet list under an "Acceptance criteria" heading, keeping the description', async () => {
    richIssue();
    const { issue } = await make().updateIssue({ key: 'DEV-1', fields: { acceptanceCriteria: ['New one', 'New\ntwo'] } });
    const put = lastBody(/issue\/DEV-1$/, 'PUT') as { fields: { description: { content: Array<{ type: string; content?: Array<{ text?: string }> }> } } };
    const heading = put.fields.description.content.find((n) => n.type === 'heading');
    expect(heading?.content?.[0]?.text).toBe('Acceptance criteria');
    expect(issue.acceptanceCriteria).toEqual(['New one', 'New\ntwo']);
    expect(issue.description).toBe('As a user\nI can log in');
    // clearing AC removes the section
    const cleared = await make().updateIssue({ key: 'DEV-1', fields: { acceptanceCriteria: [] } });
    expect(cleared.issue.acceptanceCriteria).toEqual([]);
    expect(cleared.issue.description).toBe('As a user\nI can log in');
  });

  it('writes AC to the dedicated custom field when JIRA_AC_FIELD is set, and reads it back', async () => {
    fake.seed('DEV-7', { description: adfDoc(textToAdfBlocks('plain body')) });
    const api = make([], { JIRA_AC_FIELD: 'customfield_10050' });
    const { issue } = await api.updateIssue({ key: 'DEV-7', fields: { acceptanceCriteria: ['alpha', 'beta'] } });
    const put = lastBody(/issue\/DEV-7$/, 'PUT') as { fields: Record<string, unknown> };
    expect(Object.keys(put.fields)).toEqual(['customfield_10050']);
    expect(issue.acceptanceCriteria).toEqual(['alpha', 'beta']);
    expect(issue.description).toBe('plain body');
    expect(fake.requests.find((r) => r.method === 'GET')?.query.get('fields')).toContain('customfield_10050');

    const again = await api.updateIssue({ key: 'DEV-7', fields: { description: 'changed' } });
    expect(again.issue.acceptanceCriteria).toEqual(['alpha', 'beta']); // untouched
    expect(again.issue.description).toBe('changed');
  });

  it('migrates description-embedded AC into the field when a description update would drop it', async () => {
    richIssue();
    const api = make([], { JIRA_AC_FIELD: 'customfield_10050' });
    const { issue } = await api.updateIssue({ key: 'DEV-1', fields: { description: 'fresh text' } });
    expect(issue.description).toBe('fresh text');
    expect(issue.acceptanceCriteria).toEqual(['Given a\nThen b', 'Second']);
  });

  it('reads AC stored as legacy flat text (as written by jira_create_story)', async () => {
    fake.seed('DEV-8', { description: adfDoc([{ type: 'paragraph', content: [{ type: 'text', text: 'Story text\n\nAcceptance Criteria:\nGiven x\n\nGiven y' }] }]) });
    const { issue } = await make().getIssue({ key: 'DEV-8' });
    expect(issue.description).toBe('Story text');
    expect(issue.acceptanceCriteria).toEqual(['Given x', 'Given y']);
  });

  it('optimistic concurrency: a stale expectedUpdated fails with CONFLICT and does NOT write', async () => {
    richIssue();
    const err = await code(make().updateIssue({ key: 'DEV-1', fields: { summary: 'x' }, expectedUpdated: '2025-03-01T09:00:00.000Z' }));
    expect(err).toMatchObject({ code: 'GATE_CONFLICT', httpStatus: 409 });
    expect(err.message).toContain('2025-03-01T10:01:00.000Z');
    expect(fake.count('PUT', /issue\//)).toBe(0);
    expect(fake.issues.get('DEV-1')?.fields['summary']).toBe('Login page');
  });

  it('expectedUpdated matches by instant regardless of timezone notation', async () => {
    richIssue();
    const { issue } = await make().updateIssue({ key: 'DEV-1', fields: { summary: 'ok' }, expectedUpdated: '2025-03-01T12:01:00.000+02:00' });
    expect(issue.summary).toBe('ok');
    expect(fake.count('PUT', /issue\//)).toBe(1);
  });

  it('maps Jira 400 validation errors and 404s', async () => {
    richIssue();
    fake.faults.push({ when: (r) => r.method === 'PUT', status: 400, body: { errorMessages: [], errors: { priority: 'Priority name Urgent is not valid' } } });
    const bad = await code(make().updateIssue({ key: 'DEV-1', fields: { priority: 'Urgent' } }));
    expect(bad).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(bad.message).toContain('Priority name Urgent is not valid');
    expect(await code(make().updateIssue({ key: 'DEV-404', fields: { summary: 'x' } }))).toMatchObject({ code: 'NOT_FOUND' });
  });

  it('retries a transient 503 on the (idempotent) PUT', async () => {
    richIssue();
    fake.faults.push({ when: (r) => r.method === 'PUT', status: 503 });
    await make().updateIssue({ key: 'DEV-1', fields: { summary: 'again' } });
    expect(fake.count('PUT', /issue\//)).toBe(2);
  });
});

describe('jira_transition_issue (live)', () => {
  it('matches the target status case-insensitively and posts the transition id', async () => {
    fake.seed('DEV-1');
    const { issue } = await make().transitionIssue({ key: 'DEV-1', toStatus: 'in PROGRESS' });
    expect(lastBody(/transitions$/)).toEqual({ transition: { id: '21' } });
    expect(issue).toMatchObject({ status: 'In Progress', statusCategory: 'inprogress' });
    const done = await make().transitionIssue({ key: 'DEV-1', toStatus: 'Done' });
    expect(done.issue.statusCategory).toBe('done');
  });

  it('is a no-op when already in the target status', async () => {
    fake.seed('DEV-1');
    const { issue } = await make().transitionIssue({ key: 'DEV-1', toStatus: 'to do' });
    expect(issue.status).toBe('To Do');
    expect(fake.count('POST', /transitions$/)).toBe(0);
  });

  it('lists the available target statuses when the target is unknown', async () => {
    fake.seed('DEV-1');
    const err = await code(make().transitionIssue({ key: 'DEV-1', toStatus: 'Shipped' }));
    expect(err).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(err.message).toContain('In Progress');
    expect(err.message).toContain('Done');
    expect(err.message).not.toContain('To Do,');
    expect(err.details).toMatchObject({ available: ['In Progress', 'Done'] });
    expect(fake.count('POST', /transitions$/)).toBe(0);
  });

  it('404 for unknown issues; does not replay the POST after a 5xx but does after a 429', async () => {
    expect(await code(make().transitionIssue({ key: 'DEV-404', toStatus: 'Done' }))).toMatchObject({ code: 'NOT_FOUND' });
    fake.seed('DEV-1');
    fake.faults.push({ when: (r) => r.method === 'POST', status: 503 });
    expect(await code(make().transitionIssue({ key: 'DEV-1', toStatus: 'Done' }))).toMatchObject({ code: 'TOOL_ERROR' });
    expect(fake.count('POST', /transitions$/)).toBe(1);
    fake.faults.push({ when: (r) => r.method === 'POST', status: 429, headers: { 'retry-after': '0' } });
    const ok = await make().transitionIssue({ key: 'DEV-1', toStatus: 'Done' });
    expect(ok.issue.status).toBe('Done');
    expect(fake.count('POST', /transitions$/)).toBe(3);
  });
});

describe('jira_add_comment (live)', () => {
  it('posts an ADF comment and returns id and url', async () => {
    fake.seed('DEV-1');
    const out = await make().addComment({ key: 'DEV-1', body: 'Index updated\nSee commit abc\n\nThanks' });
    expect(out).toEqual({ id: '900', url: `${fake.url}/browse/DEV-1?focusedCommentId=900` });
    const body = lastBody(/comment$/) as { body: { type: string; content: Array<{ type: string }> } };
    expect(body.body.type).toBe('doc');
    expect(body.body.content).toHaveLength(2);
    expect(fake.comments).toHaveLength(1);
  });

  it('404 for unknown issues, never replays after 503', async () => {
    expect(await code(make().addComment({ key: 'DEV-404', body: 'x' }))).toMatchObject({ code: 'NOT_FOUND' });
    fake.seed('DEV-1');
    fake.faults.push({ when: (r) => r.method === 'POST', status: 503 });
    expect(await code(make().addComment({ key: 'DEV-1', body: 'x' }))).toMatchObject({ code: 'TOOL_ERROR' });
    expect(fake.comments).toHaveLength(0);
    expect(fake.count('POST', /comment$/)).toBe(2); // 404 call + the single 503 attempt
  });
});
