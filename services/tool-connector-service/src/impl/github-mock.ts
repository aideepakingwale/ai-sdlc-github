import { createHash } from 'node:crypto';
import type { Redis } from 'ioredis';
import { SdlcError } from '@sdlc/shared';
import { assertSafePrefix, assertSafeReadPath, MAX_READ_BYTES, planCommit, type CommitIndexInput } from './git-paths.js';
import type { CommitIndexOut, TreeEntryOut } from './github-live.js';

/**
 * Redis-backed mock repository with git semantics: branches point at commits,
 * commits carry a parent and a flat path->blob map, and every sha is derived
 * from content so results are deterministic and no-op commits are detectable.
 *
 * The default branch `main` is seeded lazily with an empty root commit, like a
 * freshly created GitHub repository.
 */
export const MOCK_GH_TTL = 604_800; // 7d
const DEFAULT_BRANCH = 'main';

const refKey = (branch: string): string => `mock:gh:ref:${branch}`;
const commitKey = (sha: string): string => `mock:gh:commit:${sha}`;
const blobKey = (sha: string): string => `mock:gh:blob:${sha}`;
const prKey = (n: number): string => `mock:gh:pr:${n}`;
const prIndexKey = (repo: string, head: string, base: string): string => `mock:gh:prindex:${repo}:${head}:${base}`;

const sha1 = (...parts: Array<string | Buffer>): string => {
  const h = createHash('sha1');
  for (const p of parts) h.update(p);
  return h.digest('hex');
};

export const blobSha = (content: string): string => sha1(`blob ${Buffer.byteLength(content, 'utf8')}\0`, content);

/** Deterministic tree id over a flat path -> blob sha map. */
export function treeSha(files: Readonly<Record<string, string>>): string {
  const lines = Object.keys(files)
    .sort()
    .map((p) => `${p}\0${files[p]}\n`);
  return sha1('tree\0', lines.join(''));
}

interface MockCommit {
  sha: string;
  parent: string | null;
  tree: string;
  files: Record<string, string>; // path -> blob sha
  message: string;
}

const dirOf = (path: string): string => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '');

export interface MockRepoDeps {
  redis: Redis;
  repo: string;
}

