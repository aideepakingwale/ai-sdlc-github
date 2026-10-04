import type { Redis } from 'ioredis';
import type { z } from 'zod';
import { SdlcError } from '@sdlc/shared';
import type { jiraSearchIssues, jiraUpdateIssue, JiraIssue, ToolsEnv } from '@sdlc/shared';
import { adfDoc, buildAcFieldAdf, buildDescriptionAdf, parseAcField, splitDescription, textToAdfBlocks } from './adf.js';
import { httpRequest, resolvePolicy, type HttpPolicy, type HttpResult } from './http.js';
import { buildSearch, compileMockJql } from './jql.js';

export type SearchInput = z.output<typeof jiraSearchIssues.input>;
export type UpdateFields = z.output<typeof jiraUpdateIssue.input>['fields'];
export interface SearchResult {
  issues: JiraIssue[];
  nextPageToken: string | null;
  total: number | null;
}

/** The operations shared by the live (REST) and mock (Redis) implementations. */
export interface JiraIssueOps {
  search(input: SearchInput): Promise<SearchResult>;
  get(key: string): Promise<JiraIssue>;
  update(key: string, fields: UpdateFields, expectedUpdated?: string): Promise<JiraIssue>;
  transition(key: string, toStatus: string): Promise<JiraIssue>;
  comment(key: string, body: string): Promise<{ id: string; url: string }>;
}

export interface JiraOpsDeps {
  env: ToolsEnv;
  redis: Redis;
  http?: Partial<HttpPolicy>;
  /** Wall clock for the mock; injectable for tests. */
  now?: () => number;
}

// ---------------------------------------------------------------------------
// small unknown-narrowing helpers (no `any`)
// ---------------------------------------------------------------------------
type Dict = Record<string, unknown>;
const isDict = (v: unknown): v is Dict => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);

function toIso(value: unknown): string {
  const s = typeof value === 'string' ? value : '';
  const ms = Date.parse(s);
  return Number.isNaN(ms) ? s : new Date(ms).toISOString();
}

/** Compare two timestamps by instant, tolerating different offsets/formats. */
export function sameInstant(a: string, b: string): boolean {
  const x = Date.parse(a);
  const y = Date.parse(b);
  return Number.isNaN(x) || Number.isNaN(y) ? a === b : x === y;
}

function categoryOf(key: unknown): JiraIssue['statusCategory'] {
  switch (key) {
    case 'new':
      return 'todo';
    case 'indeterminate':
      return 'inprogress';
    case 'done':
      return 'done';
    default:
      return 'unknown';
  }
}

function pickSprint(value: unknown): JiraIssue['sprint'] {
  if (!Array.isArray(value)) return null;
  const sprints = value
    .filter(isDict)
    .map((s) => ({ id: Number(s['id']), name: str(s['name']) ?? '', state: str(s['state']) ?? 'unknown' }))
    .filter((s) => Number.isFinite(s.id));
  return sprints.find((s) => s.state === 'active') ?? sprints.find((s) => s.state === 'future') ?? sprints[sprints.length - 1] ?? null;
}

function jiraErrorDetail(body: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (isDict(parsed)) {
      const messages = Array.isArray(parsed['errorMessages']) ? parsed['errorMessages'].filter((m): m is string => typeof m === 'string') : [];
      const errors = isDict(parsed['errors']) ? Object.entries(parsed['errors']).map(([k, v]) => `${k}: ${String(v)}`) : [];
      const all = [...messages, ...errors];
      if (all.length > 0) return all.join('; ').slice(0, 500);
    }
  } catch {
    /* fall through to the raw snippet */
  }
  return body.replace(/\s+/g, ' ').slice(0, 300);
}

