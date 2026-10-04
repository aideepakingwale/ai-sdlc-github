import { createHash } from 'node:crypto';
import { beforeEach, describe, expect, it } from 'vitest';
import type { SdlcError } from '@sdlc/shared';
import { githubImpl } from './impl/github.js';
import type { CommitIndexInput } from './impl/git-paths.js';
import { fakeRedis } from './testing/fake-redis.js';
import { testEnv } from './testing/env.js';

const env = testEnv({ GITHUB_REPO: 'acme/widgets' });
const code = async (p: Promise<unknown>): Promise<SdlcError> => (await p.then(() => null, (e: unknown) => e)) as SdlcError;
const args = (over: Partial<CommitIndexInput> = {}): CommitIndexInput => ({
  branch: 'devmind/index',
  baseBranch: 'main',
  files: [{ path: '.devmind/a.md', content: 'A' }],
  deletions: [],
  message: 'index',
  createBranchIfMissing: true,
  requiredPrefix: '.devmind/',
  ...over,
});

let gh: ReturnType<typeof githubImpl>;
beforeEach(() => {
  gh = githubImpl({ env, redis: fakeRedis(), live: false });
});

const gitBlobSha = (content: string) => createHash('sha1').update(`blob ${Buffer.byteLength(content)}\0`).update(content).digest('hex');
const read = async (ref: string, paths: string[]) => (await gh.readFiles({ ref, paths })).files;