export function mockRepoOps(deps: MockRepoDeps) {
  const { redis } = deps;
  const repoName = deps.repo || 'org/repo';
  const htmlUrl = (sha: string): string => `https://github.mock.local/${repoName}/commit/${sha}`;

  async function storeCommit(files: Record<string, string>, parent: string | null, message: string): Promise<MockCommit> {
    const tree = treeSha(files);
    const sha = sha1(`commit\0tree ${tree}\nparent ${parent ?? ''}\n\n${message}`);
    const commit: MockCommit = { sha, parent, tree, files, message };
    await redis.set(commitKey(sha), JSON.stringify(commit), 'EX', MOCK_GH_TTL);
    return commit;
  }

  async function loadCommit(sha: string): Promise<MockCommit> {
    const raw = await redis.get(commitKey(sha));
    if (!raw) throw new SdlcError('NOT_FOUND', `Commit ${sha} not found`);
    return JSON.parse(raw) as MockCommit;
  }

  async function getHead(branch: string): Promise<string | null> {
    const raw = await redis.get(refKey(branch));
    if (raw) return raw;
    if (branch !== DEFAULT_BRANCH) return null;
    const seed = await storeCommit({}, null, 'Initial commit');
    await redis.set(refKey(branch), seed.sha, 'EX', MOCK_GH_TTL);
    return seed.sha;
  }

  const setHead = async (branch: string, sha: string): Promise<void> => {
    await redis.set(refKey(branch), sha, 'EX', MOCK_GH_TTL);
  };

  async function putBlobs(files: ReadonlyArray<{ path: string; content: string }>): Promise<Map<string, string>> {
    const out = new Map<string, string>();
    for (const f of files) {
      const sha = blobSha(f.content);
      await redis.set(blobKey(sha), f.content, 'EX', MOCK_GH_TTL);
      out.set(f.path, sha);
    }
    return out;
  }

  async function resolveCommit(ref: string): Promise<MockCommit> {
    const head = await getHead(ref);
    if (head) return loadCommit(head);
    if (/^[0-9a-f]{40}$/i.test(ref) && (await redis.get(commitKey(ref.toLowerCase())))) return loadCommit(ref.toLowerCase());
    throw new SdlcError('NOT_FOUND', `Ref ${ref} not found`);
  }

  async function contentsOf(commit: MockCommit): Promise<Record<string, string>> {
    const out: Record<string, string> = {};
    for (const [path, sha] of Object.entries(commit.files)) out[path] = (await redis.get(blobKey(sha))) ?? '';
    return out;
  }

  return {
    /** Branch from `from`'s head; a missing base yields an empty root commit (legacy mock behaviour). */
    async createBranch(branch: string, from: string): Promise<{ baseSha: string }> {
      let base = await getHead(from);
      if (base === null) base = (await storeCommit({}, null, 'Initial commit')).sha;
      await setHead(branch, base);
      return { baseSha: base };
    },

    /** Legacy tools: always create a commit on top of the branch (creating it if needed). */
    async commitFiles(branch: string, files: Array<{ path: string; content: string }>, message: string) {
      const head = await getHead(branch);
      const parent = head ? await loadCommit(head) : null;
      const blobs = await putBlobs(files);
      const merged = { ...(parent?.files ?? {}), ...Object.fromEntries(blobs) };
      const commit = await storeCommit(merged, parent?.sha ?? null, message);
      await setHead(branch, commit.sha);
      return { commitSha: commit.sha, tree: await contentsOf(commit), htmlUrl: htmlUrl(commit.sha) };
    },

    async commitIndex(input: CommitIndexInput): Promise<CommitIndexOut> {
      const plan = planCommit(input);
      const { branch } = input;
      const expected = input.expectedHeadSha?.toLowerCase();

      let head = await getHead(branch);
      if (expected !== undefined && head?.toLowerCase() !== expected) {
        throw new SdlcError('GATE_CONFLICT', `Branch ${branch} head is ${head ?? '(missing)'}, expected ${expected}`, {
          details: { branch, expectedHeadSha: expected, actualHeadSha: head },
        });
      }
      let missing = false;
      if (head === null) {
        if (!input.createBranchIfMissing) throw new SdlcError('NOT_FOUND', `Branch ${branch} does not exist and createBranchIfMissing is false`);
        head = await getHead(input.baseBranch);
        if (head === null) throw new SdlcError('NOT_FOUND', `Neither branch ${branch} nor base branch ${input.baseBranch} exists`);
        missing = true;
      }

      const parent = await loadCommit(head);
      const blobs = await putBlobs(plan.files);
      const next: Record<string, string> = { ...parent.files };
      for (const [path, sha] of blobs) next[path] = sha;
      for (const path of plan.deletions) delete next[path];
      const newTree = treeSha(next);

      if (newTree === parent.tree) {
        if (missing) await setHead(branch, head);
        return { commitSha: parent.sha, parentSha: parent.parent, treeSha: parent.tree, branch, htmlUrl: htmlUrl(parent.sha), noop: true };
      }
      const commit = await storeCommit(next, parent.sha, input.message);
      await setHead(branch, commit.sha);
      return { commitSha: commit.sha, parentSha: parent.sha, treeSha: commit.tree, branch, htmlUrl: htmlUrl(commit.sha), noop: false };
    },

    async readFiles(ref: string, paths: string[]) {
      const unique = [...new Set(paths)];
      unique.forEach(assertSafeReadPath);
      const commit = await resolveCommit(ref);
      let total = 0;
      const files: Array<{ path: string; content: string | null; sha: string | null }> = [];
      for (const path of unique) {
        const sha = commit.files[path];
        if (sha === undefined) {
          files.push({ path, content: null, sha: null });
          continue;
        }
        const content = (await redis.get(blobKey(sha))) ?? '';
        total += Buffer.byteLength(content, 'utf8');
        if (total > MAX_READ_BYTES) {
          throw new SdlcError('VALIDATION_FAILED', `Requested files exceed the ${MAX_READ_BYTES} byte (10 MiB) response limit. Request fewer files per call.`);
        }
        files.push({ path, content, sha });
      }
      return files;
    },

    async listTree(ref: string, prefix: string, recursive: boolean): Promise<{ entries: TreeEntryOut[]; truncated: boolean }> {
      assertSafePrefix(prefix);
      const commit = await resolveCommit(ref);
      const baseDir = dirOf(prefix);
      const all = new Map<string, TreeEntryOut>();
      const dirs = new Set<string>();
      for (const [path, sha] of Object.entries(commit.files)) {
        all.set(path, { path, type: 'blob', sha, size: Buffer.byteLength((await redis.get(blobKey(sha))) ?? '', 'utf8') });
        for (let d = dirOf(path); d !== ''; d = dirOf(d)) dirs.add(d);
      }
      for (const d of dirs) {
        const sub = Object.fromEntries(
          Object.entries(commit.files)
            .filter(([p]) => p.startsWith(`${d}/`))
            .map(([p, sha]) => [p.slice(d.length + 1), sha]),
        );
        all.set(d, { path: d, type: 'tree', sha: treeSha(sub), size: null });
      }
      const entries = [...all.values()]
        .filter((e) => e.path.startsWith(prefix) && (recursive || dirOf(e.path) === baseDir))
        .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
      return { entries, truncated: false };
    },

    async openPullRequest(input: { head: string; base: string; title: string; body: string; reuseExisting: boolean }) {
      const indexKey = prIndexKey(repoName, input.head, input.base);
      if (input.reuseExisting) {
        const existing = Number(await redis.get(indexKey));
        if (existing > 0 && (await redis.get(prKey(existing)))) {
          return { number: existing, url: `https://github.mock.local/${repoName}/pull/${existing}`, created: false };
        }
      }
      const n = await redis.incr('mock:gh:prseq');
      await redis.set(prKey(n), JSON.stringify({ ...input, state: 'open' }), 'EX', MOCK_GH_TTL);
      await redis.set(indexKey, String(n), 'EX', MOCK_GH_TTL);
      return { number: n, url: `https://github.mock.local/${repoName}/pull/${n}`, created: true };
    },
  };
}
