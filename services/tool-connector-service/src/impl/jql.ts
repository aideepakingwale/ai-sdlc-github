import { SdlcError } from '@sdlc/shared';
import type { JiraIssue } from '@sdlc/shared';

/** JQL construction and validation. Every string literal is quoted+escaped; raw JQL is vetted. */

const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;
export const MAX_RAW_JQL = 2000;

function invalid(message: string): SdlcError {
  return new SdlcError('VALIDATION_FAILED', message);
}

/** Uppercase alphanumerics, 2-10 chars. Anything else is stripped; an unusable result is an error. */
export function sanitizeProjectKey(raw: string): string {
  const cleaned = raw.toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (cleaned.length < 2 || cleaned.length > 10) {
    throw invalid(`projectKey must contain 2-10 letters/digits (got "${raw.slice(0, 32)}")`);
  }
  return cleaned;
}

/** Quote and escape a string literal for JQL. */
export function quoteJql(value: string): string {
  if (CONTROL_CHARS.test(value)) throw invalid('JQL values must not contain control characters');
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

/** `yyyy-MM-dd HH:mm` in UTC (JQL has minute resolution). Floors to the minute so no update is missed. */
export function formatJqlDate(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) throw invalid(`updatedSince is not a valid ISO timestamp: ${iso.slice(0, 40)}`);
  const d = new Date(ms);
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** Strip quoted regions (used to inspect only the JQL structure). Returns null if a quote is unterminated. */
function stripQuoted(raw: string): string | null {
  let out = '';
  let quote: string | null = null;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i] as string;
    if (quote) {
      if (ch === '\\') i++; // skip escaped char
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      out += '""';
      continue;
    }
    out += ch;
  }
  return quote ? null : out;
}

/**
 * Vet a caller-supplied JQL fragment. It will be wrapped in parentheses and
 * AND-combined with generated clauses, so it must be a single balanced boolean
 * expression: no statement separators, control characters, unbalanced
 * quotes/parentheses (which could break out of the wrapper) or its own ORDER BY.
 */
export function validateRawJql(raw: string): string {
  const jql = raw.trim();
  if (jql === '') throw invalid('jql must not be empty');
  if (jql.length > MAX_RAW_JQL) throw invalid(`jql is longer than ${MAX_RAW_JQL} characters`);
  if (CONTROL_CHARS.test(jql)) throw invalid('jql must not contain control characters');
  if (jql.includes(';')) throw invalid('jql must be a single statement (";" is not allowed)');
  const structure = stripQuoted(jql);
  if (structure === null) throw invalid('jql has an unterminated string literal');
  let depth = 0;
  for (const ch of structure) {
    if (ch === '(') depth++;
    else if (ch === ')' && --depth < 0) throw invalid('jql has unbalanced parentheses');
  }
  if (depth !== 0) throw invalid('jql has unbalanced parentheses');
  if (/\border\s+by\b/i.test(structure)) {
    throw invalid('jql must not contain ORDER BY; results are always ordered by updated ASC');
  }
  return jql;
}

export interface SearchSpec {
  projectKey?: string;
  jql?: string;
  updatedSince?: string;
  issueTypes?: string[];
}

export interface ResolvedSearch {
  /** Sanitised project keys implied by the request (empty when only raw JQL scopes the search). */
  projectKey: string | null;
  rawJql: string | null;
  updatedSince: string | null;
  issueTypes: string[];
  /** Full JQL text for live Jira. */
  jql: string;
}

/**
 * Validate a search request and build its JQL. With neither a projectKey nor a
 * raw jql the search is scoped to `defaultProjectKey` rather than the whole site.
 */
export function buildSearch(spec: SearchSpec, defaultProjectKey: string): ResolvedSearch {
  const rawJql = spec.jql === undefined ? null : validateRawJql(spec.jql);
  let projectKey: string | null = null;
  if (spec.projectKey !== undefined) projectKey = sanitizeProjectKey(spec.projectKey);
  else if (rawJql === null) projectKey = sanitizeProjectKey(defaultProjectKey);

  const issueTypes = (spec.issueTypes ?? []).map((t) => t.trim()).filter((t) => t !== '');
  const clauses: string[] = [];
  if (projectKey) clauses.push(`project = ${quoteJql(projectKey)}`);
  if (spec.updatedSince !== undefined) clauses.push(`updated >= "${formatJqlDate(spec.updatedSince)}"`);
  if (issueTypes.length > 0) clauses.push(`issuetype in (${issueTypes.map(quoteJql).join(', ')})`);
  if (rawJql !== null) clauses.push(`(${rawJql})`);
  return {
    projectKey,
    rawJql,
    updatedSince: spec.updatedSince ?? null,
    issueTypes,
    jql: `${clauses.join(' AND ')} ORDER BY updated ASC`,
  };
}

// ---------------------------------------------------------------------------
// Mock-mode evaluation of a safe JQL subset (AND-combined field comparisons).
// ---------------------------------------------------------------------------

type Predicate = (issue: JiraIssue) => boolean;

