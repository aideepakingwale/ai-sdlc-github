import { createHash } from 'node:crypto';
import type { Redis } from 'ioredis';
import { SdlcError } from '@sdlc/shared';
import type { ToolsEnv } from '@sdlc/shared';
import { credsOf, targetOf } from '../target.js';

export interface AtlassianDeps {
  env: ToolsEnv;
  redis: Redis;
  live: boolean;
}

/** Sequential mock issue/page counters persisted in Redis so keys look real. */
async function nextSeq(redis: Redis, kind: string): Promise<number> {
  return redis.incr(`mock:seq:${kind}`);
}

/** The Jira / Confluence connection this call uses: the project's own when it has one, else the platform's. */
function conn(env: ToolsEnv, kind: 'jira' | 'confluence'): { baseUrl: string | undefined; authorization: string } {
  const own = credsOf()[kind];
  const base = kind === 'jira' ? env.JIRA_BASE_URL : env.CONFLUENCE_BASE_URL;
  const [email, token] = own ? [own.email, own.apiToken] : [env.JIRA_EMAIL, env.JIRA_API_TOKEN];
  return { baseUrl: own?.baseUrl ?? base, authorization: `Basic ${Buffer.from(`${email}:${token}`).toString('base64')}` };
}

async function jiraCreateIssue(
  env: ToolsEnv,
  fields: Record<string, unknown>,
): Promise<{ id: string; key: string }> {
  const c = conn(env, 'jira');
  const res = await fetch(`${c.baseUrl}/rest/api/3/issue`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: c.authorization },
    body: JSON.stringify({ fields }),
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) {
    throw new SdlcError('TOOL_ERROR', `Jira ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  return (await res.json()) as { id: string; key: string };
}

/** Plain-text Atlassian Document Format wrapper. */
function adf(text: string) {
  return {
    type: 'doc',
    version: 1,
    content: [{ type: 'paragraph', content: [{ type: 'text', text: text.slice(0, 30_000) }] }],
  };
}

/** Normalise a caller-supplied project key: uppercase alphanumerics, 2–6 chars.
 *  Falls back to the configured default when absent or unusable. */
function projectKeyOf(supplied: string | undefined, fallback: string): string {
  const cleaned = (supplied ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  return cleaned.length >= 2 && cleaned.length <= 6 ? cleaned : fallback;
}

export function atlassianImpl(deps: AtlassianDeps) {
  const { env, redis } = deps;
  const jiraLive = (): boolean => deps.live || Boolean(credsOf().jira);
  const confluenceLive = (): boolean => deps.live || Boolean(credsOf().confluence);
  const jiraBase = (): string | undefined => conn(env, 'jira').baseUrl;
  /** The Jira project of the project the current call belongs to (platform default when none). */
  const keyOf = (): string => targetOf().jiraProjectKey ?? env.JIRA_PROJECT_KEY;
  /** A project's configured Jira project is AUTHORITATIVE: a key the model picked never overrides it. */
  const pickKey = (supplied: string | undefined): string => targetOf().jiraProjectKey ?? projectKeyOf(supplied, keyOf());
  const spaceOf = (): string => targetOf().confluenceSpaceKey ?? env.CONFLUENCE_SPACE_KEY;

  return {
    async createEpic(input: { title: string; description: string; priority: string; projectKey?: string }) {
      const projectKey = pickKey(input.projectKey);
      if (jiraLive()) {
        const issue = await jiraCreateIssue(env, {
          project: { key: projectKey },
          issuetype: { name: 'Epic' },
          summary: input.title,
          description: adf(input.description),
          priority: { name: input.priority },
        });
        return { epicId: issue.id, epicKey: issue.key, url: `${jiraBase()}/browse/${issue.key}` };
      }
      const n = await nextSeq(redis, 'issue');
      const key = `${projectKey}-${n}`;
      return { epicId: String(10_000 + n), epicKey: key, url: `https://jira.mock.local/browse/${key}` };
    },

    async createStory(input: { epicKey: string; storyText: string; gherkinCriteria: string[]; storyPoints?: number; projectKey?: string }) {
      // Prefer the epic's own prefix so a story sits under its epic's project.
      const epicPrefix = input.epicKey.split('-')[0];
      const projectKey = pickKey(input.projectKey ?? epicPrefix);
      if (jiraLive()) {
        const issue = await jiraCreateIssue(env, {
          project: { key: projectKey },
          issuetype: { name: 'Story' },
          summary: input.storyText.slice(0, 250),
          description: adf(`${input.storyText}\n\nAcceptance Criteria:\n${input.gherkinCriteria.join('\n\n')}`),
          parent: { key: input.epicKey },
        });
        return { storyId: issue.id, storyKey: issue.key, url: `${jiraBase()}/browse/${issue.key}` };
      }
      const n = await nextSeq(redis, 'issue');
      const key = `${projectKey}-${n}`;
      return { storyId: String(10_000 + n), storyKey: key, url: `https://jira.mock.local/browse/${key}` };
    },

    async createXrayTest(input: { storyKey: string; title: string; steps: Array<{ action: string; expectedResult: string }> }) {
      // Keep the test in the same project as the story it verifies.
      const projectKey = pickKey(input.storyKey.split('-')[0]);
      if (jiraLive()) {
        const issue = await jiraCreateIssue(env, {
          project: { key: projectKey },
          issuetype: { name: 'Test' },
          summary: input.title,
          description: adf(
            input.steps.map((s, i) => `Step ${i + 1}: ${s.action}\nExpected: ${s.expectedResult}`).join('\n\n'),
          ),
        });
        return { xrayTestKey: issue.key, url: `${jiraBase()}/browse/${issue.key}` };
      }
      const n = await nextSeq(redis, 'issue');
      const key = `${projectKey}-${n}`;
      return { xrayTestKey: key, url: `https://jira.mock.local/browse/${key}` };
    },

    async publishPage(input: { title: string; body: string; kind: 'prd' | 'hld' | 'lld' }) {
      if (confluenceLive()) {
        const cc = conn(env, 'confluence');
        const res = await fetch(`${cc.baseUrl}/wiki/rest/api/content`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: cc.authorization },
          body: JSON.stringify({
            type: 'page',
            title: input.title,
            space: { key: spaceOf() },
            body: { storage: { value: `<pre>${escapeHtml(input.body)}</pre>`, representation: 'storage' } },
          }),
          signal: AbortSignal.timeout(30_000),
        });
        if (!res.ok) {
          throw new SdlcError('TOOL_ERROR', `Confluence ${res.status}: ${(await res.text()).slice(0, 300)}`);
        }
        const page = (await res.json()) as { id: string; _links?: { base?: string; webui?: string } };
        return {
          pageId: page.id,
          url: `${page._links?.base ?? cc.baseUrl}${page._links?.webui ?? ''}`,
        };
      }
      const n = await nextSeq(redis, 'page');
      // Store the body so the demo UI/audit can reference stable mock content.
      const hash = createHash('sha256').update(input.body).digest('hex').slice(0, 12);
      await redis.set(`mock:page:${n}`, JSON.stringify({ title: input.title, kind: input.kind, hash }), 'EX', 604_800);
      return { pageId: String(n), url: `https://confluence.mock.local/wiki/pages/${n}` };
    },
  };
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