/** Map a non-success Jira response to the repo's error taxonomy. Never includes request headers. */
export function mapJiraError(status: number, body: string, context: string): SdlcError {
  const detail = jiraErrorDetail(body);
  const details = { status, context };
  switch (status) {
    case 401:
    case 403:
      return new SdlcError('TOOL_ERROR', `Jira ${status} on ${context}: authentication or permission failure - check JIRA_EMAIL/JIRA_API_TOKEN and that the token's user can access this resource`, { details });
    case 404:
      return new SdlcError('NOT_FOUND', `Jira ${context}: not found (404), or not visible to the configured user${detail ? ` (${detail})` : ''}`, { details });
    case 400:
      return new SdlcError('VALIDATION_FAILED', `Jira rejected the request on ${context} (400): ${detail}`, { details });
    case 409:
      return new SdlcError('GATE_CONFLICT', `Jira conflict on ${context} (409): ${detail}`, { details });
    case 429:
      return new SdlcError('RATE_LIMITED', `Jira rate limit on ${context} persisted after retries (429)`, { details });
    default:
      return new SdlcError('TOOL_ERROR', `Jira ${status} on ${context}: ${detail}`, { details });
  }
}

function conflictError(key: string, expected: string, actual: string): SdlcError {
  return new SdlcError('GATE_CONFLICT', `Jira issue ${key} was modified since ${expected} (current updated: ${actual}); re-read it and retry`, {
    details: { key, expectedUpdated: expected, actualUpdated: actual },
  });
}

function notFound(key: string): SdlcError {
  return new SdlcError('NOT_FOUND', `Jira issue ${key} not found`, { details: { key } });
}

