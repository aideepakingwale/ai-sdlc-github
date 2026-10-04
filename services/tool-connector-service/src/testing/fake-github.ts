import { createHash } from 'node:crypto';
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

/**
 * In-process fake of the slice of the GitHub REST API the connector uses
 * (Git Data API, pulls, and the legacy contents endpoints) with a REAL object
 * model: content-addressed blobs, nested trees, commits with parents and
 * fast-forward-checked refs. Test-only.
 */
export interface RecordedRequest {
  method: string;
  path: string; // without query
  query: URLSearchParams;
  body: Record<string, unknown>;
  headers: IncomingMessage['headers'];
}

export interface Fault {
  when: (req: RecordedRequest) => boolean;
  status: number;
  headers?: Record<string, string>;
  body?: unknown;
  /** Number of times to inject; default 1. */
  times?: number;
}

interface TreeEntry {
  name: string;
  mode: string;
  type: 'blob' | 'tree';
  sha: string;
}
interface CommitObj {
  sha: string;
  tree: string;
  parents: string[];
  message: string;
  author: unknown;
  committer: unknown;
}
interface FlatEntry {
  mode: string;
  sha: string;
}

const sha1 = (...parts: Array<string | Buffer>): string => {
  const h = createHash('sha1');
  for (const p of parts) h.update(p);
  return h.digest('hex');
};

export class GitStore {
  blobs = new Map<string, Buffer>();
  trees = new Map<string, TreeEntry[]>();
  commits = new Map<string, CommitObj>();
  refs = new Map<string, string>(); // "heads/main" -> commit sha
  private counter = 0;

  putBlob(buf: Buffer): string {
    const sha = sha1(`blob ${buf.length}\0`, buf);
    this.blobs.set(sha, buf);
    return sha;
  }

  /** Build nested trees from a flat path map and return the root sha. */
  putTreeFromFlat(flat: Map<string, FlatEntry>): string {
    const build = (prefix: string): string => {
      const direct = new Map<string, TreeEntry>();
      for (const [path, e] of flat) {
        if (!path.startsWith(prefix)) continue;
        const rest = path.slice(prefix.length);
        const slash = rest.indexOf('/');
        if (slash < 0) direct.set(rest, { name: rest, mode: e.mode, type: 'blob', sha: e.sha });
        else {
          const dir = rest.slice(0, slash);
          if (!direct.has(dir)) direct.set(dir, { name: dir, mode: '040000', type: 'tree', sha: build(`${prefix}${dir}/`) });
        }
      }
      const entries = [...direct.values()].sort((a, b) => (a.name < b.name ? -1 : 1));
      const sha = sha1('tree\0', entries.map((e) => `${e.mode} ${e.name}\0${e.sha}\n`).join(''));
      this.trees.set(sha, entries);
      return sha;
    };
    return build('');
  }

  flatten(treeSha: string, prefix = '', out = new Map<string, FlatEntry>()): Map<string, FlatEntry> {
    for (const e of this.trees.get(treeSha) ?? []) {
      if (e.type === 'tree') this.flatten(e.sha, `${prefix}${e.name}/`, out);
      else out.set(`${prefix}${e.name}`, { mode: e.mode, sha: e.sha });
    }
    return out;
  }

  /** Commit directly (bypassing HTTP) - used to seed state and to simulate concurrent pushers. */
  commit(branch: string, files: Record<string, string | null>, message = 'seed', parentOverride?: string | null): string {
    const parent = parentOverride === undefined ? (this.refs.get(`heads/${branch}`) ?? null) : parentOverride;
    const flat = parent ? this.flatten((this.commits.get(parent) as CommitObj).tree) : new Map<string, FlatEntry>();
    for (const [path, content] of Object.entries(files)) {
      if (content === null) flat.delete(path);
      else flat.set(path, { mode: '100644', sha: this.putBlob(Buffer.from(content, 'utf8')) });
    }
    const tree = this.putTreeFromFlat(flat);
    const sha = this.addCommit(tree, parent ? [parent] : [], message, null, null);
    this.refs.set(`heads/${branch}`, sha);
    return sha;
  }

