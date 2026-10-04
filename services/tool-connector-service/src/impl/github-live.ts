import { SdlcError } from '@sdlc/shared';
import type { ToolsEnv } from '@sdlc/shared';
import type { GithubClient } from './github-api.js';
import { mapLimit } from './concurrency.js';
import { assertSafePrefix, assertSafeReadPath, MAX_READ_BYTES, planCommit, type CommitIndexInput } from './git-paths.js';

/** Live GitHub operations built on the Git Data API (blobs/trees/commits/refs). */

export const GIT_CONCURRENCY = 4;
/** Re-read head and rebuild the tree at most this many times after a non-fast-forward. */
export const MAX_NON_FAST_FORWARD_RETRIES = 2;
/** Safety cap on entries returned by github_list_tree. */
export const MAX_TREE_ENTRIES = 100_000;

export interface TreeEntryOut {
  path: string;
  type: 'blob' | 'tree';
  sha: string;
  size: number | null;
}

export interface CommitIndexOut {
  commitSha: string;
  parentSha: string | null;
  treeSha: string;
  branch: string;
  htmlUrl: string;
  noop: boolean;
}

interface RawEntry {
  path: string;
  mode: string;
  type: string;
  sha: string;
  size?: number;
}

interface RawTree {
  sha: string;
  tree: RawEntry[];
  truncated?: boolean;
}

interface RawCommit {
  sha: string;
  html_url?: string;
  tree: { sha: string };
  parents: Array<{ sha: string }>;
}

const SHA_RE = /^([0-9a-f]{40}|[0-9a-f]{64})$/i;

const encodeRef = (name: string): string => name.split('/').map(encodeURIComponent).join('/');

function conflict(message: string, details?: Record<string, unknown>): SdlcError {
  return new SdlcError('GATE_CONFLICT', message, details ? { details } : undefined);
}

