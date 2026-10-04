import { SdlcError } from '@sdlc/shared';

/**
 * Repository path safety. Every path that reaches the Git Data API (or the
 * mock repo) passes through here BEFORE any network call is made.
 */
export const MAX_FILE_BYTES = 1024 * 1024; // 1 MiB per written file
export const MAX_TOTAL_BYTES = 20 * 1024 * 1024; // 20 MiB per commit
export const MAX_READ_BYTES = 10 * 1024 * 1024; // 10 MiB returned by github_read_files

const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;

function bad(message: string): SdlcError {
  return new SdlcError('VALIDATION_FAILED', message);
}

const show = (p: string): string => JSON.stringify(p.length > 80 ? `${p.slice(0, 80)}...` : p);

/** Shared structural checks. `allowTrailingSlash` is for list prefixes. */
function checkShape(path: string, what: string, allowTrailingSlash: boolean): void {
  if (path.length > 1024) throw bad(`${what} ${show(path)} is longer than 1024 characters`);
  if (CONTROL_CHARS.test(path)) throw bad(`${what} ${show(path)} contains control characters`);
  if (path.includes('\\')) throw bad(`${what} ${show(path)} must not contain backslashes`);
  if (path.startsWith('/') || /^[A-Za-z]:/.test(path)) throw bad(`${what} ${show(path)} must be relative, not absolute`);
  if (path.includes('..')) throw bad(`${what} ${show(path)} must not contain ".."`);
  const body = allowTrailingSlash && path.endsWith('/') ? path.slice(0, -1) : path;
  for (const segment of body.split('/')) {
    if (segment === '') throw bad(`${what} ${show(path)} has an empty path segment`);
    if (segment === '.') throw bad(`${what} ${show(path)} must not contain "." segments`);
    if (segment.toLowerCase() === '.git') throw bad(`${what} ${show(path)} must not touch ".git"`);
  }
}

/** Validate a file path for reads (no prefix requirement). */
export function assertSafeReadPath(path: string): void {
  if (path === '') throw bad('path must not be empty');
  checkShape(path, 'path', false);
}

/** Validate a tree-listing prefix ('' is allowed and means the whole repo). */
export function assertSafePrefix(prefix: string): void {
  if (prefix === '') return;
  checkShape(prefix, 'prefix', true);
}

export interface CommitIndexInput {
  branch: string;
  baseBranch: string;
  files: Array<{ path: string; content: string }>;
  deletions: string[];
  message: string;
  createBranchIfMissing: boolean;
  expectedHeadSha?: string | undefined;
  requiredPrefix: string;
}

export interface CommitPlan {
  files: Array<{ path: string; content: string; bytes: number }>;
  deletions: string[];
  totalBytes: number;
}

/** Conservative subset of `git check-ref-format` rules. */
export function assertSafeBranchName(name: string, what = 'branch'): void {
  if (
    name === '' ||
    name.length > 255 ||
    CONTROL_CHARS.test(name) ||
    /[ ~^:?*[\\]/.test(name) ||
    name.includes('..') ||
    name.includes('@{') ||
    name.includes('//') ||
    name.startsWith('/') ||
    name.endsWith('/') ||
    name.endsWith('.') ||
    name.endsWith('.lock') ||
    name.startsWith('-') ||
    name.split('/').some((s) => s.startsWith('.') || s.endsWith('.lock'))
  ) {
    throw bad(`${what} ${show(name)} is not a valid git branch name`);
  }
}

/** Validate a github_commit_index request without touching the network. */
export function planCommit(input: CommitIndexInput): CommitPlan {
  assertSafeBranchName(input.branch, 'branch');
  assertSafeBranchName(input.baseBranch, 'baseBranch');
  const prefix = input.requiredPrefix;
  if (prefix === '' || !prefix.endsWith('/')) throw bad('requiredPrefix must be a non-empty directory prefix ending with "/"');
  checkShape(prefix, 'requiredPrefix', true);

  if (input.files.length === 0 && input.deletions.length === 0) throw bad('nothing to commit: files and deletions are both empty');

  const seen = new Set<string>();
  const written = new Set<string>();
  const check = (path: string, what: string): void => {
    checkShape(path, what, false);
    if (!path.startsWith(prefix)) throw bad(`${what} ${show(path)} must start with "${prefix}"`);
    if (seen.has(path)) throw bad(`duplicate path ${show(path)}`);
    seen.add(path);
  };

  let totalBytes = 0;
  const files = input.files.map((f) => {
    check(f.path, 'file path');
    written.add(f.path);
    const bytes = Buffer.byteLength(f.content, 'utf8');
    if (bytes > MAX_FILE_BYTES) throw bad(`file ${show(f.path)} is ${bytes} bytes; the limit is ${MAX_FILE_BYTES} (1 MiB)`);
    totalBytes += bytes;
    return { path: f.path, content: f.content, bytes };
  });
  if (totalBytes > MAX_TOTAL_BYTES) throw bad(`commit is ${totalBytes} bytes in total; the limit is ${MAX_TOTAL_BYTES} (20 MiB)`);

  for (const d of input.deletions) {
    if (written.has(d)) throw bad(`path ${show(d)} is both written and deleted`);
    check(d, 'deletion path');
  }
  return { files, deletions: [...input.deletions], totalBytes };
}
