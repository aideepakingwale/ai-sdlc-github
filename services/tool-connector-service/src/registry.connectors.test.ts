import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { pino } from 'pino';
import type { JiraIssue } from '@sdlc/shared';
import { createToolRuntime } from './registry.js';
import { startFakeGithub, type FakeGithub } from './testing/fake-github.js';
import { startFakeJira, type FakeJira } from './testing/fake-jira.js';
import { fakeRedis } from './testing/fake-redis.js';
import { fastPolicy, testEnv } from './testing/env.js';

const log = pino({ level: 'silent' });

describe('runtime wiring (mock mode)', () => {
  const runtime = createToolRuntime(testEnv({ GITHUB_REPO: 'acme/widgets' }), fakeRedis(), log);
  const run = <T>(name: string, args: unknown) => runtime.execute(name, args) as Promise<T>;

  it('drives the whole Jira lifecycle by tool name with schema-validated I/O', async () => {
    const epic = await run<{ epicKey: string }>('jira_create_epic', { title: 'Index', description: 'd' });
    const story = await run<{ storyKey: string }>('jira_create_story', { epicKey: epic.epicKey, storyText: 'As a dev I query the index', gherkinCriteria: ['Given x'], storyPoints: 2 });

    const found = await run<{ issues: JiraIssue[]; nextPageToken: string | null; total: number | null }>('jira_search_issues', { issueTypes: ['Story'] });
    expect(found.issues.map((i) => i.key)).toEqual([story.storyKey]);
    expect(found.total).toBe(1);

    const upd = await run<{ issue: JiraIssue }>('jira_update_issue', { key: story.storyKey, fields: { storyPoints: 5, labels: ['devmind'] }, expectedUpdated: found.issues[0]?.updated });
    expect(upd.issue).toMatchObject({ storyPoints: 5, labels: ['devmind'] });

    const moved = await run<{ issue: JiraIssue }>('jira_transition_issue', { key: story.storyKey, toStatus: 'in progress' });
    expect(moved.issue.status).toBe('In Progress');
    const comment = await run<{ id: string; url: string }>('jira_add_comment', { key: story.storyKey, body: 'indexed' });
    expect(comment.url).toContain(story.storyKey);
    const got = await run<{ issue: JiraIssue }>('jira_get_issue', { key: story.storyKey });
    expect(got.issue.updated > upd.issue.updated).toBe(true);

    await expect(run('jira_update_issue', { key: story.storyKey, fields: { summary: 'late' }, expectedUpdated: found.issues[0]?.updated })).rejects.toMatchObject({ code: 'GATE_CONFLICT' });
  });

  it('turns schema violations into VALIDATION_FAILED before touching a connector', async () => {
    await expect(run('jira_get_issue', { key: 'nope' })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
    await expect(run('jira_search_issues', { maxResults: 500 })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
    await expect(run('jira_update_issue', { key: 'DEV-1', fields: {} })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
    await expect(run('github_commit_index', { branch: 'b', files: [], message: 'm', expectedHeadSha: 'short' })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
    await expect(run('github_read_files', { ref: 'main', paths: [] })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
  });

  it('drives the GitHub index flow by tool name', async () => {
    const commit = await run<{ commitSha: string; noop: boolean; parentSha: string | null }>('github_commit_index', {
      branch: 'devmind/index',
      files: [{ path: '.devmind/manifest.json', content: '{"generation":1}' }, { path: '.devmind/docs/a.md', content: '# A' }],
      message: 'chore(devmind): index',
      strategy: 'index-branch', // extra orchestrator field is ignored
    });
    expect(commit.noop).toBe(false);
    const again = await run<{ noop: boolean; commitSha: string }>('github_commit_index', {
      branch: 'devmind/index',
      files: [{ path: '.devmind/manifest.json', content: '{"generation":1}' }],
      message: 'no change',
    });
    expect(again).toMatchObject({ noop: true, commitSha: commit.commitSha });

    const read = await run<{ files: Array<{ path: string; content: string | null }> }>('github_read_files', { ref: 'devmind/index', paths: ['.devmind/manifest.json', 'missing.md'] });
    expect(read.files.map((f) => f.content)).toEqual(['{"generation":1}', null]);
    const tree = await run<{ entries: Array<{ path: string }>; truncated: boolean }>('github_list_tree', { ref: 'devmind/index', prefix: '.devmind/docs/' });
    expect(tree.entries.map((e) => e.path)).toEqual(['.devmind/docs/a.md']);

    const pr1 = await run<{ number: number; created: boolean }>('github_open_pull_request', { head: 'devmind/index', title: 'Index', body: 'b' });
    const pr2 = await run<{ number: number; created: boolean }>('github_open_pull_request', { head: 'devmind/index', title: 'Index', body: 'b' });
    expect(pr1.created).toBe(true);
    expect(pr2).toEqual({ ...pr1, created: false, url: expect.any(String) as unknown });

    await expect(run('github_commit_index', { branch: 'b', files: [{ path: '../x', content: '' }], message: 'm' })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
  });

  it('keeps existing tools working (create_pull_request, create_branch, commit_code)', async () => {
    const pr = await run<{ prNumber: number }>('github_create_pull_request', { branch: 'x', title: 't', body: 'b' });
    expect(pr.prNumber).toBeGreaterThan(0);
    const code = await run<{ runId: string }>('github_commit_code', { branch: 'feat/z', files: [{ path: 'a.ts', content: 'ok' }], message: 'm' });
    expect(code.runId).toMatch(/^run-/);
  });
});

describe('runtime wiring (live mode against in-process fakes)', () => {
  let gh: FakeGithub;
  let jira: FakeJira;
  beforeAll(async () => {
    [gh, jira] = await Promise.all([startFakeGithub(), startFakeJira()]);
  });
  afterAll(async () => {
    await Promise.all([gh.close(), jira.close()]);
  });

  it('resolves both connectors to live and executes new tools through real HTTP', async () => {
    gh.store.commit('main', { 'README.md': 'hi' });
    jira.seed('DEV-1');
    const env = testEnv({
      TOOLS_MODE: 'auto',
      GITHUB_TOKEN: 'test-token',
      GITHUB_REPO: 'acme/widgets',
      GITHUB_API_URL: gh.url,
      JIRA_BASE_URL: jira.url,
      JIRA_EMAIL: jira.email,
      JIRA_API_TOKEN: jira.token,
    });
    const runtime = createToolRuntime(env, fakeRedis(), log, { http: fastPolicy() });
    expect(runtime.modes).toMatchObject({ jira: 'live', github: 'live' });

    const commit = (await runtime.execute('github_commit_index', { branch: 'devmind/index', files: [{ path: '.devmind/a.md', content: 'A' }], message: 'm' })) as { noop: boolean };
    expect(commit.noop).toBe(false);
    expect(gh.store.filesAt('devmind/index')['.devmind/a.md']).toBe('A');
    const read = (await runtime.execute('github_read_files', { ref: 'devmind/index', paths: ['.devmind/a.md'] })) as { files: Array<{ content: string }> };
    expect(read.files[0]?.content).toBe('A');

    const got = (await runtime.execute('jira_get_issue', { key: 'DEV-1' })) as { issue: JiraIssue };
    expect(got.issue.key).toBe('DEV-1');
    const moved = (await runtime.execute('jira_transition_issue', { key: 'DEV-1', toStatus: 'Done' })) as { issue: JiraIssue };
    expect(moved.issue.statusCategory).toBe('done');
    await expect(runtime.execute('jira_get_issue', { key: 'DEV-404' })).rejects.toMatchObject({ code: 'NOT_FOUND' });
  });
});
