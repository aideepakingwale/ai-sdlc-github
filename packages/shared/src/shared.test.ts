import { describe, expect, it } from 'vitest';
import { AgentStateSchema } from './agent-state.js';
import { loadEnv, OrchestratorEnvSchema, ToolsEnvSchema } from './env.js';
import { SdlcError, toSdlcError } from './errors.js';
import { ulid } from './ids.js';
import { canReviewPhase, getPhase, PHASES, primaryRole } from './phases.js';
import { estimateTokens } from './tokens.js';
import { getTool, TOOL_REGISTRY } from './tools.js';

describe('phases', () => {
  it('defines exactly six ordered phases', () => {
    expect(PHASES.map((p) => p.id)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it('enforces reviewer role per phase; SUPER_ADMIN overrides; PM never reviews', () => {
    expect(canReviewPhase('PO', 1)).toBe(true);
    expect(canReviewPhase('PO', 2)).toBe(false);
    expect(canReviewPhase('SUPER_ADMIN', 5)).toBe(true);
    expect(canReviewPhase('PROJECT_MANAGER', 1)).toBe(false);
    expect(canReviewPhase('PROJECT_MANAGER', 6)).toBe(false);
    expect(canReviewPhase('QA', 4)).toBe(true);
    expect(canReviewPhase('DEV', 6)).toBe(true);
  });

  it('primaryRole picks highest-priority realm role for JIT provisioning', () => {
    expect(primaryRole(['DEV', 'SUPER_ADMIN'])).toBe('SUPER_ADMIN');
    expect(primaryRole(['offline_access', 'QA'])).toBe('QA');
    expect(primaryRole(['uma_authorization'])).toBeNull();
  });

  it('throws on unknown phase', () => {
    expect(() => getPhase(7)).toThrow(RangeError);
  });
});

describe('env', () => {
  it('lists every missing variable in one error', () => {
    expect(() => loadEnv(OrchestratorEnvSchema, {})).toThrow(/DATABASE_URL[\s\S]*JWT_SECRET/);
  });

  it('parses a valid orchestrator env', () => {
    const env = loadEnv(OrchestratorEnvSchema, {
      DATABASE_URL: 'postgresql://u:p@h:5432/db',
      DYNAMO_ENDPOINT: 'http://localhost:8000',
      JWT_SECRET: 'x'.repeat(64),
      AI_CLIENT_URL: 'http://localhost:8081',
      TOOLS_MCP_URL: 'http://localhost:8082/mcp',
    });
    expect(env.ORCHESTRATOR_PORT).toBe(8080);
    expect(env.BUILD_LOOP_MAX_ITERATIONS).toBe(5);
  });
});

describe('agent state', () => {
  it('applies defaults for a minimal state', () => {
    const state = AgentStateSchema.parse({
      projectId: 'p1',
      sessionId: 's1',
      currentPhase: 1,
      userInput: 'Build a payments API',
    });
    expect(state.contextWindow).toEqual([]);
    expect(state.gateStatus).toBe('IN_PROGRESS');
  });

  it('rejects out-of-range phase', () => {
    expect(() =>
      AgentStateSchema.parse({ projectId: 'p', sessionId: 's', currentPhase: 9, userInput: 'x' }),
    ).toThrow();
  });
});

describe('tools registry', () => {
  it('validates tool inputs against schemas', () => {
    const t = getTool('jira_create_epic');
    expect(() => t.input.parse({ description: 'no title' })).toThrow();
    const parsed = t.input.parse({ title: 'Epic', description: 'd' });
    expect(parsed).toMatchObject({ priority: 'Medium' });
  });

  it('has unique names matching registry keys', () => {
    for (const [key, def] of Object.entries(TOOL_REGISTRY)) {
      expect(def.name).toBe(key);
    }
  });
});

describe('errors', () => {
  it('maps codes to default statuses and wraps unknowns', () => {
    expect(new SdlcError('AUTH_FAILED', 'nope').httpStatus).toBe(401);
    expect(toSdlcError(new Error('boom')).code).toBe('INTERNAL');
  });
});

describe('utils', () => {
  it('estimates tokens deterministically', () => {
    expect(estimateTokens('')).toBe(0);
    expect(estimateTokens('a'.repeat(400))).toBe(100);
  });

  it('ulid is 26 chars and time-ordered', () => {
    const a = ulid(1_000_000);
    const b = ulid(2_000_000);
    expect(a).toHaveLength(26);
    expect(a < b).toBe(true);
  });
});

describe('connector tool schemas (jira)', () => {
  const input = (name: string, v: unknown) => getTool(name).input.safeParse(v);

  it('registers every new tool under its exact name', () => {
    for (const name of [
      'jira_search_issues', 'jira_get_issue', 'jira_update_issue', 'jira_transition_issue', 'jira_add_comment',
      'github_commit_index', 'github_read_files', 'github_list_tree', 'github_open_pull_request',
    ]) {
      expect(getTool(name).name).toBe(name);
      expect(getTool(name).cacheable).toBeUndefined(); // live reads/writes must never be cached
    }
  });

  it('jira_search_issues applies defaults and accepts the full filter set', () => {
    expect(input('jira_search_issues', {}).success).toBe(true);
    expect(getTool('jira_search_issues').input.parse({})).toEqual({ maxResults: 50 });
    const full = { projectKey: 'dev', jql: 'labels = x', updatedSince: '2025-01-01T00:00:00Z', issueTypes: ['Story'], maxResults: 100, nextPageToken: 'abc' };
    expect(getTool('jira_search_issues').input.parse(full)).toEqual(full);
  });

  it.each([
    ['maxResults 0', { maxResults: 0 }],
    ['maxResults 101', { maxResults: 101 }],
    ['maxResults fractional', { maxResults: 1.5 }],
    ['updatedSince not ISO', { updatedSince: 'yesterday' }],
    ['updatedSince date only', { updatedSince: '2025-01-01' }],
    ['empty jql', { jql: '' }],
    ['jql too long', { jql: 'x'.repeat(2001) }],
    ['issueTypes not array', { issueTypes: 'Story' }],
    ['empty issue type', { issueTypes: [''] }],
    ['empty token', { nextPageToken: '' }],
  ])('jira_search_issues rejects %s', (_n, v) => {
    expect(input('jira_search_issues', v).success).toBe(false);
  });

  it.each(['', 'abc', 'proj-1', 'PROJ-', 'PROJ-1/../x', '../PROJ-1', 'PROJ-1?x=1', 'PROJ 1', '1PROJ-1', 'PROJ-1\n'])('issue key %j is rejected by get/transition/comment/update', (key) => {
    expect(input('jira_get_issue', { key }).success).toBe(false);
    expect(input('jira_transition_issue', { key, toStatus: 'Done' }).success).toBe(false);
    expect(input('jira_add_comment', { key, body: 'x' }).success).toBe(false);
    expect(input('jira_update_issue', { key, fields: { summary: 'x' } }).success).toBe(false);
  });

  it.each(['PROJ-1', 'AB-123456', 'A1_B-9'])('issue key %j is accepted', (key) => {
    expect(input('jira_get_issue', { key }).success).toBe(true);
  });

  it.each([
    ['no fields', { fields: {} }],
    ['only undefined fields', { fields: { summary: undefined } }],
    ['unknown field', { fields: { reporter: 'x' } }],
    ['empty summary', { fields: { summary: '' } }],
    ['summary > 255', { fields: { summary: 'x'.repeat(256) } }],
    ['description > 30000', { fields: { description: 'x'.repeat(30_001) } }],
    ['negative points', { fields: { storyPoints: -1 } }],
    ['label with space', { fields: { labels: ['a b'] } }],
    ['empty AC item', { fields: { acceptanceCriteria: [''] } }],
    ['bad expectedUpdated', { fields: { summary: 'x' }, expectedUpdated: 'now' }],
    ['missing fields', {}],
  ])('jira_update_issue rejects %s', (_n, rest) => {
    expect(input('jira_update_issue', { key: 'DEV-1', ...rest }).success).toBe(false);
  });

  it('jira_update_issue accepts a valid payload (empty description/AC clear the field)', () => {
    const ok = input('jira_update_issue', {
      key: 'DEV-1',
      fields: { summary: 's', description: '', priority: 'High', storyPoints: 0, labels: ['a'], acceptanceCriteria: ['g'] },
      expectedUpdated: '2025-01-01T00:00:00.000+02:00',
    });
    expect(ok.success).toBe(true);
  });

  it('transition and comment require non-empty strings', () => {
    expect(input('jira_transition_issue', { key: 'DEV-1', toStatus: '' }).success).toBe(false);
    expect(input('jira_add_comment', { key: 'DEV-1', body: '' }).success).toBe(false);
    expect(input('jira_add_comment', { key: 'DEV-1', body: 'x'.repeat(30_001) }).success).toBe(false);
  });

  it('JiraIssueSchema validates the documented shape and rejects bad categories', () => {
    const issue = {
      key: 'A-1', id: '1', url: 'u', summary: 's', description: '', type: 'Story', status: 'To Do', statusCategory: 'todo', priority: null,
      storyPoints: null, labels: [], epicKey: null, sprint: null, assignee: null, created: 'c', updated: 'u', acceptanceCriteria: [],
    };
    const out = getTool('jira_get_issue').output;
    expect(out.safeParse({ issue }).success).toBe(true);
    expect(out.safeParse({ issue: { ...issue, statusCategory: 'blocked' } }).success).toBe(false);
    expect(out.safeParse({ issue: { ...issue, storyPoints: '3' } }).success).toBe(false);
    expect(out.safeParse({ issue: { ...issue, sprint: { id: 'x', name: 'n', state: 's' } } }).success).toBe(false);
  });
});

describe('connector tool schemas (github)', () => {
  const input = (name: string, v: unknown) => getTool(name).input.safeParse(v);
  const sha = 'a'.repeat(40);

  it('github_commit_index applies defaults', () => {
    const parsed = getTool('github_commit_index').input.parse({ branch: 'b', files: [{ path: '.devmind/a', content: 'x' }], message: 'm' });
    expect(parsed).toMatchObject({ baseBranch: 'main', deletions: [], createBranchIfMissing: true, requiredPrefix: '.devmind/' });
    expect(parsed).not.toHaveProperty('expectedHeadSha');
  });

  it('github_commit_index tolerates extra caller fields (e.g. strategy) by stripping them', () => {
    const parsed = getTool('github_commit_index').input.parse({ branch: 'b', files: [], message: 'm', deletions: ['.devmind/x'], strategy: 'index-branch' });
    expect(parsed).not.toHaveProperty('strategy');
  });

  it.each([
    ['empty branch', { branch: '' }],
    ['missing message', { message: undefined }],
    ['empty message', { message: '' }],
    ['501 files', { files: Array.from({ length: 501 }, (_, i) => ({ path: `.devmind/${i}`, content: '' })) }],
    ['file without content', { files: [{ path: '.devmind/a' }] }],
    ['empty file path', { files: [{ path: '', content: 'x' }] }],
    ['short sha', { expectedHeadSha: 'abc123' }],
    ['non-hex sha', { expectedHeadSha: 'z'.repeat(40) }],
    ['empty requiredPrefix', { requiredPrefix: '' }],
    ['non-boolean flag', { createBranchIfMissing: 'yes' }],
    ['501 deletions', { deletions: Array.from({ length: 501 }, (_, i) => `.devmind/${i}`) }],
  ])('github_commit_index rejects %s', (_n, over) => {
    expect(input('github_commit_index', { branch: 'b', files: [{ path: '.devmind/a', content: 'x' }], message: 'm', ...over }).success).toBe(false);
  });

  it('github_commit_index accepts sha1/sha256 expectedHeadSha', () => {
    for (const expectedHeadSha of [sha, 'B'.repeat(64)]) {
      expect(input('github_commit_index', { branch: 'b', files: [], message: 'm', expectedHeadSha }).success).toBe(true);
    }
  });

  it('github_read_files / github_list_tree / github_open_pull_request validate and default', () => {
    expect(input('github_read_files', { ref: 'main', paths: [] }).success).toBe(false);
    expect(input('github_read_files', { ref: 'main', paths: Array.from({ length: 201 }, (_, i) => `p${i}`) }).success).toBe(false);
    expect(input('github_read_files', { ref: '', paths: ['a'] }).success).toBe(false);
    expect(input('github_read_files', { ref: 'main', paths: Array.from({ length: 200 }, (_, i) => `p${i}`) }).success).toBe(true);
    expect(getTool('github_list_tree').input.parse({ ref: 'main' })).toEqual({ ref: 'main', prefix: '', recursive: true });
    expect(input('github_list_tree', { ref: 'main', recursive: 'yes' }).success).toBe(false);
    expect(getTool('github_open_pull_request').input.parse({ head: 'h', title: 't', body: 'b' })).toEqual({ head: 'h', base: 'main', title: 't', body: 'b', reuseExisting: true });
    expect(input('github_open_pull_request', { head: 'h', title: '', body: 'b' }).success).toBe(false);
    expect(input('github_open_pull_request', { title: 't', body: 'b' }).success).toBe(false);
  });

  it('output schemas enforce the documented shapes', () => {
    const commit = getTool('github_commit_index').output;
    const ok = { commitSha: sha, parentSha: null, treeSha: sha, branch: 'b', htmlUrl: 'u', noop: false };
    expect(commit.safeParse(ok).success).toBe(true);
    expect(commit.safeParse({ ...ok, noop: 'no' }).success).toBe(false);
    expect(commit.safeParse({ ...ok, parentSha: undefined }).success).toBe(false);
    expect(getTool('github_list_tree').output.safeParse({ entries: [{ path: 'a', type: 'symlink', sha, size: null }], truncated: false }).success).toBe(false);
    expect(getTool('github_read_files').output.safeParse({ files: [{ path: 'a', content: null, sha: null }] }).success).toBe(true);
    expect(getTool('github_open_pull_request').output.safeParse({ number: 1.5, url: 'u', created: true }).success).toBe(false);
  });
});

describe('connector env defaults', () => {
  const base = { AI_CLIENT_URL: 'http://localhost:9' };

  it('provides Jira and GitHub defaults', () => {
    const env = loadEnv(ToolsEnvSchema, base);
    expect(env).toMatchObject({
      JIRA_STORY_POINTS_FIELD: 'customfield_10016',
      JIRA_SPRINT_FIELD: 'customfield_10020',
      JIRA_MAX_RETRIES: 4,
      GITHUB_API_URL: 'https://api.github.com',
      GITHUB_COMMIT_AUTHOR_NAME: 'DevMind',
      GITHUB_COMMIT_AUTHOR_EMAIL: 'devmind@users.noreply.github.com',
    });
    expect(env.JIRA_AC_FIELD).toBeUndefined();
  });

  it('accepts overrides, treats an empty JIRA_AC_FIELD as unset, and rejects malformed values', () => {
    const env = loadEnv(ToolsEnvSchema, { ...base, JIRA_AC_FIELD: 'customfield_10050', JIRA_MAX_RETRIES: '2', GITHUB_API_URL: 'http://127.0.0.1:1234' });
    expect(env).toMatchObject({ JIRA_AC_FIELD: 'customfield_10050', JIRA_MAX_RETRIES: 2, GITHUB_API_URL: 'http://127.0.0.1:1234' });
    expect(loadEnv(ToolsEnvSchema, { ...base, JIRA_AC_FIELD: '  ' }).JIRA_AC_FIELD).toBeUndefined();
    expect(() => loadEnv(ToolsEnvSchema, { ...base, JIRA_AC_FIELD: 'acceptance' })).toThrow(/JIRA_AC_FIELD/);
    expect(() => loadEnv(ToolsEnvSchema, { ...base, JIRA_STORY_POINTS_FIELD: 'points' })).toThrow(/JIRA_STORY_POINTS_FIELD/);
    expect(() => loadEnv(ToolsEnvSchema, { ...base, JIRA_MAX_RETRIES: '-1' })).toThrow(/JIRA_MAX_RETRIES/);
    expect(() => loadEnv(ToolsEnvSchema, { ...base, JIRA_MAX_RETRIES: '99' })).toThrow(/JIRA_MAX_RETRIES/);
    expect(() => loadEnv(ToolsEnvSchema, { ...base, GITHUB_API_URL: 'not a url' })).toThrow(/GITHUB_API_URL/);
  });
});
