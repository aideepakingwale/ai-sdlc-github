import { describe, expect, it } from 'vitest';
import { inputChoices, upstreamOutputs, type StageIO } from './workflowInputs';

const stages: StageIO[] = [
  { key: 'po', dependsOn: [], outputs: ['PRD', 'EPIC'] },
  { key: 'sa', dependsOn: ['po'], outputs: ['HLD', 'ADR'] },
  { key: 'ta', dependsOn: ['sa'], outputs: ['LLD'], inputs: ['HLD', 'LLD'] },
  { key: 'qa', dependsOn: ['po'], outputs: ['TEST_STRATEGY'] },
];

describe('workflow stage inputs', () => {
  it('offers only upstream outputs: not the stage\'s own, not later stages\', not a sibling branch', () => {
    expect(upstreamOutputs(stages, 'sa')).toEqual(['requirements', 'PRD', 'EPIC']);
    expect(upstreamOutputs(stages, 'ta')).toEqual(['requirements', 'PRD', 'EPIC', 'HLD', 'ADR']);
    expect(upstreamOutputs(stages, 'qa')).toEqual(['requirements', 'PRD', 'EPIC']);
    expect(upstreamOutputs(stages, 'po')).toEqual(['requirements']);
    expect(upstreamOutputs(stages, 'ta')).not.toContain('LLD');
  });

  it('still shows a selected input that nothing upstream makes, flagged so it can be removed', () => {
    const c = inputChoices(stages, 'ta');
    expect(c.find((x) => x.name === 'LLD')).toEqual({ name: 'LLD', unmet: true });
    expect(c.find((x) => x.name === 'HLD')).toEqual({ name: 'HLD', unmet: false });
  });

  it('survives a dependency cycle without looping', () => {
    const cyc: StageIO[] = [{ key: 'a', dependsOn: ['b'], outputs: ['A'] }, { key: 'b', dependsOn: ['a'], outputs: ['B'] }];
    expect(upstreamOutputs(cyc, 'a')).toEqual(['requirements', 'B']);
  });
});