  addCommit(tree: string, parents: string[], message: string, author: unknown, committer: unknown): string {
    const sha = sha1(`commit ${this.counter++}\0${tree}\n${parents.join(',')}\n${message}`);
    this.commits.set(sha, { sha, tree, parents, message, author, committer });
    return sha;
  }

  isAncestor(maybeAncestor: string, of: string): boolean {
    const seen = new Set<string>();
    const stack = [of];
    while (stack.length > 0) {
      const cur = stack.pop() as string;
      if (cur === maybeAncestor) return true;
      if (seen.has(cur)) continue;
      seen.add(cur);
      stack.push(...(this.commits.get(cur)?.parents ?? []));
    }
    return false;
  }

  filesAt(branch: string): Record<string, string> {
    const head = this.refs.get(`heads/${branch}`);
    if (!head) return {};
    const out: Record<string, string> = {};
    for (const [path, e] of this.flatten((this.commits.get(head) as CommitObj).tree)) out[path] = (this.blobs.get(e.sha) as Buffer).toString('utf8');
    return out;
  }
}

export interface PullRecord {
  number: number;
  head: string;
  base: string;
  title: string;
  body: string;
  state: 'open' | 'closed';
}

export interface FakeGithub {
  url: string;
  repo: string;
  token: string;
  store: GitStore;
  requests: RecordedRequest[];
  faults: Fault[];
  /** Run before each request is routed (e.g. to simulate a concurrent push). */
  hooks: Array<(req: RecordedRequest, store: GitStore) => void>;
  pulls: PullRecord[];
  options: { truncateRecursiveAbove: number | null; blobDelayMs: number };
  stats: { maxInflightBlobWrites: number; maxInflightBlobReads: number };
  count(method: string, pathPattern: RegExp): number;
  reset(): void;
  close(): Promise<void>;
}

