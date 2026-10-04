import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

/** In-process fake of the Jira Cloud REST v3 endpoints the connector uses. Test-only. */
export interface JiraRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  body: Record<string, unknown>;
  headers: IncomingMessage['headers'];
}

export interface JiraFault {
  when: (req: JiraRequest) => boolean;
  status: number;
  headers?: Record<string, string>;
  body?: unknown;
  times?: number;
}

export interface RawJiraIssue {
  id: string;
  key: string;
  fields: Record<string, unknown>;
}

export const STATUSES: ReadonlyArray<{ id: string; name: string; category: string }> = [
  { id: '11', name: 'To Do', category: 'new' },
  { id: '21', name: 'In Progress', category: 'indeterminate' },
  { id: '31', name: 'Done', category: 'done' },
];

export interface FakeJira {
  url: string;
  email: string;
  token: string;
  issues: Map<string, RawJiraIssue>;
  requests: JiraRequest[];
  faults: JiraFault[];
  comments: Array<{ key: string; body: unknown; id: string }>;
  /** Create (or replace) an issue with sensible defaults; later `updated` values increase. */
  seed(key: string, fields?: Record<string, unknown>): RawJiraIssue;
  count(method: string, pathPattern: RegExp): number;
  /** Restart the deterministic clock used for created/updated stamps. */
  resetClock(): void;
  close(): Promise<void>;
}

export async function startFakeJira(email = 'bot@example.com', token = 'jira-secret'): Promise<FakeJira> {
  const CLOCK_START = Date.parse('2025-03-01T10:00:00.000Z');
  let clock = CLOCK_START;
  const nextTime = (): string => new Date((clock += 60_000)).toISOString().replace('Z', '+0000');

  const fake: FakeJira = {
    url: '',
    email,
    token,
    issues: new Map(),
    requests: [],
    faults: [],
    comments: [],
    seed(key, fields = {}) {
      const t = nextTime();
      const issue: RawJiraIssue = {
        id: String(10_000 + fake.issues.size),
        key,
        fields: {
          summary: `Summary of ${key}`,
          description: null,
          issuetype: { name: 'Story' },
          status: { name: 'To Do', statusCategory: { key: 'new' } },
          priority: { name: 'Medium' },
          labels: [],
          created: t,
          updated: t,
          ...fields,
        },
      };
      fake.issues.set(key, issue);
      return issue;
    },
    count: (method, re) => fake.requests.filter((r) => r.method === method && re.test(r.path)).length,
    resetClock() {
      clock = CLOCK_START;
    },
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };

  const server: Server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      const url = new URL(req.url ?? '/', 'http://fake');
      let body: Record<string, unknown> = {};
      const raw = Buffer.concat(chunks).toString('utf8');
      if (raw) {
        try {
          body = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          body = {};
        }
      }
      const rec: JiraRequest = { method: req.method ?? 'GET', path: decodeURIComponent(url.pathname), query: url.searchParams, body, headers: req.headers };
      fake.requests.push(rec);
      const send = (status: number, payload?: unknown, headers: Record<string, string> = {}): void => {
        res.writeHead(status, { 'content-type': 'application/json', ...headers });
        res.end(payload === undefined || status === 204 ? undefined : JSON.stringify(payload));
      };

      const expected = `Basic ${Buffer.from(`${email}:${token}`).toString('base64')}`;
      if (req.headers.authorization !== expected) return send(401, { errorMessages: ['You are not authenticated'] });

      const idx = fake.faults.findIndex((f) => (f.times ?? 1) > 0 && f.when(rec));
      if (idx >= 0) {
        const f = fake.faults[idx] as JiraFault;
        f.times = (f.times ?? 1) - 1;
        return send(f.status, f.body ?? { errorMessages: ['injected fault'] }, f.headers);
      }

      const p = rec.path;
      const m = rec.method;
      let match: RegExpExecArray | null;
      const notFound = (): void => send(404, { errorMessages: ['Issue does not exist or you do not have permission to see it.'], errors: {} });

      if (m === 'POST' && p === '/rest/api/3/search/jql') {
        const all = [...fake.issues.values()].sort((a, b) => String(a.fields['updated']).localeCompare(String(b.fields['updated'])));
        const offset = typeof body['nextPageToken'] === 'string' ? Number(Buffer.from(body['nextPageToken'], 'base64url').toString('utf8')) : 0;
        const size = Math.min(Number(body['maxResults'] ?? 50), 100);
        const page = all.slice(offset, offset + size);
        const more = offset + size < all.length;
        return send(200, {
          issues: page,
          ...(more ? { nextPageToken: Buffer.from(String(offset + size)).toString('base64url') } : {}),
          isLast: !more,
        });
      }
      if (m === 'GET' && (match = /^\/rest\/api\/3\/issue\/([^/]+)$/.exec(p))) {
        const issue = fake.issues.get(match[1] as string);
        return issue ? send(200, issue) : notFound();
      }
      if (m === 'PUT' && (match = /^\/rest\/api\/3\/issue\/([^/]+)$/.exec(p))) {
        const issue = fake.issues.get(match[1] as string);
        if (!issue) return notFound();
        const fields = (body['fields'] ?? {}) as Record<string, unknown>;
        if (fields['summary'] === '') return send(400, { errorMessages: [], errors: { summary: 'You must specify a summary of the issue.' } });
        Object.assign(issue.fields, fields);
        issue.fields['updated'] = nextTime();
        return send(204);
      }
      if (m === 'GET' && (match = /^\/rest\/api\/3\/issue\/([^/]+)\/transitions$/.exec(p))) {
        const issue = fake.issues.get(match[1] as string);
        if (!issue) return notFound();
        const current = (issue.fields['status'] as { name: string }).name;
        return send(200, {
          transitions: STATUSES.filter((s) => s.name !== current).map((s) => ({ id: s.id, name: `Move to ${s.name}`, to: { name: s.name, statusCategory: { key: s.category } } })),
        });
      }
      if (m === 'POST' && (match = /^\/rest\/api\/3\/issue\/([^/]+)\/transitions$/.exec(p))) {
        const issue = fake.issues.get(match[1] as string);
        if (!issue) return notFound();
        const id = (body['transition'] as { id?: string } | undefined)?.id;
        const target = STATUSES.find((s) => s.id === id);
        if (!target) return send(400, { errorMessages: ['Transition id is not valid for this issue'] });
        issue.fields['status'] = { name: target.name, statusCategory: { key: target.category } };
        issue.fields['updated'] = nextTime();
        return send(204);
      }
      if (m === 'POST' && (match = /^\/rest\/api\/3\/issue\/([^/]+)\/comment$/.exec(p))) {
        const key = match[1] as string;
        if (!fake.issues.has(key)) return notFound();
        const id = String(900 + fake.comments.length);
        fake.comments.push({ key, body: body['body'], id });
        return send(201, { id, body: body['body'] });
      }
      return send(404, { errorMessages: [`fake: unhandled ${m} ${p}`] });
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  fake.url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  return fake;
}
