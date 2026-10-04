import type { Redis } from 'ioredis';
import { SdlcError, type ToolsEnv } from '@sdlc/shared';
import { githubClient, type GithubRequestInit } from './github-api.js';
import { liveGitOps } from './github-live.js';
import { mockRepoOps } from './github-mock.js';
import type { CommitIndexInput } from './git-paths.js';
import type { HttpPolicy } from './http.js';

interface FileInput {
  path: string;
  content: string;
}

export interface GithubDeps {
  env: ToolsEnv;
  redis: Redis;
  live: boolean;
  /** Test hook: override HTTP timeout/retry/sleep behaviour. */
  http?: Partial<HttpPolicy>;
}

const MOCK_TTL = 604_800; // 7d (CI run records)

/**
 * GitHub connector. Live mode uses the REST v3 API (contents/refs/actions/pulls).
 * Mock mode simulates a repository AND a CI pipeline in Redis: a pushed commit
 * whose files contain the string `BUG-MARKER` produces a failed "typecheck" run
 * with a TS2322 log; once a fix commit removes the marker, the rerun succeeds.
 * This deterministically exercises the Build Recovery Loop offline.
 */
export function githubImpl(deps: GithubDeps) {
  const { env, redis, live } = deps;

  const client = githubClient(env, deps.http);
  const liveGit = liveGitOps(client, env);
  const mockRepo = mockRepoOps({ redis, repo: env.GITHUB_REPO ?? '' });

  /**
   * Legacy helper kept behaviour-compatible: returns the response for 2xx/404 and
   * throws TOOL_ERROR otherwise. It now goes through the shared HTTP layer, which
   * adds a timeout, rate-limit retries (429, or 403 with Retry-After) and retries
   * of transient 5xx for GETs only - non-GET calls are never replayed after an
   * ambiguous failure.
   */
  const gh = async (path: string, init?: GithubRequestInit) => {
    const res = await client.raw(path, { ...init, idempotent: (init?.method ?? 'GET') === 'GET' });
    if (!res.ok && res.status !== 404) {
      throw new SdlcError('TOOL_ERROR', `GitHub ${res.status} ${path}: ${(await res.text()).slice(0, 300)}`);
    }
    return res;
  };

  async function liveCommitFiles(branch: string, files: FileInput[], message: string) {
    const repo = env.GITHUB_REPO;
    let lastSha = '';
    for (const file of files) {
      const existing = await gh(`/repos/${repo}/contents/${file.path}?ref=${branch}`);
      const sha = existing.status === 200 ? ((await existing.json()) as { sha: string }).sha : undefined;
      const res = await gh(`/repos/${repo}/contents/${file.path}`, {
        method: 'PUT',
        body: JSON.stringify({
          message,
          branch,
          content: Buffer.from(file.content, 'utf8').toString('base64'),
          ...(sha ? { sha } : {}),
        }),
      });
      lastSha = ((await res.json()) as { commit: { sha: string } }).commit.sha;
    }
    return {
      commitSha: lastSha,
      branch,
      htmlUrl: `https://github.com/${repo}/commits/${branch}`,
    };
  }

  async function liveLatestRunId(branch: string): Promise<string> {
    const res = await gh(`/repos/${env.GITHUB_REPO}/actions/runs?branch=${branch}&per_page=1`);
    const data = (await res.json()) as { workflow_runs?: Array<{ id: number }> };
    return String(data.workflow_runs?.[0]?.id ?? '0');
  }

  // ---------- mock world ----------
  const runKey = (r: string) => `mock:gh:run:${r}`;

  async function mockCommit(branch: string, files: FileInput[], message: string) {
    const { commitSha, tree } = await mockRepo.commitFiles(branch, files, message);
    return { tree, commitSha };
  }

  /** Simulated CI: fails while any file still contains BUG-MARKER. */
  async function mockTriggerRun(branch: string, tree: Record<string, string>): Promise<string> {
    const n = await redis.incr('mock:gh:runseq');
    const runId = `run-${n}`;
    const buggy = Object.entries(tree).find(([, content]) => content.includes('BUG-MARKER'));
    const run = buggy
      ? {
          branch,
          status: 'completed',
          conclusion: 'failure',
          failedStep: 'typecheck',
          failedJobIds: [`job-${n}-typecheck`],
          log: `> tsc --noEmit\n${buggy[0]}(7,9): error TS2322: Type 'string' is not assignable to type 'number'.\n  // BUG-MARKER line rejected by compiler\nProcess completed with exit code 2.`,
        }
      : {
          branch,
          status: 'completed',
          conclusion: 'success',
          failedStep: '',
          failedJobIds: [] as string[],
          log: 'All 7 stages passed: checkout, lint, build, test, snyk-scan, inspector-scan, deploy(dry-run).',
        };
    await redis.set(runKey(runId), JSON.stringify(run), 'EX', MOCK_TTL);
    return runId;
  }

  return {
    async createBranch(input: { branch: string; from: string }) {
      if (live) {
        const repo = env.GITHUB_REPO;
        const base = await gh(`/repos/${repo}/git/ref/heads/${input.from}`);
        if (base.status === 404) throw new SdlcError('TOOL_ERROR', `Base branch ${input.from} not found`);
        const baseSha = ((await base.json()) as { object: { sha: string } }).object.sha;
        await gh(`/repos/${repo}/git/refs`, {
          method: 'POST',
          body: JSON.stringify({ ref: `refs/heads/${input.branch}`, sha: baseSha }),
        });
        return { branch: input.branch, baseSha };
      }
      const { baseSha } = await mockRepo.createBranch(input.branch, input.from);
      return { branch: input.branch, baseSha };
    },

    /** Plain docs/config commits — no CI simulation needed for these paths. */
    async commitFiles(input: { branch: string; files: FileInput[]; message: string }) {
      if (live) return liveCommitFiles(input.branch, input.files, input.message);
      const { commitSha } = await mockCommit(input.branch, input.files, input.message);
      return {
        commitSha,
        branch: input.branch,
        htmlUrl: `https://github.mock.local/${env.GITHUB_REPO || 'org/repo'}/commit/${commitSha}`,
      };
    },

    /** Code commits trigger the (real or simulated) CI pipeline and return runId. */
    async commitCode(input: { branch: string; files: FileInput[]; message: string }) {
      if (live) {
        const result = await liveCommitFiles(input.branch, input.files, input.message);
        await new Promise((r) => setTimeout(r, 3_000)); // give Actions a beat to register the run
        return { ...result, runId: await liveLatestRunId(input.branch) };
      }
      const { tree, commitSha } = await mockCommit(input.branch, input.files, input.message);
      const runId = await mockTriggerRun(input.branch, tree);
      return {
        commitSha,
        branch: input.branch,
        htmlUrl: `https://github.mock.local/${env.GITHUB_REPO || 'org/repo'}/commit/${commitSha}`,
        runId,
      };
    },

    async pollRunStatus(input: { runId: string }) {
      if (live) {
        const res = await gh(`/repos/${env.GITHUB_REPO}/actions/runs/${input.runId}`);
        const run = (await res.json()) as { status: string; conclusion: string | null };
        let failedJobIds: string[] = [];
        if (run.conclusion === 'failure') {
          const jobsRes = await gh(`/repos/${env.GITHUB_REPO}/actions/runs/${input.runId}/jobs`);
          const jobs = (await jobsRes.json()) as { jobs?: Array<{ id: number; conclusion: string | null }> };
          failedJobIds = (jobs.jobs ?? []).filter((j) => j.conclusion === 'failure').map((j) => String(j.id));
        }
        return {
          status: (run.status === 'completed' ? 'completed' : run.status === 'queued' ? 'queued' : 'in_progress') as
            | 'queued'
            | 'in_progress'
            | 'completed',
          conclusion: (run.conclusion as 'success' | 'failure' | 'cancelled' | null) ?? null,
          failedJobIds,
        };
      }
      const raw = await redis.get(runKey(input.runId));
      if (!raw) throw new SdlcError('NOT_FOUND', `Unknown run ${input.runId}`);
      const run = JSON.parse(raw) as { status: string; conclusion: string; failedJobIds: string[] };
      return {
        status: run.status as 'queued' | 'in_progress' | 'completed',
        conclusion: (run.conclusion || null) as 'success' | 'failure' | 'cancelled' | null,
        failedJobIds: run.failedJobIds,
      };
    },

    async fetchBuildLogs(input: { runId: string }) {
      if (live) {
        // Job-level annotations give the actionable failure text without the logs zip.
        const jobsRes = await gh(`/repos/${env.GITHUB_REPO}/actions/runs/${input.runId}/jobs`);
        const jobs = (await jobsRes.json()) as {
          jobs?: Array<{ name: string; conclusion: string | null; steps?: Array<{ name: string; conclusion: string | null }> }>;
        };
        const failed = (jobs.jobs ?? []).find((j) => j.conclusion === 'failure');
        const failedStep = failed?.steps?.find((s) => s.conclusion === 'failure')?.name ?? failed?.name ?? 'unknown';
        return {
          rawLogText: JSON.stringify(jobs.jobs ?? [], null, 2).slice(0, 20_000),
          failedStep,
        };
      }
      const raw = await redis.get(runKey(input.runId));
      if (!raw) throw new SdlcError('NOT_FOUND', `Unknown run ${input.runId}`);
      const run = JSON.parse(raw) as { log: string; failedStep: string };
      return { rawLogText: run.log, failedStep: run.failedStep };
    },

    async createPullRequest(input: { branch: string; title: string; body: string; checklist: string[] }) {
      const bodyWithChecklist = `${input.body}\n\n## Review checklist\n${input.checklist.map((c) => `- [ ] ${c}`).join('\n')}`;
      if (live) {
        const res = await gh(`/repos/${env.GITHUB_REPO}/pulls`, {
          method: 'POST',
          body: JSON.stringify({ title: input.title, head: input.branch, base: 'main', body: bodyWithChecklist }),
        });
        const pr = (await res.json()) as { number: number; html_url: string };
        return { prNumber: pr.number, url: pr.html_url };
      }
      const n = await redis.incr('mock:gh:prseq');
      await redis.set(`mock:gh:pr:${n}`, JSON.stringify({ ...input, body: bodyWithChecklist }), 'EX', MOCK_TTL);
      return { prNumber: n, url: `https://github.mock.local/${env.GITHUB_REPO || 'org/repo'}/pull/${n}` };
    },

    // ---------- Git Data API tools ----------
    async commitIndex(input: CommitIndexInput) {
      return live ? liveGit.commitIndex(input) : mockRepo.commitIndex(input);
    },

    async readFiles(input: { ref: string; paths: string[] }) {
      return { files: live ? await liveGit.readFiles(input.ref, input.paths) : await mockRepo.readFiles(input.ref, input.paths) };
    },

    async listTree(input: { ref: string; prefix: string; recursive: boolean }) {
      return live ? liveGit.listTree(input.ref, input.prefix, input.recursive) : mockRepo.listTree(input.ref, input.prefix, input.recursive);
    },

    /** Idempotent PR creation: returns the open PR for head/base when one exists. */
    async openPullRequest(input: { head: string; base: string; title: string; body: string; reuseExisting: boolean }) {
      return live ? liveGit.openPullRequest(input) : mockRepo.openPullRequest(input);
    },
  };
}