export async function startFakeGithub(repo = 'acme/widgets', token = 'test-token'): Promise<FakeGithub> {
  const fake: FakeGithub = {
    url: '',
    repo,
    token,
    store: new GitStore(),
    requests: [],
    faults: [],
    hooks: [],
    pulls: [],
    options: { truncateRecursiveAbove: null, blobDelayMs: 0 },
    stats: { maxInflightBlobWrites: 0, maxInflightBlobReads: 0 },
    count: (method, re) => fake.requests.filter((r) => r.method === method && re.test(r.path)).length,
    reset() {
      fake.store = new GitStore();
      fake.requests = [];
      fake.faults = [];
      fake.hooks = [];
      fake.pulls = [];
      fake.options = { truncateRecursiveAbove: null, blobDelayMs: 0 };
      fake.stats = { maxInflightBlobWrites: 0, maxInflightBlobReads: 0 };
    },
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };

  let blobWrites = 0;
  let blobReads = 0;
  const root = `/repos/${repo}`;

  const server: Server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      void (async () => {
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
        const rec: RecordedRequest = { method: req.method ?? 'GET', path: decodeURIComponent(url.pathname), query: url.searchParams, body, headers: req.headers };
        fake.requests.push(rec);

        const send = (status: number, payload: unknown, headers: Record<string, string> = {}): void => {
          const text = payload === undefined ? '' : JSON.stringify(payload);
          res.writeHead(status, { 'content-type': 'application/json', ...headers });
          res.end(status === 204 ? undefined : text);
        };

        if (req.headers.authorization !== `Bearer ${token}`) return send(401, { message: 'Bad credentials' });

        const faultIdx = fake.faults.findIndex((f) => (f.times ?? 1) > 0 && f.when(rec));
        if (faultIdx >= 0) {
          const f = fake.faults[faultIdx] as Fault;
          f.times = (f.times ?? 1) - 1;
          return send(f.status, f.body ?? { message: 'injected fault' }, f.headers);
        }
        for (const hook of fake.hooks) hook(rec, fake.store);

        try {
          await route(rec, send);
        } catch (err) {
          send(500, { message: err instanceof Error ? err.message : 'fake server error' });
        }
      })();
    });
  });

  const st = (): GitStore => fake.store;
  const notFound = (send: (s: number, p: unknown) => void): void => send(404, { message: 'Not Found' });

  async function route(rec: RecordedRequest, send: (s: number, p: unknown, h?: Record<string, string>) => void): Promise<void> {
    const p = rec.path;
    const m = rec.method;
    let match: RegExpExecArray | null;

    // ---- refs ----
    if (m === 'GET' && (match = new RegExp(`^${root}/git/ref/(.+)$`).exec(p))) {
      const sha = st().refs.get(match[1] as string);
      return sha ? send(200, { ref: `refs/${match[1]}`, object: { sha, type: 'commit' } }) : notFound(send);
    }
    if (m === 'POST' && p === `${root}/git/refs`) {
      const ref = String(rec.body['ref']);
      const key = ref.replace(/^refs\//, '');
      if (st().refs.has(key)) return send(422, { message: 'Reference already exists' });
      const sha = String(rec.body['sha']);
      if (!st().commits.has(sha)) return send(422, { message: 'Object does not exist' });
      st().refs.set(key, sha);
      return send(201, { ref, object: { sha } });
    }
    if (m === 'PATCH' && (match = new RegExp(`^${root}/git/refs/(.+)$`).exec(p))) {
      const key = match[1] as string;
      const cur = st().refs.get(key);
      if (!cur) return notFound(send);
      const sha = String(rec.body['sha']);
      if (!st().commits.has(sha)) return send(422, { message: 'Object does not exist' });
      if (rec.body['force'] !== true && !st().isAncestor(cur, sha)) return send(422, { message: 'Update is not a fast forward' });
      st().refs.set(key, sha);
      return send(200, { ref: `refs/${key}`, object: { sha } });
    }

    // ---- commits ----
    if (m === 'GET' && (match = new RegExp(`^${root}/git/commits/([0-9a-f]+)$`).exec(p))) {
      const c = st().commits.get(match[1] as string);
      if (!c) return notFound(send);
      return send(200, { sha: c.sha, html_url: `https://github.example/${repo}/commit/${c.sha}`, tree: { sha: c.tree }, parents: c.parents.map((sha) => ({ sha })), message: c.message });
    }
    if (m === 'POST' && p === `${root}/git/commits`) {
      const tree = String(rec.body['tree']);
      const parents = (rec.body['parents'] as string[] | undefined) ?? [];
      if (!st().trees.has(tree)) return send(422, { message: 'Tree does not exist' });
      const sha = st().addCommit(tree, parents, String(rec.body['message']), rec.body['author'], rec.body['committer']);
      return send(201, { sha, html_url: `https://github.example/${repo}/commit/${sha}`, tree: { sha: tree }, parents: parents.map((s) => ({ sha: s })) });
    }

    // ---- trees ----
    if (m === 'GET' && (match = new RegExp(`^${root}/git/trees/([0-9a-f]+)$`).exec(p))) {
      const sha = match[1] as string;
      const entries = st().trees.get(sha);
      if (!entries) return notFound(send);
      const sizeOf = (e: TreeEntry): { size?: number } => (e.type === 'blob' ? { size: st().blobs.get(e.sha)?.length ?? 0 } : {});
      if (rec.query.get('recursive') !== '1') {
        return send(200, { sha, truncated: false, tree: entries.map((e) => ({ path: e.name, mode: e.mode, type: e.type, sha: e.sha, ...sizeOf(e) })) });
      }
      const all: Array<Record<string, unknown>> = [];
      const walk = (treeSha: string, prefix: string): void => {
        for (const e of st().trees.get(treeSha) ?? []) {
          all.push({ path: `${prefix}${e.name}`, mode: e.mode, type: e.type, sha: e.sha, ...sizeOf(e) });
          if (e.type === 'tree') walk(e.sha, `${prefix}${e.name}/`);
        }
      };
      walk(sha, '');
      const limit = fake.options.truncateRecursiveAbove;
      if (limit !== null && all.length > limit) return send(200, { sha, truncated: true, tree: all.slice(0, limit) });
      return send(200, { sha, truncated: false, tree: all });
    }
    if (m === 'POST' && p === `${root}/git/trees`) {
      const base = String(rec.body['base_tree']);
      if (!st().trees.has(base)) return send(422, { message: 'Base tree does not exist' });
      const flat = st().flatten(base);
      for (const raw of rec.body['tree'] as Array<{ path: string; mode: string; sha: string | null }>) {
        if (raw.sha === null) {
          if (!flat.has(raw.path)) return send(422, { message: `GitRPC::BadObjectState: ${raw.path} does not exist` });
          flat.delete(raw.path);
        } else {
          if (!st().blobs.has(raw.sha)) return send(422, { message: 'Blob does not exist' });
          flat.set(raw.path, { mode: raw.mode, sha: raw.sha });
        }
      }
      return send(201, { sha: st().putTreeFromFlat(flat) });
    }

    // ---- blobs ----
    if (m === 'POST' && p === `${root}/git/blobs`) {
      blobWrites++;
      fake.stats.maxInflightBlobWrites = Math.max(fake.stats.maxInflightBlobWrites, blobWrites);
      try {
        if (fake.options.blobDelayMs > 0) await new Promise((r) => setTimeout(r, fake.options.blobDelayMs));
        const enc = rec.body['encoding'];
        const buf = enc === 'base64' ? Buffer.from(String(rec.body['content']), 'base64') : Buffer.from(String(rec.body['content']), 'utf8');
        return send(201, { sha: st().putBlob(buf) });
      } finally {
        blobWrites--;
      }
    }
    if (m === 'GET' && (match = new RegExp(`^${root}/git/blobs/([0-9a-f]+)$`).exec(p))) {
      blobReads++;
      fake.stats.maxInflightBlobReads = Math.max(fake.stats.maxInflightBlobReads, blobReads);
      try {
        if (fake.options.blobDelayMs > 0) await new Promise((r) => setTimeout(r, fake.options.blobDelayMs));
        const buf = st().blobs.get(match[1] as string);
        if (!buf) return notFound(send);
        // Real GitHub wraps base64 at 60 columns.
        return send(200, { sha: match[1], size: buf.length, encoding: 'base64', content: (buf.toString('base64').match(/.{1,60}/g) ?? []).join('\n') });
      } finally {
        blobReads--;
      }
    }

    // ---- pulls ----
    if (m === 'GET' && p === `${root}/pulls`) {
      const head = rec.query.get('head') ?? '';
      const base = rec.query.get('base');
      const branch = head.includes(':') ? head.slice(head.indexOf(':') + 1) : head;
      const list = fake.pulls.filter((x) => x.state === (rec.query.get('state') ?? 'open') && (head === '' || x.head === branch) && (!base || x.base === base));
      return send(200, list.map((x) => ({ number: x.number, html_url: `https://github.example/${repo}/pull/${x.number}`, head: { ref: x.head }, base: { ref: x.base } })));
    }
    if (m === 'POST' && p === `${root}/pulls`) {
      const head = String(rec.body['head']);
      const base = String(rec.body['base']);
      if (fake.pulls.some((x) => x.state === 'open' && x.head === head && x.base === base)) {
        return send(422, { message: 'Validation Failed', errors: [{ message: `A pull request already exists for ${repo.split('/')[0]}:${head}.` }] });
      }
      const pr: PullRecord = { number: fake.pulls.length + 1, head, base, title: String(rec.body['title']), body: String(rec.body['body']), state: 'open' };
      fake.pulls.push(pr);
      return send(201, { number: pr.number, html_url: `https://github.example/${repo}/pull/${pr.number}` });
    }

    // ---- legacy contents API ----
    if ((match = new RegExp(`^${root}/contents/(.+)$`).exec(p))) {
      const path = match[1] as string;
      if (m === 'GET') {
        const files = st().filesAt(rec.query.get('ref') ?? 'main');
        const content = files[path];
        return content === undefined ? notFound(send) : send(200, { sha: sha1(`blob ${content.length}\0${content}`), path });
      }
      if (m === 'PUT') {
        const branch = String(rec.body['branch']);
        const content = Buffer.from(String(rec.body['content']), 'base64').toString('utf8');
        const sha = st().commit(branch, { [path]: content }, String(rec.body['message']));
        return send(200, { commit: { sha } });
      }
    }

    return send(404, { message: `fake: unhandled ${m} ${p}` });
  }

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  fake.url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  return fake;
}
