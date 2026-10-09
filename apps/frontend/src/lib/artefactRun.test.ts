import { describe, expect, it } from 'vitest';
import { approxTokens, groupContext } from './artefactRun';

describe('artefact run helpers', () => {
  it('groups the context a run was given by layer, in the order it was given', () => {
    const g = groupContext([
      { layer: 'instructions', label: 'PRD writer instructions', chars: 200 },
      { layer: 'upstream', label: 'PRD', chars: 4000 }, { layer: 'upstream', label: 'EPIC', chars: 1000 },
      { layer: 'input', label: "Reviewer's brief", chars: 50 },
    ]);
    expect(g.map((x) => x.layer)).toEqual(['instructions', 'upstream', 'input']);
    expect(g[1]).toMatchObject({ label: 'Earlier stages', chars: 5000 });
  });
  it('estimates tokens as a quarter of the characters, never zero', () => {
    expect(approxTokens(4000)).toBe(1000);
    expect(approxTokens(0)).toBe(1);
  });
});
