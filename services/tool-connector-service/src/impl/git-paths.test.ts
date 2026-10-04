import { describe, expect, it } from 'vitest';
import { assertSafeBranchName, assertSafePrefix, assertSafeReadPath, MAX_FILE_BYTES, planCommit, type CommitIndexInput } from './git-paths.js';

const base = (over: Partial<CommitIndexInput> = {}): CommitIndexInput => ({
  branch: 'devmind/index',
  baseBranch: 'main',
  files: [{ path: '.devmind/a.md', content: 'x' }],
  deletions: [],
  message: 'm',
  createBranchIfMissing: true,
  requiredPrefix: '.devmind/',
  ...over,
});

const reject = (over: Partial<CommitIndexInput>, match?: RegExp) =>
  expect(() => planCommit(base(over))).toThrowError(expect.objectContaining({ code: 'VALIDATION_FAILED', ...(match ? { message: expect.stringMatching(match) as unknown } : {}) }));

describe('planCommit path safety (table driven)', () => {
  it.each([
    ['absolute path', '/etc/passwd', /absolute|start with/],
    ['windows drive', 'C:/x', /absolute/],
    ['parent traversal', '.devmind/../etc/passwd', /\.\./],
    ['bare dotdot', '..', /\.\./],
    ['backslash', '.devmind\\a.md', /backslash/],
    ['.git segment', '.devmind/.git/config', /\.git/],
    ['.GIT segment (case)', '.devmind/.GIT/config', /\.git/],
    ['control char', '.devmind/a\u0007.md', /control/],
    ['newline', '.devmind/a\n.md', /control/],
    ['NUL', '.devmind/a\u0000.md', /control/],
    ['outside prefix', 'src/index.ts', /must start with/],
    ['prefix lookalike', '.devmind-evil/x.md', /must start with/],
    ['empty segment', '.devmind//a.md', /empty path segment/],
    ['dot segment', '.devmind/./a.md', /"\."/],
    ['trailing slash', '.devmind/dir/', /empty path segment/],
    ['too long', `.devmind/${'a'.repeat(1100)}`, /1024/],
  ])('rejects file path: %s', (_name, path, re) => {
    reject({ files: [{ path, content: 'x' }] }, re);
  });

  it.each([
    ['absolute', '/.devmind/a.md'],
    ['traversal', '.devmind/../../x'],
    ['outside prefix', 'README.md'],
    ['.git', '.devmind/.git/HEAD'],
  ])('rejects deletion path: %s', (_name, path) => {
    reject({ deletions: [path] });
  });

  it('rejects duplicate paths, write+delete overlap and duplicate deletions', () => {
    reject({ files: [{ path: '.devmind/a', content: '1' }, { path: '.devmind/a', content: '2' }] }, /duplicate/);
    reject({ files: [{ path: '.devmind/a', content: '1' }], deletions: ['.devmind/a'] }, /both written and deleted/);
    reject({ deletions: ['.devmind/z', '.devmind/z'] }, /duplicate/);
  });

  it('rejects empty commits', () => {
    reject({ files: [], deletions: [] }, /nothing to commit/);
  });

  it('enforces the 1 MiB per-file and 20 MiB total limits (utf-8 bytes, not chars)', () => {
    reject({ files: [{ path: '.devmind/big', content: 'a'.repeat(MAX_FILE_BYTES + 1) }] }, /1 MiB/);
    reject({ files: [{ path: '.devmind/wide', content: '€'.repeat(Math.floor(MAX_FILE_BYTES / 3) + 1) }] }, /1 MiB/);
    const chunk = 'a'.repeat(MAX_FILE_BYTES);
    reject({ files: Array.from({ length: 21 }, (_, i) => ({ path: `.devmind/f${i}`, content: chunk })) }, /20 MiB/);
    expect(() => planCommit(base({ files: [{ path: '.devmind/ok', content: chunk }] }))).not.toThrow();
  });

  it('requires a sane requiredPrefix', () => {
    reject({ requiredPrefix: '' }, /requiredPrefix/);
    reject({ requiredPrefix: '.devmind' }, /requiredPrefix/);
    reject({ requiredPrefix: '../' }, /\.\./);
    reject({ requiredPrefix: '/abs/' }, /absolute/);
  });

  it('validates branch names', () => {
    for (const branch of ['', 'a b', 'a..b', 'a~1', 'x/', '/x', 'x.lock', 'feat//x', 'a@{b', '-flag', 'a\u0001', 'a/.hidden', 'a:b', 'a[b']) {
      reject({ branch });
    }
    reject({ baseBranch: 'bad name' });
    expect(() => assertSafeBranchName('feature/ok-1_2')).not.toThrow();
  });

  it('accepts a valid plan and reports sizes', () => {
    const plan = planCommit(base({ files: [{ path: '.devmind/a.md', content: 'héllo' }], deletions: ['.devmind/old.md'] }));
    expect(plan.totalBytes).toBe(6);
    expect(plan.deletions).toEqual(['.devmind/old.md']);
  });
});

describe('read paths and prefixes', () => {
  it.each(['', '/a', '../a', 'a/../b', 'a\\b', '.git/config', 'a//b', 'a/', 'a\u0000'])('rejects read path %j', (p) => {
    expect(() => assertSafeReadPath(p)).toThrow();
  });
  it.each(['a', 'dir/file.ts', '.devmind/x.json'])('accepts read path %j', (p) => {
    expect(() => assertSafeReadPath(p)).not.toThrow();
  });
  it.each([['', true], ['src/', true], ['src/ut', true], ['../x', false], ['/x', false], ['a//b', false], ['.git/', false]])('prefix %j ok=%s', (p, ok) => {
    if (ok) expect(() => assertSafePrefix(p)).not.toThrow();
    else expect(() => assertSafePrefix(p)).toThrow();
  });
});
