import { describe, expect, it } from 'vitest';
import {
  appendLine, auditSubmittable, decodePalette, delegationMeters, encodePalette, fromDeveloperJson, insertAt, lineDiff, sampleInputs, segments, statusChip, stepIndex,
  agentOutputTypes, moveItem, outputFileName, outputText, parseCap, parseRunValue, spentPercent, tokensLabel, toDeveloperJson, uniqueName, variablesIn, type AgentBody,
} from './agents';

const body: AgentBody = {
  description: 'd', prompt: 'Analyse {refund} and {nothing}. Use {{ refund.amount }}.', role: 'reason', model: null, fallback: null, temperature: 0.3, icon: 'shield', execution_mode: 'native_llm', tools: [],
  inputs: [{ name: 'refund', type: 'object', source: 'brief', required: true }], outputs: [{ name: 'score', type: 'number', artefact_type: 'RISK_ASSESSMENT', format: 'JSON' }], children: [], stage_kind: 'specialist',
};

describe('prompt variables', () => {
  it('finds variables, ignoring the ones inside expressions', () => {
    expect(variablesIn(body.prompt)).toEqual(['refund', 'nothing']);
  });
  it('splits a prompt into text and variables and marks the undeclared ones', () => {
    const s = segments(body.prompt, ['refund']);
    expect(s.filter((x) => x.variable).map((x) => [x.variable, x.declared])).toEqual([['refund', true], ['nothing', false]]);
    expect(s.map((x) => x.text).join('')).toBe(body.prompt);
  });
  it('inserts at the cursor and appends a block on its own line', () => {
    expect(insertAt('Use  here', 4, '{a}')).toEqual({ text: 'Use {a} here', cursor: 7 });
    expect(insertAt('x', 99, 'y').text).toBe('xy');
    expect(appendLine('One.\n\n', 'Two.')).toBe('One.\nTwo.');
  });
});

describe('names, samples and the palette', () => {
  it('picks a name that is not taken', () => {
    expect(uniqueName('input', ['input_1', 'input_2'])).toBe('input_3');
    expect(uniqueName('output', [])).toBe('output_1');
  });
  it('makes a sample input for each declared type', () => {
    expect(sampleInputs([{ name: 'a', type: 'object', source: 'brief', required: true }, { name: 'b', type: 'list', source: 'user', required: true }])).toEqual({ a: { example: 'value' }, b: ['item one', 'item two'] });
  });
  it('round-trips a dragged block and ignores anything else', () => {
    expect(decodePalette(encodePalette({ kind: 'snippet', value: 'a | b' }))).toEqual({ kind: 'snippet', value: 'a | b' });
    expect(decodePalette('file|x')).toBeNull();
    expect(decodePalette('nonsense')).toBeNull();
  });
});

describe('status and steps', () => {
  it('names each status and numbers the approved version', () => {
    expect(statusChip('published', 3)).toEqual({ label: 'Approved v3', tone: 'green' });
    expect(statusChip('pending').tone).toBe('amber');
    expect(statusChip('rejected').label).toBe('Changes requested');
  });
  it('puts a draft on step 1 until it has a fresh audit', () => {
    expect([stepIndex('draft', false), stepIndex('draft', true), stepIndex('pending', true), stepIndex('published', true), stepIndex('rejected', false)]).toEqual([0, 1, 2, 3, 0]);
  });
  it('allows submission only for a fresh audit with no blocks and no unaccepted warnings', () => {
    const a = (block: number, warn: number, ack = false, stale = false) => ({ ranAt: 'x', stale, ack, summary: { block, warn, pass: 1 } });
    expect([auditSubmittable(a(0, 0)), auditSubmittable(a(1, 0)), auditSubmittable(a(0, 2)), auditSubmittable(a(0, 2, true)), auditSubmittable(a(0, 0, false, true)), auditSubmittable(null)]).toEqual([true, false, false, true, false, false]);
  });
});

