import { describe, expect, it } from 'vitest';
import {
  appendLine, auditSubmittable, decodePalette, delegationMeters, encodePalette, fromDeveloperJson, insertAt, lineDiff, sampleInputs, segments, statusChip, stepIndex,
  toDeveloperJson, uniqueName, variablesIn, type AgentBody,
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
