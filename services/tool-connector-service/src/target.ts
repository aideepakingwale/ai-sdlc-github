import { AsyncLocalStorage } from 'node:async_hooks';
import { SdlcError } from '@sdlc/shared';

/**
 * Per-PROJECT integration targets. The credentials (GitHub token, Atlassian API token) are the platform's, but WHERE a
 * project publishes is the project's own: its repository, Confluence space and Jira project. The orchestrator sends them
 * with every call as `target`; the handler runs inside an AsyncLocalStorage scope so concurrent calls of different
 * projects can never see each other's target. Anything missing falls back to the platform default (env).
 */
export interface Target {
  githubRepo?: string;
  confluenceSpaceKey?: string;
  jiraProjectKey?: string;
}

/**
 * A project's OWN credentials (set on its Connections screen). They travel beside the target, never inside a tool's input, are
 * never logged or cached, and apply only to the call they came with. Absent = the platform's shared connection.
 */
export interface Credentials {
  github?: { token: string; apiUrl?: string };
  jira?: { baseUrl: string; email: string; apiToken: string };
  confluence?: { baseUrl: string; email: string; apiToken: string };
}

const store = new AsyncLocalStorage<{ target: Target; creds: Credentials }>();

export const withTarget = <T>(target: Target, fn: () => Promise<T>, creds: Credentials = {}): Promise<T> => store.run({ target, creds }, fn);
export const targetOf = (): Target => store.getStore()?.target ?? {};
export const credsOf = (): Credentials => store.getStore()?.creds ?? {};

const str = (v: unknown, max = 2048): string | undefined => (typeof v === 'string' && v.length > 0 && v.length <= max ? v : undefined);
const url = (v: unknown): string | undefined => {
  const s = str(v);
  return s && /^https?:\/\/[^\s]+$/.test(s) ? s.replace(/\/+$/, '') : undefined;
};

/** Validate the credentials a caller sent; anything malformed is dropped (the platform's connection is used instead). */
export function parseCredentials(raw: unknown): Credentials {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const r = raw as Record<string, Record<string, unknown> | undefined>;
  const out: Credentials = {};
  const gh = r.github, token = str(gh?.token);
  if (token) out.github = { token, ...(url(gh?.apiUrl) ? { apiUrl: url(gh?.apiUrl)! } : {}) };
  for (const k of ['jira', 'confluence'] as const) {
    const c = r[k], baseUrl = url(c?.baseUrl), email = str(c?.email, 320), apiToken = str(c?.apiToken);
    if (baseUrl && email && apiToken) out[k] = { baseUrl, email, apiToken };
  }
  return out;
}

const REPO = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
const SPACE = /^[A-Za-z0-9~][A-Za-z0-9_-]{0,254}$/;
const JIRA_KEY = /^[A-Z][A-Z0-9_]{1,19}$/;

/**
 * Validate a caller-supplied target. A project may only point at a repository under the SAME owner as the platform's
 * configured repository: the platform token is never used against a repository of another organisation.
 */
export function parseTarget(raw: unknown, defaultRepo?: string, ownGithub = false): Target {
  if (raw === undefined || raw === null) return {};
  if (typeof raw !== 'object' || Array.isArray(raw)) throw new SdlcError('VALIDATION_FAILED', 'target must be an object');
  const r = raw as Record<string, unknown>;
  const out: Target = {};
  const take = (key: keyof Target, re: RegExp, what: string): void => {
    const v = r[key];
    if (v === undefined || v === null || v === '') return;
    if (typeof v !== 'string' || !re.test(v)) throw new SdlcError('VALIDATION_FAILED', `target.${key} is not a valid ${what}`);
    out[key] = v;
  };
  take('githubRepo', REPO, 'GitHub repository (owner/name)');
  take('confluenceSpaceKey', SPACE, 'Confluence space key');
  take('jiraProjectKey', JIRA_KEY, 'Jira project key');
  if (out.githubRepo && defaultRepo && !ownGithub) {
    const owner = (s: string): string => s.split('/')[0]!.toLowerCase();
    if (owner(out.githubRepo) !== owner(defaultRepo)) {
      throw new SdlcError('FORBIDDEN', `target.githubRepo must belong to ${owner(defaultRepo)}, the organisation the platform is connected to`);
    }
  }
  return out;
}
