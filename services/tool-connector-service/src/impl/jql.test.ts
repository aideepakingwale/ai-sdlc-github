import { describe, expect, it } from 'vitest';
import type { JiraIssue } from '@sdlc/shared';
import { buildSearch, compileMockJql, formatJqlDate, quoteJql, sanitizeProjectKey, validateRawJql } from './jql.js';

describe('project key sanitising', () => {
  it.each([
    ['aqdp', 'AQDP'],
    [' my-proj_1 ', 'MYPROJ1'],
    ['AB', 'AB'],
    ['abcdefghij', 'ABCDEFGHIJ'],
  ])('%s -> %s', (raw, expected) => expect(sanitizeProjectKey(raw)).toBe(expected));

  it.each(['', 'A', '!!!', 'ABCDEFGHIJK', 'x" OR 1=1'])('rejects %j', (raw) => {
    expect(() => sanitizeProjectKey(raw === 'x" OR 1=1' ? '"' : raw)).toThrowError(expect.objectContaining({ code: 'VALIDATION_FAILED' }));
  });

  it('strips injection characters rather than passing them through', () => {
    expect(sanitizeProjectKey('AB" OR 1=1')).toBe('ABOR11');
    expect(() => sanitizeProjectKey('SDLC" OR project = "X')).toThrow(); // too long once stripped
  });
});

describe('quoteJql', () => {
  it('escapes quotes and backslashes', () => {
    expect(quoteJql('a"b\\c')).toBe('"a\\"b\\\\c"');
  });
  it('rejects control characters', () => {
    expect(() => quoteJql('a\nb')).toThrow();
    expect(() => quoteJql('a\u0000b')).toThrow();
  });
});

describe('formatJqlDate', () => {
  it('formats UTC yyyy-MM-dd HH:mm and honours offsets', () => {
    expect(formatJqlDate('2025-03-04T05:06:59.999Z')).toBe('2025-03-04 05:06');
    expect(formatJqlDate('2025-03-04T05:06:00+02:00')).toBe('2025-03-04 03:06');
  });
  it('rejects garbage', () => {
    expect(() => formatJqlDate('yesterday')).toThrow();
  });
});

describe('validateRawJql', () => {
  it.each([
    ['multiple statements', 'project = A; DROP'],
    ['newline', 'status = Done\nOR 1=1'],
    ['tab', 'status\t= Done'],
    ['NUL', 'status = \u0000'],
    ['unbalanced close paren', 'status = Done) OR (project = X'],
    ['unbalanced open paren', '(status = Done'],
    ['unterminated quote', 'summary ~ "abc'],
    ['order by', 'status = Done ORDER BY created DESC'],
    ['empty', '   '],
    ['too long', 'a'.repeat(2001)],
  ])('rejects %s', (_n, jql) => {
    expect(() => validateRawJql(jql)).toThrowError(expect.objectContaining({ code: 'VALIDATION_FAILED' }));
  });

  it('allows ORDER BY / parens / semicolons-free text inside quoted strings', () => {
    expect(validateRawJql('summary ~ "order by ) ( x" AND status = Done')).toBe('summary ~ "order by ) ( x" AND status = Done');
  });

  it('accepts a normal expression and trims', () => {
    expect(validateRawJql('  labels = devmind AND (status = "In Progress" OR status = Done) ')).toBe(
      'labels = devmind AND (status = "In Progress" OR status = Done)',
    );
  });
});

describe('buildSearch', () => {
  it('builds project + updatedSince + issueTypes ordered by updated ASC', () => {
    const s = buildSearch({ projectKey: 'sdlc', updatedSince: '2025-01-02T03:04:05Z', issueTypes: ['Story', 'Epic'] }, 'DEF');
    expect(s.jql).toBe('project = "SDLC" AND updated >= "2025-01-02 03:04" AND issuetype in ("Story", "Epic") ORDER BY updated ASC');
  });

  it('defaults to the configured project when nothing scopes the search', () => {
    expect(buildSearch({}, 'DEF').jql).toBe('project = "DEF" ORDER BY updated ASC');
  });

  it('AND-combines a raw jql in parentheses and does not add the default project', () => {
    const s = buildSearch({ jql: 'status = Done OR labels = x', issueTypes: ['Bug'] }, 'DEF');
    expect(s.jql).toBe('issuetype in ("Bug") AND (status = Done OR labels = x) ORDER BY updated ASC');
  });

  it('escapes issue type literals so they cannot break out of the string', () => {
    const s = buildSearch({ projectKey: 'AB', issueTypes: ['Story") OR project = "SECRET'] }, 'DEF');
    expect(s.jql).toBe('project = "AB" AND issuetype in ("Story\\") OR project = \\"SECRET") ORDER BY updated ASC');
  });

  it('cannot be tricked by a hostile projectKey', () => {
    expect(() => buildSearch({ projectKey: '"' }, 'DEF')).toThrow();
    expect(buildSearch({ projectKey: 'AB" OR 1=1' }, 'DEF').jql).toBe('project = "ABOR11" ORDER BY updated ASC');
  });

  it('rejects hostile raw jql', () => {
    expect(() => buildSearch({ jql: 'a = b; c = d' }, 'DEF')).toThrow();
    expect(() => buildSearch({ jql: 'a = b) OR (c = d' }, 'DEF')).toThrow();
  });
});

const issue = (over: Partial<JiraIssue>): JiraIssue => ({
  key: 'AB-1', id: '1', url: '', summary: 'Login page', description: '', type: 'Story', status: 'To Do', statusCategory: 'todo',
  priority: 'High', storyPoints: null, labels: ['web', 'auth'], epicKey: 'AB-9', sprint: null, assignee: 'Ann', created: '', updated: '', acceptanceCriteria: [],
  ...over,
});

describe('compileMockJql', () => {
  it('evaluates AND-combined comparisons case-insensitively', () => {
    const f = compileMockJql('(status = "to do" AND labels = AUTH AND priority != Low)');
    expect(f(issue({}))).toBe(true);
    expect(f(issue({ status: 'Done' }))).toBe(false);
    expect(f(issue({ priority: 'Low' }))).toBe(false);
  });

  it('supports in / not in / ~ / project / parent', () => {
    expect(compileMockJql('issuetype in (Story, Bug)')(issue({}))).toBe(true);
    expect(compileMockJql('issuetype not in (Story, Bug)')(issue({}))).toBe(false);
    expect(compileMockJql('summary ~ "login"')(issue({}))).toBe(true);
    expect(compileMockJql('project = AB')(issue({}))).toBe(true);
    expect(compileMockJql('project = ZZ')(issue({}))).toBe(false);
    expect(compileMockJql('parent = "AB-9"')(issue({}))).toBe(true);
  });

  it('does not split on AND inside quotes', () => {
    expect(compileMockJql('summary ~ "this AND that"')(issue({ summary: 'this AND that' }))).toBe(true);
  });

  it('rejects unsupported constructs explicitly', () => {
    expect(() => compileMockJql('status = Done OR status = "To Do"')).toThrowError(expect.objectContaining({ code: 'VALIDATION_FAILED' }));
    expect(() => compileMockJql('fixVersion = 1')).toThrowError(/does not support the JQL field/);
    expect(() => compileMockJql('currentUser()')).toThrow();
  });
});
