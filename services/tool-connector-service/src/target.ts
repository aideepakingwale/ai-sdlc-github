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

const store = new AsyncLocalStorage<Target>();

export const withTarget = <T>(target: Target, fn: () => Promise<T>): Promise<T> => store.run(target, fn);
export const targetOf = (): Target => store.getStore() ?? {};

const REPO = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
const SPACE = /^[A-Za-z0-9~][A-Za-z0-9_-]{0,254}$/;
const JIRA_KEY = /^[A-Z][A-Z0-9_]{1,19}$/;

/**
 * Validate a caller-supplied target. A project may only point at a repository under the SAME owner as the platform's
 * configured repository: the platform token is never used against a repository of another organisation.
 */
export function parseTarget(raw: unknown, defaultRepo?: string): Target {
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
  if (out.githubRepo && defaultRepo) {
    const owner = (s: string): string => s.split('/')[0]!.toLowerCase();
    if (owner(out.githubRepo) !== owner(defaultRepo)) {
      throw new SdlcError('FORBIDDEN', `target.githubRepo must belong to ${owner(defaultRepo)}, the organisation the platform is connected to`);
    }
  }
  return out;
}