describe('mock github_commit_index', () => {
  it('auto-creates the branch from the (lazily seeded) default branch and commits', async () => {
    const out = await gh.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A' }, { path: '.devmind/x/b.json', content: '{}' }] }));
    expect(out.noop).toBe(false);
    expect(out.branch).toBe('devmind/index');
    expect(out.commitSha).toMatch(/^[0-9a-f]{40}$/);
    expect(out.treeSha).toMatch(/^[0-9a-f]{40}$/);
    expect(out.parentSha).toMatch(/^[0-9a-f]{40}$/); // the seeded root commit of main
    expect(out.htmlUrl).toBe(`https://github.mock.local/acme/widgets/commit/${out.commitSha}`);
    expect((await read('devmind/index', ['.devmind/a.md', '.devmind/x/b.json'])).map((f) => f.content)).toEqual(['A', '{}']);
    expect((await read('main', ['.devmind/a.md']))[0]?.content).toBeNull(); // base branch unaffected
  });

  it('builds a parent chain and merges onto existing files', async () => {
    const c1 = await gh.commitIndex(args());
    const c2 = await gh.commitIndex(args({ files: [{ path: '.devmind/b.md', content: 'B' }], message: 'second' }));
    const c3 = await gh.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A2' }], message: 'third' }));
    expect(c2.parentSha).toBe(c1.commitSha);
    expect(c3.parentSha).toBe(c2.commitSha);
    expect((await read('devmind/index', ['.devmind/a.md', '.devmind/b.md'])).map((f) => f.content)).toEqual(['A2', 'B']);
    expect((await read(c1.commitSha, ['.devmind/a.md', '.devmind/b.md'])).map((f) => f.content)).toEqual(['A', null]); // history is addressable
  });

  it('is deterministic: identical inputs on fresh repos give identical shas', async () => {
    const other = githubImpl({ env, redis: fakeRedis(), live: false });
    const input = args({ files: [{ path: '.devmind/a.md', content: 'A' }, { path: '.devmind/b.md', content: 'B' }] });
    expect(await gh.commitIndex(input)).toEqual(await other.commitIndex(input));
  });

  it('detects no-ops (same tree) without creating a commit, and reports the existing head', async () => {
    const first = await gh.commitIndex(args());
    const again = await gh.commitIndex(args({ message: 'different message, same content' }));
    expect(again).toMatchObject({ noop: true, commitSha: first.commitSha, treeSha: first.treeSha, parentSha: first.parentSha });
    const third = await gh.commitIndex(args({ files: [{ path: '.devmind/b.md', content: 'B' }] }));
    expect(third.parentSha).toBe(first.commitSha); // no phantom commit in between
  });

  it('noop onto a new branch still creates the branch', async () => {
    await gh.commitIndex(args({ branch: 'seeded', files: [{ path: '.devmind/a.md', content: 'A' }] }));
    const viaMain = await gh.commitIndex(args({ branch: 'fresh', baseBranch: 'seeded', files: [{ path: '.devmind/a.md', content: 'A' }] }));
    expect(viaMain.noop).toBe(true);
    expect((await read('fresh', ['.devmind/a.md']))[0]?.content).toBe('A');
  });

  it('handles deletions (existing and missing) and mixed write+delete', async () => {
    await gh.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A' }, { path: '.devmind/b.md', content: 'B' }, { path: '.devmind/c/d.md', content: 'D' }] }));
    const out = await gh.commitIndex(args({ files: [{ path: '.devmind/new.md', content: 'N' }], deletions: ['.devmind/a.md', '.devmind/c/d.md', '.devmind/ghost.md'] }));
    expect(out.noop).toBe(false);
    expect((await read('devmind/index', ['.devmind/a.md', '.devmind/b.md', '.devmind/c/d.md', '.devmind/new.md'])).map((f) => f.content)).toEqual([null, 'B', null, 'N']);
    const tree = await gh.listTree({ ref: 'devmind/index', prefix: '', recursive: true });
    expect(tree.entries.map((e) => e.path)).toEqual(['.devmind', '.devmind/b.md', '.devmind/new.md']); // empty dir pruned
    const onlyMissing = await gh.commitIndex(args({ files: [], deletions: ['.devmind/ghost.md'] }));
    expect(onlyMissing.noop).toBe(true);
  });

  it('expectedHeadSha guards the write', async () => {
    const first = await gh.commitIndex(args());
    expect(await code(gh.commitIndex(args({ files: [{ path: '.devmind/b', content: 'b' }], expectedHeadSha: 'f'.repeat(40) })))).toMatchObject({ code: 'GATE_CONFLICT' });
    expect((await read('devmind/index', ['.devmind/b']))[0]?.content).toBeNull();
    const ok = await gh.commitIndex(args({ files: [{ path: '.devmind/b', content: 'b' }], expectedHeadSha: first.commitSha }));
    expect(ok.parentSha).toBe(first.commitSha);
    expect(await code(gh.commitIndex(args({ branch: 'nobranch', expectedHeadSha: first.commitSha })))).toMatchObject({ code: 'GATE_CONFLICT' });
  });

  it('branch creation can be disabled and a missing base fails', async () => {
    expect(await code(gh.commitIndex(args({ createBranchIfMissing: false })))).toMatchObject({ code: 'NOT_FOUND' });
    expect(await code(gh.commitIndex(args({ baseBranch: 'release' })))).toMatchObject({ code: 'NOT_FOUND' });
    expect((await read('main', ['x']))[0]?.content).toBeNull();
  });

  it.each([
    ['traversal', '.devmind/../x'],
    ['absolute', '/abs'],
    ['backslash', '.devmind\\x'],
    ['.git', '.devmind/.git/x'],
    ['control', '.devmind/a\u0002'],
    ['prefix', 'docs/x.md'],
  ])('rejects unsafe path (%s) and writes nothing', async (_n, path) => {
    expect(await code(gh.commitIndex(args({ files: [{ path, content: 'x' }] })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect((await read('main', ['x']))[0]?.content).toBeNull();
    expect(await code(gh.readFiles({ ref: 'devmind/index', paths: ['a'] }))).toMatchObject({ code: 'NOT_FOUND' }); // branch was not created either
  });

  it('enforces size limits and duplicate/overlap rules', async () => {
    expect(await code(gh.commitIndex(args({ files: [{ path: '.devmind/big', content: 'x'.repeat(1024 * 1024 + 1) }] })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(gh.commitIndex(args({ files: [{ path: '.devmind/a', content: '1' }, { path: '.devmind/a', content: '2' }] })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(gh.commitIndex(args({ files: [{ path: '.devmind/a', content: '1' }], deletions: ['.devmind/a'] })))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(gh.commitIndex(args({ files: [], deletions: [] })))).toMatchObject({ code: 'VALIDATION_FAILED' });
  });
});

describe('mock github_read_files', () => {
  it('returns git-compatible blob shas, null for missing files and de-duplicates', async () => {
    await gh.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'hello' }] }));
    const files = await read('devmind/index', ['.devmind/a.md', '.devmind/none', '.devmind/a.md', '.devmind']);
    expect(files).toEqual([
      { path: '.devmind/a.md', content: 'hello', sha: gitBlobSha('hello') },
      { path: '.devmind/none', content: null, sha: null },
      { path: '.devmind', content: null, sha: null },
    ]);
    expect(gitBlobSha('hello')).toBe('b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0'); // sanity: matches real git
  });

  it('rejects unsafe paths, unknown refs, and responses over 10 MiB', async () => {
    expect(await code(gh.readFiles({ ref: 'main', paths: ['../x'] }))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(gh.readFiles({ ref: 'ghost', paths: ['x'] }))).toMatchObject({ code: 'NOT_FOUND' });
    const chunk = 'y'.repeat(1024 * 1024 - 2);
    const files = Array.from({ length: 11 }, (_, i) => ({ path: `.devmind/big${i}`, content: `${chunk}${i}` }));
    await gh.commitIndex(args({ files }));
    expect(await code(gh.readFiles({ ref: 'devmind/index', paths: files.map((f) => f.path) }))).toMatchObject({ code: 'VALIDATION_FAILED', message: expect.stringContaining('10 MiB') as unknown });
  });
});

describe('mock github_list_tree', () => {
  beforeEach(async () => {
    await gh.commitIndex(args({ files: [{ path: '.devmind/a.md', content: 'A' }, { path: '.devmind/sub/b.md', content: 'BB' }, { path: '.devmind/sub/deep/c.md', content: 'CCC' }] }));
  });

  it('lists blobs and synthesised trees with sizes and stable shas', async () => {
    const out = await gh.listTree({ ref: 'devmind/index', prefix: '', recursive: true });
    expect(out.truncated).toBe(false);
    expect(out.entries.map((e) => [e.path, e.type, e.size])).toEqual([
      ['.devmind', 'tree', null],
      ['.devmind/a.md', 'blob', 1],
      ['.devmind/sub', 'tree', null],
      ['.devmind/sub/b.md', 'blob', 2],
      ['.devmind/sub/deep', 'tree', null],
      ['.devmind/sub/deep/c.md', 'blob', 3],
    ]);
    const again = await gh.listTree({ ref: 'devmind/index', prefix: '', recursive: true });
    expect(again).toEqual(out);
    expect(out.entries.find((e) => e.path === '.devmind/a.md')?.sha).toBe(gitBlobSha('A'));
  });

  it('supports prefixes and non-recursive listing', async () => {
    const p = (r: { entries: Array<{ path: string }> }) => r.entries.map((e) => e.path);
    expect(p(await gh.listTree({ ref: 'devmind/index', prefix: '.devmind/sub/', recursive: true }))).toEqual(['.devmind/sub/b.md', '.devmind/sub/deep', '.devmind/sub/deep/c.md']);
    expect(p(await gh.listTree({ ref: 'devmind/index', prefix: '.devmind/sub/', recursive: false }))).toEqual(['.devmind/sub/b.md', '.devmind/sub/deep']);
    expect(p(await gh.listTree({ ref: 'devmind/index', prefix: '', recursive: false }))).toEqual(['.devmind']);
    expect(p(await gh.listTree({ ref: 'devmind/index', prefix: '.devmind/s', recursive: true }))).toEqual(['.devmind/sub', '.devmind/sub/b.md', '.devmind/sub/deep', '.devmind/sub/deep/c.md']);
    expect((await gh.listTree({ ref: 'devmind/index', prefix: 'zzz/', recursive: true })).entries).toEqual([]);
    expect(await code(gh.listTree({ ref: 'devmind/index', prefix: '..', recursive: true }))).toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(await code(gh.listTree({ ref: 'nope', prefix: '', recursive: true }))).toMatchObject({ code: 'NOT_FOUND' });
  });

  it('directory tree shas change when contents change', async () => {
    const before = (await gh.listTree({ ref: 'devmind/index', prefix: '.devmind/sub', recursive: false })).entries.find((e) => e.path === '.devmind/sub');
    await gh.commitIndex(args({ files: [{ path: '.devmind/sub/b.md', content: 'CHANGED' }] }));
    const after = (await gh.listTree({ ref: 'devmind/index', prefix: '.devmind/sub', recursive: false })).entries.find((e) => e.path === '.devmind/sub');
    expect(after?.sha).not.toBe(before?.sha);
  });
});

describe('mock github_open_pull_request', () => {
  const pr = (over: Record<string, unknown> = {}) => ({ head: 'devmind/index', base: 'main', title: 'T', body: 'B', reuseExisting: true, ...over });

  it('dedupes by head/base, and honours reuseExisting=false and different bases', async () => {
    const first = await gh.openPullRequest(pr());
    expect(first).toMatchObject({ created: true, number: 1, url: 'https://github.mock.local/acme/widgets/pull/1' });
    expect(await gh.openPullRequest(pr({ title: 'other' }))).toEqual({ number: 1, url: first.url, created: false });
    expect((await gh.openPullRequest(pr({ base: 'develop' }))).created).toBe(true);
    expect((await gh.openPullRequest(pr({ reuseExisting: false }))).number).toBe(3);
    expect(await gh.openPullRequest(pr())).toMatchObject({ number: 3, created: false }); // latest PR for the pair wins
  });
});

describe('legacy mock tools on the new repo model', () => {
  it('commitFiles/commitCode/createBranch interoperate with reads and keep the CI simulation', async () => {
    const branch = await gh.createBranch({ branch: 'feat/x', from: 'main' });
    expect(branch.baseSha).toMatch(/^[0-9a-f]{40}$/);
    const c1 = await gh.commitFiles({ branch: 'feat/x', files: [{ path: 'src/a.ts', content: 'ok' }], message: 'm1' });
    expect((await read('feat/x', ['src/a.ts']))[0]?.content).toBe('ok');
    const code1 = await gh.commitCode({ branch: 'feat/x', files: [{ path: 'src/b.ts', content: 'BUG-MARKER' }], message: 'm2' });
    expect((await gh.pollRunStatus({ runId: code1.runId })).conclusion).toBe('failure');
    const fix = await gh.commitCode({ branch: 'feat/x', files: [{ path: 'src/b.ts', content: 'fixed' }], message: 'fix' });
    expect((await gh.pollRunStatus({ runId: fix.runId })).conclusion).toBe('success');
    expect(c1.commitSha).not.toBe(code1.commitSha);
    expect(code1.commitSha).not.toBe(fix.commitSha);
    // a branch created from a missing base still works (legacy behaviour)
    expect((await gh.createBranch({ branch: 'y', from: 'does-not-exist' })).baseSha).toMatch(/^[0-9a-f]{40}$/);
  });

  it('commit_index on a branch made by createBranch builds on its commits', async () => {
    await gh.createBranch({ branch: 'feat/y', from: 'main' });
    await gh.commitFiles({ branch: 'feat/y', files: [{ path: 'keep.txt', content: 'k' }], message: 'm' });
    const out = await gh.commitIndex(args({ branch: 'feat/y' }));
    expect(out.noop).toBe(false);
    expect((await read('feat/y', ['keep.txt', '.devmind/a.md'])).map((f) => f.content)).toEqual(['k', 'A']);
  });
});
