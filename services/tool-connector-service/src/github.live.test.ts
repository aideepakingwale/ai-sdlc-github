import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { SdlcError } from '@sdlc/shared';
import { githubImpl } from './impl/github.js';
import type { CommitIndexInput } from './impl/git-paths.js';
import { startFakeGithub, type FakeGithub } from './testing/fake-github.js';
import { fakeRedis } from './testing/fake-redis.js';
import { fastPolicy, testEnv } from './testing/env.js';

let fake: FakeGithub;
beforeAll(async () => {
  fake = await startFakeGithub();
});
afterAll(async () => {
  await fake.close();
});
beforeEach(() => {
  fake.reset();
  fake.store.commit('main', { 'README.md': 'hello', '.devmind/old.md': 'old', '.devmind/sub/deep.md': 'deep', 'src/app.ts': 'export {}' }, 'initial');
});

const env = (extra: Record<string, string> = {}) =>
  testEnv({ TOOLS_MODE: 'live', GITHUB_TOKEN: 'test-token', GITHUB_REPO: 'acme/widgets', GITHUB_API_URL: fake.url, ...extra });

const make = (sleeps: number[] = [], extra: Record<string, string> = {}) =>
  githubImpl({ env: env(extra), redis: fakeRedis(), live: true, http: fastPolicy(sleeps) });

const args = (over: Partial<CommitIndexInput> = {}): CommitIndexInput => ({
  branch: 'devmind/index',
  baseBranch: 'main',
  files: [{ path: '.devmind/a.md', content: 'A' }],
  deletions: [],
  message: 'chore(devmind): update index',
  createBranchIfMissing: true,
  requiredPrefix: '.devmind/',
  ...over,
});

const post = (re: RegExp) => fake.count('POST', re);
const code = async (p: Promise<unknown>): Promise<SdlcError> => (await p.then(() => null, (e: unknown) => e)) as SdlcError;
const sha = (branch: string) => fake.store.refs.get(`heads/${branch}`);

