import { createHash } from 'node:crypto';
import type { Redis } from 'ioredis';
import { SdlcError, type ToolsEnv } from '@sdlc/shared';

interface FileInput {
  path: string;
  content: string;
}

export interface GithubDeps {
  env: ToolsEnv;
  redis: Redis;
  live: boolean;
}

const MOCK_TTL = 604_800; // 7d

/**
 * GitHub connector. Live mode uses the REST v3 API (contents/refs/actions/pulls).
 * Mock mode simulates a repository AND a CI pipeline in Redis: a pushed commit
 * whose files contain the string `BUG-MARKER` produces a failed "typecheck" run
 * with a TS2322 log; once a fix commit removes the marker, the rerun succeeds.
 * This deterministically exercises the Build Recovery Loop offline.
 */
export function githubImpl(deps: GithubDeps) {
  const { env, redis, live } = deps;

  const gh = async (path: string, init?: RequestInit): Promise<Response> => {
    const res = await fetch(`https://api.github.com${path}`, {
      ...init,
      headers: {
        accept: 'application/vnd.github+json',
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        'x-github-api-version': '2022-11-28',
        ...(init?.headers ?? {}),
      },
      signal: AbortSignal.timeout(30_000),
    });
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
  const branchKey = (b: string) => `mock:gh:branch:${b}`;
  const runKey = (r: string) => `mock:gh:run:${r}`;

  async function mockCommit(branch: string, files: FileInput[], message: string) {
    const rawPrev = await redis.get(branchKey(branch));
    const tree: Record<string, string> = rawPrev ? (JSON.parse(rawPrev) as Record<string, string>) : {};
    for (const f of files) tree[f.path] = f.content;
    await redis.set(branchKey(branch), JSON.stringify(tree), 'EX', MOCK_TTL);

    const commitSha = createHash('sha256').update(`${branch}\0${message}\0${JSON.stringify(tree)}`).digest('hex').slice(0, 12);
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
      const baseSha = createHash('sha256').update(input.from).digest('hex').slice(0, 12);
      await redis.set(branchKey(input.branch), (await redis.get(branchKey(input.from))) ?? '{}', 'EX', MOCK_TTL);
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
  };
}