describe('developer mode', () => {
  it('round-trips a definition through JSON', () => {
    const back = fromDeveloperJson(toDeveloperJson('Fraud', body), body);
    expect(back.ok && back.name).toBe('Fraud');
    expect(back.ok && back.body.inputs).toEqual([{ name: 'refund', type: 'object', source: 'brief', required: true, description: '' }]);
    expect(back.ok && back.body.outputs[0]).toEqual(body.outputs[0]);
    expect(back.ok && back.body.prompt).toBe(body.prompt);
  });
  it('says what is wrong with bad JSON', () => {
    expect(fromDeveloperJson('{', body)).toMatchObject({ ok: false });
    expect(fromDeveloperJson('{"identity": {}}', body)).toEqual({ ok: false, error: 'identity.system_prompt is required' });
  });
});

describe('delegation and diffs', () => {
  it('shows the limits as meters', () => {
    expect(delegationMeters(0)[1]).toEqual({ label: 'Delegates', value: 0, max: 5 });
    expect(delegationMeters(2)[2].value).toBe(26);
  });
  it('shows what a version added and removed', () => {
    expect(lineDiff('a\nb', 'a\nc')).toEqual([{ kind: 'del', text: 'b' }, { kind: 'same', text: 'a' }, { kind: 'add', text: 'c' }]);
  });
});

describe('budgets', () => {
  it('reads a token cap: empty is the platform limit, and the range is enforced', () => {
    expect(parseCap('')).toEqual({ ok: true, value: null });
    expect(parseCap(' 5,000 ')).toEqual({ ok: true, value: 5000 });
    expect(parseCap('12.5')).toMatchObject({ ok: false });
    expect(parseCap('10')).toMatchObject({ ok: false, error: expect.stringContaining('1,000') });
    expect(parseCap('90000')).toMatchObject({ ok: false });
  });
  it('shows how much of a budget is spent, never more than all of it', () => {
    expect([spentPercent(50, 200), spentPercent(500, 200), spentPercent(5, null), spentPercent(5, 0)]).toEqual([25, 100, 0, 0]);
    expect([tokensLabel(950), tokensLabel(12_400), tokensLabel(2_500_000)]).toEqual(['950', '12k', '2.5M']);
  });
  it('keeps the cap through developer-mode JSON', () => {
    const b = { ...body, budget_tokens: 4000 };
    const back = fromDeveloperJson(toDeveloperJson('Fraud', b), body);
    expect(back.ok && back.body.budget_tokens).toBe(4000);
  });
});

describe('running and ordering', () => {
  it('moves an agent to a new place in the order, and ignores a move that goes nowhere', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a']);
    expect(moveItem(['a', 'b', 'c'], 2, 1)).toEqual(['a', 'c', 'b']);
    const same = ['a', 'b'];
    expect(moveItem(same, 0, 5)).toBe(same);
    expect(moveItem(same, 1, 1)).toBe(same);
  });
  it('checks a typed value against its type', () => {
    expect(parseRunValue('0.5', 'number')).toEqual({ ok: true, value: 0.5 });
    expect(parseRunValue('', 'number')).toMatchObject({ ok: false });
    expect(parseRunValue('Yes', 'boolean')).toEqual({ ok: true, value: true });
    expect(parseRunValue('maybe', 'boolean')).toMatchObject({ ok: false });
    expect(parseRunValue('{"a":', 'object')).toMatchObject({ ok: false });
    expect(parseRunValue('one\ntwo', 'list')).toEqual({ ok: true, value: 'one\ntwo' });
  });
  it('shows an output as text and names the file it downloads as', () => {
    expect(outputText('plain', 'Markdown')).toBe('plain');
    expect(outputText({ a: 1 }, 'JSON')).toBe('{\n  "a": 1\n}');
    expect(outputText([1], 'Markdown')).toContain('```json');
    expect(outputFileName('Refund fraud screen', 'score', 'JSON')).toBe('refund-fraud-screen-score.json');
    expect(outputFileName('!!', '', 'Text')).toBe('output.txt');
  });
  it('lists the artefact types a stage of agents writes, once each', () => {
    const o = (t: string) => ({ name: 'x', type: 'string' as const, artefact_type: t, format: 'Markdown' as const });
    expect(agentOutputTypes([{ outputsDetail: [o('REPORT'), o('CHECKLIST')] }, { outputsDetail: [o('REPORT')] }])).toEqual(['REPORT', 'CHECKLIST']);
  });
});