describe('github_commit_index (live)', () => {
  it('creates the branch from the base head and commits many files atomically', async () => {
    const baseHead = sha('main');
    const out = await make().commitIndex(
      args({ files: [{ path: '.devmind/a.md', content: 'A' }, { path: '.devmind/nested/b.json', content: '{"b":1}' }, { path: '.devmind/sub/deep.md', content: 'deep v2' }] }),
    );
    expect(out).toMatchObject({ branch: 'devmind/index', parentSha: baseHead, noop: false });
    expect(out.commitSha).toBe(sha('devmind/index'));
    expect(out.treeSha).toBe(fake.store.commits.get(out.commitSha)?.tree);
    expect(out.htmlUrl).toContain(out.commitSha);
    expect(fake.store.filesAt('devmind/index')).toEqual({
      'README.md': 'hello',
      '.devmind/old.md': 'old',
      '.devmind/sub/deep.md': 'deep v2',
      '.devmind/a.md': 'A',
      '.devmind/nested/b.json': '{"b":1}',
      'src/app.ts': 'export {}',
    });
    expect(sha('main')).toBe(baseHead); // base untouched
    expect(post(/git\/commits$/)).toBe(1);
    expect(post(/git\/refs$/)).toBe(1);
    expect(fake.count('PATCH', /git\/refs/)).toBe(0);
    expect(post(/\/contents\//)).toBe(0);
  });

  it('updates an existing branch without force and chains the parent', async () => {
    const api = make();
    const first = await api.commitIndex(args());
    const second = await api.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A2' }], message: 'second' }));
    expect(second.parentSha).toBe(first.commitSha);
    expect(second.noop).toBe(false);
    const patches = fake.requests.filter((r) => r.method === 'PATCH');
    expect(patches).toHaveLength(1);
    expect(patches[0]?.body).toMatchObject({ sha: second.commitSha, force: false });
    expect(fake.store.filesAt('devmind/index')['.devmind/a.md']).toBe('A2');
  });

  it('uses the configured commit author/committer (defaults and overrides)', async () => {
    await make().commitIndex(args());
    const defaultCommit = fake.requests.find((r) => r.method === 'POST' && /git\/commits$/.test(r.path));
    expect(defaultCommit?.body['author']).toEqual({ name: 'DevMind', email: 'devmind@users.noreply.github.com' });
    expect(defaultCommit?.body['committer']).toEqual(defaultCommit?.body['author']);
    expect(defaultCommit?.body['parents']).toEqual([sha('main')]);

    fake.requests = [];
    await make([], { GITHUB_COMMIT_AUTHOR_NAME: 'Bot', GITHUB_COMMIT_AUTHOR_EMAIL: 'bot@corp.example' }).commitIndex(
      args({ branch: 'other', files: [{ path: '.devmind/z', content: 'z' }] }),
    );
    const custom = fake.requests.find((r) => r.method === 'POST' && /git\/commits$/.test(r.path));
    expect(custom?.body['author']).toEqual({ name: 'Bot', email: 'bot@corp.example' });
  });

  it('deletes files via sha:null, ignores already-missing deletions, and combines with writes', async () => {
    const out = await make().commitIndex(args({ files: [{ path: '.devmind/new.md', content: 'n' }], deletions: ['.devmind/old.md', '.devmind/sub/deep.md', '.devmind/never-existed.md'] }));
    expect(out.noop).toBe(false);
    const files = fake.store.filesAt('devmind/index');
    expect(files['.devmind/old.md']).toBeUndefined();
    expect(files['.devmind/sub/deep.md']).toBeUndefined();
    expect(files['.devmind/new.md']).toBe('n');
    expect(files['README.md']).toBe('hello');
    const treeReq = fake.requests.find((r) => r.method === 'POST' && /git\/trees$/.test(r.path));
    const entries = treeReq?.body['tree'] as Array<{ path: string; sha: string | null }>;
    expect(entries.filter((e) => e.sha === null).map((e) => e.path).sort()).toEqual(['.devmind/old.md', '.devmind/sub/deep.md']);
  });

  it('deleting only already-missing paths is a no-op', async () => {
    const out = await make().commitIndex(args({ files: [], deletions: ['.devmind/ghost.md'] }));
    expect(out.noop).toBe(true);
    expect(post(/git\/commits$/)).toBe(0);
  });

  it('returns noop (no commit, no ref move) when the resulting tree is unchanged', async () => {
    const api = make();
    const first = await api.commitIndex(args());
    fake.requests = [];
    const again = await api.commitIndex(args({ message: 'same content, different message' }));
    expect(again).toMatchObject({ noop: true, commitSha: first.commitSha, treeSha: first.treeSha, parentSha: sha('main') });
    expect(post(/git\/commits$/)).toBe(0);
    expect(fake.count('PATCH', /git\/refs/)).toBe(0);
    expect(sha('devmind/index')).toBe(first.commitSha);
  });

  it('noop on a missing branch still creates the branch at the base head', async () => {
    const out = await make().commitIndex(args({ files: [{ path: '.devmind/old.md', content: 'old' }] }));
    expect(out.noop).toBe(true);
    expect(out.commitSha).toBe(sha('main'));
    expect(sha('devmind/index')).toBe(sha('main'));
    expect(post(/git\/commits$/)).toBe(0);
  });

  it('branch auto-create can be disabled, and a missing base is an error', async () => {
    expect(await code(make().commitIndex(args({ createBranchIfMissing: false })))).toMatchObject({ code: 'NOT_FOUND' });
    expect(await code(make().commitIndex(args({ baseBranch: 'develop' })))).toMatchObject({ code: 'NOT_FOUND', message: expect.stringContaining('develop') as unknown });
    expect(post(/git\/(blobs|trees|commits|refs)$/)).toBe(0);
  });

  it('expectedHeadSha: conflicts before writing anything when the head differs', async () => {
    const api = make();
    const first = await api.commitIndex(args());
    fake.requests = [];
    const err = await code(api.commitIndex(args({ files: [{ path: '.devmind/b', content: 'b' }], expectedHeadSha: 'a'.repeat(40) })));
    expect(err).toMatchObject({ code: 'GATE_CONFLICT', httpStatus: 409 });
    expect(post(/git\/(blobs|trees|commits|refs)$/)).toBe(0);
    expect(sha('devmind/index')).toBe(first.commitSha);

    const ok = await api.commitIndex(args({ files: [{ path: '.devmind/b', content: 'b' }], expectedHeadSha: first.commitSha.toUpperCase() }));
    expect(ok.parentSha).toBe(first.commitSha);
  });

  it('expectedHeadSha on a missing branch is a conflict', async () => {
    expect(await code(make().commitIndex(args({ expectedHeadSha: 'b'.repeat(40) })))).toMatchObject({ code: 'GATE_CONFLICT' });
  });

  it('non-fast-forward: re-reads head, rebuilds the tree on it, then succeeds', async () => {
    const api = make();
    await api.commitIndex(args());
    fake.requests = [];
    let raced = false;
    fake.hooks.push((req, store) => {
      if (!raced && req.method === 'PATCH') {
        raced = true;
        store.commit('devmind/index', { '.devmind/other-writer.md': 'theirs' }, 'concurrent push');
      }
    });
    const out = await api.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A-new' }] }));
    expect(out.noop).toBe(false);
    expect(fake.count('PATCH', /git\/refs/)).toBe(2);
    expect(post(/git\/commits$/)).toBe(2);
    expect(post(/git\/blobs$/)).toBe(1); // blobs are reused across retries
    expect(out.parentSha).toBe(fake.store.commits.get(out.commitSha)?.parents[0]);
    const files = fake.store.filesAt('devmind/index');
    expect(files['.devmind/other-writer.md']).toBe('theirs'); // concurrent change preserved
    expect(files['.devmind/a.md']).toBe('A-new');
    expect(sha('devmind/index')).toBe(out.commitSha);
  });

  it('non-fast-forward exhausted after 2 retries -> CONFLICT, never forcing', async () => {
    const api = make();
    await api.commitIndex(args());
    fake.requests = [];
    let n = 0;
    fake.hooks.push((req, store) => {
      if (req.method === 'PATCH') store.commit('devmind/index', { [`.devmind/race-${n++}.md`]: 'x' }, 'racer');
    });
    const err = await code(api.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A-new' }] })));
    expect(err).toMatchObject({ code: 'GATE_CONFLICT' });
    expect(fake.count('PATCH', /git\/refs/)).toBe(3); // 1 attempt + 2 retries
    expect(fake.requests.filter((r) => r.method === 'PATCH').every((r) => r.body['force'] === false)).toBe(true);
  });

  it('non-fast-forward with expectedHeadSha does not retry on a moved head', async () => {
    const api = make();
    const first = await api.commitIndex(args());
    fake.requests = [];
    fake.hooks.push((req, store) => {
      if (req.method === 'PATCH') store.commit('devmind/index', { '.devmind/x.md': 'x' }, 'racer');
    });
    const err = await code(api.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A2' }], expectedHeadSha: first.commitSha })));
    expect(err).toMatchObject({ code: 'GATE_CONFLICT' });
    expect(fake.count('PATCH', /git\/refs/)).toBe(1);
  });

  it('branch created concurrently while we were committing is handled via re-read', async () => {
    let raced = false;
    fake.hooks.push((req, store) => {
      if (!raced && req.method === 'POST' && /git\/refs$/.test(req.path)) {
        raced = true;
        store.commit('devmind/index', { '.devmind/theirs.md': 't' }, 'other creator', sha('main') ?? null);
      }
    });
    const out = await make().commitIndex(args());
    expect(out.noop).toBe(false);
    const files = fake.store.filesAt('devmind/index');
    expect(files['.devmind/theirs.md']).toBe('t');
    expect(files['.devmind/a.md']).toBe('A');
  });

  it('performs no network call at all when the input is unsafe', async () => {
    const api = make();
    const cases: Array<[string, Partial<CommitIndexInput>]> = [
      ['traversal', { files: [{ path: '.devmind/../x', content: '' }] }],
      ['absolute', { files: [{ path: '/etc/passwd', content: '' }] }],
      ['backslash', { files: [{ path: '.devmind\\x', content: '' }] }],
      ['.git', { files: [{ path: '.devmind/.git/config', content: '' }] }],
      ['control', { files: [{ path: '.devmind/\u0001', content: '' }] }],
      ['prefix', { files: [{ path: 'src/a.ts', content: '' }] }],
      ['dupes', { files: [{ path: '.devmind/a', content: '1' }, { path: '.devmind/a', content: '2' }] }],
      ['write+delete', { files: [{ path: '.devmind/a', content: '1' }], deletions: ['.devmind/a'] }],
      ['big file', { files: [{ path: '.devmind/a', content: 'x'.repeat(1024 * 1024 + 1) }] }],
      ['empty', { files: [], deletions: [] }],
      ['bad branch', { branch: 'a b' }],
    ];
    for (const [name, over] of cases) {
      const err = await code(api.commitIndex(args(over)));
      expect(err, name).toMatchObject({ code: 'VALIDATION_FAILED' });
    }
    expect(fake.requests).toHaveLength(0);
  });

  it('creates blobs with bounded concurrency (<= 4) and de-duplicates identical content', async () => {
    fake.options.blobDelayMs = 25;
    const files = Array.from({ length: 14 }, (_, i) => ({ path: `.devmind/f${i}.txt`, content: i < 12 ? `content ${i}` : 'same' }));
    await make().commitIndex(args({ files }));
    expect(post(/git\/blobs$/)).toBe(13); // 12 unique + 1 shared
    expect(fake.stats.maxInflightBlobWrites).toBeGreaterThan(1);
    expect(fake.stats.maxInflightBlobWrites).toBeLessThanOrEqual(4);
    expect(Object.keys(fake.store.filesAt('devmind/index')).filter((p) => p.startsWith('.devmind/f'))).toHaveLength(14);
  });

  it('writes non-ASCII content byte-exactly', async () => {
    await make().commitIndex(args({ files: [{ path: '.devmind/u.md', content: 'héllo wörld € 🚀' }] }));
    expect(fake.store.filesAt('devmind/index')['.devmind/u.md']).toBe('héllo wörld € 🚀');
  });

  describe('HTTP resilience', () => {
    it('retries 429 (honouring Retry-After) and 503 on idempotent calls', async () => {
      const sleeps: number[] = [];
      fake.faults.push({ when: (r) => r.method === 'POST' && /git\/blobs$/.test(r.path), status: 429, headers: { 'retry-after': '2' } });
      fake.faults.push({ when: (r) => r.method === 'GET' && /git\/commits\//.test(r.path), status: 503, times: 2 });
      const out = await make(sleeps).commitIndex(args());
      expect(out.noop).toBe(false);
      expect(sleeps.some((s) => s >= 2000 && s < 2200)).toBe(true);
      expect(sleeps.length).toBe(3);
    });

    it('does not replay a non-idempotent POST after a 5xx (branch creation)', async () => {
      fake.faults.push({ when: (r) => r.method === 'POST' && /git\/refs$/.test(r.path), status: 503 });
      const err = await code(make().commitIndex(args()));
      expect(err).toMatchObject({ code: 'TOOL_ERROR' });
      expect(post(/git\/refs$/)).toBe(1);
    });

    it('does retry a 429 on a non-idempotent POST (never processed)', async () => {
      fake.faults.push({ when: (r) => r.method === 'POST' && /git\/refs$/.test(r.path), status: 429, headers: { 'retry-after': '0' } });
      const out = await make().commitIndex(args());
      expect(out.noop).toBe(false);
      expect(post(/git\/refs$/)).toBe(2);
    });

    it('maps 401 to a clear token error without leaking the token', async () => {
      const api = githubImpl({ env: env({ GITHUB_TOKEN: 'ghp_SUPERSECRET' }), redis: fakeRedis(), live: true, http: fastPolicy() });
      const err = await code(api.commitIndex(args()));
      expect(err).toMatchObject({ code: 'TOOL_ERROR', message: expect.stringContaining('GITHUB_TOKEN') as unknown });
      expect(JSON.stringify(err.details)).not.toContain('SUPERSECRET');
      expect(err.message).not.toContain('SUPERSECRET');
    });

    it('maps 403 permission errors, persistent rate limits and 404 repos', async () => {
      fake.faults.push({ when: (r) => /git\/ref\/heads/.test(r.path), status: 403, body: { message: 'Resource not accessible by integration' } });
      expect(await code(make().commitIndex(args()))).toMatchObject({ code: 'TOOL_ERROR', message: expect.stringContaining('forbidden') as unknown });
      expect(fake.count('GET', /git\/ref\/heads/)).toBe(1); // plain 403 is not retried

      fake.reset();
      fake.faults.push({ when: () => true, status: 403, headers: { 'retry-after': '0' }, body: { message: 'You have exceeded a secondary rate limit' }, times: 99 });
      const limited = await code(make().commitIndex(args()));
      expect(limited).toMatchObject({ code: 'RATE_LIMITED' });
      expect(fake.requests).toHaveLength(4); // 1 + 3 retries

      fake.reset();
      const missingRepo = githubImpl({ env: env({ GITHUB_REPO: 'acme/nope' }), redis: fakeRedis(), live: true, http: fastPolicy() });
      expect(await code(missingRepo.readFiles({ ref: 'main', paths: ['a'] }))).toMatchObject({ code: 'NOT_FOUND' });
    });

    it('rejects a malformed GITHUB_REPO before calling out', async () => {
      const api = githubImpl({ env: env({ GITHUB_REPO: 'not a repo' }), redis: fakeRedis(), live: true, http: fastPolicy() });
      expect(await code(api.commitIndex(args()))).toMatchObject({ code: 'INTERNAL' });
      expect(fake.requests).toHaveLength(0);
    });
  });
});

describe('github_read_files (live)', () => {
  it('reads present files, returns null for missing ones, and de-duplicates', async () => {
    const out = await make().readFiles({ ref: 'main', paths: ['README.md', '.devmind/sub/deep.md', 'nope.txt', '.devmind', 'README.md', 'no/such/dir/x.md'] });
    expect(out.files.map((f) => f.path)).toEqual(['README.md', '.devmind/sub/deep.md', 'nope.txt', '.devmind', 'no/such/dir/x.md']);
    expect(out.files[0]).toMatchObject({ content: 'hello', sha: expect.stringMatching(/^[0-9a-f]{40}$/) as unknown });
    expect(out.files[1]?.content).toBe('deep');
    expect(out.files[2]).toEqual({ path: 'nope.txt', content: null, sha: null });
    expect(out.files[3]).toEqual({ path: '.devmind', content: null, sha: null }); // a directory is not a file
    expect(out.files[4]?.content).toBeNull();
  });

  it('reads files larger than 1 MB through the blob API and preserves multibyte content', async () => {
    const big = 'é'.repeat(900_000); // 1.8 MB in utf-8
    fake.store.commit('main', { 'data/big.txt': big, 'data/emoji.txt': 'a🚀b' });
    const out = await make().readFiles({ ref: 'main', paths: ['data/big.txt', 'data/emoji.txt'] });
    expect(out.files[0]?.content).toBe(big);
    expect(out.files[1]?.content).toBe('a🚀b');
    expect(fake.count('GET', /\/contents\//)).toBe(0); // never the 1 MB-limited contents API
    expect(fake.count('GET', /git\/blobs\//)).toBe(2);
  });

  it('resolves branches, tags, full shas and refs/ paths; unknown refs are NOT_FOUND', async () => {
    const first = sha('main') as string;
    fake.store.refs.set('tags/v1', first);
    fake.store.commit('main', { 'README.md': 'v2' });
    const api = make();
    expect((await api.readFiles({ ref: 'main', paths: ['README.md'] })).files[0]?.content).toBe('v2');
    expect((await api.readFiles({ ref: 'v1', paths: ['README.md'] })).files[0]?.content).toBe('hello');
    expect((await api.readFiles({ ref: first, paths: ['README.md'] })).files[0]?.content).toBe('hello');
    expect((await api.readFiles({ ref: 'refs/heads/main', paths: ['README.md'] })).files[0]?.content).toBe('v2');
    expect(await code(api.readFiles({ ref: 'ghost', paths: ['README.md'] }))).toMatchObject({ code: 'NOT_FOUND' });
  });

  it('caps total bytes with an explicit error before downloading anything', async () => {
    const chunk = 'x'.repeat(4 * 1024 * 1024);
    fake.store.commit('main', { 'big/a': chunk, 'big/b': `${chunk}1`, 'big/c': `${chunk}22` });
    const err = await code(make().readFiles({ ref: 'main', paths: ['big/a', 'big/b', 'big/c'] }));
    expect(err).toMatchObject({ code: 'VALIDATION_FAILED', message: expect.stringContaining('10 MiB') as unknown });
    expect(fake.count('GET', /git\/blobs\//)).toBe(0);
  });

  it('downloads blobs with bounded concurrency and one tree fetch per directory', async () => {
    fake.options.blobDelayMs = 20;
    const files: Record<string, string> = {};
    for (let i = 0; i < 12; i++) files[`docs/f${i}.md`] = `c${i}`;
    fake.store.commit('main', files);
    const out = await make().readFiles({ ref: 'main', paths: Object.keys(files) });
    expect(out.files.every((f, i) => f.content === `c${i}`)).toBe(true);
    expect(fake.stats.maxInflightBlobReads).toBeGreaterThan(1);
    expect(fake.stats.maxInflightBlobReads).toBeLessThanOrEqual(4);
    expect(fake.requests.filter((r) => r.method === 'GET' && /git\/trees\//.test(r.path))).toHaveLength(2); // root + docs
  });

  it('rejects unsafe read paths without network access', async () => {
    for (const p of ['../x', '/etc/passwd', 'a\\b', '.git/config', 'a\u0000b']) {
      expect(await code(make().readFiles({ ref: 'main', paths: [p] }))).toMatchObject({ code: 'VALIDATION_FAILED' });
    }
    expect(fake.requests).toHaveLength(0);
  });
});

describe('github_list_tree (live)', () => {
  const paths = (r: { entries: Array<{ path: string }> }) => r.entries.map((e) => e.path);

  it('lists recursively with types, shas and sizes', async () => {
    const out = await make().listTree({ ref: 'main', prefix: '', recursive: true });
    expect(out.truncated).toBe(false);
    expect(paths(out)).toEqual(['.devmind', '.devmind/old.md', '.devmind/sub', '.devmind/sub/deep.md', 'README.md', 'src', 'src/app.ts']);
    expect(out.entries.find((e) => e.path === 'README.md')).toMatchObject({ type: 'blob', size: 5 });
    expect(out.entries.find((e) => e.path === 'src')).toMatchObject({ type: 'tree', size: null });
  });

  it('filters by prefix (directory, partial name) and supports non-recursive listing', async () => {
    const api = make();
    expect(paths(await api.listTree({ ref: 'main', prefix: '.devmind/', recursive: true }))).toEqual(['.devmind/old.md', '.devmind/sub', '.devmind/sub/deep.md']);
    expect(paths(await api.listTree({ ref: 'main', prefix: '.devmind/', recursive: false }))).toEqual(['.devmind/old.md', '.devmind/sub']);
    expect(paths(await api.listTree({ ref: 'main', prefix: '.devmind/s', recursive: true }))).toEqual(['.devmind/sub', '.devmind/sub/deep.md']);
    expect(paths(await api.listTree({ ref: 'main', prefix: 'RE', recursive: false }))).toEqual(['README.md']);
    expect(paths(await api.listTree({ ref: 'main', prefix: '', recursive: false }))).toEqual(['.devmind', 'README.md', 'src']);
    expect(await api.listTree({ ref: 'main', prefix: 'nothing/here/', recursive: true })).toEqual({ entries: [], truncated: false });
    expect(await api.listTree({ ref: 'main', prefix: 'README.md/x', recursive: true })).toEqual({ entries: [], truncated: false });
  });

  it('falls back to walking subtrees when GitHub truncates, returning the complete listing', async () => {
    const files: Record<string, string> = {};
    for (let d = 0; d < 4; d++) for (let f = 0; f < 5; f++) files[`pkg${d}/mod${f}/file.ts`] = `${d}${f}`;
    fake.store.commit('main', files);
    const complete = await make().listTree({ ref: 'main', prefix: '', recursive: true });

    fake.requests = [];
    fake.options.truncateRecursiveAbove = 3;
    const viaFallback = await make().listTree({ ref: 'main', prefix: '', recursive: true });
    expect(viaFallback).toEqual(complete);
    expect(viaFallback.truncated).toBe(false);
    expect(fake.requests.filter((r) => /git\/trees\//.test(r.path) && r.query.get('recursive') === '1')).toHaveLength(1);
    expect(fake.requests.filter((r) => /git\/trees\//.test(r.path) && r.query.get('recursive') !== '1').length).toBeGreaterThan(5);

    fake.options.truncateRecursiveAbove = 3;
    expect(paths(await make().listTree({ ref: 'main', prefix: 'pkg2/', recursive: true }))).toEqual(paths(complete).filter((p) => p.startsWith('pkg2/')));
  });

  it('rejects unsafe prefixes and unknown refs', async () => {
    expect(await code(make().listTree({ ref: 'main', prefix: '../etc', recursive: true }))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(make().listTree({ ref: 'ghost', prefix: '', recursive: true }))).toMatchObject({ code: 'NOT_FOUND' });
  });
});

describe('github_open_pull_request (live)', () => {
  const pr = (over: Record<string, unknown> = {}) => ({ head: 'devmind/index', base: 'main', title: 'Index update', body: 'body', reuseExisting: true, ...over });

  it('creates a PR, then reuses the open one for the same head/base', async () => {
    const api = make();
    const first = await api.openPullRequest(pr());
    expect(first).toMatchObject({ number: 1, created: true });
    const second = await api.openPullRequest(pr({ title: 'different title' }));
    expect(second).toEqual({ number: 1, url: first.url, created: false });
    expect(post(/\/pulls$/)).toBe(1);
    expect(fake.pulls).toHaveLength(1);
    const lookup = fake.requests.find((r) => r.method === 'GET' && /\/pulls$/.test(r.path));
    expect(lookup?.query.get('head')).toBe('acme:devmind/index');
    expect(lookup?.query.get('state')).toBe('open');
  });

  it('opens a new PR for a different base, and when reuseExisting is false surfaces the conflict', async () => {
    const api = make();
    await api.openPullRequest(pr());
    expect((await api.openPullRequest(pr({ base: 'develop' }))).created).toBe(true);
    expect(await code(api.openPullRequest(pr({ reuseExisting: false })))).toMatchObject({ code: 'VALIDATION_FAILED' });
  });

  it('recovers when another creator wins the race between lookup and create', async () => {
    const api = make();
    fake.pulls.push({ number: 7, head: 'devmind/index', base: 'main', title: 't', body: '', state: 'open' });
    fake.faults.push({ when: (r) => r.method === 'GET' && /\/pulls$/.test(r.path), status: 200, body: [] }); // first lookup misses
    const out = await api.openPullRequest(pr());
    expect(out).toMatchObject({ number: 7, created: false });
  });

  it('passes through owner-qualified heads', async () => {
    await make().openPullRequest(pr({ head: 'forkowner:feature' }));
    expect(fake.requests.find((r) => r.method === 'GET' && /\/pulls$/.test(r.path))?.query.get('head')).toBe('forkowner:feature');
  });
});

describe('legacy GitHub tools keep working against the configured API root', () => {
  it('createBranch + commitFiles use GITHUB_API_URL and retry rate limits, but never replay PUT after 5xx', async () => {
    const sleeps: number[] = [];
    const api = make(sleeps);
    const branch = await api.createBranch({ branch: 'feat/x', from: 'main' });
    expect(branch).toEqual({ branch: 'feat/x', baseSha: sha('main') });

    fake.faults.push({ when: (r) => r.method === 'GET' && /\/contents\//.test(r.path), status: 429, headers: { 'retry-after': '1' } });
    const commit = await api.commitFiles({ branch: 'feat/x', files: [{ path: 'src/new.ts', content: 'x' }], message: 'm' });
    expect(commit.commitSha).toBe(sha('feat/x'));
    expect(sleeps[0]).toBeGreaterThanOrEqual(1000);

    fake.requests = [];
    fake.faults.push({ when: (r) => r.method === 'PUT', status: 503 });
    const err = await code(api.commitFiles({ branch: 'feat/x', files: [{ path: 'src/y.ts', content: 'y' }], message: 'm' }));
    expect(err).toMatchObject({ code: 'TOOL_ERROR', message: expect.stringContaining('GitHub 503') as unknown });
    expect(fake.count('PUT', /\/contents\//)).toBe(1);
  });

  it('createBranch reports a missing base as before', async () => {
    expect(await code(make().createBranch({ branch: 'x', from: 'ghost' }))).toMatchObject({ code: 'TOOL_ERROR', message: expect.stringContaining('ghost') as unknown });
  });
});