// ---------------------------------------------------------------------------
// LIVE
// ---------------------------------------------------------------------------
export function liveJiraOps(deps: JiraOpsDeps): JiraIssueOps {
  const { env } = deps;
  const policy = resolvePolicy({ maxRetries: env.JIRA_MAX_RETRIES, ...(deps.http ?? {}) });
  const spField = env.JIRA_STORY_POINTS_FIELD;
  const sprintField = env.JIRA_SPRINT_FIELD;
  const acField = env.JIRA_AC_FIELD;
  const fieldList = ['summary', 'description', 'issuetype', 'status', 'priority', 'labels', 'parent', 'assignee', 'created', 'updated', spField, sprintField, ...(acField ? [acField] : [])];

  const base = (): string => {
    const url = env.JIRA_BASE_URL?.trim().replace(/\/+$/, '');
    if (!url || !/^https?:\/\//i.test(url)) throw new SdlcError('INTERNAL', 'JIRA_BASE_URL is missing or not an http(s) URL');
    return url;
  };
  const authorization = (): string => `Basic ${Buffer.from(`${env.JIRA_EMAIL ?? ''}:${env.JIRA_API_TOKEN ?? ''}`).toString('base64')}`;

  async function call(method: string, path: string, opts: { body?: unknown; idempotent?: boolean } = {}): Promise<HttpResult> {
    return httpRequest(
      {
        label: 'Jira',
        url: `${base()}${path}`,
        method,
        headers: {
          accept: 'application/json',
          authorization: authorization(),
          ...(opts.body !== undefined ? { 'content-type': 'application/json' } : {}),
        },
        ...(opts.body !== undefined ? { body: JSON.stringify(opts.body) } : {}),
        ...(opts.idempotent !== undefined ? { idempotent: opts.idempotent } : {}),
      },
      policy,
    );
  }

  async function ok<T>(res: HttpResult, context: string): Promise<T> {
    if (!res.ok) throw mapJiraError(res.status, await res.text(), context);
    return res.json<T>();
  }

  interface Parsed {
    issue: JiraIssue;
    acSource: 'field' | 'description' | 'none';
  }

  function parse(raw: unknown): Parsed {
    if (!isDict(raw) || typeof raw['key'] !== 'string') throw new SdlcError('TOOL_ERROR', 'Jira returned an unexpected issue payload');
    const f = isDict(raw['fields']) ? raw['fields'] : {};
    const key = raw['key'];
    const split = splitDescription(f['description']);
    const fieldAc = acField ? parseAcField(f[acField]) : [];
    const acSource = fieldAc.length > 0 ? 'field' : split.acceptanceCriteria.length > 0 ? 'description' : 'none';
    // The AC section is always removed from `description` so callers see one canonical split.
    const parent = isDict(f['parent']) ? f['parent'] : null;
    const parentType = parent && isDict(parent['fields']) && isDict(parent['fields']['issuetype']) ? str(parent['fields']['issuetype']['name']) : null;
    const status = isDict(f['status']) ? f['status'] : {};
    const category = isDict(status['statusCategory']) ? status['statusCategory']['key'] : undefined;
    const assignee = isDict(f['assignee']) ? f['assignee'] : null;
    const points = f[spField];
    const issue: JiraIssue = {
      key,
      id: String(raw['id'] ?? ''),
      url: `${base()}/browse/${key}`,
      summary: str(f['summary']) ?? '',
      description: split.text,
      type: isDict(f['issuetype']) ? (str(f['issuetype']['name']) ?? 'Unknown') : 'Unknown',
      status: str(status['name']) ?? 'Unknown',
      statusCategory: categoryOf(category),
      priority: isDict(f['priority']) ? str(f['priority']['name']) : null,
      storyPoints: typeof points === 'number' && Number.isFinite(points) ? points : null,
      labels: Array.isArray(f['labels']) ? f['labels'].filter((l): l is string => typeof l === 'string') : [],
      epicKey: parent && str(parent['key']) && parentType?.toLowerCase() === 'epic' ? str(parent['key']) : null,
      sprint: pickSprint(f[sprintField]),
      assignee: assignee ? (str(assignee['displayName']) ?? str(assignee['emailAddress']) ?? str(assignee['accountId'])) : null,
      created: toIso(f['created']),
      updated: toIso(f['updated']),
      acceptanceCriteria: fieldAc.length > 0 ? fieldAc : split.acceptanceCriteria,
    };
    return { issue, acSource };
  }

  async function fetchIssue(key: string): Promise<Parsed> {
    const res = await call('GET', `/rest/api/3/issue/${encodeURIComponent(key)}?fields=${fieldList.map(encodeURIComponent).join(',')}`);
    if (res.status === 404) throw notFound(key);
    return parse(await ok<unknown>(res, `GET issue ${key}`));
  }

  return {
    async search(input) {
      const spec = buildSearch(
        {
          ...(input.projectKey !== undefined ? { projectKey: input.projectKey } : {}),
          ...(input.jql !== undefined ? { jql: input.jql } : {}),
          ...(input.updatedSince !== undefined ? { updatedSince: input.updatedSince } : {}),
          ...(input.issueTypes !== undefined ? { issueTypes: input.issueTypes } : {}),
        },
        env.JIRA_PROJECT_KEY,
      );
      // Read-only POST: safe to retry on transient failures.
      const res = await call('POST', '/rest/api/3/search/jql', {
        idempotent: true,
        body: {
          jql: spec.jql,
          maxResults: input.maxResults,
          fields: fieldList,
          ...(input.nextPageToken !== undefined ? { nextPageToken: input.nextPageToken } : {}),
        },
      });
      const data = await ok<unknown>(res, 'search');
      const rec = isDict(data) ? data : {};
      const issues = Array.isArray(rec['issues']) ? rec['issues'].map((r) => parse(r).issue) : [];
      const token = str(rec['nextPageToken']);
      return { issues, nextPageToken: rec['isLast'] === true ? null : token, total: null };
    },

    async get(key) {
      return (await fetchIssue(key)).issue;
    },

    async update(key, fields, expectedUpdated) {
      const current = await fetchIssue(key);
      if (expectedUpdated !== undefined && !sameInstant(expectedUpdated, current.issue.updated)) {
        throw conflictError(key, expectedUpdated, current.issue.updated);
      }
      const body: Dict = {};
      if (fields.summary !== undefined) body['summary'] = fields.summary;
      if (fields.priority !== undefined) body['priority'] = { name: fields.priority };
      if (fields.labels !== undefined) body['labels'] = fields.labels;
      if (fields.storyPoints !== undefined) body[spField] = fields.storyPoints;

      const ac = fields.acceptanceCriteria ?? current.issue.acceptanceCriteria;
      if (acField) {
        if (fields.description !== undefined) body['description'] = buildDescriptionAdf(fields.description, []);
        // Keep legacy description-embedded AC when the dedicated field is introduced.
        const migrate = fields.description !== undefined && current.acSource === 'description' && fields.acceptanceCriteria === undefined;
        if (fields.acceptanceCriteria !== undefined || migrate) body[acField] = buildAcFieldAdf(ac);
      } else if (fields.description !== undefined || fields.acceptanceCriteria !== undefined) {
        body['description'] = buildDescriptionAdf(fields.description ?? current.issue.description, ac);
      }

      const res = await call('PUT', `/rest/api/3/issue/${encodeURIComponent(key)}`, { body: { fields: body } });
      if (res.status === 404) throw notFound(key);
      if (!res.ok) throw mapJiraError(res.status, await res.text(), `PUT issue ${key}`);
      return (await fetchIssue(key)).issue;
    },

    async transition(key, toStatus) {
      const current = await fetchIssue(key);
      if (current.issue.status.toLowerCase() === toStatus.trim().toLowerCase()) return current.issue; // already there
      const res = await call('GET', `/rest/api/3/issue/${encodeURIComponent(key)}/transitions`);
      if (res.status === 404) throw notFound(key);
      const data = await ok<unknown>(res, `GET transitions ${key}`);
      const list = (isDict(data) && Array.isArray(data['transitions']) ? data['transitions'] : []).filter(isDict).map((t) => ({
        id: str(t['id']),
        name: str(t['name']) ?? '',
        to: isDict(t['to']) ? (str(t['to']['name']) ?? '') : '',
      }));
      const wanted = toStatus.trim().toLowerCase();
      const match = list.find((t) => t.to.toLowerCase() === wanted) ?? list.find((t) => t.name.toLowerCase() === wanted);
      if (!match || !match.id) {
        const available = [...new Set(list.map((t) => t.to).filter((s) => s !== ''))];
        throw new SdlcError(
          'VALIDATION_FAILED',
          `Cannot move ${key} (currently "${current.issue.status}") to "${toStatus}". Available target statuses: ${available.length > 0 ? available.join(', ') : '(none)'}`,
          { details: { key, available } },
        );
      }
      // Non-idempotent POST: only retried when Jira says 429 (request not processed).
      const post = await call('POST', `/rest/api/3/issue/${encodeURIComponent(key)}/transitions`, { body: { transition: { id: match.id } } });
      if (!post.ok) throw mapJiraError(post.status, await post.text(), `transition ${key}`);
      return (await fetchIssue(key)).issue;
    },

    async comment(key, body) {
      const res = await call('POST', `/rest/api/3/issue/${encodeURIComponent(key)}/comment`, {
        body: { body: adfDoc(textToAdfBlocks(body)) },
      });
      if (res.status === 404) throw notFound(key);
      const data = await ok<unknown>(res, `comment on ${key}`);
      const id = isDict(data) ? (str(data['id']) ?? (typeof data['id'] === 'number' ? String(data['id']) : null)) : null;
      if (!id) throw new SdlcError('TOOL_ERROR', 'Jira did not return a comment id');
      return { id, url: `${base()}/browse/${key}?focusedCommentId=${encodeURIComponent(id)}` };
    },
  };
}

// ---------------------------------------------------------------------------
// MOCK (Redis): hash per project + sorted set index by updated time
// ---------------------------------------------------------------------------
export const MOCK_JIRA_TTL = 604_800; // 7d, matches the other mock keys
const MOCK_STATUSES = [
  { name: 'To Do', category: 'todo' },
  { name: 'In Progress', category: 'inprogress' },
  { name: 'Done', category: 'done' },
] as const satisfies ReadonlyArray<{ name: string; category: JiraIssue['statusCategory'] }>;

const projectOf = (key: string): string => key.split('-')[0] ?? key;
const issuesKey = (project: string): string => `mock:jira:issues:${project}`;
const indexKey = (project: string): string => `mock:jira:idx:${project}`;
const commentsKey = (key: string): string => `mock:jira:comments:${key}`;
const PROJECTS_KEY = 'mock:jira:projects';
const CLOCK_KEY = 'mock:jira:clock';

export interface MockIssueSeed {
  key: string;
  id: string;
  summary: string;
  description?: string;
  type: string;
  priority?: string | null;
  storyPoints?: number | null;
  epicKey?: string | null;
  acceptanceCriteria?: string[];
  labels?: string[];
}

export interface MockJiraOps extends JiraIssueOps {
  /** Persist a newly created issue (status "To Do"). */
  create(seed: MockIssueSeed): Promise<JiraIssue>;
}

export function mockJiraOps(deps: JiraOpsDeps): MockJiraOps {
  const { redis } = deps;
  const clock = deps.now ?? Date.now;
  let localLast = 0;

  /** Strictly increasing millisecond timestamps, even across processes sharing Redis. */
  async function tick(): Promise<number> {
    const shared = Number((await redis.get(CLOCK_KEY)) ?? 0);
    const next = Math.max(clock(), shared + 1, localLast + 1);
    localLast = next;
    await redis.set(CLOCK_KEY, String(next));
    return next;
  }

  async function save(issue: JiraIssue): Promise<void> {
    const project = projectOf(issue.key);
    await redis.hset(issuesKey(project), issue.key, JSON.stringify(issue));
    await redis.zadd(indexKey(project), Date.parse(issue.updated), issue.key);
    await redis.sadd(PROJECTS_KEY, project);
    await Promise.all([redis.expire(issuesKey(project), MOCK_JIRA_TTL), redis.expire(indexKey(project), MOCK_JIRA_TTL)]);
  }

  async function load(key: string): Promise<JiraIssue> {
    const raw = await redis.hget(issuesKey(projectOf(key)), key);
    if (!raw) throw notFound(key);
    return JSON.parse(raw) as JiraIssue;
  }

  const encodeToken = (updatedMs: number, key: string): string => Buffer.from(JSON.stringify({ u: updatedMs, k: key })).toString('base64url');
  function decodeToken(token: string): { u: number; k: string } {
    try {
      const v: unknown = JSON.parse(Buffer.from(token, 'base64url').toString('utf8'));
      if (isDict(v) && typeof v['u'] === 'number' && typeof v['k'] === 'string') return { u: v['u'], k: v['k'] };
    } catch {
      /* handled below */
    }
    throw new SdlcError('VALIDATION_FAILED', 'nextPageToken is not valid');
  }

  async function touch(issue: JiraIssue, mutate: (i: JiraIssue) => void): Promise<JiraIssue> {
    const next: JiraIssue = { ...issue };
    mutate(next);
    next.updated = new Date(await tick()).toISOString();
    await save(next);
    return next;
  }

  return {
    async create(seed) {
      const now = new Date(await tick()).toISOString();
      const issue: JiraIssue = {
        key: seed.key,
        id: seed.id,
        url: `https://jira.mock.local/browse/${seed.key}`,
        summary: seed.summary,
        description: seed.description ?? '',
        type: seed.type,
        status: 'To Do',
        statusCategory: 'todo',
        priority: seed.priority ?? null,
        storyPoints: seed.storyPoints ?? null,
        labels: seed.labels ?? [],
        epicKey: seed.epicKey ?? null,
        sprint: null,
        assignee: null,
        created: now,
        updated: now,
        acceptanceCriteria: seed.acceptanceCriteria ?? [],
      };
      await save(issue);
      return issue;
    },

    async search(input) {
      const spec = buildSearch(
        {
          ...(input.projectKey !== undefined ? { projectKey: input.projectKey } : {}),
          ...(input.jql !== undefined ? { jql: input.jql } : {}),
          ...(input.updatedSince !== undefined ? { updatedSince: input.updatedSince } : {}),
          ...(input.issueTypes !== undefined ? { issueTypes: input.issueTypes } : {}),
        },
        deps.env.JIRA_PROJECT_KEY,
      );
      const predicate = spec.rawJql !== null ? compileMockJql(spec.rawJql) : (): boolean => true;
      const types = spec.issueTypes.map((t) => t.toLowerCase());
      // JQL dates have minute resolution; floor so mock and live agree.
      const floor = spec.updatedSince !== null ? Math.floor(Date.parse(spec.updatedSince) / 60_000) * 60_000 : -Infinity;
      const projects = spec.projectKey !== null ? [spec.projectKey] : await redis.smembers(PROJECTS_KEY);

      const matches: JiraIssue[] = [];
      for (const project of projects) {
        const keys = await redis.zrangebyscore(indexKey(project), Number.isFinite(floor) ? floor : '-inf', '+inf');
        if (keys.length === 0) continue;
        const raws = await redis.hmget(issuesKey(project), ...keys);
        for (const raw of raws) {
          if (!raw) continue;
          const issue = JSON.parse(raw) as JiraIssue;
          if (types.length > 0 && !types.includes(issue.type.toLowerCase())) continue;
          if (predicate(issue)) matches.push(issue);
        }
      }
      const order = (a: JiraIssue, b: JiraIssue): number => Date.parse(a.updated) - Date.parse(b.updated) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
      matches.sort(order);

      let rest = matches;
      if (input.nextPageToken !== undefined) {
        const cursor = decodeToken(input.nextPageToken);
        rest = matches.filter((i) => {
          const u = Date.parse(i.updated);
          return u > cursor.u || (u === cursor.u && i.key > cursor.k);
        });
      }
      const page = rest.slice(0, input.maxResults);
      const last = page[page.length - 1];
      const more = rest.length > page.length && last !== undefined;
      return { issues: page, nextPageToken: more ? encodeToken(Date.parse(last.updated), last.key) : null, total: matches.length };
    },

    async get(key) {
      return load(key);
    },

    async update(key, fields, expectedUpdated) {
      const current = await load(key);
      if (expectedUpdated !== undefined && !sameInstant(expectedUpdated, current.updated)) {
        throw conflictError(key, expectedUpdated, current.updated);
      }
      return touch(current, (i) => {
        if (fields.summary !== undefined) i.summary = fields.summary;
        if (fields.description !== undefined) i.description = fields.description;
        if (fields.priority !== undefined) i.priority = fields.priority;
        if (fields.storyPoints !== undefined) i.storyPoints = fields.storyPoints;
        if (fields.labels !== undefined) i.labels = [...fields.labels];
        if (fields.acceptanceCriteria !== undefined) i.acceptanceCriteria = [...fields.acceptanceCriteria];
      });
    },

    async transition(key, toStatus) {
      const current = await load(key);
      const wanted = toStatus.trim().toLowerCase();
      if (current.status.toLowerCase() === wanted) return current;
      const target = MOCK_STATUSES.find((s) => s.name.toLowerCase() === wanted);
      if (!target) {
        const available = MOCK_STATUSES.filter((s) => s.name !== current.status).map((s) => s.name);
        throw new SdlcError('VALIDATION_FAILED', `Cannot move ${key} (currently "${current.status}") to "${toStatus}". Available target statuses: ${available.join(', ')}`, {
          details: { key, available },
        });
      }
      return touch(current, (i) => {
        i.status = target.name;
        i.statusCategory = target.category;
      });
    },

    async comment(key, body) {
      const current = await load(key);
      const id = String(await redis.incr('mock:seq:jiracomment'));
      await redis.hset(commentsKey(key), id, JSON.stringify({ id, body, created: new Date(clock()).toISOString() }));
      await redis.expire(commentsKey(key), MOCK_JIRA_TTL);
      await touch(current, () => undefined); // commenting bumps `updated`, as in Jira
      return { id, url: `https://jira.mock.local/browse/${key}?focusedCommentId=${id}` };
    },
  };
}
