import { describe, expect, it } from 'vitest';
import {
  entryHealth, isDirty, joinModel, move, seedDraft, splitModel, toPayload, type CatalogModel, type RoleView,
} from './modelRoutes';

const role = (r: RoleView['role'], models: string[]): RoleView => ({
  role: r, label: r, description: '', stageSelectable: true, models, envDefault: [], effective: models,
  source: models.length ? 'admin' : 'default',
});

describe('model id helpers', () => {
  it('splits and joins provider/model, keeping slashes inside the model', () => {
    expect(splitModel('bedrock/us.anthropic.claude-haiku-4-5')).toEqual({ provider: 'bedrock', model: 'us.anthropic.claude-haiku-4-5' });
    expect(splitModel('gemini/models/flash')).toEqual({ provider: 'gemini', model: 'models/flash' });
    expect(splitModel('bare')).toEqual({ provider: '', model: 'bare' });
    expect(joinModel({ provider: 'groq', model: '  llama-3 ' })).toBe('groq/llama-3');
    expect(joinModel({ provider: '', model: 'x' })).toBe('');
  });
});

describe('payload and dirty tracking', () => {
  it('drops blanks and duplicates, omits empty roles and caps the chain', () => {
    const draft = {
      reason: [{ provider: 'bedrock', model: 'opus' }, { provider: 'bedrock', model: 'opus' }, { provider: 'bedrock', model: '' }],
      light: [],
      plan: Array.from({ length: 6 }, (_, i) => ({ provider: 'groq', model: `m${i}` })),
    };
    const out = toPayload(draft, 4);
    expect(out.reason).toEqual(['bedrock/opus']);
    expect(out.light).toBeUndefined();
    expect(out.plan).toHaveLength(4);
  });

  it('is clean when seeded from the saved routes and dirty after any edit', () => {
    const roles = [role('reason', ['bedrock/opus', 'bedrock/sonnet']), role('light', [])];
    const draft = seedDraft(roles);
    expect(isDirty(draft, roles)).toBe(false);
    expect(isDirty({ ...draft, light: [{ provider: 'gemini', model: 'flash' }] }, roles)).toBe(true);
    expect(isDirty({ ...draft, reason: move(draft.reason!, 0, 1) }, roles)).toBe(true);   // order matters
  });
});

describe('move', () => {
  it('reorders without mutating and ignores moves off the ends', () => {
    const a = ['x', 'y', 'z'];
    expect(move(a, 0, 1)).toEqual(['y', 'x', 'z']);
    expect(a).toEqual(['x', 'y', 'z']);
    expect(move(a, 0, -1)).toBe(a);
    expect(move(a, 2, 1)).toBe(a);
  });
});

describe('entryHealth', () => {
  const catalog: CatalogModel[] = [
    { id: 'bedrock/a', provider: 'bedrock', model: 'a', healthy: true, configured: true, vision: true, reason: null },
    { id: 'groq/b', provider: 'groq', model: 'b', healthy: false, configured: true, vision: false, reason: 'breaker open' },
  ];
  it('flags unconfigured, unhealthy and non-vision providers', () => {
    expect(entryHealth({ provider: 'bedrock', model: 'x' }, catalog, 'light').tone).toBe('ok');
    expect(entryHealth({ provider: 'gemini', model: 'x' }, catalog, 'light').tone).toBe('warn');
    expect(entryHealth({ provider: 'groq', model: 'x' }, catalog, 'light').text).toBe('breaker open');
    expect(entryHealth({ provider: 'bedrock', model: 'x' }, [{ ...catalog[0]!, vision: false }], 'vision').text).toMatch(/vision/);
    expect(entryHealth({ provider: '', model: '' }, catalog, 'light').tone).toBe('info');
  });
});