export function liveGitOps(client: GithubClient, env: ToolsEnv) {
  const repoPath = (): string => `/repos/${client.repo()}`;
  const authorIdent = { name: env.GITHUB_COMMIT_AUTHOR_NAME, email: env.GITHUB_COMMIT_AUTHOR_EMAIL };

  async function getRef(branch: string): Promise<string | null> {
    const res = await client.raw(`${repoPath()}/git/ref/heads/${encodeRef(branch)}`);
    if (res.status === 404) return null;
    const data = await client.ensure<{ object: { sha: string } }>(res, `get branch ${branch}`);
    return data.object.sha;
  }

  /** Branch name, tag name, `refs/...` path or commit sha -> commit sha. */
  async function resolveCommitSha(ref: string): Promise<string> {
    if (SHA_RE.test(ref)) return ref.toLowerCase();
    const candidates = ref.startsWith('refs/') ? [ref.slice('refs/'.length)] : [`heads/${ref}`, `tags/${ref}`];
    for (const candidate of candidates) {
      const res = await client.raw(`${repoPath()}/git/ref/${encodeRef(candidate)}`);
      if (res.status === 404) continue;
      let obj = (await client.ensure<{ object: { sha: string; type: string } }>(res, `resolve ref ${ref}`)).object;
      for (let hops = 0; obj.type === 'tag' && hops < 3; hops++) {
        obj = (await client.json<{ object: { sha: string; type: string } }>('GET', `${repoPath()}/git/tags/${obj.sha}`, `dereference tag ${ref}`)).object;
      }
      if (obj.type !== 'commit') throw new SdlcError('VALIDATION_FAILED', `Ref ${ref} does not point at a commit`);
      return obj.sha;
    }
    throw new SdlcError('NOT_FOUND', `Ref ${ref} not found`);
  }

  const getCommit = (sha: string): Promise<RawCommit> => client.json<RawCommit>('GET', `${repoPath()}/git/commits/${sha}`, `get commit ${sha.slice(0, 7)}`);

  async function getTree(sha: string, recursive: boolean): Promise<RawTree> {
    return client.json<RawTree>('GET', `${repoPath()}/git/trees/${sha}${recursive ? '?recursive=1' : ''}`, `get tree ${sha.slice(0, 7)}`);
  }

  /** Memoised, directory-at-a-time path lookups so N files in one folder cost one tree fetch. */
  function pathResolver(rootTreeSha: string) {
    const dirs = new Map<string, Promise<Map<string, RawEntry> | null>>();
    const load = (dirPath: string, treeSha: string): Promise<Map<string, RawEntry> | null> => {
      let p = dirs.get(dirPath);
      if (!p) {
        p = getTree(treeSha, false).then((t) => {
          if (t.truncated === true) throw new SdlcError('TOOL_ERROR', `Directory ${dirPath || '/'} is too large to list through the Git Data API`);
          return new Map(t.tree.map((e) => [e.path, e]));
        });
        dirs.set(dirPath, p);
      }
      return p;
    };
    const dirEntries = async (dirPath: string): Promise<Map<string, RawEntry> | null> => {
      if (dirPath === '') return load('', rootTreeSha);
      const cut = dirPath.lastIndexOf('/');
      const parent = await dirEntries(cut < 0 ? '' : dirPath.slice(0, cut));
      const entry = parent?.get(cut < 0 ? dirPath : dirPath.slice(cut + 1));
      return entry && entry.type === 'tree' ? load(dirPath, entry.sha) : null;
    };
    return {
      dirEntries,
      async resolve(path: string): Promise<RawEntry | null> {
        const cut = path.lastIndexOf('/');
        const dir = await dirEntries(cut < 0 ? '' : path.slice(0, cut));
        return dir?.get(cut < 0 ? path : path.slice(cut + 1)) ?? null;
      },
    };
  }

  async function createBlob(content: string): Promise<string> {
    // Content-addressed, so repeating the POST is harmless.
    const blob = await client.json<{ sha: string }>('POST', `${repoPath()}/git/blobs`, 'create blob', {
      body: { content: Buffer.from(content, 'utf8').toString('base64'), encoding: 'base64' },
      idempotent: true,
    });
    return blob.sha;
  }

  async function createTree(baseTree: string, entries: Array<{ path: string; mode: '100644'; type: 'blob'; sha: string | null }>): Promise<string> {
    const tree = await client.json<{ sha: string }>('POST', `${repoPath()}/git/trees`, 'create tree', {
      body: { base_tree: baseTree, tree: entries },
      idempotent: true,
    });
    return tree.sha;
  }

  async function createCommit(message: string, tree: string, parent: string): Promise<RawCommit> {
    return client.json<RawCommit>('POST', `${repoPath()}/git/commits`, 'create commit', {
      body: { message, tree, parents: [parent], author: authorIdent, committer: authorIdent },
      idempotent: true, // an orphaned duplicate commit object is harmless
    });
  }

  async function createRef(branch: string, sha: string): Promise<'ok' | 'exists'> {
    const res = await client.raw(`${repoPath()}/git/refs`, { method: 'POST', body: JSON.stringify({ ref: `refs/heads/${branch}`, sha }) });
    if (res.ok) return 'ok';
    if (res.status === 422 && /already exists/i.test(await res.text())) return 'exists';
    await client.ensure(res, `create branch ${branch}`); // throws the mapped error
    return 'ok';
  }

  async function updateRef(branch: string, sha: string): Promise<'ok' | 'non-fast-forward'> {
    const res = await client.raw(`${repoPath()}/git/refs/heads/${encodeRef(branch)}`, {
      method: 'PATCH',
      body: JSON.stringify({ sha, force: false }),
      idempotent: true,
    });
    if (res.ok) return 'ok';
    if (res.status === 422 && /fast.?forward|reference update failed/i.test(await res.text())) return 'non-fast-forward';
    await client.ensure(res, `update branch ${branch}`); // throws the mapped error
    return 'ok';
  }

  async function commitIndex(input: CommitIndexInput): Promise<CommitIndexOut> {
    const plan = planCommit(input); // pure validation: nothing below runs if this throws
    const { branch } = input;
    const expected = input.expectedHeadSha?.toLowerCase();

    let head = await getRef(branch);
    if (expected !== undefined && head?.toLowerCase() !== expected) {
      throw conflict(`Branch ${branch} head is ${head ?? '(missing)'}, expected ${expected}`, { branch, expectedHeadSha: expected, actualHeadSha: head });
    }
    let branchMissing = false;
    if (head === null) {
      if (!input.createBranchIfMissing) throw new SdlcError('NOT_FOUND', `Branch ${branch} does not exist and createBranchIfMissing is false`);
      head = await getRef(input.baseBranch);
      if (head === null) throw new SdlcError('NOT_FOUND', `Neither branch ${branch} nor base branch ${input.baseBranch} exists`);
      branchMissing = true;
    }

    // Blobs are content-addressed, so they survive tree rebuilds across retries.
    const uniqueContents = [...new Set(plan.files.map((f) => f.content))];
    const blobShas = await mapLimit(uniqueContents, GIT_CONCURRENCY, createBlob);
    const shaByContent = new Map(uniqueContents.map((c, i) => [c, blobShas[i] as string]));

    for (let attempt = 0; attempt <= MAX_NON_FAST_FORWARD_RETRIES; attempt++) {
      const headCommit = await getCommit(head);
      const baseTree = headCommit.tree.sha;

      const entries: Array<{ path: string; mode: '100644'; type: 'blob'; sha: string | null }> = plan.files.map((f) => ({
        path: f.path,
        mode: '100644',
        type: 'blob',
        sha: shaByContent.get(f.content) as string,
      }));
      if (plan.deletions.length > 0) {
        // Deleting a path that is already gone is a no-op (keeps retries idempotent).
        const resolver = pathResolver(baseTree);
        const existing = await mapLimit(plan.deletions, GIT_CONCURRENCY, async (p) => ((await resolver.resolve(p)) ? p : null));
        for (const p of existing) if (p !== null) entries.push({ path: p, mode: '100644', type: 'blob', sha: null });
      }

      const treeSha = entries.length === 0 ? baseTree : await createTree(baseTree, entries);
      const parentOfHead = headCommit.parents[0]?.sha ?? null;
      const commitUrl = (sha: string): string => `https://github.com/${client.repo()}/commit/${sha}`;

      if (treeSha === baseTree) {
        if (branchMissing && (await createRef(branch, head)) === 'exists') {
          // Lost a race to create the branch: re-read and evaluate against the real head.
          branchMissing = false;
          if (expected !== undefined) throw conflict(`Branch ${branch} was created concurrently`, { branch });
          head = (await getRef(branch)) ?? head;
          continue;
        }
        return { commitSha: head, parentSha: parentOfHead, treeSha, branch, htmlUrl: headCommit.html_url ?? commitUrl(head), noop: true };
      }

      const commit = await createCommit(input.message, treeSha, head);
      const outcome = branchMissing ? await createRef(branch, commit.sha) : await updateRef(branch, commit.sha);
      if (outcome === 'ok') {
        return { commitSha: commit.sha, parentSha: head, treeSha, branch, htmlUrl: commit.html_url ?? commitUrl(commit.sha), noop: false };
      }

      // The branch moved (or appeared) under us.
      if (expected !== undefined) throw conflict(`Branch ${branch} moved while committing; expectedHeadSha no longer holds`, { branch, expectedHeadSha: expected });
      const latest = await getRef(branch);
      if (latest === null) throw new SdlcError('NOT_FOUND', `Branch ${branch} disappeared while committing`);
      head = latest;
      branchMissing = false;
    }
    throw conflict(`Could not update ${branch}: it kept moving after ${MAX_NON_FAST_FORWARD_RETRIES + 1} attempts`, { branch });
  }

  async function readFiles(ref: string, paths: string[]): Promise<Array<{ path: string; content: string | null; sha: string | null }>> {
    const unique = [...new Set(paths)];
    unique.forEach(assertSafeReadPath);
    const commit = await getCommit(await resolveCommitSha(ref));
    const resolver = pathResolver(commit.tree.sha);
    const entries = await mapLimit(unique, GIT_CONCURRENCY, (p) => resolver.resolve(p));

    const blobs = entries.map((e) => (e && e.type === 'blob' ? e : null));
    const declared = blobs.reduce((n, e) => n + (e?.size ?? 0), 0);
    if (declared > MAX_READ_BYTES) {
      throw new SdlcError('VALIDATION_FAILED', `Requested files total ${declared} bytes; the limit is ${MAX_READ_BYTES} (10 MiB). Request fewer files per call.`);
    }
    let fetched = 0;
    const contents = await mapLimit(blobs, GIT_CONCURRENCY, async (e) => {
      if (!e) return null;
      const blob = await client.json<{ content: string; encoding: string }>('GET', `${repoPath()}/git/blobs/${e.sha}`, `read blob ${e.sha.slice(0, 7)}`);
      const buf = blob.encoding === 'base64' ? Buffer.from(blob.content, 'base64') : Buffer.from(blob.content, 'utf8');
      fetched += buf.length;
      if (fetched > MAX_READ_BYTES) {
        throw new SdlcError('VALIDATION_FAILED', `Requested files exceed the ${MAX_READ_BYTES} byte (10 MiB) response limit. Request fewer files per call.`);
      }
      return buf.toString('utf8');
    });
    return unique.map((path, i) => ({ path, content: contents[i] ?? null, sha: blobs[i]?.sha ?? null }));
  }

  async function listTree(ref: string, prefix: string, recursive: boolean): Promise<{ entries: TreeEntryOut[]; truncated: boolean }> {
    assertSafePrefix(prefix);
    const commit = await getCommit(await resolveCommitSha(ref));
    const cut = prefix.lastIndexOf('/');
    const baseDir = cut < 0 ? '' : prefix.slice(0, cut);

    let startSha = commit.tree.sha;
    if (baseDir !== '') {
      const dir = await pathResolver(commit.tree.sha).resolve(baseDir);
      if (!dir || dir.type !== 'tree') return { entries: [], truncated: false };
      startSha = dir.sha;
    }
    const join = (dir: string, name: string): string => (dir === '' ? name : `${dir}/${name}`);
    const collected: RawEntry[] = [];
    let truncated = false;

    const first = await getTree(startSha, recursive);
    if (!recursive || first.truncated !== true) {
      for (const e of first.tree) collected.push({ ...e, path: join(baseDir, e.path) });
    } else {
      // GitHub truncated the recursive listing: walk subtrees one directory at a time.
      let level: Array<{ sha: string; dir: string }> = [{ sha: startSha, dir: baseDir }];
      while (level.length > 0 && !truncated) {
        const listings = await mapLimit(level, GIT_CONCURRENCY, async (node) => {
          const t = await getTree(node.sha, false);
          if (t.truncated === true) throw new SdlcError('TOOL_ERROR', `Directory ${node.dir || '/'} is too large to list through the Git Data API`);
          return { dir: node.dir, tree: t.tree };
        });
        const next: Array<{ sha: string; dir: string }> = [];
        for (const { dir, tree } of listings) {
          for (const e of tree) {
            const full = join(dir, e.path);
            collected.push({ ...e, path: full });
            if (e.type === 'tree') next.push({ sha: e.sha, dir: full });
          }
        }
        if (collected.length > MAX_TREE_ENTRIES) truncated = true;
        level = next;
      }
    }

    const entries = collected
      .filter((e) => (e.type === 'blob' || e.type === 'tree') && e.path.startsWith(prefix))
      .map<TreeEntryOut>((e) => ({ path: e.path, type: e.type as 'blob' | 'tree', sha: e.sha, size: e.type === 'blob' && typeof e.size === 'number' ? e.size : null }))
      .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
    if (entries.length > MAX_TREE_ENTRIES) return { entries: entries.slice(0, MAX_TREE_ENTRIES), truncated: true };
    return { entries, truncated };
  }

  async function openPullRequest(input: { head: string; base: string; title: string; body: string; reuseExisting: boolean }): Promise<{ number: number; url: string; created: boolean }> {
    const owner = client.repo().split('/')[0] as string;
    const headRef = input.head.includes(':') ? input.head : `${owner}:${input.head}`;
    const headBranch = headRef.slice(headRef.indexOf(':') + 1);

    const findOpen = async (): Promise<{ number: number; url: string } | null> => {
      const list = await client.json<Array<{ number: number; html_url: string; head?: { ref?: string }; base?: { ref?: string } }>>(
        'GET',
        `${repoPath()}/pulls?state=open&head=${encodeURIComponent(headRef)}&base=${encodeURIComponent(input.base)}&per_page=10`,
        'list pull requests',
      );
      const hit = list.find((pr) => (pr.head?.ref ?? headBranch) === headBranch && (pr.base?.ref ?? input.base) === input.base);
      return hit ? { number: hit.number, url: hit.html_url } : null;
    };

    if (input.reuseExisting) {
      const existing = await findOpen();
      if (existing) return { ...existing, created: false };
    }
    // POST is not idempotent: only 429 (never processed) is retried by the HTTP layer.
    const res = await client.raw(`${repoPath()}/pulls`, {
      method: 'POST',
      body: JSON.stringify({ title: input.title, head: input.head, base: input.base, body: input.body }),
    });
    if (res.status === 422 && input.reuseExisting && /already exists/i.test(await res.text())) {
      // Lost a race with another creator: return the PR that now exists.
      const existing = await findOpen();
      if (existing) return { ...existing, created: false };
    }
    const pr = await client.ensure<{ number: number; html_url: string }>(res, 'open pull request');
    return { number: pr.number, url: pr.html_url, created: true };
  }

  return { commitIndex, readFiles, listTree, openPullRequest };
}