function splitTopLevelAnd(jql: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let start = 0;
  for (let i = 0; i < jql.length; i++) {
    const ch = jql[i] as string;
    if (quote) {
      if (ch === '\\') i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if (ch === '(') depth++;
    else if (ch === ')') depth--;
    else if (depth === 0 && /\s/.test(ch)) {
      const m = /^\s+and\s+/i.exec(jql.slice(i));
      if (m) {
        parts.push(jql.slice(start, i));
        i += m[0].length - 1;
        start = i + 1;
      }
    }
  }
  parts.push(jql.slice(start));
  return parts.map((p) => p.trim()).filter((p) => p !== '');
}

function unquote(token: string): string {
  const t = token.trim();
  const q = t[0];
  if ((q === '"' || q === "'") && t.endsWith(q) && t.length >= 2) return t.slice(1, -1).replace(/\\(["'\\])/g, '$1');
  return t;
}

function parseValues(token: string): string[] {
  const t = token.trim();
  if (t.startsWith('(') && t.endsWith(')')) {
    const inner = t.slice(1, -1);
    const values: string[] = [];
    let cur = '';
    let quote: string | null = null;
    for (let i = 0; i < inner.length; i++) {
      const ch = inner[i] as string;
      if (quote) {
        cur += ch;
        if (ch === '\\') cur += inner[++i] ?? '';
        else if (ch === quote) quote = null;
      } else if (ch === '"' || ch === "'") {
        quote = ch;
        cur += ch;
      } else if (ch === ',') {
        values.push(unquote(cur));
        cur = '';
      } else cur += ch;
    }
    if (cur.trim() !== '') values.push(unquote(cur));
    return values;
  }
  return [unquote(t)];
}

function fieldValues(issue: JiraIssue, field: string): string[] | null {
  switch (field) {
    case 'project':
      return [issue.key.split('-')[0] ?? ''];
    case 'issuetype':
    case 'type':
      return [issue.type];
    case 'status':
      return [issue.status];
    case 'priority':
      return issue.priority === null ? [] : [issue.priority];
    case 'labels':
    case 'label':
      return issue.labels;
    case 'key':
    case 'issuekey':
      return [issue.key];
    case 'assignee':
      return issue.assignee === null ? [] : [issue.assignee];
    case 'parent':
    case 'epic':
      return issue.epicKey === null ? [] : [issue.epicKey];
    case 'summary':
    case 'text':
      return [issue.summary, ...(field === 'text' ? [issue.description] : [])];
    default:
      return null;
  }
}

const SUPPORTED_FIELDS = new Set([
  'project', 'issuetype', 'type', 'status', 'priority', 'labels', 'label', 'key', 'issuekey', 'assignee', 'parent', 'epic', 'summary', 'text',
]);

/** True when the first "(" closes at the very end of `expr` (so the parentheses are redundant). */
function isFullyWrapped(expr: string): boolean {
  if (!expr.startsWith('(') || !expr.endsWith(')')) return false;
  const structure = stripQuoted(expr);
  if (structure === null) return false;
  let depth = 0;
  for (let i = 0; i < structure.length; i++) {
    if (structure[i] === '(') depth++;
    else if (structure[i] === ')' && --depth === 0) return i === structure.length - 1;
  }
  return false;
}

/** Compile the supported JQL subset to a predicate; anything else is a VALIDATION_FAILED error. */
export function compileMockJql(raw: string): Predicate {
  let expr = raw.trim();
  while (isFullyWrapped(expr)) expr = expr.slice(1, -1).trim();
  if (/\b(or|not)\b/i.test((stripQuoted(expr) ?? '').replace(/\bnot\s+in\b/gi, 'notin'))) {
    throw invalid('Mock Jira supports only AND-combined "field op value" clauses (no OR / NOT / grouping)');
  }
  const predicates: Predicate[] = splitTopLevelAnd(expr).map((clause) => {
    const m = /^([A-Za-z_]+)\s*(!=|=|~|\bnot\s+in\b|\bin\b)\s*([\s\S]+)$/i.exec(clause);
    if (!m) throw invalid(`Mock Jira supports only AND-combined "field op value" clauses; cannot evaluate: ${clause.slice(0, 60)}`);
    const field = (m[1] as string).toLowerCase();
    const op = (m[2] as string).toLowerCase().replace(/\s+/g, ' ');
    if (!SUPPORTED_FIELDS.has(field)) throw invalid(`Mock Jira does not support the JQL field "${field}"`);
    const operand = (m[3] as string).trim();
    const single = /^("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s"'()]+)$/.test(operand);
    if (op === 'in' || op === 'not in' ? !/^\([\s\S]*\)$/.test(operand) : !single) {
      throw invalid(`Mock Jira cannot evaluate this JQL clause: ${clause.slice(0, 60)}`);
    }
    const wanted = parseValues(operand).map((v) => v.toLowerCase());
    return (issue: JiraIssue): boolean => {
      const have = (fieldValues(issue, field) ?? []).map((v) => v.toLowerCase());
      switch (op) {
        case '=':
        case 'in':
          return have.some((h) => wanted.includes(h));
        case '!=':
        case 'not in':
          return !have.some((h) => wanted.includes(h));
        default: // '~'
          return have.some((h) => wanted.some((w) => h.includes(w)));
      }
    };
  });
  return (issue) => predicates.every((p) => p(issue));
}
