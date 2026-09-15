import { describe, expect, it } from 'vitest';
import { AgentStateSchema } from './agent-state.js';
import { loadEnv, OrchestratorEnvSchema } from './env.js';
import { SdlcError, toSdlcError } from './errors.js';
import { ulid } from './ids.js';
import { canReviewPhase, getPhase, PHASES, primaryRole } from './phases.js';
import { estimateTokens } from './tokens.js';
import { getTool, TOOL_REGISTRY } from './tools.js';

describe('phases', () => {
  it('defines exactly six ordered phases', () => {
    expect(PHASES.map((p) => p.id)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it('enforces reviewer role per phase; SUPER_ADMIN overrides; PM never reviews', => {
    expect(canReviewPhase('PO', 1)).toBe(true);
    expect(canReviewPhase('PO', 2)).toBe(false);
    expect(canReviewPhase('SUPER_ADMIN', 5)).toBe(true);
    expect(canReviewPhase('PROJECT_MANAGER', 1)).toBe(false);
    expect(canReviewPhase('PROJECT_MANAGER', 6)).toBe(false);
    expect(canReviewPhase('QA', 4)).toBe(true);
    expect(canReviewPhase('DEV', 6)).toBe(true);
  });

  it('primaryRole picks highest-priority realm role for JIT provisioning', () => {
    expect(primaryRole(['DEV', 'SUPER_ADMIN'])).toBe('SUPER_ADMIN');
    expect(primaryRole(['offline_access', 'QA'])).toBe('QA');
    expect(primaryRole(['uma_authorization'])).toBeNull();
  });

  it('throws on unknown phase', () => {
    expect(() => getPhase(7)).toThrow(RangeError);
  });
});

describe('env', () => {
  it('lists every missing variable in one error', () => {
    expect(() => loadEnv(OrchestratorEnvSchema, {})).toThrow(/DATABASE_URL[\s\S]*JWT_SECRET/);
  });

  it('parses a valid orchestrator env', () => {
    const env = loadEnv(OrchestratorEnvSchema, {
      DATABASE_URL: 'postgresql://u:p@h:5432/db',
      DYNAMO_ENDPOINT: 'http://localhost:8000',
      JWT_SECRET: 'x'.repeat(64),
      AI_CLIENT_URL: 'http://localhost:8081',
      TOOLS_MCP_URL: 'http://localhost:8082/mcp',
    });
    expect(env.ORCHESTRATOR_PORT).toBe(8080);
    expect(env.BUILD_LOOP_MAX_ITERATIONS).toBe(5);
  });
});

describe('agent state', () => {
  it('applies defaults for a minimal state', () => {
    const state = AgentStateSchema.parse({
      projectId: 'p1',
      sessionId: 's1',
      currentPhase: 1,
      userInput: 'Build a payments API',
    });
    expect(state.contextWindow).toEqual([]);
    expect(state.gateStatus).toBe('IN_PROGRESS');
  });

  it('rejects out-of-range phase', () => {
    expect(() =>
      AgentStateSchema.parse({ projectId: 'p', sessionId: 's', currentPhase: 9, userInput: 'x' }),
    ).toThrow();
  });
});

describe('tools registry', () => {
  it('validates tool inputs against schemas', () => {
    const t = getTool('jira_create_epic');
    expect(() => t.input.parse({ description: 'no title' })).toThrow();
    const parsed = t.input.parse({ title: 'Epic', description: 'd' });
    expect(parsed).toMatchObject({ priority: 'Medium' });
  });

  it('has unique names matching registry keys', () => {
    for (const [key, def] of Object.entries(TOOL_REGISTRY)) {
      expect(def.name).toBe(key);
    }
  });
});

describe('errors', () => {
  it('maps codes to default statuses and wraps unknowns', () => {
    expect(new SdlcError('AUTH_FAILED', 'nope').httpStatus).toBe(401);
    expect(toSdlcError(new Error('boom')).code).toBe('INTERNAL');
  });
});

describe('utils', () => {
  it('estimates tokens deterministically', () => {
    expect(estimateTokens('')).toBe(0);
    expect(estimateTokens('a'.repeat(400))).toBe(100);
  });

  it('ulid is 26 chars and time-ordered', () => {
    const a = ulid(1_000_000);
    const b = ulid(2_000_000);
    expect(a).toHaveLength(26);
    expect(a < b).toBe(true);
  });
});
